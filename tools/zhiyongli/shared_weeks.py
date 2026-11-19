"""同来源周评跨任务复用；只存签名索引，原文及失败回执不复制、不覆盖。"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path

from runtime import atomic,digest,policy,compatible_policy,validate_review
from semantic_audit import validate as validate_audit

FILES=('input.json','packet.json','identified.json','unit.json','review.json','audit.json','native-report.json','bundle.json','review.receipt.json')

def result_files(mode):
    from single_pass import MODE
    if mode is None:return FILES
    if mode!=MODE:raise ValueError('未知评分执行方式')
    return tuple(n for n in FILES if n!='audit.json')+('single-pass.json','single-pass-case.json','single-pass-presentation.json')


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
    for name in ('context-navigation','navigation-access','audit-navigation-access','audit-capacity-access','model-view','identification/readback','identification/navigation-access'):
        paths.extend(p for p in (root/name).rglob('*') if p.is_file() and p.suffix in {'.json','.sqlite'})
    return sorted(paths)


def candidate_segment_files(directory):
    root=Path(directory)
    if not (root/'candidate-segments.json').exists():return []
    return [root/name for name in ('candidate-segments.json','reading-view.json') if (root/name).exists()]


def efficient_reading_files(directory):
    """新合同单独签入来源计划与程序证明；旧合同的共享索引保持原约束。"""
    proof=Path(directory)/'reading-efficiency.json'
    if not proof.exists():return []
    value=json.loads(proof.read_text())
    paths=[proof]+[Path(ref['path']) for ref in value['files']]
    view=Path(directory)/'reading-view.json'
    if view.exists():paths.append(view)
    return sorted(set(paths))


def check_efficient_reading(entry,packet,unit=None):
    paths=efficient_reading_files(entry['directory']);refs=entry.get('efficient_reading_files',[])
    if not paths and not refs:
        if unit and (unit.get('reading_provenance') or {}).get('reading_strategy'):
            raise ValueError('共享周评缺正文复用来源证明')
        return
    if len(paths)!=len(refs) or {str(p.resolve()) for p in paths}!={r['path'] for r in refs}:
        raise ValueError('共享周评缺正文复用计划及程序来源证明签名')
    for ref in refs:read_ref(ref)
    from reading_efficiency import check_evidence
    proof=json.loads((Path(entry['directory'])/'reading-efficiency.json').read_text())
    evidence=Path(proof['evidence_directory'])
    actual={str(p.resolve()) for p in evidence.glob('*.json') if not p.name.endswith('.receipt.json')}
    if actual!={r['path'] for r in proof['files']} or proof['packet_sha256']!=digest(packet):
        raise ValueError('共享周评正文复用的实际来源检查点缺失或变化')
    for ref in proof['files']:read_ref(ref)
    manifest=check_evidence(packet,evidence)
    if proof['reading_strategy']!=manifest['reading_strategy']:
        raise ValueError('共享周评正文复用合同不符')
    if 'page_navigation_mode' in manifest:
        from page_navigation import verify_tree
        from context_navigation import Navigation
        status=json.loads((evidence/'status.json').read_text())
        verify_tree(Navigation(Path(entry['directory'])/'context-navigation',packet),status)


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


def accepted_reference(shared_root, packet, directory, *, page_navigation=False):
    """定位本次实际采用的规范共享索引；最终内容由消费方再验签。"""
    from review_control import selected
    from page_navigation import MODE
    root = Path(shared_root).resolve()/source_identity(packet)
    directory = selected(directory).resolve()
    mode = MODE if page_navigation else None
    pointer = directory/'shared-week.json'
    if pointer.exists():
        ref = json.loads(pointer.read_text())['entry']
        path = Path(ref['path']).resolve(); entry = read_ref(ref)
        candidates = [(ref, entry)]
    else:
        # 先固定签名，再核同一对象；不能核旧内容后重新登记被替换的文件。
        refs = [reference(path) for path in root.glob('*.json')]
        candidates = [(ref, read_ref(ref)) for ref in refs]
        candidates = [(ref, entry) for ref, entry in candidates
            if entry.get('state') == 'accepted' and entry.get('directory') == str(directory)
            and entry.get('page_navigation_mode') == mode and compatible_policy(entry.get('policy_sha256'))]
    if len(candidates) != 1:
        raise ValueError('正式说明缺少唯一已核共享评分索引，禁止从同目录文件猜原评分')
    ref, entry = candidates[0]
    path = Path(ref['path']).resolve()
    key = digest(dict(policy_sha256=entry.get('policy_sha256'), page_navigation_mode=mode)) if mode else entry.get('policy_sha256')
    if (path.parent != root or path.stem != key or entry.get('state') != 'accepted'
            or entry.get('source_identity') != source_identity(packet)
            or entry.get('page_navigation_mode') != mode or not compatible_policy(entry.get('policy_sha256'))):
        raise ValueError('正式说明共享索引不属于实际来源、模式或执行合同')
    if read_ref(ref) != entry:
        raise ValueError('正式说明共享索引在来源校验期间变化，禁止重登记')
    return ref


def checked(entry):
    if entry.get('state')!='accepted' or not compatible_policy(entry.get('policy_sha256')):
        raise ValueError('共享周评不是当前合同已核结果')
    data={}
    for name in result_files(entry.get('review_mode')):
        value=read_ref(entry['files'][name])
        if name=='input.json' and (value.get('page_navigation_mode')!=entry.get('page_navigation_mode')
                or value.get('review_mode')!=entry.get('review_mode')):
            raise ValueError('共享周评与执行起点的阅读或评分模式不符')
        # 辅助文件仍逐一验签、解析，但后续不消费的完整副本不常驻内存。
        # 保留原有全部验证，避免大冻结材料和识别结果无谓叠加。
        if name in {'packet.json','unit.json','review.json','audit.json','bundle.json'}:
            data[name]=value
        del value
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
    unit=data['unit.json'];review=data['review.json']['value']
    check_efficient_reading(entry,data['packet.json'],unit)
    check_navigation(entry,unit)
    check_candidate_segments(entry,data['packet.json'],unit)
    if unit.get('context_readback') and not entry.get('context_readback_files'):
        raise ValueError('共享周评缺补读链的签名，不能复用')
    from review_control import model_receipt
    model_receipt(entry['directory'],unit)
    if entry.get('review_mode'):
        from single_pass import verify
        return verify(entry,data)
    audit=data['audit.json']
    from review_control import resolution
    resolved=resolution(entry['directory'],unit)
    explicit=data['review.json']['adaptation'].get('version')=='explicit-review-resolution'
    if explicit:
        if not resolved or digest(resolved[0])!=digest(review):raise ValueError('已核周评的显式处置丢失或变化')
    validate_review(unit,review)
    from runtime import RUBRIC
    from audit_readback import context as audit_context,files as audit_files
    paths=audit_files(entry['directory']);refs=entry.get('audit_readback_files',[])
    if len(paths)!=len(refs) or {str(p.resolve()) for p in paths}!={r['path'] for r in refs}:
        raise ValueError('共享周评缺完整复查补读链签名')
    for ref in refs:read_ref(ref)
    actual=validate_audit(unit,review,audit['value'],RUBRIC['items'],context=audit_context(entry['directory'],unit),
        require_facts=not explicit)
    if actual!=audit or not actual['supported']:raise ValueError('共享周评语义复查未通过')
    from pipeline import build_week_bundle
    native,bundle=build_week_bundle(data['packet.json'],unit,review,audit,Path(entry['directory']),
        resolved[2] if explicit else None)
    if (digest(bundle)!=digest(data['bundle.json'])
            or digest(native)!=digest(read_ref(entry['files']['native-report.json']))):
        raise ValueError('共享周评报告与已核原判的确定性导出不一致')
    return data['bundle.json']


@contextmanager
def slot(shared_root,packet,directory, *, page_navigation=False,review_mode=None):
    """同一来源锁涵盖模型执行；不同规则也不能绕过旧unknown另发请求。"""
    root=Path(shared_root)/source_identity(packet);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    from page_navigation import MODE
    mode=MODE if page_navigation else None
    def key(stamp):
        if review_mode:return digest(dict(policy_sha256=stamp,page_navigation_mode=mode,review_mode=review_mode))
        return digest(dict(policy_sha256=stamp,page_navigation_mode=mode)) if mode else stamp
    path=root/(key(policy())+'.json');directory=Path(directory).resolve()
    with (root/'week.lock').open('a') as lock:
        from pipeline import Paused
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise Paused('queued','相同来源周评正在执行，等待原任务落盘后继续') from None
        compatible=[]
        for candidate in root.glob('*.json'):
            value=json.loads(candidate.read_text())
            if (value.get('page_navigation_mode')==mode
                    and value.get('review_mode')==review_mode
                    and candidate.stem==key(value.get('policy_sha256'))
                    and compatible_policy(value.get('policy_sha256'))):compatible.append(candidate)
        if len(compatible)>1:raise ValueError('同来源存在多个兼容执行索引，须核对原执行记录')
        if compatible:path=compatible[0]
        for prior in root.glob('*.json'):
            entry=json.loads(prior.read_text())
            if entry['directory']==str(directory):continue
            from request_retry import blocking_status
            if any(blocking_status(p) in {'unknown','requesting'}
                for p in Path(entry['directory']).rglob('*.receipt.json')):
                raise Paused('unknown','同来源周评已有结果不明请求，跨任务或换合同也不能重发；请核对原执行记录')
            if entry.get('page_navigation_mode')!=mode and entry.get('state')!='accepted':
                raise Paused('needs_review','同来源尚有未完成的另一阅读模式，须先处理原执行，不换模式重复评审')
            if entry.get('review_mode')!=review_mode and entry.get('state')!='accepted':
                raise Paused('needs_review','同来源已有未完成的另一评分流程，保留原请求与异议，不换流程重复评分')
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
            if mode:entry['page_navigation_mode']=mode
            if review_mode:entry['review_mode']=review_mode
            if existing:entry['previous']=existing
            atomic(path,entry)
        yield None
        entry.update(state='accepted',files={name:reference(directory/name) for name in result_files(review_mode)})
        entry['context_readback_files']=[reference(p) for p in sorted((directory/'context-readback').rglob('*.json'))]
        if (directory/'current-model-review.json').exists():
            entry['context_readback_files'].append(reference(directory/'current-model-review.json'))
        entry['goal_comparison_files']=[reference(p) for p in sorted((directory/'identification/global-comparisons').rglob('*.json'))]
        entry['navigation_files']=[reference(p) for p in navigation_files(directory)]
        entry['candidate_segment_files']=[reference(p) for p in candidate_segment_files(directory)]
        efficient=efficient_reading_files(directory)
        if efficient:entry['efficient_reading_files']=[reference(p) for p in efficient]
        from audit_readback import files as audit_files
        entry['audit_readback_files']=[reference(p) for p in audit_files(directory)]
        checked(entry)
        atomic(path,entry)
