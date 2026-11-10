"""月末必要缺口逐项有界核对；保留全期间签名、实际提供范围及另次检查。"""
import copy
import json
from pathlib import Path

from bounded_read import raw_size
from context_navigation import Navigation
from navigation_access import Access,freeze,query_schema
from month_evidence import slots,validate,schema as status_schema
from review_schema import obj,array,enum
from runtime import digest,moment

INPUT_BYTES=120_000
MAX_ROUNDS=24


def references(value):
    if isinstance(value,dict):
        return set(value.get('refs',[])) | set().union(*(references(v) for v in value.values()))
    if isinstance(value,list):return set().union(*(references(v) for v in value))
    return set()


def prepare(periods,directories,item,root):
    views=[];accesses={}
    for period in periods:
        unit=period['unit'];pid=period['row']['period_id'];review=period['review']
        cells=[c for c in period['row']['cells'] if c['item']==item]
        context=[p['context'] for p in review.get('period_reviews',[])]
        refs=references(cells+context)
        ledger=review.get('evidence',[])
        view=dict(user_id=unit['user_id'],since=unit['periods'][0]['start'],until=unit['periods'][0]['end'],
            period_id=pid,native_sha256=digest(period),cells=cells,task_context=context,events=[],
            supplied_quotes=[copy.deepcopy(e) for e in ledger if e['id'] in refs],
            evidence_index=[dict(id=e['id'],event=e['event'],quote_sha256=digest(e['quote'])) for e in ledger],
            source_coverage={k:dict(count=len(unit.get(k,[])),sha256=digest(unit.get(k,[])))
                for k in ('events','person_events','source_keys','source_gaps')})
        directory=Path(directories[pid]);tree=directory/'context-navigation/tree.json'
        if tree.exists():
            packet=json.loads((directory/'packet.json').read_text())
            access=Access(Navigation(directory/'context-navigation',packet),root/'access'/pid)
            view=access.start(view);accesses[pid]=access
        else:
            # 短期间可提供全部原文；长期间必须有自己的截止前导航，不能只看少数引文。
            view['events']=copy.deepcopy(unit['events'])
        views.append(view)
    return views,accesses


def schema(key,periods,views):
    result=status_schema(periods);slot=result['properties']['slots']['properties'][key]
    result['properties']['slots']={'anyOf':[obj({key:slot}),obj({})]}
    queries=[]
    for view in views:
        if view.get('context_access'):
            q=query_schema(view)['items']
            queries.append(obj(dict(period_id=enum(view['period_id']),**q['properties'])))
    result['properties']['needs_period_nodes']=dict(array({'anyOf':queries}),maxItems=24) if queries else dict(array(dict(type='string')),maxItems=0)
    result['required'].append('needs_period_nodes');return result


def check_quotes(value,views):
    by_id={v['period_id']:v for v in views}
    for decision in value['slots'].values():
        for ref in decision.get('source_quotes',[]):
            view=by_id.get(ref.get('period_id'));quote=ref.get('quote');eid=ref.get('event')
            available=[] if view is None else [e['text'] for e in view['events'] if e['event']==eid or e.get('source_event')==eid]
            if view is not None:available += [e['quote'] for e in view['supplied_quotes'] if e['event']==eid]
            if not quote or not any(quote in text for text in available):
                raise ValueError('月末核对引用未实际提供的原文')


def expose_quotes(period,view):
    """原文补读精确覆盖已核引文时，才提供它的原生引用键；不重新判断本人。"""
    native={e['event']:e for e in period['unit']['events']}
    known={e['id'] for e in view['supplied_quotes']}
    def location(event):
        return (event.get('source_original_event',event.get('source_event',event['event'])),
            event.get('source_original_span',event.get('source_span',[0,len(event['text'])])))
    for proof in period['review'].get('evidence',[]):
        if proof['id'] in known:continue
        event=native[proof['event']];parent,(a,b)=location(event)
        for supplied in view['events']:
            root,(lo,hi)=location(supplied)
            if parent!=root:continue
            left,right=max(a,lo),min(b,hi)
            if left>=right:continue
            text=event['text'][left-a:right-a]
            if text==supplied['text'][left-lo:right-lo] and proof['quote'] in text:
                view['supplied_quotes'].append(copy.deepcopy(proof));known.add(proof['id']);break


def stage(periods,directories,key,start,end,root,client,receive,previous=None):
    if (len({p['unit']['user_id'] for p in periods})!=1 or any(
            moment(p['unit']['periods'][0]['start'])<moment(start)
            or moment(p['unit']['periods'][0]['end'])>moment(end) for p in periods)):
        raise ValueError('月末核对不能跨人或包含月外期间')
    item=slots(periods)[key]['item'];views,accesses=prepare(periods,directories,item,root)
    freeze(root/'input.json',dict(periods_sha256=digest(periods),slot=key,start=start,end=end,previous=previous))
    for index in range(MAX_ROUNDS+1):
        data=dict(slot=key,start=start,end=end,periods=views,previous=previous,
            limitation='完整期间签名绑定全量分母；当前引文及已补读范围不等于全部材料。缺上下文沿导航核原文，不能把未读当没有恢复。')
        if raw_size(data)>INPUT_BYTES:raise ValueError('月末有界材料超容量，原判断保留，本次不发送')
        path=root/str(index);freeze(path/'context.json',data)
        record=client.month_status_view(data,schema(key,periods,views),path/'status.receipt.json')
        value=receive(record);queries=value.get('needs_period_nodes',[])
        if not queries:
            if set(value.get('slots',{}))!={key}:raise ValueError('月末核对须覆盖且仅覆盖当前缺口')
            check_quotes(value,views)
            # 用完整原生材料核对恢复时点与既有依据；局部视图不成为分母。
            validate(periods,value,only_keys=[key]);freeze(root/'result.json',value);return value['slots'][key]
        if value.get('slots')!={} or not isinstance(queries,list) or len(queries)>24:
            raise ValueError('月末补读不能混合提交判断或超出查询限额')
        if index>=MAX_ROUNDS:raise ValueError('月末补读达到执行上限，原回执保留待核')
        grouped={}
        for query in queries:
            if not isinstance(query,dict) or set(query)!={'period_id','node_id','mode'} or query['period_id'] not in accesses:
                raise ValueError('月末补读期间、字段或来源不符')
            grouped.setdefault(query['period_id'],[]).append({k:query[k] for k in ('node_id','mode')})
        for i,view in enumerate(views):
            pid=view['period_id']
            if pid in grouped:
                views[i]=accesses[pid].expand(view,grouped[pid],str(index))
                expose_quotes(next(p for p in periods if p['row']['period_id']==pid),views[i])
                accesses[pid].remember(views[i])
    raise AssertionError('月末补读未到达终态')


def complete(periods,directories,start,end,root,client,receive):
    root=Path(root);candidate={};audit={}
    for key in slots(periods):
        directory=root/digest(key)
        candidate[key]=stage(periods,directories,key,start,end,directory/'candidate',client,receive)
        audit[key]=stage(periods,directories,key,start,end,directory/'audit',client,receive,previous=candidate[key])
    candidate=dict(slots=candidate);audit=dict(slots=audit)
    return candidate,audit,validate(periods,candidate),validate(periods,audit)
