#!/usr/bin/env python3
"""独立只读观测Redis、PG及显式选择的Worker；任一失败不抹掉其他结果。"""
import argparse
import datetime
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

REDIS = "\nlocal a=ARGV[1]\nlocal k=ARGV[2]\nreturn {redis.call('ZCARD','concurrency:account:'..a),\n redis.call('ZCARD','concurrency:live:account:'..a),\n tonumber(redis.call('GET','wait:account:'..a) or '0'),\n redis.call('ZCARD','concurrency:api_key:'..k),\n redis.call('ZCARD','concurrency:live:api_key:'..k)}\n"
WORKER = "\nimport json,sqlite3,time,sys\nfrom pathlib import Path\nc=json.loads(Path(sys.argv[1]).read_text())\nh=json.loads(Path(sys.argv[2]).read_text())\nd=sqlite3.connect(Path(sys.argv[3]).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)\nwith d:\n states=dict(d.execute('select status,count(*) from attempts group by status'))\n recent429=d.execute('select count(*) from attempts where error=? and started>?',('http_429',time.time()-300)).fetchone()[0]\nd.close()\nmemory={n:Path('/sys/fs/cgroup/memory',n).read_text().strip() for n in ('memory.usage_in_bytes','memory.max_usage_in_bytes','memory.failcnt')}\nprint(json.dumps(dict(states=states,recent_5m_429=recent429,memory=memory,\n heartbeat_age_seconds=round(time.time()-h['at']),heartbeat_at=h['at'],workers=h['workers'],\n config={k:c[k] for k in ('max_concurrency','requests_per_minute','max_requests','budget_key')},\n paused=Path(sys.argv[4]).exists())))\n"


def run(args,payload=None):
    return subprocess.check_output(args,input=payload,universal_newlines=True,
        timeout=20,stderr=subprocess.PIPE).strip()


def container(service):
    value=run(['docker','ps','-q','--filter','label=com.docker.compose.project=deploy',
        '--filter','label=com.docker.compose.service='+service])
    if not value or '\n' in value:raise ValueError('container_count_invalid')
    return value


def unavailable(reason='sample_failed'):
    # 不输出异常正文、命令、stderr、连接串或凭据。
    return dict(status='unavailable',reason=reason)


def observe_redis(args):
    values=json.loads(run(['docker','exec',container('redis'),'sh','-c',
        'if [ -z "${REDISCLI_AUTH:-}" ]; then unset REDISCLI_AUTH; fi; exec redis-cli --json "$@"',
        'load-monitor','EVAL_RO',REDIS,'0',str(args.account_id),str(args.review_key_id)]))
    if not isinstance(values,list) or len(values)!=5 or any(type(v) is not int or v<0 for v in values):
        raise ValueError('redis_sample_invalid')
    return dict(status='observed',account_reserved_slots=values[0]+values[1],account_waiting=values[2],
        review_key_reserved_slots=values[3]+values[4])


def observe_postgres(args):
    sql="""BEGIN READ ONLY; SET LOCAL statement_timeout='10s';
SELECT model,count(*),count(*) FILTER (WHERE api_key_id=%d),round(avg(duration_ms)),round(avg(first_token_ms))
FROM usage_logs WHERE created_at>now()-interval '5 minutes' AND account_id=%d GROUP BY model;
COMMIT;""" % (args.review_key_id,args.account_id)
    usage=run(['docker','exec','-i',container('postgres'),'sh','-c',
        'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F"|"'],sql)
    return dict(status='observed',usage_5m_by_model=usage.splitlines())


def observe_worker(args):
    selected=args.worker_container
    if not selected:return unavailable('worker_not_configured')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',selected):raise ValueError('worker_selector_invalid')
    selector='id='+selected if re.fullmatch(r'[a-fA-F0-9]{12,64}',selected) else 'name=^/'+re.escape(selected)+'$'
    found=run(['docker','ps','-a','-q','--filter',selector])
    if not found:return dict(unavailable('worker_missing'),container_state='missing')
    if '\n' in found:raise ValueError('worker_count_invalid')
    meta=json.loads(run(['docker','inspect',found]))
    if not isinstance(meta,list) or len(meta)!=1:raise ValueError('worker_inspect_invalid')
    meta=meta[0];state=meta['State'];host=meta['HostConfig']
    if type(state['Running']) is not bool:raise ValueError('worker_running_invalid')
    result=dict(worker_container_id=meta['Id'],container_state='running' if state['Running'] else 'stopped',
        worker_restart_count=meta['RestartCount'],worker_started_at=state['StartedAt'],
        worker_memory_limit_bytes=host['Memory'],worker_cpu_limit=host['NanoCpus']/1e9)
    if not state['Running']:return dict(result,**unavailable('worker_stopped'))
    for path in (args.flow_path,args.heartbeat_path,args.flow_db_path,args.pause_path):
        if not isinstance(path,str) or not path.startswith('/') or '?' in path or '\x00' in path:
            raise ValueError('worker_path_invalid')
    try:
        worker=json.loads(run(['docker','exec',found,'python','-c',WORKER,
            args.flow_path,args.heartbeat_path,args.flow_db_path,args.pause_path]))
        if not isinstance(worker,dict) or set(worker)!={'states','recent_5m_429','memory','heartbeat_age_seconds',
                'heartbeat_at','workers','config','paused'}:raise ValueError('worker_sample_invalid')
        age=worker['heartbeat_age_seconds'];stamp=worker['heartbeat_at']
        if (type(age) not in (int,float) or not math.isfinite(age) or age<0
                or type(stamp) not in (int,float) or not math.isfinite(stamp)
                or abs(time.time()-stamp-age)>5):raise ValueError('heartbeat_invalid')
        if age>args.heartbeat_max_age:return dict(result,**unavailable('worker_heartbeat_stale'))
        return dict(result,status='observed',worker=worker)
    except Exception:
        return dict(result,**unavailable('worker_runtime_unavailable'))


def sample(args):
    if any(type(v) is not int or v<1 for v in (args.account_id,args.review_key_id,args.account_limit,args.heartbeat_max_age)):
        raise ValueError('monitor_argument_invalid')
    blocks={}
    for name,observer in (('redis',observe_redis),('postgres',observe_postgres),('worker',observe_worker)):
        started=datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:blocks[name]=observer(args)
        except Exception:blocks[name]=unavailable()
        blocks[name].update(sample_started_at=started,sample_finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    count=sum(b['status']=='observed' for b in blocks.values())
    result=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        status='observed' if count==3 else 'partial' if count else 'unavailable',
        account_id=args.account_id,configured_account_limit=args.account_limit,observations=blocks,
        control='observe_only',automatic_increase_enabled=False,
        scope='独立采样AI Hub账号Redis槽位、该账号近5分钟PG用量及显式所选Worker。Redis包含未裁剪过期槽位；账号与Key槽位不可相减，不代表GPU或模型全局并发。PG空行只表示当前查询无记录；Worker不可用不代表负载为零。')
    # 保留旧正常采样字段；缺失块不补零，也不沿用上次值。
    for block in blocks.values():
        if block['status']=='observed':result.update({k:v for k,v in block.items() if k not in {'status','sample_started_at','sample_finished_at'}})
    return result


def save(root,value):
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    temp=root/'latest.tmp';temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');temp.chmod(0o600)
    os.replace(temp,root/'latest.json')
    with (root/('samples-'+value['at'][:10]+'.jsonl')).open('a') as stream:
        stream.write(json.dumps(value,ensure_ascii=False)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--account-id',type=int,required=True);parser.add_argument('--review-key-id',type=int,required=True)
    parser.add_argument('--account-limit',type=int,required=True);parser.add_argument('--once',action='store_true')
    parser.add_argument('--worker-container',help='显式所选容器名或ID；未选时仅Worker块不可用')
    parser.add_argument('--flow-path',default='/run/zhiyongli/flow.json')
    parser.add_argument('--heartbeat-path',default='/state/heartbeat.json')
    parser.add_argument('--flow-db-path',default='/state/flow/model-flow.sqlite')
    parser.add_argument('--pause-path',default='/state/pause-new-requests')
    parser.add_argument('--heartbeat-max-age',type=int,default=120)
    args=parser.parse_args()
    if min(args.account_id,args.review_key_id,args.account_limit,args.heartbeat_max_age)<1:parser.error('标识、限额及心跳时限必须为正整数')
    os.umask(0o077)
    while True:
        try:value=sample(args)
        except Exception:
            value=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='unavailable',
                control='observe_only',automatic_increase_enabled=False,
                observations={name:unavailable() for name in ('redis','postgres','worker')},
                scope='本轮采样不可用；未给出负载或Worker状态，不代表GPU或模型全局并发。')
        save(args.output,value)
        if args.once:print(json.dumps(value,ensure_ascii=False));return
        time.sleep(60)


if __name__=='__main__':main()
