"""v6简化评审合同：事实与覆盖分开，档位由固定标签映射，不补未知分。"""
from collections import Counter
from fractions import Fraction

from .common import moment, require, display
from .policy import ITEMS, DIMENSIONS, LEVELS

VERSION = 'pmje-trial-20261031.6'
BANDS = {'critical_breach': 0, 'major_gap': 40, 'necessary_gap': 60,
         'adequate': 80, 'reasoned': 90, 'complete': 100}
UNKNOWN = {'missing_source', 'attribution_conflict', 'evidence_conflict', 'pipeline_pending'}
STATES = {'scored', 'no_opportunity', 'not_applicable', 'related_only', 'unknown'}

RUBRIC = {
    'version': VERSION, 'items': ITEMS,
    'bands': {
        'critical_breach': '0：本人有据的主动关键越界，知情且可控、有实际影响。',
        'major_gap': '40：关键必要控制严重缺失或错误，属于本人可处理范围且未妥善处理。',
        'necessary_gap': '60：有正面处理，但存在具体、可避免、可归责的必要缺口；不要求严重事故或本人承认。',
        'adequate': '80：有正面事实支持当前实际机会处理充分；不是没有发现错误就默认80。',
        'reasoned': '90：在80基础上有具体依据的选择、取舍、安排或调整；理由可体现于行动与上下文，不要求本人写论证。',
        'complete': '100：在90基础上，评价对象实际适用的要求与边界完整满足；简单任务也可达到，不强求未来采用、业务成功、多个任务或五张形式清单。',
    },
    'scope': [
        '对已证的具体本人行为评分，未知范围另列。局部有据不因其他后续未见而整项作废；已证必要缺口不得被正例平均掩盖。早期问题已妥善纠正时按完整轨迹评价，保留当时影响与修复事实，不因曾有问题永久封顶，也不因后来成功抹去未处理的缺口。',
        '每项说明实际机会、本人事实、相邻档位理由；不能因可能还有别的解释就未知。未知须指明缺什么及为何足以改变判断。',
        'J3根据已有反馈作出接受/拒绝/纠正，反馈可早于决定；整改落实或最终通过是另一主张，不得混同。',
        'M3看已有反馈后实际更新范围、计划、责任与下一步；本人已明确改变执行安排即可确认该安排，若声称执行产物已变须有相应结果。',
        'J2安排检查可评价其安排及跟进行为，不把安排自动称已验证；执行缺口有据则扣，无材料不硬认成功。',
        'E2需真实失效场景及已作止损/切换；明确停止决定可以证明该决定，不自动证明损失已止住。E3需实际复用，不把打算建Skill算完成复用。',
        'Skill/多Agent协作是方法与复用的证据，不是额外维度；工具自主动作不自动归本人，不按调用数评分。普通命令只作为按需执行上下文。',
        '同一独立行为只主归一项；同句确有多个独立事实须写明差别，不能按不同措辞重复奖励。',
        '对正常追问、探索、AI失误、平台重试均结合当时条件判，不因轮数直接判高低；具体可避免缺口须指出可行替代及实际影响。',
        '负面归责可依据当时日志作合理推断并写明依据，不要求本人承认；不能用未来才知道的信息要求事前预知。',
        '父目标边界有疑点时，先定位可确认的局部行为/目标；P与局部判断不自动被全局边界阻断，跨目标推进与效率结论须有明确关系。',
        '每周只用当期本人行为及周末前可见结果。月度独立重评，不能把后来成功倒填前周。',
        '没有机会/不适用须给目标性质与事实依据；不是没有看到行为就认定没有机会。',
    ],
    'complexity': '仅M项：C2写清两个条件、相互作用、常规路径为何不足、本人如何处理；confirmed权重3/2，none为1，pending保留基础表现和敏感区间。未知加权资格不删除基础分。',
}


def find_proof(ref, events, period, *, personal=False):
    require(isinstance(ref, dict) and ref.get('event') in events and ref.get('quote', '').strip(), '证据缺事件或引文')
    event = events[ref['event']]
    quote = ref['quote']
    positions = []
    start = event['text'].find(quote)
    while start >= 0:
        positions.append(start)
        start = event['text'].find(quote, start + 1)
    require(positions, '引文不逐字存在：' + ref['event'])
    require(moment(event['at']) < moment(period['end']), '引用未来证据')
    if personal:
        require(event.get('actor_class') == 'human' or (
            event.get('actor_class') == 'tool_with_human_answer' and
            event.get('answer_source', {}).get('status') == 'confirmed_ui_answers'),
            '非本人来源不能主归本人')
        positions = [a for a in positions if any(x <= a and a + len(quote) <= y for x, y in event.get('human_spans', []))]
        require(positions, '引文超出已确认本人范围')
        require(moment(period['start']) <= moment(event['at']), '本人主张不属于本期')
    return dict(ref, event_id=event['event_id'], start=positions[0], end=positions[0] + len(quote),
                at=event['at'], actor_class=event.get('actor_class'),
                source_limited=any(event.get(k) for k in ('truncated', 'response_ambiguous', 'response_timestamp_unknown', 'source_snapshot_conflict')))


def validate_period(data, period, out):
    require(out.get('period_id') == period['period_id'], '期间不匹配')
    require(len(out.get('cells', [])) == 12 and {c.get('item') for c in out['cells']} == set(ITEMS), '须处置全部12项')
    events = {e['event']: e for e in data['events']}
    facts, results = {}, []
    for original in out['cells']:
        cell = dict(original)
        item, status = cell['item'], cell.get('status')
        require(status in STATES and cell.get('reason', '').strip(), '状态或理由缺失')
        refs = [find_proof(p, events, period) for p in cell.get('evidence', [])]
        cell['evidence'] = refs
        require(cell.get('opportunity', '').strip(), '缺实际机会或不适用依据')
        if status == 'scored':
            verdict = cell.get('verdict')
            require(verdict in BANDS, '档位标签错误')
            missing_facts=[key for key in ('fact','adjacent_reason') if not cell.get(key,'').strip()]
            require(not missing_facts, '缺本人事实或相邻档位比较；须显式填写字段：'+','.join(missing_facts)+'；reason/anchor_basis/limits中的解释不能代替所缺字段，不得为过校验改变判分。')
            persons = [find_proof(p, events, period, personal=True) for p in cell.get('person_evidence', [])]
            require(persons, '可评项缺本期本人证据')
            cell['person_evidence'] = persons
            signature = cell['fact'].strip()
            require(signature not in facts, '同一事实不能跨项重复奖励')
            facts[signature] = item
            if BANDS[verdict] <= 60:
                data_resp = cell.get('responsibility', {})
                for key in ('known_at_time', 'person_control', 'feasible_alternative', 'actual_gap'):
                    require(isinstance(data_resp.get(key), str) and data_resp[key].strip(),
                            item+'低档缺显式字段responsibility.'+key+'；本cell须填写responsibility对象及known_at_time/person_control/feasible_alternative/actual_gap四个非空字符串。reason里的解释不能代替字段；不得为绕过校验改变档位或归责事实。')
                require(refs, '低档缺反例证据')
            limits = cell.get('limits', [])
            require(isinstance(limits, list), '范围限制须为列表')
            cell['band'] = BANDS[verdict]
        else:
            require('verdict' not in cell and 'band' not in cell, '非评分项不能补分')
            cell['band'] = None
            if status == 'unknown':
                missing = cell.get('missing', {})
                require(missing.get('type') in UNKNOWN and missing.get('needed') and missing.get('why_changes_judgment'),
                        item+'未知须显式填写missing对象：type为missing_source/attribution_conflict/evidence_conflict/pipeline_pending之一，needed和why_changes_judgment为非空字符串。reason里的解释不能代替字段；不得为绕过校验改变证据事实或状态。')
            if status == 'related_only':
                require(cell.get('primary_item') in ITEMS and cell['primary_item'] != item,
                        item+'关联项须指明当前家族同期间另一子项primary_item；当前值='+str(cell.get('primary_item'))+
                        '。不能自引、用primary_goal/primary_family假定其他家族已评分，或凭同名业务排除本人事实。独立局部目标须按固定parts规则处理，不得为过校验改变事实归属。')
        control = cell.get('complex', {'status': 'none'})
        require(control.get('status') in {'none', 'confirmed', 'pending'}, '复杂机会状态错误')
        if not item.startswith('M'):
            require(control['status'] == 'none', '复杂权重只进入M')
        if control['status'] == 'confirmed':
            missing_complex=[k for k in ('condition_a', 'condition_b', 'interaction', 'routine_insufficient', 'person_handling') if not control.get(k)]
            require(not missing_complex, item+'复杂条件或本人处理缺字段：'+','.join(missing_complex)+
                    '；须按实际原文填写complex.condition_a/condition_b/interaction/routine_insufficient/person_handling，不能为过校验改变复杂资格。')
            require(status == 'scored' and refs, '复杂加权须本项有据表现')
        cell['complex'] = control
        cell['weight_exact'] = '3/2' if control['status'] == 'confirmed' else '1'
        cell['weight_pending'] = control['status'] == 'pending'
        results.append(cell)
    shared_without_difference=[]
    for i, a in enumerate(results):
        if a['status'] != 'scored':
            continue
        for b in results[i+1:]:
            if b['status'] != 'scored':
                continue
            sa = {(p['event_id'], p['start'], p['end']) for p in a['person_evidence']}
            sb = {(p['event_id'], p['start'], p['end']) for p in b['person_evidence']}
            if sa & sb:
                if not (a.get('independent_fact_difference') and b.get('independent_fact_difference')):
                    shared_without_difference.append(a['item']+'/'+b['item'])
    require(not shared_without_difference, '同引文跨项须说明独立事实区别：'+period['period_id']+'/'+','.join(shared_without_difference)+'；涉及的双方cell都必须显式填写independent_fact_difference字段，不能只写在reason或limits中')
    require(out.get('boundary', {}).get('status') in {'confirmed', 'local_scope', 'unresolved'}, '须核查评价单元边界')
    if out['boundary']['status'] != 'confirmed':
        require(out['boundary'].get('reason'), '边界疑点缺说明')
        missing_local=[cell['item'] for cell in results if cell['status']=='scored' and not cell.get('local_scope')]
        require(not missing_local, '边界待定只能评价明确局部对象：'+period['period_id']+'/'+','.join(missing_local)+'；列出的每个scored cell都必须显式填写local_scope字段。opportunity/reason/limits中的局部范围说明不能代替这个字段')
    return dict(out, user_id=data['user_id'], goal_id=data['goal_id'], title=data['title'],
                policy_version=VERSION, cells=results)


def summarize_period(results):
    """保留基础表现；复杂权重待定只影响加权范围，不吞掉整项。"""
    items = {}
    for item in ITEMS:
        cells = [c for r in results for c in r['cells'] if c['item'] == item]
        scored = [c for c in cells if c['status'] == 'scored']
        numerator = sum((Fraction(c['weight_exact']) * c['band'] for c in scored), Fraction())
        denominator = sum((Fraction(c['weight_exact']) for c in scored), Fraction())
        value = numerator / denominator if denominator else None
        baseline = sum((Fraction(c['band']) for c in scored), Fraction()) / len(scored) if scored else None
        pending = [c for c in scored if c['weight_pending']]
        # 未决复杂权重采用基础1仅作显示基线，另计算1至1.5的全部极值端点。
        endpoints = [value] if value is not None else []
        for seq in (sorted(pending, key=lambda c: c['band']), sorted(pending, key=lambda c: c['band'], reverse=True)):
            n, d = numerator, denominator
            for cell in seq:
                n += Fraction(cell['band'], 2)
                d += Fraction(1, 2)
                endpoints.append(n/d)
        items[item] = {'exact': str(value) if value is not None else None, 'score': display(value),
                       'base_exact': str(baseline) if baseline is not None else None, 'base_score': display(baseline),
                       'scored': len(scored), 'unknown': sum(c['status'] == 'unknown' for c in cells),
                       'status_counts': dict(Counter(c['status'] for c in cells)), 'weight_pending': len(pending),
                       'lower_exact': str(min(endpoints)) if endpoints else None,
                       'upper_exact': str(max(endpoints)) if endpoints else None}
    dims = {}
    for dim, spec in DIMENSIONS.items():
        eligible = [items[i] for i in spec['items'] if items[i]['exact'] is not None]
        value = sum((Fraction(i['exact']) for i in eligible), Fraction()) / len(eligible) if eligible else None
        dims[dim] = {'exact': str(value) if value is not None else None, 'score': display(value), 'covered_items': len(eligible),
                     'lower_exact': str(sum(Fraction(i['lower_exact']) for i in eligible)/len(eligible)) if eligible else None,
                     'upper_exact': str(sum(Fraction(i['upper_exact']) for i in eligible)/len(eligible)) if eligible else None}
    value = sum(Fraction(d['exact']) for d in dims.values()) / 4 if all(d['exact'] is not None for d in dims.values()) else None
    return {'items': items, 'dimensions': dims, 'exact': str(value) if value is not None else None,
            'score': display(value), 'weight_pending': sum(i['weight_pending'] for i in items.values()),
            'lower_exact': str(sum(Fraction(d['lower_exact']) for d in dims.values())/4) if value is not None else None,
            'upper_exact': str(sum(Fraction(d['upper_exact']) for d in dims.values())/4) if value is not None else None}


def level(value):
    return next({'id': code, 'name': name} for bound, code, name in LEVELS if value >= bound)
