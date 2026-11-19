"""将既有任务过程与显式核查意见绑定；不识别任务、不评分、不补造分析。"""
import copy
import hashlib
import json
import re

VERSION = 'task-evidence-20261106.1'


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def unit_digest(unit):
    return sha(json.dumps(unit, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def task_records(unit, task_key, *, context_index=None):
    from export_results import safe
    from scoring_pipeline.weekly_performance.task_context import task_context
    matches = [s for s in unit['source_units'] if s['key'] == task_key]
    if len(matches) != 1:
        raise ValueError('任务标识未唯一匹配来源')
    task = matches[0]
    isolated = copy.copy(unit)
    members = set(task['person_events'])
    isolated['person_events'] = [e for e in unit['person_events'] if e in members]
    if not isolated['person_events']:
        raise ValueError('案例没有本期本人记录')
    context = task_context(isolated, task_person_events=members, context_index=context_index)
    requests = {(e.get('session_id'), e.get('request_id')) for e in unit['events']
                if e['event'] in members and e.get('session_id') and e.get('request_id')}
    # 既有读取器的未知回复清单覆盖整周，只保留确能关联当前任务的部分。
    unknown = [e for e in unit['events'] if e['event'] in context['withheld_time_unknown']
               and (e.get('session_id'), e.get('request_id')) in requests]
    selected = context['events'] + sorted(unknown, key=lambda e: e['event'])
    limits = ['展示已保存且能关联本任务的记录；不保证包含平台未采集的对话或工具事件。']
    canonical = {e.get('event_id'): e for e in selected if e.get('event_id') and e['actor_class'] != 'source_context'}
    unique = []
    omitted_content = False
    for e in selected:
        original = canonical.get(e.get('event_id'))
        if e['actor_class'] == 'source_context' and original and original['text'] == e['text'] and original['at'] == e['at']:
            limits.append('同一来源事件的上下文副本已合并；不同请求中的同文输入仍逐条保留。')
            continue
        actor = e['actor_class']
        if actor == 'human' and e['event'] not in members:
            # 同请求可以包含多个已识别任务，不能把另一任务的本人动作混入本案例。
            continue
        if actor == 'context':
            # 生产协议将AI和工具统一保存为context，具体身份与正文类型由协议字段确定。
            if e.get('protocol_role') not in ('assistant', 'tool'):
                limits.append('系统提示词、自动注入背景及归属未确认的历史输入未向页面公开。')
                continue
            if e.get('content_kind') not in ('text', 'raw_response') or e.get('protocol_role') == 'tool' and e.get('content_kind') != 'text':
                limits.append('关联记录包含未还原的非文本或协议对象，没有将其冒充对话正文。')
                omitted_content = True
                continue
            unique.append(e)
            continue
        if actor not in ('human', 'assistant', 'tool_call', 'tool_result', 'source_context') or actor == 'source_context' and e.get('original_actor_class') not in ('human', 'assistant', 'tool_call', 'tool_result'):
            limits.append('系统提示词及身份无法确认的关联记录未向页面公开。')
            continue
        unique.append(e)
    selected = unique
    if context['unpaired_person_events']:
        limits.append('部分本人记录缺少请求关联，无法确认相应AI反馈。')
    for gap in task.get('source_gaps', []):
        limits.append(safe(gap if isinstance(gap, str) else gap.get('needed', '任务来源存在缺口。')))
    incomplete = bool(unknown or omitted_content or task.get('source_gaps') or context['unpaired_person_events'])
    records = []
    for e in selected:
        actor = e['actor_class']
        if actor == 'human' and e['event'] in members:
            role = 'user'
            origin = 'direct_expression' if e.get('human_spans') == [[0, len(e['text'])]] else 'mixed_material'
        elif actor == 'assistant' or actor == 'context' and e.get('protocol_role') == 'assistant':
            role, origin = 'assistant', 'assistant_output'
        elif actor in ('tool_call', 'tool_result'):
            role, origin = actor, 'recorded_tool'
        else:
            role, origin = 'context', 'unattributed'
        display_text, record_limits = e['text'], []
        if actor == 'context' and e.get('protocol_role') == 'tool':
            record_limits.append('原协议仅标记为工具上下文，不能区分调用与结果，不据此确认执行成功。')
        if e.get('content_kind') == 'raw_response':
            record_limits.append('此条保留审计保存的原始响应文字，未解析或补造协议内容；不等同于外部执行或验收回执。')
            incomplete = True
        if actor == 'human' and origin != 'direct_expression':
            display_text = '\n'.join(e['text'][a:b] for a, b in e.get('human_spans', []))
            record_limits.append('仅展示已确认的本人输入；自动注入背景与附件正文未向页面公开。')
        if actor == 'source_context':
            record_limits.append('此条为关联背景，不能重复计为本人本任务表现。')
            if e.get('original_actor_class') == 'human':
                display_text = '\n'.join(e['text'][a:b] for a, b in e.get('original_human_spans', []))
        text = safe(display_text)
        if text != display_text:
            record_limits.append('记录中的凭证已脱敏。')
        if '\x00' in text:
            text = text.replace('\x00', '〔NUL控制字符〕')
            record_limits.append('展示正文中的NUL控制字符已显式标记；原始来源及其哈希保留，未改写评审依据。')
        if not text.strip():
            record_limits.append('来源记录未保存可展示的正文，不能据此确认执行情况。')
            incomplete = True
        if e.get('truncated'):
            record_limits.append('原始来源存在截断，缺失部分没有补造。')
            incomplete = True
        if e.get('response_ambiguous') or e.get('source_snapshot_conflict'):
            record_limits.append('来源存在关联歧义或快照冲突，未据此确认任务结果。')
            incomplete = True
        at = None if e.get('timestamp_unknown') or e.get('response_timestamp_unknown') else e['at']
        from eligibility import moment
        if at is not None and moment(at) < moment(unit['periods'][0]['start']):
            record_limits.append('此条为评价周之前的任务背景，不计为本期本人表现。')
        if at is None:
            incomplete = True
            record_limits.append('原始记录时间未知；采集定位时间不作为发送或完成时间，不能据此证明在评价截止前已完成。')
        records.append(dict(id=e['event'], source_hash=sha(e['text']), text_hash=sha(text),
            occurred_at=at, role=role, origin=origin, text=text, limitations=record_limits))
    if not any(r['role'].startswith('tool_') for r in records):
        limits.append('未取得独立工具调用或结果记录；AI自述不等同于工具回执。')
    return task, records, limits, 'source_incomplete' if incomplete else 'available_records'


def attach_task_review(report, unit, authored):
    from export_results import safe
    if authored['source_digest'] != report['source_digest'] or authored['unit_digest'] != unit_digest(unit):
        raise ValueError('核查意见与冻结报告或任务来源不匹配')
    if report['period_type'] != 'week' or report['user_email'] != authored['user_email']:
        raise ValueError('案例核查人与报告身份不一致')
    reviewer = authored['reviewer']
    if reviewer == 'model_generated_separately_reviewed':
        # 摘要只能绑定回执身份；调用方仍须按原回执链验签，不能由两个字符串证明复查通过。
        digests = [authored.get(k, '') for k in ('generation_receipt_digest', 'review_receipt_digest')]
        if any(not isinstance(d, str) or not re.fullmatch(r'[0-9a-f]{64}', d) for d in digests) or digests[0] == digests[1]:
            raise ValueError('模型任务解读须绑定不同的生成与复查回执')
        if authored['status'] != 'case_checked' or authored.get('week_judgments'):
            raise ValueError('代表案例生成不能声明整周核查完成')
        if set(authored['checked_task_keys']) != {c['task_key'] for c in authored['cases']}:
            raise ValueError('代表案例不得扩大已核任务范围')
        if any(not c.get('assessment') for s in authored['cases'] for c in s['claims']):
            raise ValueError('模型任务分析须逐段明确评价作用与理由')
    elif reviewer != 'codex_direct_self_review':
        raise ValueError('任务解读核查来源无效')
    if report.get('task_review') is not None:
        raise ValueError('已有任务解读不得由附加器静默覆盖')
    cases = []
    for spec in authored['cases']:
        task, records, limits, status = task_records(unit, spec['task_key'])
        ids = {r['id'] for r in records}
        for claim in spec['claims']:
            if not claim['record_ids'] or not set(claim['record_ids']) <= ids:
                raise ValueError('判断没有引用本任务的真实记录')
        case = dict(id=sha(task['key']), task_key=task['key'], title=safe(task['title']),
            goal=safe(task.get('purpose') or task['title']), outcome=safe(spec['outcome']),
            context_status=status, record_count=len(records), claims=copy.deepcopy(spec['claims']),
            limitations=list(dict.fromkeys(limits + spec.get('limitations', []))), records=records)
        if len(records) > 1000 or len(json.dumps(case, ensure_ascii=False).encode()) > 2 << 20:
            raise ValueError('完整案例超出上限，应另选有界案例，不得静默截断')
        cases.append(case)
    report['task_review'] = dict(version=VERSION, status=authored['status'],
        task_keys=list(unit['source_keys']), checked_task_keys=authored['checked_task_keys'],
        scope_note=safe(authored['scope_note']), cases=cases,
        week_judgments=copy.deepcopy(authored.get('week_judgments', [])))
    return report


def main():
    import argparse
    from pathlib import Path
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'unit', 'review', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    args = p.parse_args()
    bundle, unit, authored = (json.loads(path.read_text()) for path in (args.bundle, args.unit, args.review))
    matches = [r for r in bundle['reports'] if r['user_email'] == authored['user_email']
        and r['period_type'] == 'week' and r['period_start'] == unit['periods'][0]['start'][:10]]
    if len(matches) != 1:
        raise ValueError('结果包没有唯一对应周报告')
    attach_task_review(matches[0], unit, authored)
    if bundle.get('auto_publish') and authored['status'] != 'week_checked':
        raise ValueError('案例核查未完成整周核查，不自动发布')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as target:
        target.write(json.dumps(bundle, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
