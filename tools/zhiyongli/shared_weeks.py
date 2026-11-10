"""同来源周评跨任务复用；只存签名索引，原文及失败回执不复制、不覆盖。"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path

from runtime import atomic,digest,policy,validate_review
from semantic_audit import validate as validate_audit

FILES=('input.json','packet.json','identified.json','unit.json','review.json','audit.json','native-report.json','bundle.json','review.receipt.json')


def source_identity(packet):
    # 封口采集时刻可不同；完整事件、身份、时窗、缺口和所有其他实际评审材料必须相同。
    return digest({k:v for k,v in packet.items() if k!='source_snapshot'})


def reference(path):
    path=Path(path).resolve()
    return dict(path=str(path),sha256=file_digest(path))


def file_digest(path):
    # 节点库可达数十MB；验签不把整个数据库复制进Worker内存。
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):result.update(chunk)
    return result.hexdigest()


def read_ref(ref):
    raw=Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('共享周评的实际文件签名变化')
    return json.loads(raw)


def navigation_files(directory):
    root=Path(directory)
    paths=[]
    for name in ('context-navigation','navigation-access','audit-navigation-access','model-view','identification/readback','identification/navigation-access'):
        paths.extend(p for p in (root/name).rglob('*') if p.is_file() and p.suffix in {'.json','.sqlite'})
    return sorted(paths)


def candidate_segment_files(directory):
    root=Path(directory)
    if not (root/'candidate-segments.json').exists():return []
    return [root/name for name in ('candidate-segments.json','reading-view.json') if (root/name).exists()]


def check_candidate_segments(entry,packet,unit):
    directory=Path(entry['directory']);paths=candidate_segment_files(directory)
    refs=entry.get('candidate_segment_files',[])
    if not paths and not refs and not unit.get('candidate_segmentation'):return
    if (not paths or not refs or len(paths)!=len(refs)
            or {r['path'] for r in refs}!={str(p.resolve()) for p in paths}):
        raise ValueError('共享周评缺完整候选分段签名')
    for ref in refs:read_ref(ref)
    from candidate_segments import split
    # 分段来源可为阅读视图，原始冻结包仍单独保留并验签。
    view=directory/'reading-view.json'
    source=json.loads(view.read_text()) if view.exists() else packet
    expected=split(source).get('candidate_segmentation')
    if json.loads((directory/'candidate-segments.json').read_text())!=expected or unit.get('candidate_segmentation')!=expected:
        raise ValueError('共享周评的候选分段来源或区间变化')


def check_navigation(entry,unit):
    paths=navigation_files(entry['directory'])
    refs=entry.get('navigation_files',[])
    if not paths and not refs and not unit.get('context_access'):return
    if (not refs or len(refs)!=len(paths)
            or {r['path'] for r in refs}!={str(p.resolve()) for p in paths}):
        raise ValueError('共享周评缺完整分层导航及访问链签名，不能复用')
    for ref in refs:
        if file_digest(ref['path'])!=ref['sha256']:
            raise ValueError('共享周评分层导航或访问链的签名变化')
    from context_navigation import check_cached_receipt
    for path in (Path(entry['directory'])/'context-navigation').glob('*.cache.json'):
        check_cached_receipt(path)


def resolve(directory):
    from review_control import selected
    root=selected(directory);path=root/'shared-week.json'
    if not path.exists():return root
    proof=json.loads(path.read_text());entry=read_ref(proof['entry'])
    checked(entry)
    return Path(entry['directory'])


def checked(entry):
    if entry.get('state')!='accepted' or entry.get('policy_sha256')!=policy():
        raise ValueError('共享周评不是当前合同已核结果')
    data={name:read_ref(entry['files'][name]) for name in FILES}
    # 补读后的实际候选、输入及每轮回执也须验签，不能只核最初的补读请求。
    for ref in entry.get('context_readback_files',[]):read_ref(ref)
    for ref in entry.get('goal_comparison_files',[]):read_ref(ref)
    comparison_root=Path(entry['directory'])/'identification/global-comparisons'
    if comparison_root.exists() and not entry.get('goal_comparison_files'):
        raise ValueError('共享周评缺全局目标比较的签名，不能复用')
    from request_retry import read_receipt
    # 链入口的签名不覆盖后续尝试正文，复用时必须逐条核验。
    for path in Path(entry['directory']).rglob('*.receipt.json'):read_receipt(path)
    if source_identity(data['packet.json'])!=entry['source_identity']:
        raise ValueError('共享周评来源身份不符')
    unit=data['unit.json'];review=data['review.json']['value'];audit=data['audit.json']
    check_navigation(entry,unit)
    check_candidate_segments(entry,data['packet.json'],unit)
    if unit.get('context_readback') and not entry.get('context_readback_files'):
        raise ValueError('共享周评缺补读链的签名，不能复用')
    from review_control import model_receipt
    model_receipt(entry['directory'],unit)
    from review_control import resolution
    resolved=resolution(entry['directory'],unit)
    if data['review.json']['adaptation'].get('version')=='explicit-review-resolution':
        if not resolved or digest(resolved[0])!=digest(review):raise ValueError('已核周评的显式处置丢失或变化')
    validate_review(unit,review)
    from runtime import RUBRIC
    from audit_readback import context as audit_context,files as audit_files
    paths=audit_files(entry['directory']);refs=entry.get('audit_readback_files',[])
    if len(paths)!=len(refs) or {str(p.resolve()) for p in paths}!={r['path'] for r in refs}:
        raise ValueError('共享周评缺完整复查补读链签名')
    for ref in refs:read_ref(ref)
    actual=validate_audit(unit,review,audit['value'],RUBRIC['items'],context=audit_context(entry['directory'],unit))
    if actual!=audit or not actual['supported']:raise ValueError('共享周评语义复查未通过')
    return data['bundle.json']


@contextmanager
def slot(shared_root,packet,directory):
    """同一来源锁涵盖模型执行；不同规则也不能绕过旧unknown另发请求。"""
    root=Path(shared_root)/source_identity(packet);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=root/(policy()+'.json');directory=Path(directory).resolve()
    with (root/'week.lock').open('a') as lock:
        from pipeline import Paused
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise Paused('queued','相同来源周评正在执行，等待原任务落盘后继续') from None
        for prior in root.glob('*.json'):
            entry=json.loads(prior.read_text())
            if entry['directory']==str(directory):continue
            from request_retry import blocking_status
            if any(blocking_status(p) in {'unknown','requesting'}
                for p in Path(entry['directory']).rglob('*.receipt.json')):
                raise Paused('unknown','同来源周评已有结果不明请求，跨任务或换合同也不能重发；请核对原执行记录')
        existing=json.loads(path.read_text()) if path.exists() else None
        if existing and existing.get('state')=='accepted':
            result=checked(existing)
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            if Path(existing['directory'])!=directory:atomic(directory/'shared-week.json',dict(entry=reference(path)))
            yield result
            return
        if existing and existing['directory']!=str(directory):
            from review_control import is_revision
            if not is_revision(directory,existing['directory'],packet):
                raise Paused('needs_review','同来源周评已有未完成或待核执行，复用其完成阶段须先处理原任务，不另起重复评审')
        if existing and existing['directory']==str(directory):
            # 正常轮转接续同一执行，不把每轮都嵌套成一次历史修订。
            entry=existing
        else:
            entry=dict(source_identity=source_identity(packet),policy_sha256=policy(),directory=str(directory),state='pending')
            if existing:entry['previous']=existing
            atomic(path,entry)
        yield None
        entry.update(state='accepted',files={name:reference(directory/name) for name in FILES})
        entry['context_readback_files']=[reference(p) for p in sorted((directory/'context-readback').rglob('*.json'))]
        if (directory/'current-model-review.json').exists():
            entry['context_readback_files'].append(reference(directory/'current-model-review.json'))
        entry['goal_comparison_files']=[reference(p) for p in sorted((directory/'identification/global-comparisons').rglob('*.json'))]
        entry['navigation_files']=[reference(p) for p in navigation_files(directory)]
        entry['candidate_segment_files']=[reference(p) for p in candidate_segment_files(directory)]
        from audit_readback import files as audit_files
        entry['audit_readback_files']=[reference(p) for p in audit_files(directory)]
        checked(entry)
        atomic(path,entry)
