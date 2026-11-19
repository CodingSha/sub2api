"""从已验签周评进入独立说明补核；不导入产品、不发布、不改变原评分。"""
from pathlib import Path

from runtime import digest
from shared_weeks import checked, file_digest, read_ref, reference
import task_evidence
import task_interpretation
import week_basis

VERSION = 'accepted-week-explanation-owned-intents-20261116.3'


def enrich(entry_ref, root, client, *, read_only=False, prior=None):
    """先复验评分/复查链，避免把同目录任意review当作父评分的依据。"""
    entry=read_ref(entry_ref)
    bundle=checked(entry)
    directory=Path(entry['directory']).resolve()
    output=Path(root).resolve()
    if output==directory or directory in output.parents or output in directory.parents:
        raise ValueError('说明须使用与冻结评分分离的执行目录')
    from explanation_run import claim
    with claim(entry_ref,root,read_only=read_only) as ownership:
        class OwnedClient:
            config=client.config
            def _call(self,payload,receipt):return ownership.call(client,payload,receipt)
        return _enrich(entry_ref,root,OwnedClient(),entry,bundle,ownership,read_only=read_only,prior=prior)


def _enrich(entry_ref,root,client,entry,bundle,ownership,*,read_only=False,prior=None):
    files = entry['files']
    directory = Path(entry['directory']).resolve()
    root = Path(root).resolve()
    if root == directory or directory in root.parents or root in directory.parents:
        raise ValueError('说明须使用与冻结评分分离的执行目录')
    for name in ('unit.json', 'review.json', 'bundle.json'):
        if Path(files[name]['path']).resolve() != directory/name:
            raise ValueError('说明父包、原判与输入不属于同一已核评分目录')
    unit = read_ref(files['unit.json'])
    original_review = read_ref(files['review.json'])['value']
    if digest(read_ref(files['bundle.json'])) != digest(bundle):
        raise ValueError('已核评分包在装配说明输入前变化')
    stamp = dict(version=VERSION, accepted_entry=entry_ref,
        unit=files['unit.json'], review=files['review.json'], bundle=files['bundle.json'],
        executor=reference(__file__), generator=reference(week_basis.__file__),
        owner=reference(ownership.directory/'owner.json'),
        ownership_helper=reference(__import__('explanation_run').__file__),
        role_helper=reference(task_interpretation.__file__), records_helper=reference(task_evidence.__file__))
    task_interpretation.freeze(root/'accepted-input.json', stamp, read_only)
    result = week_basis.enrich(bundle, unit, directory/'bundle.json', root/'basis', client,
        read_only=read_only, prior=prior, original_review=original_review)
    # 结束时流式复验原评分链全部已登记签名，不再驻留一份完整材料对象。
    refs = [entry_ref] + list(files.values())
    for name in ('context_readback_files', 'goal_comparison_files', 'navigation_files',
                 'candidate_segment_files', 'efficient_reading_files', 'audit_readback_files'):
        refs.extend(entry.get(name, []))
    refs.extend(stamp[name] for name in ('executor', 'generator', 'role_helper', 'records_helper', 'owner', 'ownership_helper'))
    for ref in refs:
        if file_digest(ref['path']) != ref['sha256']:
            raise ValueError('说明生成期间已核评分链或说明代码变化，保留回执待核')
    ownership.load()
    task_interpretation.freeze(root/'result.json', dict(version=VERSION, result=result,
        accepted_input=reference(root/'accepted-input.json'),
        basis_manifest=reference(root/'basis/manifest.json'),
        requests=reference(ownership.path),
        imported=False, published=False), read_only)
    return result


def verify(entry_ref, root, config, *, prior=None):
    """只读重放完整输入及原回执；缺件拒绝，不补写或补发。"""
    class Cached:
        def __init__(self):
            self.config = config

        def _call(self, value, receipt):
            from request_retry import read_receipt
            if not receipt.is_file():
                raise ValueError('缺说明原回执，只读验证不能发送')
            return read_receipt(receipt)

    return enrich(entry_ref, root, Cached(), read_only=True, prior=prior)
