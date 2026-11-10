"""合同返修薄适配：实际发送范围说明及原判差异，不替代完整校验。"""
import json

from .batch_client import atomic_json, digest
from .common import require
from .stable_client import JSONClient

VERSION = 'contract-repair-scope-20261103.1'
INSTRUCTION = '本次是合同返修。依据contract_error及原文，修复报错处和受其事实、归属或共享引用影响的部分；逐字引用不得为通过校验改写。保持未受影响的事实和结论。若发现其他结论原本误用了既有判据，可修正，但须在相应既有reason中说明原误用、适用判据及证据，不能无据重判。仍返回完整输出，接受完整合同校验。'


def adapt_payload(data):
    """只替换合同返修instruction；补读及初评原样保留，不修改调用方对象。"""
    correction = data.get('correction')
    if not isinstance(correction, dict) or 'contract_error' not in correction:
        return data
    return dict(data, correction=dict(correction, instruction=INSTRUCTION))


def _normalize(value, ledger):
    if isinstance(value, list):
        return [_normalize(v, ledger) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == 'refs' and isinstance(item, list):
            # 不存在的引用也保留；此处不把无效输出修成可接纳输出。
            result[key] = sorted([ledger.get(r, {'missing_ref': r}) if isinstance(r, str)
                                  else {'invalid_ref': r} for r in item], key=lambda r:digest(r))
        else:
            result[key] = _normalize(item, ledger)
    return result


def output_diff(before, after):
    """消除引文ID变化，保存实际内容变化；任何差异均不自动判错。"""
    ledgers = [{e['id']: {k:v for k,v in e.items() if k!='id'} for e in value.get('evidence', [])}
               for value in (before, after)]
    left = {(tuple(p['period_ids']), c['item']): c for p in before.get('period_reviews', []) for c in p['cells']}
    right = {(tuple(p['period_ids']), c['item']): c for p in after.get('period_reviews', []) for c in p['cells']}
    changes = []
    for key in sorted(set(left) | set(right)):
        old, new = left.get(key), right.get(key)
        a, b = _normalize(old, ledgers[0]), _normalize(new, ledgers[1])
        if a == b:
            continue
        fields = sorted(set(a or {}) | set(b or {}))
        changes.append(dict(period_ids=list(key[0]), item=key[1],
            before_state=(old or {}).get('state'), after_state=(new or {}).get('state'),
            before_verdict=(old or {}).get('performance', {}).get('verdict'),
            after_verdict=(new or {}).get('performance', {}).get('verdict'),
            changed_fields={f:dict(before=(a or {}).get(f), after=(b or {}).get(f))
                            for f in fields if (a or {}).get(f)!=(b or {}).get(f)}))
    return dict(changed_cells=changes,
        context_before=[_normalize(p.get('context'),ledgers[0]) for p in before.get('period_reviews',[])],
        context_after=[_normalize(p.get('context'),ledgers[1]) for p in after.get('period_reviews',[])],
        facts_before=[_normalize(p.get('fact_coverage'),ledgers[0]) for p in before.get('period_reviews',[])],
        facts_after=[_normalize(p.get('fact_coverage'),ledgers[1]) for p in after.get('period_reviews',[])],
        evidence_before=before.get('evidence',[]), evidence_after=after.get('evidence',[]),
        automatic_semantic_verdict=False)


class ScopedClient(JSONClient):
    def __init__(self, *args, audit_dir, **kwargs):
        super().__init__(*args, **kwargs)
        self.audit_dir = audit_dir

    def call(self, instructions, data, cache, run_id):
        actual = adapt_payload(data)
        require(len(json.dumps(actual,ensure_ascii=False)) <= 1_200_000, 'input_budget_exceeded')
        record = super().call(instructions, actual, cache, run_id)
        correction = actual.get('correction')
        if isinstance(correction, dict) and 'contract_error' in correction:
            note = dict(adapter_version=VERSION, request_sha256=record['request_sha256'],
                actual_input_sha256=digest(actual), instruction=correction['instruction'],
                contract_error=correction['contract_error'],
                previous_output_sha256=digest(correction.get('previous_output')),
                current_output_sha256=digest(record['text']))
            try:
                note['diff'] = output_diff(json.loads(correction['previous_output']), json.loads(record['text']))
            except (ValueError, KeyError, TypeError, AttributeError) as ex:
                # 输出恶形时保留真实响应及诊断失败；原完整校验仍负责拒绝。
                note['diff_unavailable'] = type(ex).__name__
            atomic_json(self.audit_dir/(record['request_sha256']+'.json'),note)
        return record
