"""发布资料限制处置；保留024原始结果，不改变评分或补未知分。"""
import copy
import hashlib
import json

VERSION = 'scope-disclosure-20261106.1'
OBSERVATION_VERSION = 'scope-disclosure-20261106.2'


def signature(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def assess(computed, decisions, eligibility=None):
    """逐条绑定原提示；新合同仅在整月资格充分且同项另有可评期间时接受局部未知。"""
    gaps = {signature(g): g for g in computed['source_gaps']}
    if len(decisions) != len(gaps) or {d['id'] for d in decisions} != set(gaps):
        raise ValueError('资料提示未逐条处置或出现多余处置')
    issues = []
    for d in decisions:
        if d.get('status') not in {'limitation', 'blocking'} or not d.get('reason') or not d.get('basis'):
            raise ValueError('资料处置缺状态、理由或本轮评分依据')
        if d.get('source') != gaps[d['id']]:
            raise ValueError('资料处置原提示发生变化')
        issues.append(copy.deepcopy(d))
    blockers = [d['id'] for d in issues if d['status'] == 'blocking']
    # 保持旧合同；新合同不豁免归属争议、待复核或同项全月不可观察。
    for g in computed['source_gaps']:
        if isinstance(g, str) and signature(g) not in blockers:
            item = g.split('：', 1)[0].rsplit('/', 1)[-1]
            local_unknown = (eligibility and eligibility['eligible'] and g.endswith('：unobservable')
                             and computed.get('items', {}).get(item, {}).get('exact') is not None)
            if not local_unknown:
                raise ValueError('实际未决评分状态不能豁免')
    return dict(version=OBSERVATION_VERSION if eligibility is not None else VERSION, source_gap_digest=signature(computed['source_gaps']),
                issues=issues, blockers=blockers,
                scope='冻结本地来源中的可观察AI使用表现；资料限制完整保留，非独立人评或全域能力证明',
                gradeable=computed['exact'] is not None and not blockers and (eligibility is None or eligibility['eligible']))
