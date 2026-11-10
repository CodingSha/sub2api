"""只读查看积压与模型请求账簿；不收集员工正文、不触发评审。"""
import argparse
from datetime import datetime,timezone
import json
import math
import os
from pathlib import Path
import time


def queue_status(db,now=None):
    rows=db.execute('''SELECT state,count(*),min(created_at),min(updated_at)
        FROM zhiyongli_jobs GROUP BY state ORDER BY state''').fetchall()
    now=now or datetime.now(timezone.utc)
    return [dict(state=state,count=count,oldest_created_at=created.isoformat(),oldest_updated_at=updated.isoformat(),
        oldest_age_seconds=max(0,(now-created).total_seconds()),
        oldest_wait_seconds=max(0,(now-created).total_seconds()) if state in {'queued','running'} else None)
        for state,count,created,updated in rows]


def deadlines(db,now=None):
    from worker import RULES
    now=now or datetime.now(timezone.utc)
    counts=dict(pending=0,overdue=0,without_deadline=0,oldest_due_at=None,maximum_overdue_seconds=None,states={})
    for state,scope in db.execute("SELECT state,workday_scope FROM zhiyongli_jobs WHERE rules_version=%s AND state NOT IN ('completed','no_activity')",(RULES,)):
        counts['pending']+=1
        counts['states'][state]=counts['states'].get(state,0)+1
        try:
            if not isinstance(scope,dict):raise ValueError('缺失工作日范围')
            due=datetime.fromisoformat(scope['candidate_due_at'])
            if due.tzinfo is None:raise ValueError('截止缺少时区')
        except (KeyError,TypeError,ValueError):
            counts['without_deadline']+=1;continue
        if counts['oldest_due_at'] is None or due<datetime.fromisoformat(counts['oldest_due_at']):counts['oldest_due_at']=due.isoformat()
        if due<=now:
            counts['overdue']+=1
            counts['maximum_overdue_seconds']=max(counts['maximum_overdue_seconds'] or 0,(now-due).total_seconds())
    return counts


def heartbeat(state,now=None):
    now=time.time() if now is None else now
    path=Path(state)/'heartbeat.json'
    if not path.exists():return dict(state='missing',age_seconds=None)
    try:
        at=json.loads(path.read_text())['at']
        if type(at) not in (int,float) or not math.isfinite(at):raise ValueError('心跳时刻无效')
    except (OSError,ValueError,KeyError,TypeError):return dict(state='invalid',age_seconds=None)
    status='future_timestamp' if at>now+60 else 'stale' if now-at>60 else 'fresh'
    return dict(state=status,age_seconds=max(0,now-at),limitation='只说明落盘心跳的新鲜度，不证明模型或评分流程健康。')


def alerts(value,now=None):
    now=time.time() if now is None else now
    result=[]
    def add(code,count=None):
        result.append(dict(code=code,**(dict(count=count) if count is not None else {})))
    for field,code in [('overdue','candidate_overdue'),('without_deadline','pending_deadline_unknown')]:
        if value['deadlines'][field]:add(code,value['deadlines'][field])
    for state,count in value['deadlines']['states'].items():
        if state in {'unknown','failed','needs_review'}:add('assessment_'+state,count)
    if value['heartbeat']['state']!='fresh':add('heartbeat_'+value['heartbeat']['state'])
    model=value['model'];states={r['status']:r['count'] for r in model['states']}
    if states.get('unknown'):add('model_unknown_requires_reconciliation',states['unknown'])
    pending=value['deadlines']['pending']+sum(r['count'] for r in value.get('preparations',[]) if r['state']!='prepared')
    if pending and model['request_budget']['remaining']==0:add('global_request_budget_exhausted')
    if model['circuit']['until_at']>now:add('service_cooldown')
    schedule=value.get('schedule')
    if isinstance(schedule,dict) and schedule.get('state')=='needs_review':add('schedule_needs_review')
    for row in value.get('preparations',[]):
        if row['state'] in {'unknown','failed','needs_review'}:add('preparation_'+row['state'],row['count'])
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True);parser.add_argument('--flow-config',type=Path,required=True)
    args=parser.parse_args()
    # 不创建不存在的账簿，缺失状态须保留，不能显示成已用零次。
    if not (args.state/'flow'/'model-flow.sqlite').exists():raise ValueError('尚无全局模型账簿，不能确认请求量')
    from model_flow import ModelFlow
    from worker import private_value
    import psycopg
    config=json.loads(args.flow_config.read_text())
    flow=ModelFlow(args.state/'flow',config,read_only=True)
    path=os.environ.get('ZHIYONGLI_DB_DSN_FILE')
    dsn=private_value(path) if path else os.environ.get('ZHIYONGLI_DB_DSN')
    if not dsn:raise ValueError('未配置任务库连接')
    with psycopg.connect(dsn,connect_timeout=10) as db:
        db.execute('SET TRANSACTION READ ONLY')
        value=dict(queue=queue_status(db),deadlines=deadlines(db),model=flow.snapshot(),heartbeat=heartbeat(args.state),capacity_acceptance='not_verified')
    if (args.state/'schedule-status.json').exists():value['schedule']=json.loads((args.state/'schedule-status.json').read_text())
    if (args.state/'preparations.sqlite').exists():
        import sqlite3
        with sqlite3.connect((args.state/'preparations.sqlite').resolve().as_uri()+'?mode=ro',uri=True) as state:
            value['preparations']=[dict(state=s,count=c,oldest_update=at) for s,c,at in state.execute('SELECT state,count(*),min(updated) FROM preparations GROUP BY state')]
    value['alerts']=alerts(value)
    print(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
