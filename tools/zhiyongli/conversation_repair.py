"""只修复明确指出的分项；未受影响的判断由程序原样保留并继续整体核验。"""
import copy
import re

from conversation_quality import packets,USER_METHODS,BEHAVIOR_OWNERSHIP
from review_schema import obj
from runtime import digest

INSTRUCTIONS='''根据correction中的具体问题，核对任务理解、当前原文和该项固定锚点，只返回items_in_scope的修正。可以撤回无依据的项（返回null），或纠正其档位、理由及引用；不得另开分项重复奖励。candidate是上一次完整判断，其中不在修正范围的项保持原样，仅用于检查是否重复计分。不要只改措辞保留错误结论，也不要为了提高档位新增用户本来不需要的义务。用户安排本身可评价，AI实际执行成功不是所有正向安排的前提。items中的证据编号必须来自当前records；summary为null表示保留原整体解读，只有summary_in_scope=true时才允许修正整体解读。任务目标和其他项保持原样，由程序合并后重新核验全部引用、独立行为和事实。只返回符合合同的JSON。'''
INSTRUCTIONS+='''\nrecords保留完整的已读任务上下文。旧引文不成立或重复，不等于整项没有证据：对每个受影响分项，应回看目标变化、用户其他独立选择、后续接续及反证，再决定换用更合适的依据还是撤回。不能为消除当前问题而忽略材料中其他已成立的独立行为；也不能用同一行为换个名字重复保分。判断用户怎样运用AI时，既看安排，也看当前使用中的后续调整和处理；AI代执行的背景可以解释使用过程，但不能冒充本人独立验收。next_band只解释相邻原锚点；没有具体差距可留空，不强迫用户量化、补台账、重复说明或增加当前目标不需要的工作。'''
INSTRUCTIONS+=USER_METHODS
INSTRUCTIONS+='\n'+BEHAVIOR_OWNERSHIP


def with_context(data, contract, tasks, originals, correction):
    """按受影响判断的真实引用取回紧邻原文，不把工作集外的反馈当成不存在。"""
    from conversation_transport import encode
    aliases={key:'r'+str(index+1) for index,key in enumerate(originals)}
    reverse={alias:key for key,alias in aliases.items()}
    fragments={key:[reverse[row['id']],text] for row in data['records'] for key,text in row.get('text_parts',[]) if key}
    transport=dict(aliases=aliases,fragments=fragments)
    records=[dict(id=reverse[row['id']],role=originals[reverse[row['id']]]['role'],
        start=row['start'],end=row['end'],text=''.join(text for _,text in row['text_parts']) if 'text_parts' in row else row['text'],
        excerpt=row.get('excerpt',False),boundary_context=bool(originals[reverse[row['id']]].get('boundary_context')))
        for row in data['records']]
    refs=[]
    for code in scope(correction):
        cell=correction['previous_invalid']['items'].get(code,{})
        refs.extend(e['fragment_id'] for e in cell.get('evidence',[]));refs.extend(cell.get('context_refs',[]))
        for key in ('selection_refs','execution_refs','effect_refs'):refs.extend((cell.get('e3_claim') or {}).get(key,[]))
    refs.extend(ref for issue in correction.get('quality_findings',[]) for ref in issue.get('source_refs',[]))
    targets={reverse[ref] if ref in reverse else fragments[ref][0] for ref in refs if ref in reverse or ref in fragments}
    added=[]
    for task in tasks:
        rows=task['records']
        neighbors={j for i,row in enumerate(rows) if row['id'] in targets for j in range(max(0,i-1),min(len(rows),i+2))}
        for index in sorted(neighbors):
            row=originals[rows[index]['id']]
            if row['role']=='user' or any(r['id']==row['id'] and r['start']==0 and r['end']==len(row['text']) for r in records):continue
            records.append(dict(id=row['id'],role=row['role'],start=0,end=len(row['text']),text=row['text'],
                excerpt=bool(row.get('excerpt') or row.get('truncated')),boundary_context=bool(row.get('boundary_context'))))
            added.append(row['id'])
    if not added:return data,contract,transport,records,[]
    notes=[dict(note,source_refs=[reverse[ref] for ref in note.get('source_refs',[])]) for note in data.get('reading_notes',[])]
    searches=[dict(search,hits=[dict(hit,record_id=reverse[hit['record_id']]) for hit in search['hits']]) for search in data.get('search_results',[])]
    from conversation_review import unread_ids
    available=unread_ids(originals,records) if data.get('remaining_read_rounds',1)>0 else []
    expanded,schema,transport=encode(tasks,records,originals,notes,searches,available)
    for key in ('evaluation_objects','remaining_read_rounds'):
        if key in data:expanded[key]=data[key]
    return expanded,schema,transport,records,added


def source_data(data):
    return {key:value for key,value in data.items() if key not in {'output_contract','response_format'}}


def scope(correction):
    if correction.get('product_validation_errors'):return []
    codes=set(re.findall(r'\b[PMJE][123]\b',str(correction.get('validation_errors',''))))
    codes.update(code for row in correction.get('quality_findings',[]) for code in [row['item'],*row.get('related_items',[])])
    return sorted(codes)


def binding_conflicts(candidate, transport, originals):
    """指出实际冲突的两项主张和引用，不替模型决定主归或改分。"""
    seen=[];conflicts=[]
    for code,cell in candidate['items'].items():
        for evidence in cell['evidence']:
            ref,quote=transport['fragments'][evidence['fragment_id']];text=originals[ref]['text']
            if text.count(quote)!=1:continue
            start=text.index(quote);end=start+len(quote)
            current=dict(item=code,fragment_id=evidence['fragment_id'],claim=evidence['claim'],quote=quote)
            for oldref,lo,hi,old in seen:
                if oldref==ref and old['item']!=code and (start<hi and lo<end or old['claim'].strip()==evidence['claim'].strip()):
                    conflicts.append(dict(record_id=transport['aliases'][ref],judgments=[old,current]))
            seen.append((ref,start,end,current))
    return conflicts


def package(data, contract, correction):
    if correction.get('product_tasks_in_scope'):
        from conversation_product import workflow_packet
        return workflow_packet(data,contract,correction)
    codes=scope(correction);candidate=correction['previous_invalid']
    if not codes:raise ValueError('局部修正必须有明确受影响分项')
    # 只压缩传输范围；原综合判断的完整材料及其他项仍保留在父阶段中。
    narrowed=dict(candidate,items={code:candidate['items'][code] for code in codes if code in candidate['items']})
    groups=packets(data,narrowed,maximum=150000,include_coverage=False,include_comparison=False)
    if len(groups)!=1:raise ValueError('受影响分项超过修正容量，保留错误，不截断来源')
    packet=groups[0]
    # 修正仍理解完整任务；不能只交付旧引文附近，导致独立证据被连带丢掉。
    packet['records']=data['records']
    packet['reading_notes']=data.get('reading_notes',[])
    # 新发现的遗漏项也须拿到完整原锚点，不能用“是否有行为”的最低档检查代替判档。
    packet['rubric']=dict(items={code:data['rubric']['items'][code] for code in codes},
        performance_anchors={code:data['rubric']['performance_anchors'][code] for code in codes},
        item_counterexamples={code:data['rubric']['item_counterexamples'][code] for code in codes})
    packet.update(items_in_scope=codes,correction={k:v for k,v in correction.items() if k!='previous_invalid'},
        candidate=candidate,original_data_sha256=digest(source_data(data)))
    for key in ('task_summaries','other_judgments','overall_summary'):packet.pop(key,None)
    summary_allowed=any(row['claim_pointer']=='/summary' for row in correction.get('quality_findings',[]))
    packet['summary_in_scope']=summary_allowed
    def node(value):
        while '$ref' in value:
            target=contract
            for part in value['$ref'][2:].split('/'):target=target[part]
            value=target
        if 'anyOf' in value:value=next(v for v in value['anyOf'] if v.get('type')!='null')
        if '$ref' in value:return node(value)
        return value
    cells=node(contract['properties']['items'])['properties']
    schema=obj(dict(items=obj({code:dict(anyOf=[dict(type='null'),copy.deepcopy(cells[code])]) for code in codes}),
        summary=dict(anyOf=[dict(type='null'),dict(type='string',minLength=1,maxLength=600)]) if summary_allowed else dict(type='null')))
    definitions={};pending=[schema]
    while pending:
        value=pending.pop()
        if isinstance(value,list):pending.extend(value)
        elif isinstance(value,dict):
            ref=value.get('$ref')
            if ref and ref.startswith('#/$defs/'):
                key=ref.split('/')[-1]
                if key not in definitions:
                    definitions[key]=copy.deepcopy(contract['$defs'][key]);pending.append(definitions[key])
            pending.extend(v for k,v in value.items() if k!='$ref')
    if definitions:schema['$defs']=definitions
    return packet,schema


def apply(parent, patch, packet):
    if set(patch['items'])!=set(packet['items_in_scope']):raise ValueError('修正分项与问题范围不一致')
    result=copy.deepcopy(parent)
    refs={row['id'] for row in packet['records']}
    fragments={key for row in packet['records'] for key,_ in row.get('text_parts',[]) if key}
    refs.update(fragments)
    for code,cell in patch['items'].items():
        if cell is None:result['items'].pop(code,None);continue
        if any(row['fragment_id'] not in fragments for row in cell['evidence']):raise ValueError('修正引用未交付原文')
        used=list(cell['context_refs'])
        if cell.get('e3_claim'):
            for field in ('selection_refs','execution_refs','effect_refs'):used.extend(cell['e3_claim'][field])
        if set(used)-refs:raise ValueError('修正上下文未交付原文')
        result['items'][code]=copy.deepcopy(cell)
    if patch['summary'] is not None:
        if not packet['summary_in_scope']:raise ValueError('修正不能改写未被指出问题的整体解读')
        result['summary']=patch['summary']
    if packet.get('task_workflows_in_scope'):
        if set(patch.get('task_workflows',{}))!=set(packet['task_workflows_in_scope']):
            raise ValueError('工作流说明修正超出指定任务')
        for task,workflows in patch['task_workflows'].items():
            result['task_summaries'][task]['workflow']=copy.deepcopy(workflows)
    elif 'task_workflows' in patch:raise ValueError('分项修正不能改写任务说明')
    return result
