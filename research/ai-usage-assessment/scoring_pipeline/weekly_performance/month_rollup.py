"""024月汇总：显式引文的023期间判断形成月分，不再次语义定月档。"""
import copy
import json
from datetime import timedelta
from fractions import Fraction
from zoneinfo import ZoneInfo

from ..common import moment, require
from .review022 import VERSION as SCALE_VERSION, ITEMS, PERFORMANCE, STATES, check
from .scores022 import NECESSARY, POLICY, WEIGHTS, level_for, mean, packed
from .scope022 import check_window

VERSION = 'pmje-month-rollup-20261105.024'
TZ = ZoneInfo('Asia/Shanghai')


def month_windows(start, end):
    """以自然周与月份的交集声明实际评价时段。"""
    period = dict(start=start, end=end, period_id='month-'+moment(start).astimezone(TZ).date().isoformat())
    a, b = check_window('person_month', period)
    cursor, result = a-timedelta(days=a.weekday()), []
    while cursor < b:
        result.append(dict(calendar_id='week-'+cursor.date().isoformat(),
                           start=max(a, cursor), end=min(b, cursor+timedelta(days=7))))
        cursor += timedelta(days=7)
    return result


def bound_period(unit, review):
    """仅接收逐项显式引用的原生评审；绝不从首句或档位自动生成事实字段。"""
    scope = unit.get('evaluation_scope')
    require(scope in {'person_week', 'person_week_fragment'}, '月来源须为周或明确月内片段')
    require(unit.get('version') == SCALE_VERSION and review.get('version') == SCALE_VERSION,
            '期间评分尺不一致')
    require(isinstance(unit.get('source_gaps'), list), '期间须显式记录来源缺口')
    require(len(unit.get('periods', [])) == 1, '一次只接收一个实际评价时段')
    original = unit['periods'][0]
    start, end = moment(original['start']).astimezone(TZ), moment(original['end']).astimezone(TZ)
    require(start < end, '评价时段为空')
    # 片段仍沿用023十二项检查；仅在内存投影为自然周容器，返回时保留真实片段。
    u, v = copy.deepcopy(unit), copy.deepcopy(review)
    if scope == 'person_week_fragment':
        require(start.time().isoformat() == end.time().isoformat() == '00:00:00', '片段须按北京时间零点切分')
        monday = start-timedelta(days=start.weekday())
        require(end <= monday+timedelta(days=7) and end-start < timedelta(days=7), '片段须在同一自然周内')
        require(len(v.get('period_reviews', [])) == 1, '片段须有一次明确判断')
        part = v['period_reviews'][0]
        require(part.get('period_ids') == [original['period_id']], '片段评审期间不匹配')
        require(part.get('context', {}).get('scope') == 'scored_fragment_scope', '不能将片段冒称整周')
        events = {e['event']: e for e in u['events']}
        for ref in v.get('evidence', []):
            require(ref.get('event') in events, '片段引文事件不存在')
            if ref.get('origin') in {'direct_expression', 'explicit_adoption'}:
                require(start <= moment(events[ref['event']]['at']) < end, '本人引文超出实际片段')
            require(moment(events[ref['event']]['at']) < end, '片段引用未来证据')
        calendar_id = 'week-'+monday.date().isoformat()
        u['evaluation_scope'] = 'person_week'
        u['periods'] = [dict(period_id=calendar_id, start=monday.isoformat(), end=(monday+timedelta(days=7)).isoformat())]
        part['period_ids'] = [calendar_id]
        part['context']['scope'] = 'scored_week_scope'
        for cell in part.get('cells', []):
            if 'performance' in cell:
                require(cell['performance'].get('scope') == 'scored_fragment_scope', '片段子项范围错误')
                cell['performance']['scope'] = 'scored_week_scope'
    rows = check(u, v)
    require(len(rows) == 1, '一个期间只能产生一次十二项判断')
    row = rows[0]
    require(set(row['source_keys']) == set(unit['source_keys']), '装配改变了来源集合')
    row.update(period_id=original['period_id'], evaluation_scope=scope, window=copy.deepcopy(original),
               scale_version=SCALE_VERSION, evidence_ids=[e['id'] for e in review['evidence']])
    return row


def summarize_month(periods, *, start, end, gap_dispositions=()):
    """输入为已核期间行；状态、覆盖、缺口处置独立于数值汇总。"""
    windows = month_windows(start, end)
    require(periods and len({p['user_id'] for p in periods}) == 1, '月份来源为空或跨人')
    require(len({p['period_id'] for p in periods}) == len(periods), '重复评分期间')
    slots = {(w['start'], w['end']): w for w in windows}
    seen, gaps, numeric, necessary, by_period = set(), [], {i: [] for i in ITEMS}, {}, {}
    coverage = []
    for row in periods:
        require(row.get('scale_version') == SCALE_VERSION, '不能混用期间评分尺')
        a, b = moment(row['window']['start']).astimezone(TZ), moment(row['window']['end']).astimezone(TZ)
        require((a, b) in slots and (a, b) not in seen, '期间重复、重叠或不等于月内自然周片段')
        seen.add((a, b))
        require(len(row['cells']) == 12 and {c['item'] for c in row['cells']} == set(ITEMS), '期间十二项不完整')
        require(isinstance(row.get('source_gaps'), list) and isinstance(row.get('source_keys'), list), '来源集合与缺口须显式声明')
        require(len(row['source_keys']) == len(set(row['source_keys'])), '来源任务重复')
        if not row['source_keys']:
            audit = row.get('empty_source_check', {})
            require(audit.get('expected_person_events') == 0 and audit.get('exact_match') is True
                    and audit.get('audit_sha256') and audit.get('audit_path'), '空期间须核冻结来源，不能以空索引代替')
            require(all(c['state'] == 'no_opportunity' for c in row['cells']), '空期间不得补评分或未知')
        days = (b-a).days
        gaps.extend(row['source_gaps'])
        if not row.get('evidence_checked'):
            gaps.append(row['period_id']+'：引文语义对应尚未核完')
        by_period[row['period_id']] = row
        coverage.append(dict(period_id=row['period_id'], days=days,
                             evidence_checked=bool(row.get('evidence_checked')), source_keys=row['source_keys']))
        for cell in row['cells']:
            key = cell['item']
            require(cell['state'] in STATES, '未知状态不能静默排除')
            if cell['state'] != 'observed':
                if cell['state'] in {'unobservable', 'attribution_unclear', 'pending'}:
                    gaps.append(row['period_id']+'/'+key+'：'+cell['state'])
                continue
            verdict = cell['performance']['verdict']
            if verdict == 'unresolved':
                gaps.append(row['period_id']+'/'+key+'：判据未决')
                continue
            require(verdict in PERFORMANCE, '未知表现标签不能补数')
            value = Fraction(PERFORMANCE[verdict])
            numeric[key].append(dict(period_id=row['period_id'], days=days, exact=str(value)))
            if cell['quality']['label'] in NECESSARY:
                necessary[(row['period_id'], key)] = value
    for slot in set(slots)-seen:
        gaps.append(slots[slot]['calendar_id']+'：评价来源未完成')
    dispositions = {(d['period_id'], d['item']): d for d in gap_dispositions}
    require(len(dispositions) == len(gap_dispositions), '缺口处置重复')
    require(set(dispositions) <= set(necessary), '缺口处置引用了不存在的必要缺口')
    open_gaps = {d: [] for d in 'PMJE'}
    for key, value in necessary.items():
        decision = dispositions.get(key)
        if decision is None:
            gaps.append('/'.join(key)+'：必要缺口月末处置未核')
            continue
        require(decision.get('status') in {'open', 'recovered'} and decision.get('reason'), '缺口处置缺状态/理由')
        if decision['status'] == 'open':
            open_gaps[key[1][0]].append(value)
        else:
            recovered = by_period.get(decision.get('recovery_period'))
            require(recovered and moment(recovered['window']['start']) >= moment(by_period[key[0]]['window']['end']),
                    '恢复须引用月内后续期间，不能回填过去')
            require(recovered.get('evidence_checked') and decision.get('recovery_refs'), '恢复须有已核引文')
            require(set(decision['recovery_refs']) <= set(recovered.get('evidence_ids', [])), '恢复引文不存在')
    items = {}
    for key, components in numeric.items():
        days = sum(c['days'] for c in components)
        value = sum((c['days']*Fraction(c['exact']) for c in components), Fraction())/days if days else None
        items[key] = dict(**packed(value), assessed_days=days, components=components,
                          states={p['period_id']: next(c['state'] for c in p['cells'] if c['item'] == key) for p in periods})
    dimensions = {}
    for d in 'PMJE':
        values = [Fraction(v['exact']) for k, v in items.items() if k.startswith(d) and v['exact'] is not None]
        value = min(open_gaps[d]) if open_gaps[d] else mean(values)
        dimensions[d] = dict(**packed(value), item_count=len(values),
                             rule='月末仍开放必要缺口优先' if open_gaps[d] else '期间汇总后可评子项等权')
    total = (sum((WEIGHTS[d]*Fraction(dimensions[d]['exact']) for d in 'PMJE'), Fraction())
             if all(v['exact'] is not None for v in dimensions.values()) else None)
    # 未核缺口保留算术暂存，不生成发布评级。
    publishable = total is not None and not gaps
    level = level_for(total) if publishable else None
    rounded = packed(total)['score']
    crossed = publishable and level_for(Fraction(rounded)) != level
    return dict(version=VERSION, scale_version=SCALE_VERSION, user_id=periods[0]['user_id'],
                window=dict(start=start, end=end), **packed(total), items=items, dimensions=dimensions,
                weights={d: str(v) for d, v in WEIGHTS.items()}, coverage=coverage,
                source_gaps=list({json.dumps(g, ensure_ascii=False, sort_keys=True): g for g in gaps}.values()),
                publishable=publishable,
                rating=dict(policy=POLICY['version'], level=level, exact=str(total) if total is not None else None,
                            status='本地月度评级候选' if publishable else '暂存计算，待核/待评价',
                            rounding_crosses_boundary=crossed,
                            score_display=(rounded+'（未舍入为'+str(total)+'，仍按'+level+'）') if crossed else rounded),
                method='先按月内天数汇总各期间子项，再按原维内规则与PMJE权重汇总；不重新判月档',
                validation_scope='同一Codex会话核证据，程序核字面、窗口、集合及算术；非独立复核、人评或生产验收')
