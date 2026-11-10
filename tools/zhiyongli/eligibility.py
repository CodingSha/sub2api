"""从已核原生输入提取观察范围；资格不参与023/024算分。"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re

VERSION = 'eligibility-20261106.1'
RULES = '1.3'
TZ = timezone(timedelta(hours=8))


def moment(value):
    return datetime.fromisoformat(re.sub(r'\.(\d+)', lambda m: '.' + m.group(1)[:6].ljust(6, '0'), value))


def week(day):
    value = datetime.fromisoformat(day)
    return (value - timedelta(days=value.weekday())).date().isoformat()


def decide(kind, observations, dimension_refs, complete=True):
    """仅核确定性范围；事件出处不等同于独立统计样本。"""
    dates = {moment(e['occurred_at']).astimezone(TZ).date().isoformat() for e in observations}
    tasks = {k for e in observations for k in e['task_keys']}
    weeks = {week(d) for d in dates}
    by_ref = {e['source_ref']: e for e in observations}
    coverage, reasons = [], []
    days_needed, tasks_needed = (3, 5) if kind == 'week' else (10, 20)
    if not complete:
        reasons.append('来源或期间评测尚未完成')
    if len(dates) < days_needed:
        reasons.append(f'本人记录{len(dates)}天，至少需要{days_needed}天')
    if kind == 'month' and len(weeks) < 3:
        reasons.append('本人记录未覆盖至少3个自然周')
    for d in 'PMJE':
        own = [by_ref[r] for r in set(dimension_refs.get(d, []))]
        ds = {moment(e['occurred_at']).astimezone(TZ).date().isoformat() for e in own}
        ts = {t for e in own for t in e['task_keys']}
        ws = {week(day) for day in ds}
        enough = len(own) >= 2 and (len(ts) >= 2 or len(ds) >= 2) if kind == 'week' else len(ws) >= 2
        coverage.append(dict(code=d, events=len(own), tasks=len(ts), days=len(ds), weeks=len(ws)))
        if not enough:
            reasons.append(d + ('维度尚无两处不同任务或跨日的本人依据' if kind == 'week' else '维度本人依据尚未覆盖2个自然周'))
    route = 'standard'
    if len(tasks) < tasks_needed:
        route = 'insufficient_task_coverage'
        for task in tasks:
            own = [e for e in observations if task in e['task_keys']]
            ds = {moment(e['occurred_at']).astimezone(TZ).date().isoformat() for e in own}
            if len(ds) < days_needed or (kind == 'month' and len({week(day) for day in ds}) < 3):
                continue
            enough = True
            for d in 'PMJE':
                dd = {moment(e['occurred_at']).astimezone(TZ).date().isoformat() for e in own if e['source_ref'] in dimension_refs.get(d, [])}
                enough = enough and (len(dd) >= 2 if kind == 'week' else len({week(day) for day in dd}) >= 2)
            if enough:
                route = 'long_task'
                break
        if route != 'long_task':
            reasons.append(f'任务{len(tasks)}个，至少需要{tasks_needed}个，且未满足持续任务通道')
    return dict(eligible=not reasons, route=route, reasons=reasons, task_count=len(tasks),
                active_days=len(dates), active_weeks=len(weeks), dimension_coverage=coverage)


def build(report, sources, complete=True):
    """sources为(unit, review, row)；只保存出处/日期/身份，不复制原文。"""
    observations, refs = {}, {d: set() for d in 'PMJE'}
    lo = moment(report['period_start'] + 'T00:00:00+08:00')
    hi = moment(report['period_end'] + 'T00:00:00+08:00')
    for unit, review, row in sources:
        events = {e['event']: e for e in unit['events']}
        task_sources = unit.get('source_units')
        if task_sources is None or {t['key'] for t in task_sources} != set(unit['source_keys']):
            raise ValueError('资格缺少与当前任务索引一致的来源集合')
        for task in task_sources:
            for rid in task['person_events']:
                if rid not in unit['person_events']:
                    continue
                e = events[rid]
                if not lo <= moment(e['at']) < hi:
                    continue
                if not any(e['text'][a:b].strip() for a, b in e['human_spans']):
                    raise ValueError('任务本人事件没有实际本人片段')
                value = dict(source_ref=rid, source_hash=hashlib.sha256(e['text'].encode()).hexdigest(),
                             occurred_at=e['at'], task_keys=[])
                current = observations.setdefault(rid, value)
                if any(current[k] != value[k] for k in ('source_hash', 'occurred_at')):
                    raise ValueError('同一来源事件的内容或时间冲突')
                # 局部本人表达继续进入观察和分维依据，但不能冒充已识别任务。
                if task.get('source_kind','task') == 'task' and task['key'] not in current['task_keys']:
                    current['task_keys'].append(task['key'])
        ledger = {e['id']: e for e in review['evidence']}
        for cell in row['cells']:
            if cell['state'] != 'observed' or cell.get('performance', {}).get('verdict') == 'unresolved':
                continue
            for rid in cell['action']['refs']:
                ev = ledger[rid]
                allowed = {'direct_expression', 'explicit_adoption'} | ({'quoted_or_forwarded'} if cell['item'].startswith('P') else set())
                if ev['origin'] in allowed:
                    if ev['event'] not in observations:
                        raise ValueError('分维资格依据未绑定本期本人任务')
                    refs[cell['item'][0]].add(ev['event'])
    values = sorted(observations.values(), key=lambda e: e['source_ref'])
    for e in values:
        e['task_keys'].sort()
    refs = {d: sorted(v) for d, v in refs.items()}
    signed = [dict(source_ref=e['source_ref'], source_hash=e['source_hash'],
                   date=moment(e['occurred_at']).astimezone(TZ).date().isoformat(), task_keys=e['task_keys']) for e in values]
    signature = hashlib.sha256(json.dumps(dict(observations=signed, dimension_refs=refs), sort_keys=True,
                                        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    return dict(version=VERSION, source_digest=signature, sources_complete=complete,
                observations=values, dimension_refs=refs, **decide(report['period_type'], values, refs, complete))


def attach(report, sources, complete=True):
    report['eligibility'] = build(report, sources, complete)
    report['policy_versions']['rules'] = RULES
    return report
