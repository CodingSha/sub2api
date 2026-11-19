"""实验评审的可消费输出合同：补读与终判互斥，错误只作有界局部纠正。"""
import copy
import json
from pathlib import Path

VERSION = 'review-result-20261117.2'
MAX_REPAIRS = 2
# 完整十二项及说明的已测生成时间接近三分钟；仍在正常输出时不能按短调用时限切断。
SCORING_TIMEOUT_SECONDS = 600
INSTRUCTIONS = '''
本次仅输出两个顶层字段：review_output_protocol="review-result-20261117.2"和review。
review只选择一种互斥内容：补读时只请求一种补读，evidence和period_reviews为空，presentation=null；
终判时不得请求任何补读，必须完成本期十二项判断和同次说明（月内片段presentation=null）。
没有员工审核步骤。证据不足是本次终判的分项处置：按固定规则明确不计分及原因，其他可评项照常判断；
不得要求员工、管理员或后续模型替本次完成判断，不得用“待观察、待支持、待审核、待审查”代替终判。
证据不足不等于低分，不得编造本人行为、确认归属或验收事实。
contract_feedback如存在，只纠正指出的合同错误；保持原评分规则和来源，确需补读时输出独立readback。
使用简洁完整的事实和原因，每个解释字段用一至两句表达；共用事实通过证据ID引用，避免在多个字段复述大段原文。
保留决定性支持、反证和归属依据；不输出推演过程。presentation的basis直接解释计分，higher_band只解释邻档差异。
输出紧凑完整JSON，不缩进、不输出连续空白。闭合review和顶层对象后立即结束，不追加内容。
'''


def active(client):
    from source_index import enabled
    from single_pass import active as single
    return enabled(client) and single(client) and getattr(client, 'supports_review_protocol', False) is True


def schema(base):
    from review_schema import obj, enum
    final = copy.deepcopy(base)
    definitions = final.pop('$defs', {})
    props = final['properties']
    props['needs_events'] = dict(type='array', items=dict(type='string'), maxItems=0)
    if 'needs_nodes' in props:
        props['needs_nodes'] = dict(type='array', items=dict(type='string'), maxItems=0)
    props['period_reviews'] = dict(props['period_reviews'], minItems=1, maxItems=1)
    if 'anyOf' in props['presentation']:
        props['presentation'] = next(v for v in props['presentation']['anyOf'] if v.get('type') != 'null')
    variants = [final]
    for name in ('needs_events', 'needs_nodes'):
        original = base['properties'].get(name)
        if original is None or original.get('maxItems') == 0:
            continue
        readback = copy.deepcopy(final)
        for empty in ('evidence', 'period_reviews'):
            readback['properties'][empty] = dict(type='array', items=dict(type='string'), maxItems=0)
        readback['properties']['presentation'] = dict(type='null')
        readback['properties'][name] = dict(copy.deepcopy(original), minItems=1)
        variants.append(readback)
    result = obj(dict(review_output_protocol=enum(VERSION), review=dict(anyOf=variants)))
    if definitions:
        result['$defs'] = definitions
    return result


def decode(value, *, required=False):
    """只解包明确版本；历史回执保持原样，不猜字段、不改事实。"""
    if not isinstance(value, dict):
        raise ValueError('评审输出须为对象')
    if 'review_output_protocol' not in value:
        if required:
            raise ValueError('缺少本次评审输出协议')
        return value
    if (set(value) != {'review_output_protocol', 'review'} or value['review_output_protocol'] != VERSION):
        raise ValueError('评审输出协议版本或字段不符')
    review = value['review']
    if not isinstance(review, dict) or not {'needs_events', 'presentation', 'evidence', 'period_reviews'} <= set(review):
        raise ValueError('评审输出缺少必需字段')
    if any(not isinstance(review.get(k, []), list) for k in ('needs_events', 'needs_nodes', 'evidence', 'period_reviews')):
        raise ValueError('评审数组字段格式错误')
    events, nodes = review['needs_events'], review.get('needs_nodes', [])
    if events or nodes:
        if (bool(events) == bool(nodes) or review['evidence'] or review['period_reviews']
                or review['presentation'] is not None):
            raise ValueError('补读与评分必须互斥，且每轮只请求一种补读')
    else:
        if len(review['period_reviews']) != 1:
            raise ValueError('最终评判须覆盖本期且不得请求补读')
    return copy.deepcopy(review)


def failure(ex):
    """实验路径没有员工审核出口；记录原状态便于追查旧调用点。"""
    reason=ex.reason_code
    budget=reason in {'request_budget_exhausted','global_budget_exhausted','goal_budget_exhausted','request_capacity_exceeded','acceptance_deadline'}
    contract=reason in {'model_contract_invalid','model_request_rejected','source_attribution_unresolved','semantic_dispute'}
    state=ex.state if ex.state in {'queued','unknown','no_activity'} else 'failed'
    phase=('capacity_blocked' if budget else 'model_contract_failed' if contract else 'execution_blocked') if state=='failed' else 'done' if state=='no_activity' else 'paused'
    return dict(state=state,original_state=ex.state,reason_code=reason,phase=phase,
        category='capacity' if budget else 'transport_unknown' if state=='unknown' else 'waiting' if state=='queued' else 'execution_contract',
        employee_review_required=False,automatic_resend_allowed=False if state!='queued' else None)


def call_checked(unit, receipt, client, receive, *, feedback=None, validate_final=None, prepare_readback=None):
    """同一材料局部纠正，独立回执持久化；不重跑归属、不重发unknown、不重置预算。"""
    from bounded_read import checkpoint
    from runtime import digest, response_text
    receipt = Path(receipt)
    local_feedback = feedback
    for attempt in range(MAX_REPAIRS + 1):
        target = receipt if attempt == 0 else receipt.parent/'review-contract-repair'/str(attempt)/receipt.name
        if attempt:
            checkpoint(target.parent/'input.json', dict(protocol=VERSION, unit_sha256=digest(unit),
                feedback_sha256=digest(local_feedback)))
        record = client.call(unit, target) if local_feedback is None else client.call(unit, target, feedback=local_feedback)
        # 先分离传输状态；unknown、拒绝和预算异常不进入格式纠正。
        if record['status'] != 'received':
            receive(record)
            raise ValueError('模型未返回可消费终态')
        try:
            value = decode(json.loads(response_text(record['response'])), required=True)
            from runtime import VERSION as REVIEW_VERSION, REVIEW_POLICY
            fields={'version','review_policy','key','evidence','period_reviews','needs_events','presentation'}
            if unit.get('context_access'):
                fields.add('needs_nodes')
            if (set(value)!=fields or value.get('version')!=REVIEW_VERSION
                    or value.get('review_policy')!=REVIEW_POLICY or value.get('key')!=unit['key']):
                raise ValueError('评审输出未绑定当前版本、对象或字段合同')
            prepared=None
            if value['needs_events'] or value.get('needs_nodes'):
                if prepare_readback is not None:prepared=prepare_readback(value)
            elif validate_final is not None:
                validate_final(unit, value)
            return record, target, prepared
        except (ValueError, KeyError, TypeError) as ex:
            if hasattr(ex,'state'):raise
            error = dict(protocol=VERSION, unit_sha256=digest(unit), response_sha256=digest(record),
                attempt=attempt, error=str(ex)[:2000])
            checkpoint(target.parent/(target.stem+'-contract-error.json'), error)
            if attempt == MAX_REPAIRS:
                from pipeline import Paused
                from navigation_access import ReadbackCapacity
                capacity=isinstance(ex,ReadbackCapacity)
                raise Paused('failed', '模型输出合同在有界纠正后仍失败，原回执保留',
                    reason_code='request_capacity_exceeded' if capacity else 'model_contract_invalid') from None
            local_feedback = dict(contract_feedback=error, prior_revision_feedback=feedback)
    raise AssertionError('评审纠正未到终态')
