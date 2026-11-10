"""v7行为锚点与家族内局部对象汇总；不按预期人数调整分布。"""
import copy
from collections import Counter, defaultdict
from fractions import Fraction

from .common import require, display
from .policy import ITEMS, DIMENSIONS
from .revised_review import BANDS, STATES, validate_period as validate_v6, level

VERSION = 'pmje-trial-20261031.7'
ANCHORS = {
    'P1': ['对象和动作可识别。', '依据当时歧义区分当前阶段、目标或范围。', '在具体目标冲突/范围漂移中，明确优先级及完成边界并消解冲突；普通清楚请求不是100。'],
    'P2': ['所需背景足以支持当前动作。', '选择有针对性的事实、版本或来源，明确已知与待查。', '对已出现的来源冲突、过期/缺失信息完成交叉核对或给出防误用边界；不是提供路径即可100。'],
    'P3': ['约束相容、可执行。', '依据具体场景给出可检查要求或优先次序。', '对实际存在的冲突、例外或权限风险统一规则并设置可检查边界；不是格式要求齐全即可100。'],
    'M1': ['有独立的方法/步骤/角色/权限选择且适合当前任务。', '针对具体条件选择方法、分工或权限，区别常规直接委派。', '在真实方法失效或职责/权限冲突下，处理替代路径和控制边界，使安排可接续；不要求实施产物成功。'],
    'M2': ['识别并安排具体前置依赖或阻塞。', '依据依赖作用安排先后和交接，处理一处改变的连带影响。', '对实际依赖阻塞同时明确恢复条件、责任和退出/回退边界，反馈表明依赖已处置或有据交接。'],
    'M3': ['新事实后明确更新执行安排。', '据反馈调整范围/顺序/职责，并保留有效工作。', '在相互影响的变更下，对已完成部分、剩余责任及实际出现的失效边界一并更新且有承接证据；不要求业务成功。'],
    'J1': ['对具体结论核对有关依据。', '用具体反证/版本差异/竞争解释支持质疑或接受。', '对真实关键反证、替代解释与证据局限作核对，并明确仍未知范围；一次指出错误通常90。'],
    'J2': ['安排相称的具体检查，并有执行或跟进行为。', '针对关键风险选择检查范围、方法或执行者，处理检查限制。', '必要边界得到执行核对或明确有据交接，对实际出现的失败/未覆盖项给出处置；只问测试没/只安排自测不是100。'],
    'J3': ['依据反馈接受、拒绝、纠正或保留未知。', '用具体反证纠正结论或限定采用范围。', '将实际存在的通过/失败/未知范围分别处置，明确采用条件及限制；对单一错误提出正确修正通常90，不强求修复已执行。'],
    'E1': ['增量接续或合理中断恢复，有具体交互组织事实。', '针对已出现的往返选择合并、上下文接续或反馈组织，避免具体重复。', '有实际反馈证明针对性组织消除了已出现的多环节重复，且必要信息/核验未被省略；一次正常重发是80。'],
    'E2': ['对已知无效过程明确停止、切换或缩小。', '依据失效情况选择止损时点和保留范围，避免扩大返工。', '对实际失败路径、替代路径与遗留改动都作相称处置，有证据确认停止或有据交接；单一停止决定不自动100。'],
    'E3': ['实际复用已有成果处理重复需要。', '依据新场景调整复用内容，检查适用条件或维护负担。', '重复工作有可追溯复用反馈，同时实际变化/失效边界得到核对或处理；直接复用一次不是100。'],
}
RUBRIC = {
    'version': VERSION, 'items': {k: {'name': ITEMS[k]['name'], 'scope': ITEMS[k]['scope'],
        '80': v[0], '90': v[1], '100': v[2]} for k,v in ANCHORS.items()},
    'bands': {'critical_breach':0, 'major_gap':40, 'necessary_gap':60,
              'adequate':80, 'reasoned':90, 'complete':100},
    'principles': [
        '80是有正面事实的常规充分处理，90须具体条件下有据选择，100须处理实际出现的关键边界。不是把旧100批量改80，也不以没看见错误默认80。',
        '简单任务也可出现反证、权限风险或来源冲突并达到100；没有此类机会不要求制造流程，得80/90并不表示做错。',
        '普通帮我分析/安装只表达目标，主归P1；必须出现额外方法、角色、权限或步骤选择才有独立M1表现。',
        '同一纠正的理由、决定和后果不自动构成J1/J3/M3三次奖励。须用不同本人动作或同句实质独立事实分别举证。',
        '未知更高档、未来采用或最终业务结果不抹掉已证的表达、决定、安排和止损；J3依据可以早于决定。',
        '无机会必须有目标性质依据；看不到执行结果不等于无机会；AI自主操作不归本人。平台故障不归责本人。',
        '60为有正面处理但存在具体必要缺口，40为关键控制严重缺失，0为主动关键越界；低档保留知情、控制权、可行替代和实际缺口。',
        '同一机会已妥善纠正看完整轨迹；不同局部目标不能互相抵消。父目标混合集合及已确认含独立目标的家族用parts逐局部目标评分后在家族内平均；同一目标不同阶段不拆开消解未处理缺口，不以一处代表全体。',
        '保留原312审阅家族用于对照；每家族每子项基础总权重1，局部拆分不按消息数增加家族权重。M复杂资格对相应局部正反表现均按1.5处理，待核展示敏感范围。',
        '不同会话可属于同一目标，但依据联合关系必须说明连续性；相邻时间不是同一授权/发布链证据。',
        '只读冻结缓存；未知须先查相关原文，材料检索未完成属于流程待办，不能交付为用户缺证。',
        '周只引用本期本人行为与期末前依据；月度独立看整月，不平均周分。四维各25%，月度表现90%参与10%。',
    ],
}


def validate_period(data, period, output):
    """沿用逐字、主体、时点和低档归责校验，另校验v7锚点与局部对象。"""
    out = copy.deepcopy(output)
    require(len(out.get('cells', [])) == 12 and {c.get('item') for c in out['cells']} == set(ITEMS), '须处置全部12项')
    result=[]
    for cell in out['cells']:
        item=cell['item']; parts=cell.get('parts') or []
        if parts:
            require(data.get('unattributed_bucket') or out.get('boundary',{}).get('status') in {'local_scope','unresolved'}, '只有混合集合或已说明独立局部边界的家族可用parts')
            require(len(parts)>=2, 'parts须至少两个不同局部目标')
        checked_parts=[];keys=set();proof_sets=[]
        for original in parts or [cell]:
            part=copy.deepcopy(original);part['item']=item;part.pop('parts',None)
            require(part.get('status') in STATES,
                    item+'状态不在冻结合同中：'+str(part.get('status'))+'；只允许'+
                    '/'.join(sorted(STATES))+'。无本人行为记录不能直接补分；实际缺口用unknown并给missing，真实无机会须有具体依据。')
            if parts:
                require(part.get('unit_id') and part.get('local_scope'), '局部对象须有unit_id和local_scope')
                require(part['unit_id'] not in keys, '局部对象编号重复')
                keys.add(part['unit_id'])
                needed=['status','reason','opportunity']
                if part.get('status')=='scored':needed+=['fact','person_evidence','adjacent_reason']
                missing=[k for k in needed if not part.get(k)]
                require(not (part.get('reuse_facts') and missing),
                        item+'/'+str(part['unit_id'])+'：局部parts不能用reuse_facts继承整族候选；当前缺'+
                        ','.join(missing)+'。整族候选没有unit_id映射，须为该局部显式给完整字段及对应原文，不能为过校验改变状态或档位。')
            if part['status']!='scored':part.pop('band',None)
            # 单独核验后统一检查所有真实局部对象；容器不借用任一局部档位。
            placeholders=[{'item':k,'status':'no_opportunity','opportunity':'校验占位，不进入最终结果',
                'reason':'仅用于单项来源校验','complex':{'status':'none'}} for k in ITEMS if k!=item]
            trial=dict(out,cells=[part]+placeholders)
            try:
                checked=validate_v6(data,period,trial)['cells'][0]
            except (ValueError, KeyError, TypeError, AssertionError) as exc:
                raise ValueError(item+('/'+str(part.get('unit_id')) if parts else '')+'：'+str(exc)) from exc
            check_anchor(checked)
            refs=frozenset((r['event_id'],r['start'],r['end']) for r in checked.get('person_evidence',[])) if checked['status']=='scored' else frozenset()
            if parts and checked['status']=='scored':
                require(refs not in proof_sets,'同一组本人依据不能拆成多个局部目标重复加权')
                proof_sets.append(refs)
            checked_parts.append(checked)
        if parts:
            result.append({'item':item,'status':'mixed','band':None,'aggregate_container':True,
                'reason':'逐局部对象裁决；容器无单一档位','parts':checked_parts})
        else:result.append(checked_parts[0])
    leaves=[c for outer in result for c in observations(outer) if c['status']=='scored']
    repeated_facts=[];missing_differences=[]
    for n,a in enumerate(leaves):
        for b in leaves[n+1:]:
            if a['item']==b['item']:continue
            if a['fact'].strip()==b['fact'].strip():repeated_facts.append(a['item']+'/'+b['item'])
            ra={(r['event_id'],r['start'],r['end']) for r in a['person_evidence']}
            rb={(r['event_id'],r['start'],r['end']) for r in b['person_evidence']}
            if ra & rb:
                if not (a.get('independent_fact_difference') and b.get('independent_fact_difference')):
                    missing_differences.append(a['item']+'/'+b['item'])
    # 一次列出完整缺项，避免长材料为同一类合同错误反复请求；判据不变。
    require(not repeated_facts,'同一事实不能跨项重复奖励：'+','.join(dict.fromkeys(repeated_facts)))
    require(not missing_differences,'同引文跨项双方均须independent_fact_difference：'+','.join(dict.fromkeys(missing_differences)))
    value=dict(out,user_id=data['user_id'],goal_id=data['goal_id'],title=data['title'],
        cells=result,policy_version=VERSION)
    # 关联归属必须终止，不让归属循环把事实排除出评分。
    by={c['item']:c for c in result}
    for c in result:
        for leaf in observations(c):
            seen={c['item']};cur=leaf
            while cur['status']=='related_only':
                target=cur['primary_item'];require(target not in seen,'主归循环')
                seen.add(target);cur=by[target]
                if cur['status']=='mixed':break
    return value


def check_anchor(cell):
    if cell['status']!='scored':return
    if cell['band']>=90:
        require(cell.get('anchor_basis','').strip(), '90/100须给本项具体选择依据anchor_basis')
    if cell['band']==100:
        require(cell.get('boundary_handling','').strip(), '100须给实际关键边界的处理boundary_handling')
    if cell['item']=='M1':
        require(cell.get('independent_method','').strip(), 'M1须说明超出普通请求的独立方法/分工事实independent_method')


def observations(cell):
    return cell.get('parts') or [cell]


def summarize_period(rows):
    """局部先在家族内汇总；每家族基础权重1，避免拆成多条就多计分。"""
    items = {}
    for item in ITEMS:
        families=[];counts=Counter();local_counts=Counter();pending=0;unknown_parts=0;local_count=0
        for row in rows:
            c=next(c for c in row['cells'] if c['item']==item)
            parts=observations(c);counts[c['status']]+=1;local_counts.update(x['status'] for x in parts)
            unknown_parts+=sum(x['status']=='unknown' for x in parts)
            scored=[x for x in parts if x['status']=='scored']
            local_count+=len(scored)
            if not scored:continue
            n=len(scored)
            entries=[(Fraction(x['band']),Fraction(x['weight_exact'])/n,x['weight_pending']) for x in scored]
            pending+=sum(t for _,_,t in entries)
            families.append(entries)
        entries=[x for f in families for x in f]
        numerator=sum((v*w for v,w,t in entries),Fraction());denominator=sum((w for v,w,t in entries),Fraction())
        value=numerator/denominator if denominator else None
        base=sum((sum(v for v,w,t in f)/len(f) for f in families),Fraction())/len(families) if families else None
        endpoints=[value] if value is not None else []
        pend=[x for x in entries if x[2]]
        for seq in (sorted(pend),sorted(pend,reverse=True)):
            a,b=numerator,denominator
            for v,w,_ in seq:
                a+=v*w/2;b+=w/2;endpoints.append(a/b)
        items[item]={'exact':str(value) if value is not None else None,'score':display(value),
                     'base_exact':str(base) if base is not None else None,'base_score':display(base),
                     'scored':len(families),'unknown':unknown_parts,'local_scored':local_count,
                     'status_counts':dict(counts),'local_status_counts':dict(local_counts),'weight_pending':pending,
                     'lower_exact':str(min(endpoints)) if endpoints else None,'upper_exact':str(max(endpoints)) if endpoints else None}
    dims={}
    for dim,spec in DIMENSIONS.items():
        eligible=[items[i] for i in spec['items'] if items[i]['exact'] is not None]
        value=sum((Fraction(i['exact']) for i in eligible),Fraction())/len(eligible) if eligible else None
        dims[dim]={'exact':str(value) if value is not None else None,'score':display(value),'covered_items':len(eligible),
                   'lower_exact':str(sum(Fraction(i['lower_exact']) for i in eligible)/len(eligible)) if eligible else None,
                   'upper_exact':str(sum(Fraction(i['upper_exact']) for i in eligible)/len(eligible)) if eligible else None}
    value=sum((Fraction(d['exact']) for d in dims.values()),Fraction())/4 if all(d['exact'] is not None for d in dims.values()) else None
    return {'items':items,'dimensions':dims,'exact':str(value) if value is not None else None,'score':display(value),
            'weight_pending':sum(i['weight_pending'] for i in items.values()),
            'lower_exact':str(sum(Fraction(d['lower_exact']) for d in dims.values())/4) if value is not None else None,
            'upper_exact':str(sum(Fraction(d['upper_exact']) for d in dims.values())/4) if value is not None else None}
