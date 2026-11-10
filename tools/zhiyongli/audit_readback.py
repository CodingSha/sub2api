"""语义复查自行补读冻结原文；评分对象不变，复查材料及每次回执分别留存。"""
import copy
import json
from pathlib import Path

from bounded_read import raw_size
from context_readback import expand, MAX_ROUNDS, MAX_NODE_ROUNDS
from navigation_access import freeze
from runtime import digest, RUBRIC, moment
from semantic_audit import validate, ContractError

INPUT_BYTES=120_000


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
        if raw_size(dict(unit=current,review=review))>INPUT_BYTES:
            raise ValueError('复查材料连同候选超过容量，本轮不发送；原评分与反证保留待分层综合')
        record=client.audit_review(current,review,receipt);value=receive(record)
        events=value.get('needs_events',[]);nodes=value.get('needs_nodes',[])
        if not isinstance(events,list) or not isinstance(nodes,list):raise ValueError('复查补读请求须为明确列表')
        if not events and not nodes:
            try:result=validate(unit,review,value,RUBRIC['items'],context=current)
            except ValueError as ex:raise ContractError(str(ex)) from None
            save(root,unit,review,packet,current,receipt)
            return result
        if value.get('items') or value.get('global_issues') or value.get('remarks'):
            raise ValueError('复查补读不能同时提交逐项结论或跨项结论；原回执保留')
        fields={'items','global_issues','needs_events'} | ({'needs_nodes'} if access is not None else set())
        if set(value)-{'remarks'}!=fields or value['items']!={} or value['global_issues']!=[] or events and nodes:
            raise ValueError('复查补读字段不符或混合事件与节点请求')
        stage=str(event_rounds+node_rounds+1)
        if nodes:
            if access is None or node_rounds>=MAX_NODE_ROUNDS:raise ValueError('复查节点补读不可用或达到执行上限')
            next_unit=access.expand(current,nodes,'audit-'+stage);node_rounds+=1
        else:
            if event_rounds>=MAX_ROUNDS:raise ValueError('复查原文补读达到执行上限，保留待核')
            next_unit=expand(current,packet,events);event_rounds+=1
            if access is not None:access.remember(next_unit)
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
    if (root/'audit.receipt.json').exists():paths.append(root/'audit.receipt.json')
    if (root/'current-model-audit.json').exists():paths.append(root/'current-model-audit.json')
    return paths
