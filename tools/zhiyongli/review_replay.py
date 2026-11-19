"""隔离回放冻结包；默认离线，显式模型配置才发送，绝不导入或发布。"""
import argparse
from collections import Counter
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import time
from contextlib import contextmanager
import resource
import sys

from bounded_read import checkpoint
from runtime import atomic, digest, policy
from source_index import MODE, SourceIndex, build_view


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select(packet, request_id=None, excluded_keys=()):
    """旧包不补造Key身份；要求排除时，来源缺失必须阻断，不能算排除成功。"""
    result=copy.deepcopy(packet)
    events=result['events']
    if excluded_keys:
        if any(type(e.get('api_key_id')) is not int or e['api_key_id']<1 for e in events):
            raise ValueError('冻结包缺少可核API Key ID；请从原审计行恢复身份后排除')
        events=[e for e in events if e['api_key_id'] not in excluded_keys]
    if request_id:
        events=[e for e in events if e.get('request_id')==request_id]
        if not events or len({(e.get('source_user_id'),e.get('session_id')) for e in events})!=1:
            raise ValueError('请求样本不存在或跨身份/会话，不猜测合并')
    if not events:
        raise ValueError('回放没有剩余来源事件')
    result['events']=events
    result['replay_scope']=dict(experiment_only=True, request_id=request_id,
        excluded_api_key_ids=sorted(set(excluded_keys)),
        service_key_exclusion_verified=bool(excluded_keys),
        limitation='隔离质量实验；抽样可能缺跨请求反证，禁止作为正式分数或生产导入。')
    return result


def offline(packet, root):
    """核验全部字符可重建；目录可达不等于模型已读或语义通过。"""
    from reading_profile import reading_cost
    from candidate_segments import split
    from goal_batches import plan
    started=time.monotonic()
    old=reading_cost(packet)
    view=build_view(packet,root)
    nav=SourceIndex(root/'context-navigation',packet)
    keys=json.loads((nav.root/'leaves.json').read_text())['node_ids']
    actual={e['event']:hashlib.sha256() for e in packet['events']}
    lengths=Counter()
    for key in keys:
        leaf=nav.read([key],raw=True)['nodes'][0]
        actual[leaf['event_id']].update(leaf['text'].encode())
        lengths[leaf['event_id']]+=len(leaf['text'])
    if any(actual[e['event']].hexdigest()!=hashlib.sha256(e['text'].encode()).hexdigest()
           or lengths[e['event']]!=len(e['text']) for e in packet['events']):
        raise ValueError('原文重建失败')
    return dict(events=len(packet['events']),actors=dict(Counter(e['actor_class'] for e in packet['events'])),
        timestamp_basis=dict(Counter(e.get('timestamp_basis','missing') for e in packet['events'])),
        key_identity_missing=sum(e.get('api_key_id') is None for e in packet['events']),
        legacy_reading=old,source_index_fragments=len(keys),candidate_batches=len(plan(split(view))),
        source_index_model_calls=0,all_characters_verified=True,semantic_acceptance=False,
        offline_seconds=round(time.monotonic()-started,3))


def bind_contract(state, root, contract, revision_from=None, budget_revision_reason=None):
    """显式工程修订保留原合同和累计账；额度调整另存审计，不能换Key清账。"""
    path=state/'model-contract.json'
    if path.exists():
        parent=json.loads(path.read_text())
        if parent!=contract:
            if root.resolve()==state.resolve():
                raise ValueError('代码修订必须使用新的执行目录，不覆盖原失败执行')
            old=copy.deepcopy(parent);new=copy.deepcopy(contract)
            old.pop('policy_sha256');new.pop('policy_sha256')
            old_limit=old['settings'].pop('max_requests');new_limit=new['settings'].pop('max_requests')
            old_retries=old['settings'].pop('max_429_retries',0);new_retries=new['settings'].pop('max_429_retries',0)
            if old!=new:
                raise ValueError('修订不能改变累计预算、模型、端点或墙钟上限')
            budget=None
            if (old_limit,old_retries)!=(new_limit,new_retries):
                audit=state/'budget-revisions'/(digest(contract)+'.json')
                if audit.exists():
                    budget=json.loads(audit.read_text())
                    if (budget.get('parent_contract_sha256')!=digest(parent)
                            or budget.get('contract_sha256')!=digest(contract)
                            or budget.get('previous_limit')!=old_limit or budget.get('new_limit')!=new_limit
                            or budget.get('previous_429_retries',0)!=old_retries or budget.get('new_429_retries',0)!=new_retries):
                        raise ValueError('工程预算修订与原合同不符')
                else:
                    if (new_limit<old_limit or not 0<=old_retries<=new_retries<=3
                            or not isinstance(budget_revision_reason,str) or not budget_revision_reason.strip()):
                        raise ValueError('不能改变累计预算；增加工程上限须明确修订原因，保留原账')
                    import sqlite3
                    ledger=state/'flow/model-flow.sqlite'
                    if not ledger.exists():raise ValueError('调整工程上限前须保留原累计账簿')
                    with sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro',uri=True) as db:
                        used=db.execute("SELECT count(*) FROM attempts WHERE budget_key=? AND status!='cancelled'",
                            (parent['settings']['budget_key'],)).fetchone()[0]
                    budget=dict(version=1,parent_contract_sha256=digest(parent),contract_sha256=digest(contract),
                        budget_key=parent['settings']['budget_key'],previous_limit=old_limit,new_limit=new_limit,
                        previous_429_retries=old_retries,new_429_retries=new_retries,
                        used_at_revision=used,reason=budget_revision_reason,at=time.time(),
                        authority='已有任务授权内的工程参数调整，不代表用户指定次数或服务商配额')
            record=dict(version=1,parent_contract_sha256=digest(parent),
                parent_policy_sha256=parent['policy_sha256'],policy_sha256=contract['policy_sha256'],
                contract_sha256=digest(contract),reason='根因修复后的显式隔离复验，继承原累计账簿与截止时间')
            if budget is not None:record['budget_revision']=budget
            marker=root/'model-revision.json'
            if revision_from!=digest(parent) and not (marker.exists() and json.loads(marker.read_text())==record):
                raise ValueError('代码身份变化须显式指定原模型合同SHA256，不能静默兼容')
            checkpoint(marker,record)
            if budget is not None:checkpoint(audit,budget)
        elif revision_from is not None:
            raise ValueError('当前代码合同未变化，不应声明修订')
    else:
        if revision_from is not None:raise ValueError('修订的原合同不存在')
        checkpoint(path,contract)
    checkpoint(root/'model-contract.json',contract)


def seed_identification(packet, root, parent, *, reuse_initial_review=False):
    """复制同一冻结包的已收终态回执；实际执行仍逐个重算请求签名，不迁移评分。"""
    import shutil
    from request_retry import read_receipt
    parent=Path(parent).resolve();target=Path(root)/'run'
    if parent==target.resolve() or not (parent/'identified.json').exists():
        raise ValueError('复用来源须是另一个已完成归属识别的执行目录')
    prior=json.loads((parent/'packet.json').read_text())
    if digest(prior)!=digest(packet):raise ValueError('归属回执绑定另一冻结包')
    originals=[]
    for path in sorted(parent.rglob('*.receipt.json')):
        relative=path.relative_to(parent)
        if relative.parts[0]!='identification' and not (len(relative.parts)==1 and relative.name.startswith('identify.')):
            continue
        if read_receipt(path).get('status')!='received':raise ValueError('归属回执没有收到终态，不能局部复用')
        originals.append(dict(path=str(path),relative=str(relative),sha256=file_sha(path)))
    if not originals:raise ValueError('没有可核的归属回执')
    if reuse_initial_review:
        path=parent/'review.receipt.json'
        if not path.exists() or read_receipt(path).get('status')!='received':
            raise ValueError('初始评分回执未收到终态，不能复用')
        originals.append(dict(path=str(path),relative=path.name,sha256=file_sha(path)))
    proof=dict(parent=str(parent),packet_sha256=digest(packet),identified_sha256=file_sha(parent/'identified.json'),
        receipts=originals,reuse_initial_review=reuse_initial_review,
        validation='重放时逐请求核对签名、引文、归属与归并；禁止发送新的归属请求')
    checkpoint(Path(root)/'identification-reuse.json',proof)
    for ref in originals:
        source=Path(ref['path']);destination=target/ref['relative']
        if file_sha(source)!=ref['sha256']:raise ValueError('复制前原归属回执发生变化')
        destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if destination.exists():
            if file_sha(destination)!=ref['sha256']:raise ValueError('复用回执发生变化')
        else:shutil.copy2(source,destination)


def phase_deadline(state, root, contract, name, seconds):
    """显式新验收阶段保留旧耗时与全局账；同阶段重入不能重置时间。"""
    import re
    if not re.fullmatch('[a-z][a-z0-9-]{0,63}',name) or not 1<=seconds<=1800:
        raise ValueError('局部验收阶段名称或墙钟上限无效')
    old=state/'deadline.json'
    if not old.exists():raise ValueError('局部验收须保留原累计墙钟记录')
    prior=json.loads(old.read_text())
    if time.time()<prior['started_at']+prior['max_seconds']:
        raise ValueError('原验收墙钟尚未结束，继续使用原阶段')
    path=state/'validation-phases'/(name+'.json')
    record=dict(version=1,name=name,seconds=seconds,original_deadline_sha256=file_sha(old),
        contract_sha256=digest(contract),purpose='评分合同修复后的局部复验；累计请求上限及消耗沿用原账')
    if path.exists():
        existing=json.loads(path.read_text())
        if {k:v for k,v in existing.items() if k not in {'started_at','contract_sha256'}}!={k:v for k,v in record.items() if k!='contract_sha256'}:
            raise ValueError('同名验收阶段不能改变范围或重置时间')
        if existing['contract_sha256']!=record['contract_sha256']:
            revision=root/'model-revision.json'
            if not revision.exists() or json.loads(revision.read_text()).get('contract_sha256')!=digest(contract):
                raise ValueError('验收阶段代码变化须有显式模型合同修订证明')
        record=existing
    else:
        record['started_at']=time.time();checkpoint(path,record)
    checkpoint(root/'validation-phase.json',dict(record,execution_contract_sha256=digest(contract)))
    return record['started_at']+seconds


def model_run(packet, root, config_path, maximum, state_root=None, *, budget_key='review-redesign-20261117', max_seconds=None, revision_from=None,
              reuse_identification_from=None, reuse_initial_review=False, validation_phase=None, phase_max_seconds=900,
              full_person_validation=False, session_review=False, session_seed_from=None, session_seed_reading_only=False,
              budget_revision_reason=None,session_seed_allow_terminal=False,max_429_retries=0,session_resume_scoring=False):
    from model_config import ModelConfig
    from model_flow import ModelFlow
    from pipeline import evaluate_week, Paused
    from worker import JobClient
    if packet.get('workday_scope') is not None:
        from workday_scope import validate
        validate(packet['workday_scope'])
    config=ModelConfig.load(config_path)
    if session_review and (reuse_identification_from is not None or reuse_initial_review):
        raise ValueError('会话执行器必须独立建立事实，不能复用旧归属或旧评分')
    if session_seed_from is not None and not session_review:
        raise ValueError('只有会话执行器可显式承接会话事实')
    state=Path(state_root or root).resolve()
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    checkpoint(root/'model-state-ref.json',dict(state_root=str(state)))
    settings=dict(version=1,budget_key=budget_key,approved_by='李煜',
        reason='用户授权的冻结数据隔离验证，不复用生产额度或账簿',max_concurrency=1,
        requests_per_minute=20,reserved_tokens_per_minute=1_000_000,max_requests=maximum,
        transient_failure_limit=1,cooldown_seconds=60,max_429_retries=max_429_retries,max_interrupted_retries=0)
    contract=dict(settings=settings,policy_sha256=policy(),endpoint=config.responses_url,model=config.model)
    if max_seconds is not None:contract['max_seconds']=max_seconds
    with (state/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        bind_contract(state,root,contract,revision_from,budget_revision_reason)
        if reuse_identification_from is not None:
            seed_identification(packet,root,reuse_identification_from,reuse_initial_review=reuse_initial_review)
        flow=ModelFlow(state/'flow',settings)
        flow.reconcile_startup(lock)
        deadline=None
        if max_seconds is not None:
            path=state/'deadline.json'
            stamp=json.loads(path.read_text()) if path.exists() else dict(started_at=time.time(),max_seconds=max_seconds)
            if stamp.get('max_seconds')!=max_seconds:raise ValueError('累计墙钟预算不能在恢复时更改')
            checkpoint(path,stamp);deadline=stamp['started_at']+max_seconds
        if validation_phase is not None:
            if full_person_validation:
                if reuse_identification_from is not None:raise ValueError('完整人员复验不能复用旧归属')
                checkpoint(root/'full-person-validation.json',dict(packet_sha256=digest(packet),
                    purpose='新材料装配合同从全部冻结材料独立执行；累计额度沿原账，不重置旧耗时',request_limit=maximum))
            elif reuse_identification_from is None:raise ValueError('局部评分复验须复用已核归属；整人新验收须显式声明')
            deadline=phase_deadline(state,root,contract,validation_phase,phase_max_seconds)
        class ReplayClient(JobClient):
            def _call(self,payload,receipt,**kwargs):
                if (reuse_identification_from is not None
                        and payload.get('text',{}).get('format',{}).get('name') in {'zhiyongli_task_identification','zhiyongli_goal_merge'}
                        and not Path(receipt).exists()):
                    raise ValueError('局部评分复验缺少匹配的归属回执，禁止新增归属请求')
                return super()._call(payload,receipt,**kwargs)
            @contextmanager
            def send_slot(self,*args):
                if deadline is not None and time.time()>=deadline:
                    raise Paused('queued','达到本次验收累计墙钟上限，保留阶段及全部回执',reason_code='acceptance_deadline')
                with super().send_slot(*args):yield
        client=ReplayClient(config,root/'run',maximum,new_request_limit=maximum,flow=flow,state_root=state)
        client.evidence_first=not session_review
        client.session_review=session_review
        client.session_seed_from=session_seed_from
        client.session_seed_reading_only=session_seed_reading_only
        client.session_seed_allow_terminal=session_seed_allow_terminal
        client.session_resume_scoring=session_resume_scoring
        history=root/'model-invocations'
        history.mkdir(parents=True,exist_ok=True,mode=0o700)
        prior=root/'model-result.json'
        if prior.exists():
            value=json.loads(prior.read_text())
            checkpoint(history/(digest(value)+'.json'),value)
        started=time.monotonic()
        cpu_started=time.process_time()
        def progress(phase,done,total,message):
            atomic(root/'progress.json',dict(phase=phase,done=done,total=total,message=message,at=time.time()))
        try:
            bundle=evaluate_week(packet,root/'run',client,progress)
            outcome=dict(state='candidate_generated',reports=len(bundle['reports']),
                semantic_acceptance=False,production_imported=False)
        except Paused as ex:
            from review_protocol import failure
            outcome=dict(failure(ex),error=str(ex),semantic_acceptance=False,production_imported=False)
        except (ValueError,RuntimeError) as ex:
            # 详细错误仅写私有目录；终态未知和校验失败都不能被转换成分数。
            outcome=dict(state='validation_failed',error_type=type(ex).__name__,error=str(ex),
                semantic_acceptance=False,production_imported=False)
        outcome.update(invocation_seconds=round(time.monotonic()-started,3),cpu_seconds=round(time.process_time()-cpu_started,3),
            process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),
            cumulative_wall_seconds=round(time.time()-stamp['started_at'],3) if max_seconds is not None else None,
            flow=flow.snapshot())
        atomic(history/(str(time.time_ns())+'.json'),outcome)
        atomic(root/'model-result.json',outcome)
        return outcome


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet',type=Path,required=True)
    parser.add_argument('--expected-sha256',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--request-id')
    parser.add_argument('--exclude-api-key-id',type=int,action='append',default=[])
    parser.add_argument('--model-config',type=Path)
    parser.add_argument('--session-review',action='store_true',help='使用会话取证循环，替代旧首读与任务归并')
    parser.add_argument('--full-person-validation',action='store_true',help='新合同从完整冻结材料独立验收，沿用累计请求账簿')
    parser.add_argument('--session-seed-from',type=Path,help='同一冻结包的已核会话前缀；逐动作重验，不复用旧评分')
    parser.add_argument('--session-seed-reading-only',action='store_true',help='只承接已核原文，旧语义归属重新判断')
    parser.add_argument('--session-seed-allow-terminal',action='store_true',help='逐动作重验已完成执行的事实；不复用或改写旧评分')
    parser.add_argument('--model-state-root',type=Path,help='同一验证任务的多个样本共用累计模型账簿')
    parser.add_argument('--max-requests',type=int,default=8)
    parser.add_argument('--budget-key',default='review-redesign-20261117')
    parser.add_argument('--max-seconds',type=int)
    parser.add_argument('--wait-for-quota',action='store_true',help='仅对明确配额等待自动续跑，必须指定累计墙钟上限')
    parser.add_argument('--revision-from-sha256',help='修复后新执行须显式绑定原模型合同；保留累计消耗和截止时间')
    parser.add_argument('--budget-revision-reason',help='已有授权内调整本地工程请求上限的原因；保留原合同、账簿和消耗')
    parser.add_argument('--reuse-identification-from',type=Path,help='只复验评分；逐请求验签复用此执行的归属回执，禁止新增归属请求')
    parser.add_argument('--reuse-initial-review',action='store_true',help='显式复用同一来源初始评分终态，实际发送端仍重验请求签名')
    parser.add_argument('--validation-phase',help='原验收墙钟到期后的显式局部阶段名称，保留旧消耗和请求上限')
    parser.add_argument('--phase-max-seconds',type=int,default=900)
    args=parser.parse_args()
    source=args.packet.resolve();root=args.output.resolve()
    if args.max_requests<1 or any(key<1 for key in args.exclude_api_key_id):
        parser.error('预算及Key ID须为正整数')
    if args.max_seconds is not None and args.max_seconds<1 or args.wait_for_quota and args.max_seconds is None:
        parser.error('自动等待须有正整数累计墙钟上限')
    if source.is_relative_to(root) or file_sha(source)!=args.expected_sha256:
        parser.error('来源须在输出目录之外且SHA256必须匹配')
    original=json.loads(source.read_text())
    packet=select(original,args.request_id,args.exclude_api_key_id)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    # 锁覆盖离线、核账与发送，阻止同目录并行重入；已有请求不能重置累计预算。
    with (root/'replay.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        checkpoint(root/'replay-input.json',dict(source=str(source),source_sha256=args.expected_sha256,
            packet_sha256=digest(packet),mode=MODE,scope=packet['replay_scope']))
        if args.session_review:
            from session_evidence import SessionEvidence
            store=SessionEvidence.build(packet,root/'offline/session-evidence')
            try:
                result=dict(events=store.manifest['events'],sessions=len(store.manifest['sessions']),
                    candidate_inputs=store.list_inputs()['total'],all_characters_verified=True,
                    semantic_acceptance=False,source_access_mode='session_review')
            finally:store.close()
        else:result=offline(packet,root/'offline')
        atomic(root/'offline-result.json',result)
        try:
            if args.model_config:
                while True:
                    result['model']=model_run(packet,root,args.model_config,args.max_requests,args.model_state_root,
                        budget_key=args.budget_key,max_seconds=args.max_seconds,revision_from=args.revision_from_sha256,
                        reuse_identification_from=args.reuse_identification_from,validation_phase=args.validation_phase,
                        phase_max_seconds=args.phase_max_seconds,reuse_initial_review=args.reuse_initial_review,
                        full_person_validation=args.full_person_validation,session_review=args.session_review,
                        session_seed_from=args.session_seed_from,session_seed_reading_only=args.session_seed_reading_only,
                        budget_revision_reason=args.budget_revision_reason,session_seed_allow_terminal=args.session_seed_allow_terminal)
                    if not (args.wait_for_quota and result['model'].get('state')=='queued'
                            and result['model'].get('reason_code')=='model_flow_wait'):
                        break
                    time.sleep(5)
        finally:
            if file_sha(source)!=args.expected_sha256:
                raise ValueError('回放期间来源文件发生变化')
        result['source_file_unchanged']=True
        atomic(root/'result.json',result)
        # 不输出员工正文、模型原答或凭证；明细只留在权限受限的回放目录。
        print(json.dumps(dict(output=str(root),events=result['events'],
            all_characters_verified=True,source_file_unchanged=True,
            model_state=result.get('model',{}).get('state')),ensure_ascii=False))


if __name__=='__main__':
    main()
