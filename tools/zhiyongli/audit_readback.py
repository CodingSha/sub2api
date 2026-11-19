"""语义复查自行补读冻结原文；评分对象不变，复查材料及每次回执分别留存。"""
import copy
import json
from pathlib import Path

from bounded_read import raw_size
from context_readback import expand, MAX_ROUNDS, MAX_NODE_ROUNDS
from navigation_access import freeze,ReadbackCapacity
from runtime import digest, RUBRIC, moment
from semantic_audit import validate, ContractError, QuoteError

INPUT_BYTES=120_000
CONTRACT_RETRIES=2


def bounded_context(unit,review,packet,root,current):
    """候选与必要原文一起计量；未先展示的背景仍保留在完整导航中。"""
    from context_navigation import Navigation
    from navigation_access import Access,set_remaining
    from model_view import create,refresh,verify
    overhead=raw_size(dict(unit={},review=review,contract_feedback=None))-2
    maximum=INPUT_BYTES-overhead
    if maximum<=0:raise ValueError('候选本身已超过复查容量，原候选保留待核')
    events={e['event']:e for e in current['events']}
    required={e['event'] for e in review.get('evidence',[])}
    for part in review.get('period_reviews',[]):
        for cell in part.get('cells',[]):
            for obj in (cell,cell.get('quality',{})):
                required.update(obj.get('missing',{}).get('checked_events',[]))
    if not required<=set(events):raise ValueError('复查候选引用或缺失检查不在评分已提供原文中')
    access=Access(Navigation(root/'context-navigation',packet),root/'audit-capacity-access',native=unit,unit_bytes=maximum)
    base=create(unit,seeds=[e for e in current['events'] if e['event'] in required])
    result=access.start(base)
    for event in current['events']:
        if event['event'] in required:continue
        candidate=copy.deepcopy(result);candidate['events'].append(copy.deepcopy(event))
        refresh(unit,candidate);set_remaining(candidate,maximum)
        if raw_size(candidate)<=maximum:result=candidate
    verify(unit,result,packet);access.remember(result)
    return result,access


def preserve_judgments(previous,value):
    """格式修复不能撤销既有判断或异议；需要改判断时须走有效复查后的评分修订。"""
    if not isinstance(previous,dict):return
    items=previous.get('items')
    for item,judgment in (items.items() if isinstance(items,dict) else []):
        if not isinstance(judgment,dict):continue
        for key in ('verdict','own_state','person_act','reason','problem_codes'):
            old=judgment.get(key)
            if key=='verdict':valid=isinstance(old,str) and old in {'supported','revise','unresolved'}
            elif key=='own_state':
                from semantic_audit import STATES
                valid=isinstance(old,str) and old in STATES
            elif key=='problem_codes':valid=isinstance(old,list) and all(isinstance(v,str) for v in old)
            else:valid=isinstance(old,str) and bool(old.strip())
            if valid and value.get('items',{}).get(item,{}).get(key)!=old:
                raise ValueError('复查格式修复改变原有判断或异议：'+item+'.'+key)
    issues=previous.get('global_issues')
    if isinstance(issues,list) and all(isinstance(v,str) for v in issues) and value.get('global_issues')!=issues:
        raise ValueError('复查格式修复改变原有跨项异议')


def checked(unit,current,review,receipt,client,receive,check_request=None):
    """只重试已知无效的复查输出；候选、原复查、异议和每次修复回执均保留。"""
    feedback=None;previous=None
    for attempt in range(CONTRACT_RETRIES+1):
        quote_fix=feedback is not None and 'quote_slot' in feedback
        path=receipt if not attempt else receipt.with_name('audit.'+('quote' if quote_fix else 'fix')+str(attempt)+'.receipt.json')
        # 单槽引文修复只发送该事件及原引文；不能按未实际发送的整份前序复查误判超容量。
        if quote_fix and callable(getattr(client,'audit_payload',None)):
            from bounded_read import REQUEST_BYTES
            exceeds=raw_size(client.audit_payload(current,review,feedback))>REQUEST_BYTES
        else:exceeds=raw_size(dict(unit=current,review=review,contract_feedback=feedback))>INPUT_BYTES
        if exceeds:
            raise ValueError('复查材料连同候选及格式修复反馈超过容量，本轮不发送，保留待核')
        record=(client.audit_review(current,review,path) if feedback is None
            else client.audit_review(current,review,path,feedback))
        value=None
        try:
            value=receive(record)
            if not isinstance(value,dict):raise ValueError('复查输出须为对象')
            if quote_fix:
                # 只应用模型选择的新引文；所有分项判断、异议和其他引用均从原复查复制。
                if 'quote_candidates' in feedback:
                    from quote_candidates import resolve
                    try:value=resolve(current,feedback,value)
                    except ValueError as ex:raise ContractError(str(ex)) from None
                if set(value)!={'event','quote'}:raise ValueError('引用修正只接受event与quote')
                corrected=copy.deepcopy(previous);slot=feedback['quote_slot']
                corrected['items'][slot['item']]['source_quotes'][slot['index']]=value
                value=corrected
            if feedback is not None:preserve_judgments(previous,value)
            if value.get('needs_events') or value.get('needs_nodes'):
                if feedback is not None and not feedback.get('readback_request'):
                    raise ValueError('复查格式修复须保留原逐项结论，不改为补读请求')
            else:
                if feedback and feedback.get('readback_request'):raise ValueError('超容量补读修正须返回合法补读请求，不能跳过所需原文直接提交结论')
                result=validate(unit,review,value,RUBRIC['items'],context=current,require_facts=True)
                return record,value,result,path
        except (ValueError,KeyError,TypeError) as ex:
            if hasattr(ex,'state'):raise
            # 候选选择拒绝/不符仍是待核，不能再退回自由抄写或重判来绕过。
            if quote_fix and 'quote_candidates' in feedback:raise ContractError(str(ex)) from None
            if attempt>=CONTRACT_RETRIES or not getattr(client,'accepts_revision_feedback',False):
                raise ContractError(str(ex)) from None
            if isinstance(ex,QuoteError):previous=copy.deepcopy(value)
            elif previous is None and isinstance(value,dict):previous=copy.deepcopy(value)
            request_repair=bool(feedback and feedback.get('readback_request'))
            feedback=dict(attempt=attempt+1,error=(type(ex).__name__+': '+str(ex))[:2000],previous_audit=previous)
            if request_repair:feedback['readback_request']=True
            if isinstance(ex,QuoteError):
                feedback['quote_slot']=ex.slot
                if getattr(client,'supports_quote_candidates',False):
                    from quote_candidates import propose
                    candidates=propose(current,ex.slot,previous)
                    if candidates:feedback['quote_candidates']=candidates
            error_suffix='.quote.contract-error.json' if isinstance(ex,QuoteError) else '.contract-error.json'
            freeze(path.with_name(path.name.replace('.receipt.json',error_suffix)),
                dict(feedback=feedback,unit_sha256=digest(unit),review_sha256=digest(review),response_sha256=digest(record)))
            continue
        # 访问证明/来源校验在格式修复捕获范围之外；只接管明确的节点容量错误。
        if check_request is not None:
            try:check_request(value)
            except ReadbackCapacity as ex:
                if attempt>=CONTRACT_RETRIES or not getattr(client,'accepts_revision_feedback',False):raise ContractError(str(ex)) from None
                previous=copy.deepcopy(value)
                feedback=dict(attempt=attempt+1,error=str(ex)[:2000],previous_audit=previous,readback_request=True)
                freeze(path.with_name(path.name.replace('.receipt.json','.request.contract-error.json')),
                    dict(feedback=feedback,unit_sha256=digest(unit),review_sha256=digest(review),response_sha256=digest(record)))
                continue
        return record,value,None,path


def complete(unit, review, packet, directory, client, receive, progress=lambda *args:None, access=None, model_context=None):
    root=Path(directory);current=model_context if model_context is not None else unit
    if model_context is not None:
        from model_view import verify
        verify(unit,current,packet)
    if str(unit['user_id'])!=str(packet['user_id']):raise ValueError('复查补读人员与冻结来源不符')
    if (len(unit.get('periods',[]))!=1 or moment(unit['periods'][0]['start'])!=moment(packet['since'])
            or moment(unit['periods'][0]['end'])!=moment(packet['until'])):
        raise ValueError('复查补读评价时窗与冻结来源不符')
    if access is not None:
        if current.get('context_access'):access.verify(current)
        else:current=access.start(current)
    freeze(root/'audit-readback/input.json',dict(unit_sha256=digest(unit),review_sha256=digest(review),packet_sha256=digest(packet)))
    receipt=root/'audit.receipt.json';event_rounds=node_rounds=0
    while True:
        stage=str(event_rounds+node_rounds+1)
        prepared=[]
        def check_request(value):
            events=value.get('needs_events',[]);nodes=value.get('needs_nodes',[])
            if not isinstance(events,list) or not isinstance(nodes,list):raise ValueError('复查补读请求须为明确列表')
            if value.get('items') or value.get('global_issues') or value.get('remarks'):
                raise ValueError('复查补读不能同时提交逐项结论或跨项结论；原回执保留')
            fields={'items','global_issues','needs_events'} | ({'needs_nodes'} if access is not None else set())
            if set(value)-{'remarks'}!=fields or value['items']!={} or value['global_issues']!=[] or events and nodes:
                raise ValueError('复查补读字段不符或混合事件与节点请求')
            if nodes:
                if access is None or node_rounds>=MAX_NODE_ROUNDS:raise ValueError('复查节点补读不可用或达到执行上限')
                prepared.append(access.expand(current,nodes,'audit-'+stage))
            else:
                if event_rounds>=MAX_ROUNDS:raise ValueError('复查原文补读达到执行上限，保留待核')
                if access is not None:access.verify(current)
                prepared.append(expand(current,packet,events))
                if access is not None:
                    from navigation_access import set_remaining
                    if access.directory_mode is not None:
                        set_remaining(prepared[-1],access.unit_bytes)
                        if raw_size(prepared[-1])>access.unit_bytes:
                            raise ReadbackCapacity('完整事件补读超过当前复查实例容量，保留原状态与原文，不发送后续请求')
                    access.remember(prepared[-1])
        record,value,result,final_receipt=checked(unit,current,review,receipt,client,receive,check_request)
        events=value.get('needs_events',[]);nodes=value.get('needs_nodes',[])
        if not events and not nodes:
            save(root,unit,review,packet,current,final_receipt)
            return result
        next_unit=prepared[0]
        if nodes:node_rounds+=1
        else:event_rounds+=1
        freeze(root/'audit-readback'/stage/'unit.json',next_unit)
        freeze(root/'audit-readback'/stage/'request.json',dict(previous_unit_sha256=digest(current),
            previous_response_sha256=digest(record),requested_events=events,requested_nodes=nodes,next_unit_sha256=digest(next_unit)))
        current=next_unit;receipt=root/'audit-readback'/stage/'audit.receipt.json'
        progress('checking',event_rounds+node_rounds,MAX_ROUNDS+(MAX_NODE_ROUNDS if access else 0),'语义复查自行补读冻结原文；不改写评分输入')


def save(root,unit,review,packet,context,receipt):
    from shared_weeks import reference
    path=root/'audit-readback/context.json';freeze(path,context)
    freeze(root/'current-model-audit.json',dict(unit_sha256=digest(unit),review_sha256=digest(review),
        packet_sha256=digest(packet),context=reference(path),receipt=reference(receipt)))


def context(root,unit):
    """重验复查材料只能增加本人的冻结背景；评分时的本人表达、范围与判断不变。"""
    from shared_weeks import read_ref
    root=Path(root).resolve();pointer=root/'current-model-audit.json'
    if not pointer.exists():
        if (root/'audit-readback').exists():raise ValueError('复查补读缺少最终候选及材料指针')
        return unit
    proof=json.loads(pointer.read_text());packet=json.loads((root/'packet.json').read_text())
    original_review=json.loads((root/'model-review.json').read_text())['value']
    if (proof['unit_sha256']!=digest(unit) or proof['packet_sha256']!=digest(packet)
            or proof['review_sha256']!=digest(original_review)):
        raise ValueError('复查补读未绑定当前评分对象、原候选及冻结来源')
    for key in ('context','receipt'):
        if root not in Path(proof[key]['path']).resolve().parents:raise ValueError('复查引用越出本轮目录')
    value=read_ref(proof['context']);read_ref(proof['receipt'])
    verify_extension(unit,value,packet)
    return value


def verify_extension(unit,value,packet):
    if value.get('model_view'):
        from model_view import verify
        verify(unit,value,packet)
        return
    mutable={'events','context_access','context_readback'}
    if ({k:v for k,v in unit.items() if k not in mutable}!={k:v for k,v in value.items() if k not in mutable}
            or value['events'][:len(unit['events'])]!=unit['events']):
        raise ValueError('复查补读改变原有材料、本人身份或计分范围')
    events={e['event']:e for e in packet['events']};ids={e['event'] for e in unit['events']}
    for event in value['events'][len(unit['events']):]:
        key=event['event'];original=event.get('source_event',key)
        if key in ids or original not in events:raise ValueError('复查新增背景重复或不在冻结来源')
        expected=copy.deepcopy(events[original])
        if moment(expected['at'])>=moment(packet['until']):raise ValueError('复查背景越过评价截止')
        if key!=original:
            span=event.get('source_span',[])
            if (len(span)!=2 or any(type(n) is not int for n in span) or not 0<=span[0]<=span[1]<=len(expected['text'])
                    or key!=original+'-span-'+str(span[0])+'-'+str(span[1])):
                raise ValueError('复查新增背景片段越界或引用不符')
            expected.update(event=key,text=expected['text'][span[0]:span[1]],source_event=original,source_span=span)
        expected.update(event_id=key,actor_class='context',human_spans=[])
        if event!=expected:raise ValueError('复查新增背景原文或归属变化')
        ids.add(key)


def receipt(root,unit):
    root=Path(root)
    if not (root/'current-model-audit.json').exists():return root/'audit.receipt.json'
    context(root,unit)
    return Path(json.loads((root/'current-model-audit.json').read_text())['receipt']['path'])


def files(root):
    root=Path(root)
    paths=sorted((root/'audit-readback').rglob('*.json'))
    paths.extend(sorted(root.glob('audit*.receipt.json')))
    paths.extend(sorted(root.glob('audit*.contract-error.json')))
    if (root/'current-model-audit.json').exists():paths.append(root/'current-model-audit.json')
    return paths
