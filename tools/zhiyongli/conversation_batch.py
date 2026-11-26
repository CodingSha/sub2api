"""基于冻结来源批量生成新版日周候选；本程序不采集、不导入、不发布。"""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import fcntl
import hashlib
import json
from pathlib import Path
import time

from bounded_read import checkpoint
from conversation_reporting import evaluate_day,empty_day,evaluate_week,canonical_daily_review
from conversation_review import VERSION
from model_config import ModelConfig
from model_flow import ModelFlow
from personal_scope import require_personal_user
from runtime import atomic,digest,policy_files
from task_review_policy import RUBRIC
from workday_scope import window,daily_window
from worker import JobClient

MODULES=('conversation_review.py','conversation_transport.py','conversation_quality.py','conversation_repair.py','conversation_product.py','conversation_attribution.py',
    'conversation_provenance.py','conversation_source.py','conversation_reporting.py','conversation_batch.py','task_review_policy.py')


def signature():
    return digest(dict(base=policy_files(),conversation={name:Path(__file__).with_name(name).read_text() for name in MODULES}))


def roster(manifest, scope):
    if manifest.get('version')!=3 or manifest.get('failures'):raise ValueError('冻结来源未完整通过核对')
    groups={};identities={}
    for row in manifest['entries']:
        uid=row['user_id'];require_personal_user(uid)
        if row['file']!=str(uid)+'-'+row['day']+'.json' or row['source_status'] not in {'tasks','no_activity'}:
            raise ValueError('来源文件名、人员日期或状态不符')
        if uid in identities and identities[uid]!=row['user_email']:raise ValueError('同人五日身份不一致')
        identities[uid]=row['user_email'];groups.setdefault(uid,[]).append(row)
    for rows in groups.values():
        rows.sort(key=lambda row:row['day'])
        if [row['day'] for row in rows]!=scope['working_dates']:raise ValueError('全员来源缺日、重复或超出本周')
    if not groups:raise ValueError('来源名单为空')
    return groups


def source(base, row):
    path=base/row['file'];raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('冻结原文签名变化')
    value=json.loads(raw)
    if any(value[key]!=row[key] for key in ('user_id','user_email','day','source_status')):
        raise ValueError('冻结原文身份或范围与清单不符')
    if (row['source_status']=='no_activity')!=(not bool(value['material']['tasks'])):
        raise ValueError('无活动标记与真实任务不符')
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','output','state','model-config','flow-config'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--week',required=True)
    parser.add_argument('--period',choices=['day','week'],required=True)
    parser.add_argument('--daily-reports',type=Path,help='产品库实际生成的日评ID、来源签名及正文；可先生成周候选，正式导入仍须已发布日评')
    parser.add_argument('--acceptance',type=Path,help='当前实现及模型的真实质量验收记录')
    parser.add_argument('--held-results',type=Path,help='待核候选清单；只隔离指定来源，不生成评分或重发请求')
    parser.add_argument('--company-api',action='store_true')
    parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--request-limit',type=int,default=32)
    args=parser.parse_args()
    from datetime import date,timedelta
    start=date.fromisoformat(args.week)
    if start.weekday()!=0:parser.error('周开始须为星期一')
    scope=window(args.week,str(start+timedelta(days=7)));manifest=json.loads(args.manifest.read_text());groups=roster(manifest,scope)
    config=ModelConfig.load(args.model_config,conversation=True);settings=json.loads(args.flow_config.read_text())
    implementation=signature()
    if not 1<=args.workers<=settings['max_concurrency'] or not 1<=args.request_limit<=96:
        parser.error('执行线程不能超出已有并发配额，单任务累计请求须为1至96')
    planned=dict(users=len(groups),daily_reports=sum(map(len,groups.values())),
        model_days=sum(row['source_status']=='tasks' for rows in groups.values() for row in rows),
        model_weeks=sum(any(row['source_status']=='tasks' for row in rows) for rows in groups.values()),
        model=config.model,engine=VERSION,production_changed=False)
    if not args.company_api:print(json.dumps(planned,ensure_ascii=False));return
    if not args.acceptance:parser.error('发送模型请求前须提供当前真实质量验收记录')
    acceptance=json.loads(args.acceptance.read_text())
    if (acceptance.get('reviewer')!='codex_direct_review' or acceptance.get('status')!='accepted'
            or acceptance.get('model')!=config.model or acceptance.get('engine')!=VERSION
            or acceptance.get('implementation_sha256')!=implementation or acceptance.get('rubric_sha256')!=digest(RUBRIC)):
        parser.error('验收记录与实际实现、固定规则或模型不符')
    if args.period=='week' and not args.daily_reports:parser.error('周评须提供实际日评记录，不使用虚构报告ID')
    root=args.output.resolve();root.mkdir(mode=0o700,parents=True,exist_ok=True)
    checkpoint(root/'campaign.json',dict(week=scope,source_manifest_sha256=hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
        model=config.model,endpoint=config.responses_url,engine=VERSION,implementation_sha256=implementation,
        acceptance_sha256=digest(acceptance),budget_key=settings['budget_key']))
    flow=ModelFlow(args.state/'flow',settings)
    held=json.loads(args.held_results.read_text()) if args.held_results else []
    def client(folder):
        current=JobClient(config,folder,args.request_limit,args.request_limit,flow=flow,state_root=args.state)
        current.reasoning_effort='high' if args.period=='week' else 'medium';current.temperature=0
        return current
    daily_index={}
    if args.daily_reports:
        for row in json.loads(args.daily_reports.read_text())['reports']:
            key=(row['user_id'],row['day'])
            if key in daily_index:raise ValueError('产品日评记录重复')
            daily_index[key]=row
    def grade(uid, rows):
        if args.period=='day':row=rows[0];day=row['day'];folder=root/'days'/str(uid)/day
        else:folder=root/'weeks'/str(uid)
        folder.mkdir(mode=0o700,parents=True,exist_ok=True)
        output=folder/'bundle.json';checked=folder/'bundle-check.json'
        # 实现变化必须另存检查点，原目录只用于按实际请求签名复用回执。
        review_root=folder/('review-'+implementation[:16])
        def reviewer():
            current=client(folder)
            previous_roots=sorted((p for p in folder.iterdir() if p.is_dir()
                and (p.name=='review' or p.name.startswith('review-')) and p!=review_root),
                key=lambda p:p.stat().st_mtime,reverse=True)
            if previous_roots:current.reuse_root=previous_roots[0]
            return current
        frozen=[source(args.manifest.parent,row) for row in rows]
        checkpoint(folder/'source-contract.json',dict(sources=[row['sha256'] for row in rows],model=config.model,
            implementation_sha256=implementation,period=args.period))
        if output.exists():
            bundle=json.loads(output.read_text())
            if digest(bundle)!=json.loads(checked.read_text())['bundle_sha256']:raise ValueError('已完成候选包签名变化')
            return dict(user_id=uid,day=rows[0]['day'] if args.period=='day' else None,status='candidate',reused=True)
        for hold in held:
            if hold['user_id']==uid and hold['day']==(rows[0]['day'] if args.period=='day' else None):
                if hold['sources']!=[row['sha256'] for row in rows] or not hold['reason']:
                    raise ValueError('隔离记录与实际冻结来源不符')
                return dict(user_id=uid,day=hold['day'],status='held',reason=hold['reason'],
                    reason_code='awaiting_direct_review',production_changed=False)
        if args.period=='day':
            packet=dict(user_id=uid,user_email=row['user_email'],workday_scope=daily_window(day),frozen_source_sha256=row['sha256'])
            bundle=(evaluate_day(packet,day,review_root,reviewer(),frozen[0]['material'])
                if row['source_status']=='tasks' else empty_day(packet,day,review_root,config.model))
        else:
            days=[];sources=[]
            for row,payload in zip(rows,frozen):
                actual=daily_index[(uid,row['day'])]
                daily=json.loads((root/'days'/str(uid)/row['day']/'bundle.json').read_text())['reports'][0]
                if (actual['user_email']!=row['user_email'] or actual['source_digest']!=daily['source_digest']
                        or actual['request_digest']!=daily['daily_review']['request_digest']
                        or canonical_daily_review(actual['review'])!=canonical_daily_review(daily['daily_review'])):raise ValueError('产品日评与本轮候选来源不一致')
                days.append(dict(user_id=uid,user_email=row['user_email'],day=row['day'],material=payload['material'],
                    score_status=daily['score_status'],review=actual['review']))
                sources.append(dict(report_id=actual['report_id'],period_start=row['day'],source_digest=actual['source_digest'],request_digest=actual['request_digest']))
            bundle=evaluate_week(dict(user_id=uid,user_email=rows[0]['user_email'],period_start=scope['period_start'],
                period_end=scope['period_end'],workday_scope=scope),review_root,reviewer(),days,sources)
        checkpoint(output,bundle);checkpoint(checked,dict(bundle_sha256=digest(bundle),publication='not_imported'))
        return dict(user_id=uid,day=rows[0]['day'] if args.period=='day' else None,status='candidate',reused=False)
    with (args.state/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        results=[]
        tasks=[(uid,[row]) for uid,rows in groups.items() for row in rows] if args.period=='day' else list(groups.items())
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending={pool.submit(grade,uid,rows):(uid,rows) for uid,rows in tasks}
            for future in as_completed(pending):
                uid,rows=pending[future]
                try:result=future.result()
                except Exception as error:result=dict(user_id=uid,day=rows[0]['day'] if args.period=='day' else None,
                    status='failed',error_type=type(error).__name__,reason=str(error),
                    state=getattr(error,'state',None),reason_code=getattr(error,'reason_code',None))
                results.append(result);atomic(root/(args.period+'-progress.json'),dict(planned=planned,results=results,at=time.time(),production_changed=False))
                print(json.dumps(result,ensure_ascii=False),flush=True)
        if any(result['status']!='candidate' for result in results):raise SystemExit(1)


if __name__=='__main__':main()
