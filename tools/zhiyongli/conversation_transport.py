"""综合评审只返回原文定位和判断，原句由程序恢复，不让模型重抄长引文。"""
import copy
import json

from anchored_judgment import CALIBRATION
from daily_joint_judgment import numbered_tasks
from daily_reasoning import facts_schema
from evidence_review import result_schema
from review_schema import obj, array, enum, compact
from task_review_policy import RUBRIC


def format_correction(name,instructions,raw,contract,receipt_sha256):
    """明确收全但未合约时让模型有界纠正；程序不补字段、不裁引用或改判断。"""
    import re
    import jsonschema
    from runtime import digest
    errors=[dict(path=list(e.absolute_path),constraint=e.validator,expected=e.validator_value)
        for e in jsonschema.Draft202012Validator(contract).iter_errors(raw)]
    if not errors:raise ValueError('回复已经合约，不能另发格式纠正')
    detail=dict(previous_response=raw,validation_errors=errors)
    corrected=instructions+'\n上次已完整返回但未通过输出合同。只纠正下列格式问题，保持原判断及依据；不得为凑引用数量删除必要语境，引用可使用同一完整记录编号。必须返回完整的合约对象；所有判断仍须重新通过原证据和事实核查。\n'+json.dumps(detail,ensure_ascii=False)
    tag='-formatfix-'+digest(detail)[:16]
    match=re.search(r'-\d+(?=(?:-effort-(?:low|medium|high|none))?(?:-without-reasoning)?(?:-service-retry-\d+)?$)',name)
    resumed=name[:match.start()]+tag+name[match.start():] if match else name+tag
    proof=dict(parent_stage=name,parent_receipt_sha256=receipt_sha256,
        original_sha256=digest(raw),validation_errors=errors)
    return resumed,corrected,proof


def normalize_terminal(value, records=()):
    """只修复已观测的技术格式；背景片段归位到同一来源记录，与decode既有语义一致。"""
    if not isinstance(value,dict):return value,[]
    normalized=copy.deepcopy(value);moves=[]
    # 局部修正已返回明确的空summary，只纠正层级；缺值、冲突和正文均不补造。
    if (set(value)=={'items'} and isinstance(value['items'],dict)
            and 'summary' in value['items'] and value['items']['summary'] is None
            and set(value['items'])-{'summary'}
            and set(value['items'])-{'summary'}<=set(RUBRIC['items'])):
        normalized['summary']=normalized['items'].pop('summary')
        moves.append(dict(op='move',source='/items/summary',target='/summary'))
    if (isinstance(value.get('items'),dict) and not any(key in value for key in ('requests','queries')) and isinstance(value.get('summary'),str)
            and isinstance(value.get('task_summaries'),dict)
            and all(value['items'].get(key)==[] for key in ('requests','queries'))):
        for key in ('requests','queries'):
            normalized[key]=normalized['items'].pop(key)
            moves.append(dict(op='move',source='/items/'+key,target='/'+key))
    aliases={key:row['id'] for row in records for key,_ in row.get('text_parts',[]) if key}
    personal_aliases={key:row['id'] for row in records if row.get('role')=='user' for key,_ in row.get('text_parts',[]) if key}
    summaries=normalized.get('task_summaries')
    for task,summary in (summaries if isinstance(summaries,dict) else {}).items():
        if not isinstance(summary,dict) or not isinstance(summary.get('workflow'),list):continue
        for index,usage in enumerate(summary['workflow']):
            if not isinstance(usage,dict):continue
            refs=usage.get('user_refs')
            if not isinstance(refs,list) or not all(isinstance(ref,str) for ref in refs):continue
            grouped=list(dict.fromkeys(personal_aliases.get(ref,ref) for ref in refs))
            if grouped!=refs:
                usage['user_refs']=grouped
                moves.append(dict(op='resolve_workflow_user_aliases',path='/task_summaries/'+task+'/workflow/'+str(index)+'/user_refs',original=refs,result=grouped))
    cells=normalized.get('items')
    for code,cell in (cells if isinstance(cells,dict) else {}).items():
        if code not in RUBRIC['items'] or not isinstance(cell,dict):continue
        refs=cell.get('context_refs')
        if not isinstance(refs,list) or len(refs)<=12 or not all(isinstance(ref,str) for ref in refs):continue
        grouped=list(dict.fromkeys(aliases.get(ref,ref) for ref in refs))
        if len(grouped)<=12:
            cell['context_refs']=grouped
            moves.append(dict(op='collapse_context_aliases',path='/items/'+code+'/context_refs',original=refs,result=grouped))
    for index,issue in enumerate(normalized.get('issues') or []):
        if not isinstance(issue,dict):continue
        refs=issue.get('source_refs')
        if not isinstance(refs,list) or len(refs)<=6 or not all(isinstance(ref,str) for ref in refs):continue
        grouped=list(dict.fromkeys(aliases.get(ref,ref) for ref in refs))
        if len(grouped)<=6:
            issue['source_refs']=grouped
            moves.append(dict(op='collapse_audit_source_aliases',path='/issues/'+str(index)+'/source_refs',original=refs,result=grouped))
    return (normalized,moves) if moves else (value,[])


def encode(tasks, records, originals, notes, searches, available):
    aliases={key:'r'+str(index+1) for index,key in enumerate(originals)}
    reverse={value:key for key,value in aliases.items()}
    owners={row['id']:t['id'] for t in tasks for row in t['records']}
    working=[dict(r,id=aliases[r['id']],task_id=owners.get(r['id'],originals[r['id']].get('task_id','')),
        role='context' if r.get('boundary_context') else r['role']) for r in records]
    numbered,_=numbered_tasks([dict(id='working',records=working)])
    fragments={}
    for row in numbered[0]['records']:
        if 'text_parts' not in row:continue
        complete=''.join(part for _,part in row['text_parts']);parts=[]
        for identity,text in row['text_parts']:
            for start in range(0,len(text),700):
                piece=text[start:start+700]
                key='q'+str(len(fragments)+1) if identity and complete.count(piece)==1 else ''
                if key:fragments[key]=[reverse[row['id']],piece]
                parts.append([key,piece])
        row['text_parts']=parts
    model_tasks=[dict(t,records=[dict(r,id=aliases[r['id']]) for r in t['records']],
        workflow_index=[dict(w,record_id=aliases[w['record_id']]) for w in t.get('workflow_index',[])]) for t in tasks]
    ids=list(dict.fromkeys(r['id'] for r in working))
    # 背景定位可使用整条记录或已展示片段；两者均严格解析到冻结来源。
    reference_ids=list(dict.fromkeys(ids+list(fragments)))
    cells={}
    for code in RUBRIC['items']:
        original=result_schema(code,reference_ids)['properties']
        evidence=obj(dict(fragment_id=enum(*fragments) if fragments else dict(type='string'),
            claim=dict(type='string',minLength=1,maxLength=240),origin=enum('personal','supplied') if code.startswith('P') else enum('personal')))
        fields=dict(band=enum(*RUBRIC['performance_values']),reason=dict(type='string',minLength=1,maxLength=400),
            next_band=dict(type='string',maxLength=300),evidence=dict(array(evidence),minItems=1,maxItems=8 if fragments else 0),
            context_refs=original['context_refs'],negative=original['negative'])
        if code=='E3':fields['e3_claim']=original['e3_claim']
        cells[code]=obj(fields)
    items=obj(cells);items['required']=[]
    if not fragments:items['maxProperties']=0
    directory=[dict(id=aliases[key],role='context' if r.get('boundary_context') else r['role'],chars=len(r['text'])) for key,r in originals.items()] if available else []
    paged=len(json.dumps(directory,ensure_ascii=False).encode())>24000
    request=obj(dict(record_id=(dict(type='string',pattern=r'^r[1-9][0-9]*$') if paged else enum(*(aliases[r] for r in available))) if available else dict(type='string'),
        start=dict(type='integer',minimum=0),chars=dict(type='integer',minimum=1,maximum=4000)))
    def terminal(value):return dict(anyOf=[dict(type='null'),value]) if available else value
    contract=compact(obj(dict(summary=terminal(dict(type='string',minLength=1,maxLength=600)),
        task_summaries=terminal(facts_schema(model_tasks,compress=False)['properties']['task_summaries']),
        items=terminal(items),requests=dict(array(request),maxItems=6 if available else 0),
        queries=dict(array(dict(type='string',minLength=2,maxLength=80)),maxItems=3 if available else 0,uniqueItems=True))))
    if available:
        # 补读与终判必须二选一，空请求配空结论属于完整响应的合同错误，可有界纠正。
        contract['anyOf']=[dict(properties=dict(requests=dict(minItems=1))),
            dict(properties=dict(queries=dict(minItems=1))),
            dict(properties={key:dict(type=kind) for key,kind in
                [('summary','string'),('items','object'),('task_summaries','object')]})]
    data=dict(tasks=[dict(id=t['id'],source_title=t.get('title',''),workflow_index=t['workflow_index']) for t in model_tasks],
        records=numbered[0]['records'],reading_notes=[dict(n,source_refs=[aliases[r] for r in n.get('source_refs',[])]) for n in notes],
        directory=directory[:160] if paged else directory,
        available_read_ids=[aliases[r] for r in (available[:160] if paged else available)],
        search_results=[dict(s,hits=[dict(h,record_id=aliases[h['record_id']]) for h in s['hits']]) for s in searches],
        rubric=RUBRIC,calibration=CALIBRATION,source_scope='directory是冻结来源全集；未保存的反馈无法查询，不能推定用户没做。',
        fragment_contract='user的text_parts按顺序拼接是完整已交付原文；q编号只定位片段，不代表独立任务或独立加分。')
    if paged:
        data['directory_window']=dict(total=len(originals),shown=len(data['directory']),
            omitted=len(originals)-len(data['directory']),all_records_searchable=True,
            navigation='目录仅展示首段，r1至r'+str(len(originals))+'均对应冻结记录；queries检索完整来源，返回定位后用requests读取。省略目录不等于原文不存在。')
    return data,contract,dict(aliases=aliases,fragments=fragments)


def decode(value, transport):
    value=copy.deepcopy(value)
    reverse={alias:ref for ref,alias in transport['aliases'].items()}
    def source_ref(ref):
        if ref in reverse:return reverse[ref]
        return transport['fragments'][ref][0]
    for request in value['requests']:request['record_id']=reverse[request['record_id']]
    if value['items'] is None:return value
    for code,cell in value['items'].items():
        evidence=[]
        for ev in cell['evidence']:
            ref,quote=transport['fragments'][ev['fragment_id']]
            evidence.append(dict(record_id=ref,quote=quote,claim=ev['claim'],origin=ev['origin'],primary_item=code))
        cell.update(item_code=code,state='observed',evidence=evidence,
            context_refs=list(dict.fromkeys(source_ref(ref) for ref in cell['context_refs'])),limitations=[])
        cell.setdefault('e3_claim',None)
        if cell['e3_claim']:
            for key in ('selection_refs','execution_refs','effect_refs'):
                cell['e3_claim'][key]=list(dict.fromkeys(source_ref(ref) for ref in cell['e3_claim'][key]))
    for summary in value['task_summaries'].values():
        for usage in summary['workflow']:
            usage['user_refs']=[reverse[ref] for ref in usage['user_refs']]
            usage['tool_ref']=reverse[usage['tool_ref']]
    return value
