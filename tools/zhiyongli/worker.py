#!/usr/bin/env python3
"""管理员任务的后台Worker；专用数据库与导入权限，逐阶段续跑，无自动发布。"""
import argparse
from contextlib import contextmanager,nullcontext
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_EXCEPTION
from datetime import datetime,timedelta,timezone
import fcntl
import json
import jsonschema
import os
from pathlib import Path
import signal
import stat
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from model_config import NoRedirect, ModelError
from runtime import CompanyClient, atomic, digest
from source_snapshot import collect,require_current_query
from source_frames import read_signed
from pipeline import prepare,evaluate_week,evaluate_month,Paused

RULES='1.6'

@contextmanager
def source_session(dsn,slots):
    """仅采集期间占来源连接；任务锁继续由各线程的产品库会话持有。"""
    import psycopg
    with slots:
        with psycopg.connect(dsn,connect_timeout=10) as connection:
            yield connection

class JobClient(CompanyClient):
    """按任务账簿预记新发送；跨任务复用回执不消耗本任务预算。"""
    # 复查分歧时可接收前一候选及逐项意见做有界修订；修订请求同样计入任务与全局预算。
    accepts_revision_feedback=True
    def __init__(self,config,root,limit,new_request_limit=1,flow=None,*,state_root=None,budget_root=None):
        super().__init__(config);self.root=root;self.limit=limit;self.new_request_limit=new_request_limit
        # 日常准备使用嵌套执行目录，运维暂停仍由整个Worker状态根统一控制。
        self.state_root=Path(state_root) if state_root is not None else Path(root).parent
        from request_budget import RequestBudget
        self.budget=RequestBudget(root if budget_root is None else budget_root,limit,new_request_limit)
        self.reading_cache_root=None
        self.reading_plan_root=None
        self.navigation_cache_root=None
        self.flow=flow
    def preflight_reading(self,reading,root,events,cache,used):
        from reading_capacity import preflight
        return preflight(self,reading,root,events,cache,used)
    def preflight_navigation(self,packet,reading,root,events,cache,used,navigation_root):
        from reading_capacity import preflight_navigation
        return preflight_navigation(self,packet,reading,root,events,cache,used,navigation_root)
    def preflight_goal_merge(self,base,groups,root,receive):
        from goal_capacity import preflight
        return preflight(self,base,groups,root,receive)
    def _call(self,payload,receipt,*,before_send=None):
        from disk_guard import require_space
        require_space(self.root)
        if not receipt.exists():self.budget.check(receipt)
        from request_retry import call
        send=super()._call
        def admitted(value,path):return send(value,path,before_send=before_send)
        return call(self,payload,receipt,admitted)
    @contextmanager
    def send_slot(self,payload,receipt,request_hash,body):
        # 运维暂停只阻止新请求，已发送请求仍按原账簿落盘，避免强杀制造unknown。
        if (self.state_root/'pause-new-requests').exists():
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

def import_path(job_id, draft_only):
    return '/api/v1/internal/zhiyongli/jobs/'+str(job_id)+('/import-draft' if draft_only else '/import')


def draft_import_for(job, sender):
    return job.get('recheck_report_id') is not None or getattr(sender,'draft_only',False) is True


def importer(base_url,key_file,*,draft_only=False):
    url=urlsplit(base_url)
    if (url.username or url.password or url.query or url.fragment or url.path not in {'','/'}
        or not (url.scheme=='https' or url.scheme=='http' and url.hostname in {'127.0.0.1','localhost','sub2api'})):
        raise ValueError('报告接入端点须为HTTPS或明确的本地/容器内部服务')
    def send(job,bundle):
        request=urllib.request.Request(base_url.rstrip('/')+import_path(job['id'],draft_import_for(job,send)),
            data=json.dumps(bundle,ensure_ascii=False).encode(),headers={'Content-Type':'application/json',
                'X-Zhiyongli-Worker-Key':private_key(key_file)})
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=60) as stream:
            value=json.load(stream)
        if value.get('code')!=0: raise ValueError('候选接入未成功')
        return value['data']
    send.base_url=base_url.rstrip('/')
    send.draft_only=draft_only
    return send


def import_candidate(root,job,bundle,send):
    """冻结导入意图并校验回执；同包重发由服务端摘要幂等，不把文件存在当成功。"""
    from bounded_read import checkpoint
    root=Path(root);receipt=root/'import.json';input_path=root/'import-input.json';checked=root/'import-checked.json'
    if not isinstance(getattr(send,'base_url',None),str):raise ValueError('候选导入缺少明确端点身份')
    intent=dict(version=1,job_id=job['id'],endpoint=send.base_url+import_path(job['id'],draft_import_for(job,send)),
        bundle_sha256=digest(bundle))
    if (receipt.exists() or checked.exists()) and not input_path.exists():raise ValueError('旧导入回执缺少事前任务及结果包绑定，须先核对，禁止补签完成')
    checkpoint(input_path,intent)
    prior=json.loads(receipt.read_text()) if receipt.exists() else None
    def validate(value):
        if (not isinstance(value,dict) or set(value)!={'id','digest','source_name','report_count','created_at'}
                or type(value['id']) is not int or value['id']<1
                or not isinstance(value['digest'],str) or len(value['digest'])!=64
                or any(c not in '0123456789abcdef' for c in value['digest'])
                or value['source_name']!=bundle['source_name'] or type(value['report_count']) is not int
                or value['report_count']!=len(bundle['reports']) or not isinstance(value['created_at'],str)
                or datetime.fromisoformat(value['created_at'].replace('Z','+00:00')).tzinfo is None):
            raise ValueError('候选导入回执不符合当前结果包及Run字段合同')
    if receipt.exists():validate(prior)
    if checked.exists():
        if prior is None:raise ValueError('候选导入已核证明存在但原回执缺失，禁止重新导入')
        checkpoint(checked,dict(input_sha256=digest(intent),receipt_sha256=digest(prior)))
    # 本地已核不证明产品库恢复后仍有候选；标记任务完成前须在原端点同包核对幂等回执。
    # 仅重查已经冻结的同包，不重新评分，不覆盖旧Run或已核证明。
    run=send(job,bundle);validate(run)
    if prior is not None and run!=prior:raise ValueError('同包幂等回执与已有记录不同，保留原件待核，禁止覆盖')
    checkpoint(receipt,run)
    checkpoint(checked,dict(input_sha256=digest(intent),receipt_sha256=digest(run)))
    return run

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

def inherit_request_budget(root,state,prior_ids,limit):
    """新范围承接同人同周全部旧发送，不改原账簿，也不重置个人额度。"""
    proof=[]
    for jid in sorted(prior_ids):
        path=Path(state)/str(jid)/'request-budget.json'
        if not path.is_file():raise ValueError('旧任务请求账簿缺失，不能按零用量续评')
        value=json.loads(path.read_text())
        if value.get('version')!=1 or not isinstance(value.get('entries'),dict):raise ValueError('旧任务请求账簿格式无效')
        proof.append(dict(job_id=jid,sha256=digest(value),used=len(value['entries'])))
    path=Path(root)/'inherited-request-budget.json'
    if path.exists():
        if json.loads(path.read_text())!=proof:raise ValueError('范围切换后旧任务请求账簿变化，保留新旧记录待核')
    else:atomic(path,proof)
    return max(0,limit-sum(p['used'] for p in proof))

def formal_week_review_required(report):
    """仅正式任务需要当前任务说明合同；局部表达不伪造目标，也不改变原判。"""
    eligibility=report.get('eligibility')
    if not isinstance(eligibility,dict) or not isinstance(eligibility.get('observations'),list):
        raise ValueError('周报告缺少已核资格观察范围，禁止跳过正式说明')
    formal=set()
    for observation in eligibility['observations']:
        if not isinstance(observation,dict) or not isinstance(observation.get('task_keys'),list):
            raise ValueError('周报告观察缺少正式任务字段，禁止把合同损坏当局部表达')
        keys=observation['task_keys']
        if any(not isinstance(key,str) or not key.strip() for key in keys):
            raise ValueError('周报告正式任务标识无效，禁止跳过正式说明')
        formal.update(keys)
    return bool(formal)


def process(db,job,state,source,client,send,excluded_keys,max_requests=100,new_request_limit=1):
    root=Path(state)/str(job['id']);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    evidence_first=getattr(client,'evidence_first',False) is True
    daily_review=job['period_type']=='day'
    pilot=daily_review and job['id'] in (getattr(client,'pilot_day_job_ids',None) or ())
    session_review=getattr(client,'session_review',False) is True or daily_review
    last=['collecting',0,1]
    def progress(phase,done,total,message):
        from disk_guard import require_space
        require_space(root)
        last[:]=[phase,done,total];update(db,job['id'],'running',phase,done,total,message)
    try:
        draft_import=draft_import_for(job,send)
        if pilot!=(getattr(send,'draft_only',False) is True) or (not pilot and (root/'pilot-draft.json').exists()):
            raise ValueError('日评试评与正式导入隔离，禁止切换同一任务的发布方式')
        if pilot:
            from bounded_read import checkpoint
            checkpoint(root/'pilot-draft.json',dict(job_id=job['id'],mode='draft_only'))
        from personal_scope import require_personal_user
        require_personal_user(job['user_id'])
        from dsh_client import DSHClient
        reading_concurrency=getattr(client,'reading_concurrency',1)
        single_pass_enabled=getattr(client,'single_pass',True)
        reading_page_navigation=getattr(client,'reading_page_navigation',False) is True
        preparations=getattr(client,'preparations',None)
        if daily_review:
            preparations=None
        if evidence_first and (job['period_type']!='week' or preparations is not None):
            raise ValueError('证据优先当前仅支持独立周评验证；月评和日常准备尚未完成接入')
        if preparations is not None and (not hasattr(client,'preparation_first_week') or getattr(client,'schedule_enabled',True) is False):
            raise Paused('queued','尚未取得服务端自动计划，保留任务等待调度核对')
        first_week=getattr(client,'preparation_first_week',None)
        if not first_week or str(job['period_start'])<first_week:preparations=None
        client=(DSHClient(client.config,client.dsh,root,max_requests) if isinstance(client,DSHClient)
            else JobClient(client.config,root,max_requests,new_request_limit,getattr(client,'flow',None),state_root=state))
        client.reading_concurrency=reading_concurrency
        client.single_pass=single_pass_enabled
        client.evidence_first=evidence_first
        client.session_review=session_review
        client.reading_content_identity=preparations is not None
        client.reading_efficiency=preparations is not None
        client.reading_page_navigation=reading_page_navigation
        if isinstance(client,JobClient):
            client.reading_cache_root=Path(state)/'read-pages'
            client.reading_plan_root=Path(state)/'reading-plans'
            client.navigation_cache_root=Path(state)/'navigation-branches'
        scope=job.get('workday_scope')
        if scope is None:raise Paused('failed' if session_review else 'needs_review',
            '历史自然周任务未绑定工作日范围，保留原记录；请按当前日历另建评审任务',
            **(dict(reason_code='source_incomplete') if session_review else {}))
        from workday_scope import validate
        validate(scope)
        if datetime.now(timezone.utc)<datetime.fromisoformat(scope['freeze_at']):
            raise Paused('queued','尚未到本期来源封口时刻，不提前采集和评分')
        if preparations is not None and job['period_type']=='week' and not scope.get('evaluation_dates'):
            proof=preparations.ready_budget(job,**(dict(session_review=True) if session_review else {}))
            if proof is not None:
                path=root/'preparation-budget.json'
                if path.exists() and json.loads(path.read_text())!=proof:raise ValueError('日常预处理账簿发生变化，须核对后接续周评')
                atomic(path,proof)
                client.limit=client.budget.limit=max(0,max_requests-proof['used'])
            if session_review:client.session_fact_from=preparations.fact_reference(job)
        progress('collecting',0,1,'读取所选人员本期来源；冻结后从原回执续跑')
        source_root=root/'sources'
        start=str(job['period_start'])+'T00:00:00+08:00';end=str(job['period_end'])+'T00:00:00+08:00'
        lo=datetime.fromisoformat(start);scan_start=lo if daily_review else lo-timedelta(days=lo.weekday())
        if 'evaluation_dates' in scope:
            if not isinstance(client,JobClient):raise ValueError('局部范围仅支持已接入累计账簿的API执行器')
            prior=db.execute("SELECT id,workday_scope FROM zhiyongli_jobs WHERE user_id=%s AND period_type='week' AND period_start=%s AND period_end=%s AND id<>%s ORDER BY id DESC",
                (job['user_id'],str(job['period_start']),str(job['period_end']),job['id'])).fetchall()
            remaining=inherit_request_budget(root,state,[r[0] for r in prior],max_requests)
            client.limit=client.budget.limit=remaining
            # 局部交付只借用同人同周已采集的冻结来源；不重采、不改旧任务目录。
            previous=[row for row in prior if row[1] is not None and 'evaluation_dates' not in row[1]]
            source_root=next((Path(state)/str(row[0])/'sources' for row in previous
                if (Path(state)/str(row[0])/'sources'/'latest.json').is_file()),source_root)
            if not (source_root/'latest.json').is_file():
                raise Paused('failed' if session_review else 'needs_review',
                    '所选三日评估缺少该用户同周已采集来源，未重新采集或发送模型请求',
                    **(dict(reason_code='source_incomplete') if session_review else {}))
        if not (source_root/'latest.json').exists():
            collect(source,source_root,excluded_keys,scan_start.isoformat(),[job['user_id']],shared_root=Path(state)/'source-cache')
        snapshot=read_signed(source_root,json.loads((source_root/'latest.json').read_text()))
        if daily_review:
            from personal_scope import excluded_api_key_ids
            if not set(excluded_api_key_ids(excluded_keys)).issubset(snapshot.get('excluded_api_key_ids',[])):
                raise ValueError('日评冻结来源未排除当前服务密钥；保留旧快照，禁止带入服务调用评分')
        person=next((u for u in snapshot['users'] if u['id']==job['user_id']),None)
        if not person or person['email']!=job['user_email']:
            raise Paused('failed','来源账户与任务人员映射不一致；未发送模型请求，请先核对两个数据库的账户身份')
        require_current_query(snapshot)
        from review_control import selected
        review_root=selected(root/'review')
        if daily_review:
            from bounded_read import checkpoint
            from daily_assessment import evaluate as evaluate_daily
            from daily_export import bundle as daily_bundle
            packet=prepare(source_root,job['user_id'],start,end,workday_scope=scope,
                metadata_cache=Path(state)/'source-frames',session_review=True)
            checkpoint(root/'daily-source.json',dict(packet_sha256=digest(packet),model=client.config.model,
                workday_scope=scope))
            progress('evaluating',0,1,'按完整任务评估当日使用表现')
            # 与周评的分页阅读/逐句归项分开；同任务恢复仍使用原请求账簿。
            client.reasoning_effort=None;client.temperature=0;client.output_token_limit=8000
            evaluate_daily(packet,str(job['period_start']),review_root,client)
            bundle=daily_bundle(packet,str(job['period_start']),review_root,client.config.model)
        elif job['period_type']=='week':
            if 'evaluation_dates' in scope:
                from pipeline import selected_packet
                prepared_root=Path(state)/'prepared-scopes'/digest(scope)/str(job['user_id'])
                if session_review:prepared_root=prepared_root/'session-source'
                packet=selected_packet(prepared_root,
                    source_root,job['user_id'],start,end,scope,
                    **(dict(create=True,session_review=True) if session_review else {}))
            else:packet=prepare(source_root,job['user_id'],start,end,workday_scope=scope,metadata_cache=Path(state)/'source-frames',
                **(dict(session_review=True) if session_review else {}))
            # 传未选定的基目录：自动修订的各轮目录平铺在同一revisions下，由evaluate_week选定当前轮。
            bundle=evaluate_week(packet,root/'review',client,progress,
                shared_root=Path(state)/'shared-session-weeks' if session_review else None if evidence_first else Path(state)/'shared-weeks')
            if len(bundle['reports'])>1:raise ValueError('单人周评返回多份报告，禁止猜测正式说明归属')
            from single_pass import active
            if bundle['reports'] and not active(client) and formal_week_review_required(bundle['reports'][0]):
                from shared_weeks import accepted_reference
                from week_presentation import complete
                entry_ref=accepted_reference(Path(state)/'shared-weeks',packet,selected(root/'review'),page_navigation=reading_page_navigation)
                # 说明归属共享来源，跨任务恢复仍使用同一意图账和回执。
                output=Path(entry_ref['path']).parent/'explanation'
                bundle=complete(entry_ref,output,client,progress)
        elif job['period_type']=='month':
            bundle=evaluate_month(source_root,job['user_id'],start,end,review_root,client,progress,
                shared_root=Path(state)/('shared-session-weeks' if session_review else 'shared-weeks'),workday_scope=scope)
        else:
            raise ValueError('未登记的评审周期，禁止猜测执行路径')
        progress('importing',0,1,'保存并发布日评分' if daily_review and not draft_import else '接入已核候选；等待管理员确认发布')
        import_candidate(root,job,bundle,send)
        update(db,job['id'],'completed','done',1,1,'日评分已保存并发布' if daily_review and not draft_import else '候选报告已生成，等待管理员确认发布')
    except Paused as ex:
        if evidence_first or session_review:
            from review_protocol import failure
            outcome=failure(ex)
            atomic(root/'outcome.json',dict(outcome,execution_phase=last[0]))
            if outcome['state']=='failed':
                message={'capacity_blocked':'执行容量预算不足，已有回执保留',
                    'model_contract_failed':'模型服务或输出合同失败，原回执保留',
                    'execution_blocked':'执行前置条件检查未通过，原阶段已保留'}[outcome['phase']]
                update(db,job['id'],outcome['state'],outcome['phase'],message=message)
            elif ex.state=='queued':update(db,job['id'],'queued',*last,message=str(ex))
            else:update(db,job['id'],outcome['state'],outcome['phase'],message=str(ex))
        elif ex.state=='queued':update(db,job['id'],'queued',*last,message=str(ex))
        else:update(db,job['id'],ex.state,'done' if ex.state=='no_activity' else 'paused',message=str(ex))
    except (ValueError,KeyError,TypeError,ModelError,jsonschema.ValidationError) as ex:
        # 错误细节只在私有文件留存，不把原文、路径或远端错误体返回页面。
        import traceback
        from navigation_access import ReadbackCapacity
        capacity_failure=isinstance(ex,ReadbackCapacity)
        atomic(root/'failure.json',dict(phase=last[0],error_type=type(ex).__name__,traceback=traceback.format_exc(),
            category='capacity' if (evidence_first or session_review) and capacity_failure else 'execution_contract' if evidence_first or session_review else 'unclassified',employee_review_required=False if evidence_first or session_review else None,
            at=datetime.now(timezone.utc).isoformat()))
        if evidence_first or session_review:
            update(db,job['id'],'failed','capacity_blocked' if capacity_failure else 'execution_contract_failed',
                message='补读装配超过容量；原文及回执保留，需处理执行容量，不属于员工表现审核' if capacity_failure else
                    '来源或执行合同校验失败；已保留阶段与回执，不属于员工表现审核')
        else:update(db,job['id'],'needs_review','paused',message='来源、评审合同或判断校验未通过；已保留原始回执，需核对后继续')
    except Exception as ex:
        import traceback
        from execution_recovery import transient_connection
        retryable=session_review and transient_connection(ex,last[0])
        atomic(root/'failure.json',dict(phase=last[0],error_type=type(ex).__name__,at=time.time(),
            category='transient_connection' if retryable else 'unclassified',employee_review_required=False,
            traceback=traceback.format_exc(),
            **(dict(http_status=ex.code,response_body=ex.read(4096).decode('utf-8','replace')) if isinstance(ex,urllib.error.HTTPError) else {})))
        update(db,job['id'],'failed','paused',message='后台执行失败；已有阶段和回执已保留，可恢复校验')

def tick(db,source,state,client,send,excluded_keys,max_requests=100,new_request_limit=1):
    from psycopg.rows import dict_row
    if getattr(client,'preparations',None) is not None and (not hasattr(client,'preparation_first_week') or getattr(client,'schedule_enabled',True) is False):return False
    first_week=getattr(client,'preparation_first_week',None) if getattr(client,'preparations',None) is not None else None
    first_day=getattr(client,'daily_first_day',None)
    recovery_enabled=getattr(client,'session_review',False) is True or bool(first_day)
    scope_key=os.environ.get('ZHIYONGLI_ACTIVE_SCOPE_KEY')
    pilot_jobs=getattr(client,'pilot_day_job_ids',None)
    if scope_key=='':raise ValueError('活动范围过滤不能为空字符串，禁止误领旧范围任务')
    with db.cursor(row_factory=dict_row) as cursor:
        jobs=cursor.execute("""SELECT j.*,u.email AS user_email FROM zhiyongli_jobs j
            JOIN users u ON u.id=j.user_id
            WHERE (j.state IN ('queued','running') OR (%(recovery)s AND j.state='failed' AND j.recheck_report_id IS NULL))
            AND j.rules_version=%(rules)s
            AND (%(scope)s::text IS NULL OR j.scope_key=%(scope)s OR (j.recheck_report_id IS NOT NULL AND split_part(j.scope_key,':recheck:',1)=%(scope)s))
            AND (%(week)s::date IS NULL OR j.period_start>=%(week)s::date)
            AND (%(day)s::date IS NULL OR (j.period_type='day' AND (j.period_start>=%(day)s::date OR j.recheck_report_id IS NOT NULL)))
            AND (%(pilot)s::bigint[] IS NULL OR (j.period_type='day' AND j.recheck_report_id IS NULL AND j.id=ANY(%(pilot)s::bigint[])))
            ORDER BY j.updated_at,j.id LIMIT 100""",dict(recovery=recovery_enabled,rules=RULES,scope=scope_key,week=first_week,day=first_day,pilot=pilot_jobs)).fetchall()
    for job in jobs:
        locked=db.execute('SELECT pg_try_advisory_lock(245,%s::int)',(job['id'],)).fetchone()[0]
        if not locked:continue
        try:
            # 拿锁后重读，避免等待期间已完成的同一任务再次执行。
            current=db.execute('SELECT state FROM zhiyongli_jobs WHERE id=%s',(job['id'],)).fetchone()[0]
            if current=='failed' and recovery_enabled and job['recheck_report_id'] is None:
                from execution_recovery import reserve
                if not reserve(Path(state)/str(job['id'])):continue
            elif current not in {'queued','running'}:continue
            process(db,job,state,source,client,send,excluded_keys,max_requests,new_request_limit)
            return True
        finally:db.execute('SELECT pg_advisory_unlock(245,%s::int)',(job['id'],))
    return False

def run_turn(db,source,state,client,send,excluded_keys,max_requests=100,new_request_limit=1,*,preparation_first=True):
    """每线程交替领取日常与封口任务，某一队列持续积压不饿死另一队列。"""
    preparations=getattr(client,'preparations',None)
    for daily in (preparation_first,not preparation_first):
        if daily:
            worked=(preparations is not None and getattr(client,'schedule_enabled',False)
                and preparations.tick(source,client,excluded_keys,max_requests,new_request_limit))
        else:
            # process仍逐人核最后工作日准备；轮转不绕过来源、预算或封口门禁。
            worked=tick(db,source,state,client,send,excluded_keys,max_requests,new_request_limit)
        if worked:return True,not daily
    return False,preparation_first

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
    parser.add_argument('--source-connections',type=int,default=1,help='按需来源采集最大连接数，不影响产品库任务锁')
    parser.add_argument('--reading-concurrency',type=int,choices=[1,2,4],default=1,help='每人独立阅读页并发；归并和评分仍顺序执行，受统一模型配额限制')
    parser.add_argument('--schedule-mode',choices=['disabled','enabled'],default='disabled',help='显式启用服务端受控调度和日常来源准备，仍不自动发布')
    parser.add_argument('--pilot-day-job-id',type=int,action='append',help='关闭调度，仅领取显式指定的一至两个日评任务并保存草稿；可重复传参')
    parser.add_argument('--evidence-first',action='store_true',help='独立周评验证使用程序索引；未验收前不得用于生产切换')
    parser.add_argument('--session-review',action='store_true',help='会话主线取证及同次终判；独立状态验证，不接旧首读与全对归并')
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
    from personal_scope import excluded_api_key_ids
    args.exclude_api_key_id=excluded_api_key_ids(args.exclude_api_key_id)
    if args.max_requests_per_job<1:parser.error('任务请求预算必须为正整数')
    if not 1<=args.new_requests_per_turn<=20:parser.error('每轮新请求数须在1至20之间')
    if not 1<=args.workers<=32:parser.error('执行线程数须在1至32之间')
    if not 1<=args.source_connections<=args.workers:parser.error('来源连接数须在1至执行线程数之间')
    if args.executor=='api' and args.flow_config is None:parser.error('API执行必须指定全局模型配额配置，不使用猜测配额')
    if args.pilot_day_job_id and (len(set(args.pilot_day_job_id))!=len(args.pilot_day_job_id) or len(args.pilot_day_job_id)>2
            or any(not 0<job_id<=2147483647 for job_id in args.pilot_day_job_id) or args.schedule_mode!='disabled'
            or args.executor!='api' or args.evidence_first or args.session_review):
        parser.error('两人试评仅支持指定一至两个不同的日评任务ID、API执行器和关闭调度')
    if args.executor=='dsh' and args.workers!=1:parser.error('DSH尚未接入同一全局配额，只允许单执行线程')
    if args.executor=='dsh' and args.reading_concurrency!=1:parser.error('DSH尚未接入并行阅读合同')
    if args.schedule_mode=='enabled' and os.environ.get('ZHIYONGLI_ACTIVE_SCOPE_KEY'):
        parser.error('自动日常计划不能绑定临时单一活动范围；请在新计划的配置中解除该限制，保留旧评审配置')
    if args.executor=='dsh' and args.schedule_mode=='enabled':parser.error('日常准备当前只接入API全局配额，不隐式切换DSH执行器')
    if args.evidence_first and (args.executor!='api' or args.schedule_mode!='disabled'):
        parser.error('证据优先验证仅支持API和独立周评，日常调度尚未验收')
    if args.session_review and (args.evidence_first or args.executor!='api'):
        parser.error('会话评审须使用API独立状态，不叠加旧证据优先或日常执行模式')
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
        if args.workers*args.reading_concurrency>client.flow.config['max_concurrency']:
            parser.error('执行线程与单人阅读并发乘积不能超过已确认的模型并发配额')
        client.reading_concurrency=args.reading_concurrency
    send=importer(args.backend_url,os.environ['ZHIYONGLI_WORKER_KEY_FILE'],draft_only=bool(args.pilot_day_job_id))
    from preparation import Preparations
    preparations=Preparations(args.state) if args.schedule_mode=='enabled' else None
    client.preparations=preparations
    client.pilot_day_job_ids=args.pilot_day_job_id
    client.evidence_first=args.evidence_first
    client.session_review=args.session_review
    args.state.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(args.state,0o700)
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,stop)
    source_slots=threading.BoundedSemaphore(args.source_connections)
    def source():return source_session(source_dsn,source_slots)
    with (args.state/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if getattr(client,'flow',None):client.flow.reconcile_startup(lock)
        heartbeat_stop=threading.Event()
        def heartbeat():
            while not heartbeat_stop.is_set():
                value=dict(pid=os.getpid(),at=time.time(),rules_version=RULES,workers=args.workers,reading_concurrency=args.reading_concurrency)
                if getattr(client,'flow',None):
                    value.update(model_flow=client.flow.snapshot(),waiting_for=client.flow.blocked())
                atomic(args.state/'heartbeat.json',value)
                heartbeat_stop.wait(15)
        pulse=threading.Thread(target=heartbeat,daemon=True);pulse.start()
        def schedule(once=False):
            if not once:heartbeat_stop.wait(60)
            while not stopped:
                try:
                    value=schedule_request(args.backend_url,os.environ['ZHIYONGLI_WORKER_KEY_FILE'])
                    client.preparation_first_week=value.get('preparation_first_week')
                    client.daily_first_day=value.get('first_day')
                    daily_items=([value['preparation']] if value.get('preparation') else [])+(value.get('preparations') or [])
                    for daily in daily_items:
                        if args.session_review:daily=dict(daily,mode='session_facts')
                        preparations.enqueue(daily)
                    client.schedule_enabled=value['enabled'] is True
                    atomic(args.state/'schedule-status.json',dict(state='enabled' if value['enabled'] else 'disabled',
                        at=time.time(),assessment_jobs=len(value.get('jobs') or []),
                        non_evaluation_weeks=value.get('non_evaluation_weeks') or [],daily_preparation=bool(value.get('preparation') or value.get('preparations')),preparations=preparations.snapshot()))
                except Exception as ex:
                    client.schedule_enabled=False
                    atomic(args.state/'schedule-status.json',dict(state='needs_review',at=time.time(),error_type=type(ex).__name__))
                if once or args.once:break
                heartbeat_stop.wait(60)
        scheduler=threading.Thread(target=schedule,daemon=True) if preparations else None
        if scheduler:
            schedule(once=True)
            if not args.once:scheduler.start()
        try:
            def run():
                # psycopg连接和会话锁不能在线程间共用。
                with psycopg.connect(product_dsn,autocommit=True,connect_timeout=10) as db:
                    preparation_first=True
                    while not stopped:
                        worked,preparation_first=run_turn(db,source,args.state,client,send,args.exclude_api_key_id,
                            args.max_requests_per_job,args.new_requests_per_turn,preparation_first=preparation_first)
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
