"""已有合同错误的独立诊断候选；不判分、不修原判、不接管执行器。"""
from . import candidate_review_linkage as base
from .common import moment
from .revised_review import find_proof

VERSION = 'contract-diagnostics-20261103.1'


def _refs(value):
    return isinstance(value, list) and all(isinstance(r, str) for r in value)


def known_issues(unit, value):
    """只收集可独立检查的三类既有错误，不声称合同完整或语义完整。"""
    if not isinstance(value, dict) or value.get('needs_events'):
        return []
    ledger = value.get('evidence')
    if not isinstance(ledger, list) or not all(isinstance(e, dict) for e in ledger):
        return []
    ids = [e.get('id') for e in ledger]
    if not all(isinstance(r, str) and r for r in ids) or len(ids) != len(set(ids)):
        return []  # 账本身份未成立时，不生成依赖它的连锁错误。
    by_id = dict(zip(ids, ledger))
    events = {e['event']: e for e in unit['events']}
    latest = max(unit['periods'], key=lambda p: moment(p['end']))
    issues = []
    for entry in ledger:
        if not isinstance(entry.get('event'), str) or not isinstance(entry.get('quote'), str):
            continue
        try:
            find_proof(entry, events, latest)
        except ValueError as ex:
            issues.append(dict(code='引文来源', path='evidence/' + entry['id'], detail=str(ex)))
    parts = value.get('period_reviews')
    if not isinstance(parts, list):
        return issues
    for pi, part in enumerate(parts):
        if not isinstance(part, dict):
            continue
        seen = []
        cells = part.get('cells')
        for ci, cell in enumerate(cells if isinstance(cells, list) else []):
            if not isinstance(cell, dict) or cell.get('state') != 'observed':
                continue
            action = cell.get('action')
            refs = action.get('refs') if isinstance(action, dict) else None
            if not _refs(refs) or not set(refs) <= set(by_id):
                continue
            entries = [by_id[r] for r in refs]
            if not all(isinstance(e.get('event'), str) and isinstance(e.get('quote'), str) for e in entries):
                continue
            signature = {(e['event'], e['quote']) for e in entries}
            for oi, other, old in seen:
                if signature & old and (not cell.get('difference') or not other.get('difference')):
                    issues.append(dict(code='共引独立说明', path=f'period_reviews[{pi}].cells[{oi},{ci}]',
                        detail='双方均须说明独立事实；程序不裁定该说明是否真实。'))
            seen.append((ci, cell, signature))
        coverage = part.get('fact_coverage')
        for fi, fact in enumerate(coverage if isinstance(coverage, list) else []):
            if not isinstance(fact, dict):
                continue
            refs = fact.get('refs')
            if not _refs(refs) or not refs or not set(refs) <= set(by_id):
                issues.append(dict(code='事实引用', path=f'period_reviews[{pi}].fact_coverage[{fi}]',
                    detail='须有账本内的有效引用；不自动补引用或删除事实。'))
    return issues


def check_with_diagnostics(unit, value):
    """通过仍返回原完整校验结果；失败仅补定位，不接纳或修改无效输出。"""
    try:
        return base.check(unit, value)
    except (ValueError, KeyError, TypeError, AttributeError) as ex:
        first = str(ex)
        issues = known_issues(unit, value)
        locations = '；'.join(i['code'] + '@' + i['path'] for i in issues)
        raise ValueError(first + ('\n同份输出已检出：' + locations if locations else '')) from None
