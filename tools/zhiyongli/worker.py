#!/usr/bin/env python3
"""管理员任务的后台Worker；专用数据库与导入权限，逐阶段续跑，无自动发布。"""
import argparse
from contextlib import contextmanager,nullcontext
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_EXCEPTION
from datetime import datetime,timedelta,timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from model_config import NoRedirect, ModelError
from runtime import CompanyClient, atomic
from source_snapshot import collect,require_current_query
from source_frames import read_signed
from pipeline import prepare,evaluate_week,evaluate_month,Paused

RULES='1.6'

class JobClient(CompanyClient):
    """按任务账簿预记新发送；跨任务复用回执不消耗本任务预算。"""
    # 复查分歧时可接收前一候选及逐项意见做有界修订；修订请求同样计入任务与全局预算。
    accepts_revision_feedback=True
    def __init__(self,config,root,limit,new_request_limit=1,flow=None):
        super().__init__(config);self.root=root;self.limit=limit;self.new_request_limit=new_request_limit
        from request_budget import RequestBudget
        self.budget=RequestBudget(root,limit,new_request_limit)
        self.reading_cache_root=None
        self.reading_plan_root=None
        self.navigation_cache_root=None
        self.flow=flow
    def preflight_minimum(self,count):
        if self.reading_cache_root is not None:return
        used=self.budget.used()
        if not used and count>self.limit:
            raise Paused('needs_review','完整处理的最低请求量已超过本任务预算；未发送模型请求，保留全部来源，待调整执行预算')
    def _call(self,payload,receipt):
        from disk_guard import require_space
        require_space(self.root)
        if not receipt.exists():self.budget.check(receipt)
        from request_retry import call
        return call(self,payload,receipt,super()._call)
    @contextmanager
    def send_slot(self,payload,receipt,request_hash,body):
        # 运维暂停只阻止新请求，已发送请求仍按原账簿落盘，避免强杀制造unknown。
        if (self.root.parent/'pause-new-requests').exists():
            raise Paused('queued','评审新请求已暂停，已有回执保留，等待明确恢复')
        slot=self.flow.slot(request_hash,receipt,body,payload.get('max_output_tokens',0)) if self.flow else nullcontext()
        with slot:
            self.budget.reserve(receipt,request_hash)
            yield

def private_value(path):
    p=Path(path);info=p.lstat()
    if not p.is_absolute() or not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_size>8192:
        raise ValueError('专用配置须为绝对路径的私有普通文件')
    return p.read_text().strip()

def private_key(path):
    key=private_value(path)
    if len(key)<32 or any(c.isspace() for c in key): raise ValueError('Worker密钥内容无效')
    return key

def importer(base_url,key_file):
    url=urlsplit(base_url)
    if (url.username or url.password or url.query or url.fragment or url.path not in {'','/'}
        or not (url.scheme=='https' or url.scheme=='http' and url.hostname in {'127.0.0.1','localhost','sub2api'})):
        raise ValueError('报告接入端点须为HTTPS或明确的本地/容器内部服务')
    def send(job,bundle):
        request=urllib.request.Request(base_url.rstrip('/')+'/api/v1/internal/zhiyongli/jobs/'+str(job['id'])+'/import',
            data=json.dumps(bundle,ensure_ascii=False).encode(),headers={'Content-Type':'application/json',
                'X-Zhiyongli-Worker-Key':private_key(key_file)})
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=60) as stream:
            value=json.load(stream)
        if value.get('code')!=0: raise ValueError('候选接入未成功')
        return value['data']
    return send

def schedule_request(base_url,key_file):
    # 复用接入端点的地址校验；调度无调用方自填的身份、周期或结果。
    importer(base_url,key_file)
    request=urllib.request.Request(base_url.rstrip('/')+'/api/v1/internal/zhiyongli/schedule',data=b'',method='POST',
        headers={'X-Zhiyongli-Worker-Key':private_key(key_file)})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=30) as stream:value=json.load(stream)
    if value.get('code')!=0:raise ValueError('自动调度未成功')
    return value['data']

def update(db,jid,state,phase,done=0,total=0,message=''):
    db.execute('''UPDATE zhiyongli_jobs SET state=%s,phase=%s,completed_units=%s,total_units=%s,
        message=%s,updated_at=now() WHERE id=%s''',(state,phase,done,total,message,jid))

def process(db,job,state,source,client,send,excluded_keys,max_requests=100,new_request_limit=1):
    root=Path(state)/str(job['id']);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    last=['collecting',0,1]
    def progress(phase,done,total,message):
        from disk_guard import require_space
        require_space(root)
        last[:]=[phase,done,total];update(db,job['id'],'running',phase,done,total,message)
    try:
        from personal_scope import require_personal_user
        require_personal_user(job['user_id'])
        from dsh_client import DSHClient
        client=(DSHClient(client.config,client.dsh,root,max_requests) if isinstance(client,DSHClient)
            else JobClient(client.config,root,max_requests,new_request_limit,getattr(client,'flow',None)))
        if isinstance(client,JobClient):
            client.reading_cache_root=Path(state)/'read-pages'
            client.reading_plan_root=Path(state)/'reading-plans'
            client.navigation_cache_root=Path(state)/'navigation-branches'
        scope=job.get('workday_scope')
        if scope is None:raise Paused('needs_review','历史自然周任务未绑定工作日范围，保留原记录；请按当前日历另建评审任务')
        from workday_scope import validate
        validate(scope)
        if datetime.now(timezone.utc)<datetime.fromisoformat(scope['freeze_at']):
            raise Paused('queued','尚未到本期来源封口时刻，不提前采集和评分')
        progress('collecting',0,1,'读取所选人员本期来源；冻结后从原回执续跑')
        source_root=root/'sources'
        start=str(job['period_start'])+'T00:00:00+08:00';end=str(job['period_end'])+'T00:00:00+08:00'
        lo=datetime.fromisoformat(start);scan_start=lo-timedelta(days=lo.weekday())
        if not (source_root/'latest.json').exists():
            collect(source,source_root,excluded_keys,scan_start.isoformat(),[job['user_id']],shared_root=Path(state)/'source-cache')
        snapshot=read_signed(source_root,json.loads((source_root/'latest.json').read_text()))
        person=next((u for u in snapshot['users'] if u['id']==job['user_id']),None)
        if not person or person['email']!=job['user_email']:
            raise Paused('failed','来源账户与任务人员映射不一致；未发送模型请求，请先核对两个数据库的账户身份')
        require_current_query(snapshot)
        from review_control import selected
        review_root=selected(root/'review')
        if job['period_type']=='week':
            packet=prepare(source_root,job['user_id'],start,end,workday_scope=scope)
            # 传未选定的基目录：自动修订的各轮目录平铺在同一revisions下，由evaluate_week选定当前轮。
            bundle=evaluate_week(packet,root/'review',client,progress,shared_root=Path(state)/'shared-weeks')
        else:
            bundle=evaluate_month(source_root,job['user_id'],start,end,review_root,client,progress,shared_root=Path(state)/'shared-weeks',workday_scope=scope)
        progress('importing',0,1,'接入已核候选；等待管理员确认发布')
        receipt=root/'import.json'
        if not receipt.exists(): atomic(receipt,send(job,bundle))
        update(db,job['id'],'completed','done',1,1,'候选报告已生成，等待管理员确认发布')
    except Paused as ex:
        if ex.state=='queued':update(db,job['id'],'queued',*last,message=str(ex))
        else:update(db,job['id'],ex.state,'done' if ex.state=='no_activity' else 'paused',message=str(ex))
    except (ValueError,KeyError,TypeError,ModelError):
        # 错误细节只在私有文件留存，不把原文、路径或远端错误体返回页面。
        import traceback
        atomic(root/'failure.json',dict(traceback=traceback.format_exc(),at=datetime.now(timezone.utc).isoformat()))
        update(db,job['id'],'needs_review','paused',message='来源、评审合同或判断校验未通过；已保留原始回执，需核对后继续')
    except Exception as ex:
        atomic(root/'failure.json',dict(error_type=type(ex).__name__,at=datetime.now(timezone.utc).isoformat()))
        update(db,job['id'],'failed','paused',message='后台执行失败；已有阶段和回执已保留，可恢复校验')

def tick(db,source,state,client,send,excluded_keys,max_requests=100,new_request_limit=1):
    from psycopg.rows import dict_row
    with db.cursor(row_factory=dict_row) as cursor:
        jobs=cursor.execute("""SELECT j.*,u.email AS user_email FROM zhiyongli_jobs j
            JOIN users u ON u.id=j.user_id
            WHERE j.state IN ('queued','running') AND j.rules_version=%s ORDER BY j.updated_at,j.id LIMIT 100""",(RULES,)).fetchall()
    for job in jobs:
        locked=db.execute('SELECT pg_try_advisory_lock(245,%s::int)',(job['id'],)).fetchone()[0]
        if not locked:continue
        try:
            # 拿锁后重读，避免等待期间已完成的同一任务再次执行。
            current=db.execute('SELECT state FROM zhiyongli_jobs WHERE id=%s',(job['id'],)).fetchone()[0]
            if current not in {'queued','running'}:continue
            process(db,job,state,source,client,send,excluded_keys,max_requests,new_request_limit)
            return True
        finally:db.execute('SELECT pg_advisory_unlock(245,%s::int)',(job['id'],))
    return False

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--backend-url',default='http://127.0.0.1:8080')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--company-api',action='store_true',help='显式启用已授权模型接入；无此参数仅检查配置')
    parser.add_argument('--executor',choices=['api','dsh'],default='api',help='DSH同样调用模型API，须提供独立运行时配置')
    parser.add_argument('--healthcheck',action='store_true',help='仅核对Worker进程心跳，不发模型请求')
    parser.add_argument('--max-requests-per-job',type=int,default=100,help='含已有回执的每任务请求预算，达到后暂停，不删除来源')
    parser.add_argument('--new-requests-per-turn',type=int,default=1,help='API执行器每轮最多新请求数；完成阶段落盘后公平轮转，不是全任务预算')
    parser.add_argument('--flow-config',type=Path,help='API全局并发/RPM/保守token预留和总预算配置，须显式确认配额')
    parser.add_argument('--workers',type=int,default=1,help='同一状态卷内的执行线程数，每线程独立数据库连接')
    parser.add_argument('--schedule-mode',choices=['disabled','enabled'],default='disabled',help='显式启用服务端受控调度和日常来源准备，仍不自动发布')
    parser.add_argument('--exclude-api-key-id',type=int,action='append')
    args=parser.parse_args()
    if args.healthcheck:
        value=json.loads((args.state/'heartbeat.json').read_text())
        if time.time()-value['at']>60:raise SystemExit(1)
        os.kill(value['pid'],0)
        return
    if not args.company_api:
        print(json.dumps(dict(state='disabled',model_requests=0)));return
    if not args.exclude_api_key_id or any(i<1 for i in args.exclude_api_key_id):
        parser.error('必须显式排除评审专用API Key ID')
    if args.max_requests_per_job<1:parser.error('任务请求预算必须为正整数')
    if not 1<=args.new_requests_per_turn<=20:parser.error('每轮新请求数须在1至20之间')
    if not 1<=args.workers<=32:parser.error('执行线程数须在1至32之间')
    if args.executor=='api' and args.flow_config is None:parser.error('API执行必须指定全局模型配额配置，不使用猜测配额')
    if args.executor=='dsh' and args.workers!=1:parser.error('DSH尚未接入同一全局配额，只允许单执行线程')
    if args.executor=='dsh' and args.schedule_mode=='enabled':parser.error('日常准备当前只接入API全局配额，不隐式切换DSH执行器')
    import psycopg
    def dsn(name):
        path=os.environ.get(name+'_FILE')
        return private_value(path) if path else os.environ.get(name)
    product_dsn=dsn('ZHIYONGLI_DB_DSN');source_dsn=dsn('ZHIYONGLI_SOURCE_DSN')
    if not product_dsn or not source_dsn:parser.error('缺少产品库或只读来源库连接')
    if args.executor=='dsh':
        from dsh_client import DSHClient,DSHConfig
        from model_config import ModelConfig
        client=DSHClient(ModelConfig.load(),DSHConfig.load())
    else:
        from model_flow import ModelFlow
        client=CompanyClient();client.flow=ModelFlow(args.state/'flow',json.loads(args.flow_config.read_text()))
        if args.workers>client.flow.config['max_concurrency']:parser.error('执行线程数不能超过已确认的模型并发配额')
    send=importer(args.backend_url,os.environ['ZHIYONGLI_WORKER_KEY_FILE'])
    from preparation import Preparations
    preparations=Preparations(args.state) if args.schedule_mode=='enabled' else None
    args.state.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(args.state,0o700)
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,stop)
    with (args.state/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if getattr(client,'flow',None):client.flow.reconcile_startup(lock)
        heartbeat_stop=threading.Event()
        def heartbeat():
            while not heartbeat_stop.is_set():
                value=dict(pid=os.getpid(),at=time.time(),rules_version=RULES,workers=args.workers)
                if getattr(client,'flow',None):
                    value.update(model_flow=client.flow.snapshot(),waiting_for=client.flow.blocked())
                atomic(args.state/'heartbeat.json',value)
                heartbeat_stop.wait(15)
        pulse=threading.Thread(target=heartbeat,daemon=True);pulse.start()
        def schedule():
            while not stopped:
                try:
                    value=schedule_request(args.backend_url,os.environ['ZHIYONGLI_WORKER_KEY_FILE'])
                    preparations.enqueue(value.get('preparation'))
                    atomic(args.state/'schedule-status.json',dict(state='enabled' if value['enabled'] else 'disabled',
                        at=time.time(),assessment_jobs=len(value.get('jobs') or []),
                        non_evaluation_weeks=value.get('non_evaluation_weeks') or [],daily_preparation=bool(value.get('preparation'))))
                except Exception as ex:
                    atomic(args.state/'schedule-status.json',dict(state='needs_review',at=time.time(),error_type=type(ex).__name__))
                if args.once:break
                heartbeat_stop.wait(60)
        scheduler=threading.Thread(target=schedule,daemon=True) if preparations else None
        if scheduler:
            if args.once:schedule()
            else:scheduler.start()
        try:
            def run():
                # psycopg连接和会话锁不能在线程间共用。
                with psycopg.connect(product_dsn,autocommit=True,connect_timeout=10) as db,psycopg.connect(source_dsn,connect_timeout=10) as source:
                    while not stopped:
                        worked=tick(db,source,args.state,client,send,args.exclude_api_key_id,args.max_requests_per_job,args.new_requests_per_turn)
                        if not worked and preparations:
                            worked=preparations.tick(source,client,args.exclude_api_key_id,args.max_requests_per_job,args.new_requests_per_turn)
                        if args.once:break
                        # 配额等待时任务仍queued，不能无休止紧循环扫库。
                        if worked:
                            waiting=getattr(client,'flow',None) and client.flow.blocked()
                            heartbeat_stop.wait(2 if waiting else 0.2);continue
                        for _ in range(10):
                            if stopped:break
                            heartbeat_stop.wait(1)
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures=[pool.submit(run) for _ in range(args.workers)]
                try:
                    done,_=wait(futures,return_when=FIRST_EXCEPTION)
                    for future in done:future.result()
                except BaseException:
                    stopped=True;heartbeat_stop.set();raise
        finally:
            heartbeat_stop.set();pulse.join(timeout=2)
            if scheduler and scheduler.is_alive():scheduler.join(timeout=2)
            (args.state/'heartbeat.json').unlink(missing_ok=True)

if __name__=='__main__':main()
