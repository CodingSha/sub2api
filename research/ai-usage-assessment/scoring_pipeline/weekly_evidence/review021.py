"""隔离021：替换现有分档合同；不注册、不执行真实评审。"""
import copy
import json
from pathlib import Path
from ..common import require, moment
from ..revised_review import find_proof
VERSION = 'pmje-weekly-evidence-20261105.021-proposal1.scope19'
RUBRIC=json.loads(Path(__file__).with_name('contract-rubric.json').read_text())
ITEMS={k:v['name'] for k,v in RUBRIC['items'].items()}
FACETS=tuple(RUBRIC['challenge_facets'])
STATES={'observed','no_opportunity','related','unobservable','attribution_unclear','not_applicable'}
ORIGINS={'direct_expression','explicit_adoption','quoted_or_forwarded','uncertain','context'}
QUALITY={'sufficient','minor_gap','necessary_gap','major_gap','critical_breach','unresolved'}
PERFORMANCE=RUBRIC['performance_values']
INSTRUCTIONS=Path(__file__).with_name('contract-instructions.txt').read_text()

def check(unit, value):
    """只验证确定性合同；通过不证明模型语义正确。"""
    require(value.get('version') == VERSION and value.get('key') == unit['key'], 'key/版本错误')
    require(unit.get('evaluation_scope') in {'local','person_week'}, '须显式声明局部或整周评价对象')
    if unit['evaluation_scope']=='person_week':
        require(unit.get('source_keys') and len(unit['source_keys'])==len(set(unit['source_keys'])), '整周来源集合缺失/重复')
    require(not value.get('needs_events'), '补读未完成')
    periods = {p['period_id']: p for p in unit['periods']}
    events = {e['event']: e for e in unit['events']}
    ledger = value.get('evidence')
    require(isinstance(ledger, list) and ledger, '缺引文账本')
    ids = [e.get('id') for e in ledger]
    require(all(isinstance(i,str) and i for i in ids) and len(ids)==len(set(ids)), '引文ID缺失/重复')
    refs = {e['id']: e for e in ledger}
    latest = max(unit['periods'], key=lambda p: moment(p['end']))
    for e in ledger:
        require(e.get('origin') in ORIGINS and e.get('origin_reason'), '缺来源判断')
        try:
            find_proof(e, events, latest)
        except ValueError as ex:
            raise ValueError('引文'+e['id']+' '+e.get('event','')+'：'+str(ex)) from None
        if e['origin'] in {'direct_expression', 'explicit_adoption'}:
            require(e['event'] in unit['person_events'], '引文'+e['id']+' '+e['event']+'不在当前person_events，属于其他任务/历史背景；如仅作背景，origin须context，不能认领为本期本人动作')
            # 在账本级核对human_span，具体期间级再核对本期归属。
            try:
                find_proof(e, events, dict(latest,start=events[e['event']]['at']), personal=True)
            except ValueError as ex:
                raise ValueError('账本本人归属/引文'+e['id']+' '+e['event']+'：'+str(ex)) from None

    rows, seen = [], []
    for part in value.get('period_reviews', []):
        for pid in part.get('period_ids', []):
            require(pid in periods and pid not in seen, '期间多余/重复')
            seen.append(pid)
            p = periods[pid]

            def use(items, personal=False, supplied=False, label='任务范围'):
                location = pid+'/'+label
                require(isinstance(items,list), location+'：引文ID须列表')
                require(not personal or items, location+'：本人动作证据为空')
                require(set(items)<=set(refs), location+'：不存在的引文ID '+str([rid for rid in items if rid not in refs]))
                for rid in items:
                    r = refs[rid]
                    try:
                        find_proof(r,events,p,personal=personal or supplied)
                        if personal or supplied:
                            require(r['event'] in unit['person_events'], '本人动作跨任务')
                        if personal:
                            require(r['origin'] in {'direct_expression','explicit_adoption'}, '转贴/未知来源不能认领本人分析')
                    except ValueError as ex:
                        raise ValueError(location+'/引文'+rid+' '+r['event']+'：'+str(ex)) from None
                return items

            ctx = part.get('context',{})
            require(all(ctx.get(k) for k in ('effective_request','necessary_scope','observed_execution','boundary_reason')), '缺当时任务范围')
            require(ctx.get('boundary') in {'consistent','uncertain','needs_correction'}, '缺范围一致性判断')
            require(use(ctx.get('refs',[])), '范围缺来源')
            if unit['evaluation_scope']=='person_week':
                # 整周是已有独立任务的来源集合，不是一个父任务。
                require(ctx.get('scope')=='scored_week_scope', '整周须声明当前已评分来源范围，不能声称单父任务')
                require('challenge' not in ctx, '整周不能输出虚构的统一任务挑战；各任务挑战保留在冻结来源')
            else:
                require(ctx.get('scope') in {'parent','bounded_local','uncertain'}, '缺任务边界')
                ch = ctx.get('challenge',{})
                require(ch.get('level') in {'routine','substantial','complex','unknown'} and ch.get('reason') and ch.get('alternative'), '缺挑战判断/替代解释')
                basis=ch.get('scope_basis',{});routine=ch.get('routine_test',{})
                require(basis.get('kind') in {'explicit_requirement','necessary_implication','execution_revealed','uncertain'} and basis.get('fact'), '缺必要范围来源判断')
                require(use(basis.get('refs',[]),label='必要范围来源'), '必要范围缺来源')
                require(routine.get('sufficient') in {'yes','no','unknown'} and routine.get('reason'), '缺常规路径检验')
                require(use(routine.get('refs',[]),label='常规路径检验'), '常规路径检验缺来源')
                require(routine['sufficient']=={'routine':'yes','substantial':'no','complex':'no','unknown':'unknown'}[ch['level']], '挑战结论与模型自己的常规路径判断不自洽')
                facets = ch.get('facets',[])
                require(len(facets)==5 and {f.get('aspect') for f in facets}==set(FACETS), '五方面须恰一次')
                for f in facets:
                    require(f.get('status') in {'present','not_demonstrated','unknown'} and f.get('fact'), '挑战方面缺判断')
                    use(f.get('refs',[]),label='挑战/'+f['aspect'])
                    require(f['status']!='present' or f.get('refs'), '已证挑战缺证据')
            cells = part.get('cells',[])
            require(len(cells)==12 and {c.get('item') for c in cells}==set(ITEMS), '12项须恰一次')
            by = {c['item']:c for c in cells}
            action_sets = []
            for c in cells:
                item = c['item']
                require(c.get('state') in STATES and c.get('reason'), item+'状态/理由缺失')
                op = c.get('opportunity',{})
                require(op.get('description'), item+'缺机会/无机会说明')
                use(op.get('refs',[]),label=item+'/机会')
                if c['state']=='observed':require(op.get('refs'), item+'已观察机会须有来源')
                if c['state'] in {'unobservable','attribution_unclear','related'}:require(op.get('refs'), item+'具体机会/候选动作须有来源，不能将理论需要视为实际机会')
                if c['state']=='observed':
                    require('behavior' not in c, item+'不得恢复额外B评级')
                    b,q = c.get('action',{}),c.get('quality',{})
                    require(all(b.get(k) for k in ('fact','ai_role','attribution_explanation')) and 'level' not in b, item+'本人动作缺字段/存在额外评级')
                    use(b.get('refs',[]),personal=not item.startswith('P'),supplied=item.startswith('P'),label=item+'/本人动作')
                    require(b.get('refs'), item+'缺本人输入/动作')
                    require(q.get('label') in QUALITY and q.get('reason') and use(q.get('refs',[]),label=item+'/质量'), item+'缺质量事实')
                    if q['label'] not in {'sufficient','unresolved'}:
                        require(all(q.get('responsibility',{}).get(k) for k in ('known_at_time','person_control','feasible_alternative','actual_gap')), item+'缺当时归责')
                    if q['label']=='unresolved':check_missing(q,events,p)
                    perf=c.get('performance',{})
                    verdict=perf.get('verdict')
                    allowed={'sufficient':{'basic_control','selective_control','relational_control','verified_control'},'minor_gap':{'partial'}}.get(q['label'],{q['label']})
                    require(verdict in allowed and perf.get('reason'), item+'表现档与质量判断缺失/冲突')
                    progression=perf.get('progression',{})
                    for stage in ('effective','adapted','controlled','verified'):
                        step=progression.get(stage,{})
                        require(type(step.get('met')) is bool and step.get('fact'), item+'缺逐项递进判断 '+stage)
                        use(step.get('refs',[]),label=item+'/表现/'+stage)
                        if step['met']:
                            # 输入有效可引用本人提供材料，选择及核对须有可归属的本人动作。
                            require(step.get('refs'), item+'表现递进缺证据 '+stage)
                            own=[rid for rid in step['refs'] if refs[rid]['event'] in unit['person_events'] and refs[rid]['origin'] in ({'direct_expression','explicit_adoption','quoted_or_forwarded'} if stage=='effective' and item.startswith('P') else {'direct_expression','explicit_adoption'})]
                            require(own, item+'表现递进缺本人动作 '+stage)
                            use(own,personal=not(stage=='effective' and item.startswith('P')),supplied=stage=='effective' and item.startswith('P'),label=item+'/表现/'+stage+'/本人')
                    eff,adapt,controlled,verified=(progression[s] for s in ('effective','adapted','controlled','verified'))
                    require(not adapt['met'] or eff['met'], item+'针对性选择不能跳过有效行为')
                    require(not controlled['met'] or adapt['met'], item+'关系控制不能跳过有据选择')
                    require(not verified['met'] or controlled['met'], item+'作用核对不能跳过关系控制')
                    if controlled['met']:
                        require(all(controlled.get(k) for k in ('actual_condition','choice','relation_effect')), item+'关系控制须具体关系及作用')
                        require(controlled['fact'].strip() not in {eff['fact'].strip(),adapt['fact'].strip()}, item+'关系控制不能复制前档事实')
                    if adapt['met']:
                        require(all(adapt.get(k) for k in ('actual_condition','choice','difference_from_basic')), item+'针对性处理须具体条件及选择')
                        require(adapt['fact'].strip()!=eff['fact'].strip(), item+'选择不能复制普通有效事实')
                    if verified['met']:
                        require(all(verified.get(k) for k in ('check_object','actual_check','observed_result','supports_choice')), item+'核对须同一对象动作结果关系')
                        require(verified['fact'].strip() not in {eff['fact'].strip(),adapt['fact'].strip()}, item+'核对不能复制前档事实')
                    require(perf.get('scope') in {'local','scored_week_scope'}, item+'缺明确当前已评分范围')
                    if unit.get('evaluation_scope')=='person_week':
                        require(perf['scope']=='scored_week_scope', item+'须声明仅代表本周已评分范围')
                    if q['label']=='sufficient':
                        expected='verified_control' if verified['met'] else 'relational_control' if controlled['met'] else 'selective_control' if adapt['met'] else 'basic_control'
                        require(eff['met'] and verdict==expected, item+'分档与固定锚点不自洽')
                    signature = {(refs[r]['event'],refs[r]['quote']) for r in b['refs']}
                    for other,old in action_sets:
                        require(b['fact'].strip()!=other['action']['fact'].strip(), '同一事实重复主归')
                        if signature & old:require(c.get('difference') and other.get('difference'), pid+'/'+item+'/'+other['item']+'共引须双方说明独立事实')
                    action_sets.append((c,signature))
                else:
                    require(not {'behavior','action','quality','performance'} & set(c), item+'无分状态不得补档/质量')
                    if c['state']=='related':
                        target=by.get(c.get('primary_item'),{})
                        require(target.get('state')=='observed' and c.get('primary_item')!=item, item+'关联须终止于当期实际观察项')
                    if c['state'] in {'unobservable','attribution_unclear'}:check_missing(c,events,p)
                if item.startswith('M'):
                    h=c.get('challenge_handling',{})
                    require(h.get('status') in {'confirmed','not_demonstrated','unresolved'} and h.get('reason'), item+'缺挑战处理状态')
                    use(h.get('refs',[]),label=item+'/挑战处理')
                    if c['state'] in {'no_opportunity','related','not_applicable'}:
                        require(h['status']=='not_demonstrated', item+'本项未证明独立挑战处理，不能将理论需要填成unresolved')
                    if h['status']=='unresolved':require(h.get('refs'), item+'挑战处理待定须有具体候选节点来源')
                    if h['status']=='confirmed':
                        require(c['state']=='observed' and h.get('issue'), item+'局部条件处理缺本人机会/行为')
                        use(h.get('refs',[]),personal=True,label=item+'/挑战处理本人动作')
            rows.append(dict(key=unit['key'],user_id=unit['user_id'],title=unit['title'],period_id=pid,
                             evaluation_scope=unit['evaluation_scope'],source_keys=unit.get('source_keys',[]),
                             context=copy.deepcopy(ctx),cells=copy.deepcopy(cells)))
    require(set(seen)==set(periods), '期间未全覆盖')
    return rows


def check_missing(obj,events,period):
    m=obj.get('missing',{})
    require(m.get('needed') and m.get('why_changes_judgment'), '未知缺决定性原因')
    checked=m.get('checked_events')
    require(isinstance(checked,list) and checked and set(checked)<=set(events), '未知须已检查相关事件')
    require(all(moment(events[e]['at'])<moment(period['end']) for e in checked), '缺失检查引用未来')
