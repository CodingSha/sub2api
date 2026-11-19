"""归并前沿原请求/修复链只读核查；必要下界不等于最终成本或发送许可。"""
import json
from pathlib import Path

from runtime import atomic, digest, MAX_REQUEST_BYTES
from task_identification import CONTRACT_RETRIES, contract_value, correction


def pending(client, data, schema, receipt, receive, check, entries):
    """返回首次可证明待发或合法传输续发未知量，不改回执、反馈及发送账。"""
    from pipeline import Paused
    from request_retry import read_receipt, retry_kind, limits, VERSIONS
    receipt = Path(receipt); feedback = None
    paths = [receipt] + [receipt.with_name(receipt.name.replace('.receipt.json',
        '.fix'+str(i)+'.receipt.json')) for i in range(1, CONTRACT_RETRIES+1)]
    for attempt, path in enumerate(paths):
        payload = client.merge_payload(data, schema, feedback)
        request_hash = digest(dict(endpoint=client.config.responses_url, payload=payload))
        if not path.exists():
            if str(path.resolve()) in entries:
                raise Paused('unknown', '目标归并已有发送意图但回执缺失，保留账簿待核，禁止重发')
            prefix = str(path.resolve()).removesuffix('.receipt.json')
            if (any(later.exists() for later in paths[attempt+1:])
                    or any(str(later.resolve()) in entries for later in paths[attempt+1:])
                    or any(path.parent.glob(path.name.removesuffix('.receipt.json')+'.attempt-*.receipt.json'))
                    or any(name.startswith(prefix+'.attempt-') for name in entries)):
                raise ValueError('目标归并原回执缺失但后续尝试仍存在，禁止从中间重新发送')
            if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_REQUEST_BYTES:
                raise ValueError('目标归并完整请求超过已验证容量，保留全部材料，不发送部分比较')
            return 1, 0
        raw = json.loads(path.read_text()); record = read_receipt(path)
        if raw.get('request_sha256') != request_hash or record.get('request_sha256') != request_hash:
            raise ValueError('API请求签名漂移')
        if record.get('status') == 'requesting' or record.get('error') == 'missing_retry_receipt':
            raise Paused('unknown', '目标归并已有结果不明发送，保留原回执待核，禁止重发')
        # 真实续发资格、冷却、pending落盘及次数仍由原request_retry状态机核验。
        # 这里只保守计0下界并显式列未知，不能把合法续发链当热缓存或替它授权。
        kind = retry_kind(record)
        if (raw.get('status') == 'retry_wait' and raw.get('transport_version') in VERSIONS
                or raw.get('transport_version') not in VERSIONS and kind is not None and limits(client).get(kind, 0)):
            return 0, 1
        try:
            contract_value(record, receive, check)
            return 0, 0
        except (ValueError, KeyError, TypeError) as error:
            if (hasattr(error, 'state') or attempt >= CONTRACT_RETRIES
                    or not getattr(client, 'accepts_revision_feedback', False)):
                raise
            feedback = correction(attempt, error)


def preflight(client, base, groups, root, receive):
    """扫描整个已冻结比较集合再发第一条；不以本轮轮转额度限制整个阶段。"""
    from goal_reconciliation import comparison_schema, checked
    from pipeline import Paused
    root = Path(root)
    with client.budget.lock() as saved:
        entries = dict(saved)
    lower = deferred = cached = 0
    for left in range(len(groups)):
        for right in range(left, len(groups)):
            data = dict(base, local_goals=groups[left]+(groups[right] if left != right else []))
            count, unknown = pending(client, data, comparison_schema(data), root/(digest(data)+'.receipt.json'),
                receive, lambda value, data=data: checked(data, value), entries)
            lower += count; deferred += unknown; cached += not (count or unknown)
    global_budget = client.flow.remaining_requests() if client.flow is not None else None
    remaining = max(0, client.limit-len(entries))
    available = min(remaining, global_budget['remaining']) if global_budget else remaining
    result = dict(comparisons=len(groups)*(len(groups)+1)//2, known_new_request_lower_bound=lower,
        validated_comparisons=cached, deferred_transport_comparisons=deferred,
        personal_used=len(entries), personal_remaining=remaining, global_budget=global_budget,
        available_requests=available, whole_pipeline_feasibility=False,
        state='insufficient_known_goal_budget' if lower > available else 'goal_observation_saved',
        unestimated_stages=['future_contract_repairs', 'transport_retries', 'scoring', 'semantic_audit',
            'explanation_generation_and_audit', 'human_review_and_publish'],
        limitation='只读时点必要下界，不预留额度；合法续发成本未知而非已完成。'
            '未来坏输出、其他用户消耗和后续阶段未计零，观测通过不证明归并或全流程可完成。')
    identity = dict(version=1, input_sha256=digest(dict(base, local_goals=[g for group in groups for g in group])),
        request_budget=str(client.budget.path.resolve()), endpoint=client.config.responses_url, model=client.config.model)
    path = root/'capacity-observation.json'
    if path.exists():
        prior = json.loads(path.read_text())
        if prior.get('identity') != identity or prior.get('result_sha256') != digest(prior.get('result')):
            raise ValueError('目标归并容量观测来源或签名变化，保留原记录待核')
    proof = dict(identity=identity, result=result, result_sha256=digest(result))
    if not path.exists() or prior != proof:
        atomic(path, proof)
    if lower > available:
        raise Paused('needs_review', '目标归并至少仍需'+str(lower)+'次新请求，当前个人/全局可用额度仅'
            +str(available)+'次；已在新增比较前暂停，全部表达及既有回执保留，后续评分复查另需预算',reason_code='goal_budget_exhausted')
    return result
