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


def reading_recovery_diagnosis(path):
    """只核有效回执链；不核页面正文、不判断用户能力，也不授权恢复或补发。"""
    from request_retry import VERSIONS,attempt_path,gateway_limited,read_receipt
    from runtime import digest
    result=dict(automatic_resume_allowed=False,recovery_mechanism='not_implemented',
        page_validation_performed=False,receipt_chain_verified=False,
        limitation='仅诊断回执与续发链，不证明阅读完成、评分质量或用户能力；恢复仍需队列拥有者及明确处置机制。')
    try:
        entry=Path(path);raw=json.loads(entry.read_text());shared_refresh=False
        record=read_receipt(entry)
        if 'shared_reading_receipt' in record:
            reference=record;entry=Path(reference['shared_reading_receipt']);raw=json.loads(entry.read_text())
            record=read_receipt(entry)
            if 'shared_reading_receipt' in record:raise ValueError('共享回执不可递归嵌套')
            if record.get('request_sha256')!=reference.get('request_sha256'):raise ValueError('共享请求签名变化')
            if digest(record)!=reference.get('shared_receipt_sha256'):
                original=json.loads(attempt_path(entry,0).read_text()) if raw.get('transport_version') in VERSIONS else record
                if not gateway_limited(original) or digest(original)!=reference.get('shared_receipt_sha256'):
                    raise ValueError('共享原回执签名变化')
                shared_refresh=True
        chained=raw.get('transport_version') in VERSIONS
        records=[json.loads(attempt_path(entry,i).read_text()) for i in range(len(raw['attempts']))] if chained else [record]
        result.update(receipt_chain_verified=True,shared_reference_requires_refresh=shared_refresh,
            effective_status=record['status'],attempts=len(records),exhausted=raw.get('exhausted') is True,
            effective_receipt_sha256=digest(record),entry_sha256=digest(raw))
        response=record.get('response') or {}
        if record['status'] in {'unknown','requesting'}:
            classification='unknown_requires_reconciliation'
        elif chained and raw['status']=='retry_wait':
            classification='owned_executor_retry_wait'
        elif gateway_limited(record):
            if not all(gateway_limited(value) for value in records):classification='mixed_retry_history_requires_review'
            else:classification='gateway_retry_exhausted' if result['exhausted'] else 'gateway_failure_requires_owned_executor_review'
        elif record['status']=='received' and response.get('status')=='completed':
            classification='completed_response_requires_page_validation'
        else:classification='failed_not_eligible_for_gateway_recovery'
        # 在线只读盘点可能遇到链推进；前后状态变化不能拼成一份已核证据。
        if digest(json.loads(entry.read_text()))!=digest(raw) or digest(read_receipt(entry))!=digest(record):
            raise ValueError('诊断期间回执链发生变化')
        result['classification']=classification
    except OSError:
        result.update(classification='unavailable',receipt_chain_verified=False)
    except (ValueError,KeyError,TypeError,AttributeError):
        result.update(classification='invalid_receipt_chain',receipt_chain_verified=False)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path);parser.add_argument('--flow-config',type=Path)
    parser.add_argument('--reading-receipt',type=Path,help='单独只读诊断指定阅读回执；不连数据库、不恢复任务')
    args=parser.parse_args()
    if args.reading_receipt is not None:
        print(json.dumps(reading_recovery_diagnosis(args.reading_receipt),ensure_ascii=False,indent=2));return
    if args.state is None or args.flow_config is None:parser.error('默认状态检查须提供--state及--flow-config')
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
            value['preparations']=[dict(state=s,count=c,oldest_update=at,oldest_target_date=first,
                latest_target_date=last,earliest_prepared_through=ready) for s,c,at,first,last,ready in state.execute(
                'SELECT state,count(*),min(updated),min(through_date),max(through_date),min(prepared_through) FROM preparations GROUP BY state')]
    value['alerts']=alerts(value)
    print(json.dumps(value,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
