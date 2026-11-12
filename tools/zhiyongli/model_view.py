"""完整校验材料的有界读取视图；局部已读范围不能替代整周覆盖分母。"""
import copy

from bounded_read import raw_size
from runtime import digest, moment

VERSION = 'model-view-20261111.4'
INPUT_BYTES = 120_000
SEED_BYTES = 30_000
FIELDS = ('version', 'key', 'user_id', 'title', 'evaluation_scope', 'periods', 'workday_scope')


def manifest(unit):
    return dict(version=VERSION, native_sha256=digest(unit),
        collections={name: dict(count=len(unit.get(name, [])), sha256=digest(unit.get(name, [])))
                     for name in ('events', 'person_events', 'source_keys', 'source_units', 'source_gaps', 'source_issues')},
        limitation='完整分母保存在原生材料；当前原文仅为已提供范围。同会话背景不独立证明时序因果或本人执行。导航摘要不作逐字证据，不能从局部已读推断全周没有反证。')


def create(unit, seeds=None):
    result={k: copy.deepcopy(unit[k]) for k in FIELDS if k in unit}
    result.update(model_view=manifest(unit), events=[], person_events=[], source_units=[],
        source_keys=[], reading_notes=[], source_gaps_summary=dict(
            count=len(unit.get('source_gaps', [])), sha256=digest(unit.get('source_gaps', []))))
    if seeds is None:
        # 相同来源人员、会话与原句仅延后展示，避免重复采集挤掉其他表达。
        # 不删除原生事件，也不据此认定重复人工发起；其余仍通过导航补读。
        seeds=[]
        size=0;own=set(unit['person_events']);distinct=[];deferred=[];seen=set()
        for event in unit['events']:
            if event['event'] not in own:continue
            identity=(event.get('source_user_id'),event.get('session_id'),event['text'])
            (deferred if identity in seen else distinct).append(event);seen.add(identity)
        for event in distinct+deferred:
            cost=raw_size(event)
            if size+cost>SEED_BYTES:continue
            seeds.append(event);size+=cost
        # 同一人员、会话和非空请求标识才能配对当次回复；不按相邻时间猜因果，
        # 不把随请求携带的时间未知历史当作该次回复，也不将AI行为认领为本人。
        requests={(e.get('source_user_id'),e.get('session_id'),e['request_id'])
                  for e in seeds if e.get('source_user_id') is not None
                  and str(e['source_user_id'])==str(unit['user_id'])
                  and e.get('request_id') and e.get('session_id')}
        sessions={(user,session) for user,session,_ in requests}
        replies={session:sorted([e for e in unit['events']
            if (e.get('source_user_id'),e.get('session_id'))==session
            and e.get('actor_class')=='context' and e.get('protocol_role')=='assistant'
            and e.get('timestamp_unknown') is False and e['text']],key=lambda e:moment(e['at']))
            for session in sessions}
        anchors=[]
        for event in seeds:
            # 采集位置仅用于选择先展示的背景，不作为实际发送顺序或因果证明。
            before=[e for e in replies.get((event.get('source_user_id'),event.get('session_id')),[])
                if moment(e['at'])<=moment(event['at'])]
            if before:anchors.append(before[-1])
        anchors.extend(events[-1] for events in replies.values() if events)
        context_size=0;included={e['event'] for e in seeds}
        # 先配对当次回复，再提供同一会话后续AI反馈，避免仅见开工承诺而漏交付。
        # 同会话只确定背景范围，不据相邻时间或不同请求标识推定业务因果。
        for paired,candidates in ((True,unit['events']),(False,anchors),(False,unit['events'])):
            for event in candidates:
                request=(event.get('source_user_id'),event.get('session_id'),event.get('request_id'))
                eligible=(request in requests if paired else request[:2] in sessions
                    and event.get('protocol_role')=='assistant')
                if (not eligible or event['event'] in included or event.get('actor_class')!='context'
                        or event.get('protocol_role') not in {'assistant','tool'}
                        or event.get('timestamp_unknown') is not False or not event['text']):continue
                cost=raw_size(event)
                # 整条装不下仍保留在完整来源及导航中，不裁正文、不宣称会话背景已读全。
                if context_size+cost>SEED_BYTES:continue
                seeds.append(event);included.add(event['event']);context_size+=cost
    result['events']=copy.deepcopy(seeds)
    refresh(unit,result)
    if raw_size(result)>INPUT_BYTES:raise ValueError('初始模型视图超出容量，不能裁掉原文后继续')
    return result


def refresh(unit, view):
    own=set(unit['person_events']);ids={e['event'] for e in view['events']}
    view['person_events']=sorted(own & ids)
    sources=[]
    for source in unit.get('source_units', []):
        members=[eid for eid in source['person_events'] if eid in ids]
        value={k:copy.deepcopy(source[k]) for k in (
            'key','title','purpose','boundary','boundary_reason','source_kind') if k in source}
        value.update(person_events=members, full_membership=dict(
            count=len(source['person_events']),sha256=digest(source['person_events'])))
        sources.append(value)
    view['source_units']=sources
    view['source_keys']=[s['key'] for s in sources]


def background(packet, event):
    """只接受同一冻结包的完整原文或精确连续区间，不能认领为本人。"""
    key=event['event'];original=event.get('source_event',key)
    source=next((e for e in packet['events'] if e['event']==original),None)
    if source is None or moment(source['at'])>=moment(packet['until']):
        raise ValueError('视图新增原文不在本人的截止前冻结来源')
    expected=copy.deepcopy(source)
    if key!=original:
        span=event.get('source_span',[])
        if (len(span)!=2 or any(type(n) is not int for n in span)
                or not 0<=span[0]<=span[1]<=len(source['text'])
                or key!=original+'-span-'+str(span[0])+'-'+str(span[1])):
            raise ValueError('视图新增原文区间或标识不符')
        expected.update(event=key,text=source['text'][span[0]:span[1]],source_event=original,source_span=span)
    expected.update(event_id=key,actor_class='context',human_spans=[])
    if event!=expected:raise ValueError('视图新增原文或本人归属发生变化')


def verify(unit, view, packet):
    if (str(unit['user_id'])!=str(packet['user_id']) or len(unit['periods'])!=1
            or moment(unit['periods'][0]['start'])!=moment(packet['since'])
            or moment(unit['periods'][0]['end'])!=moment(packet['until'])):
        raise ValueError('模型视图冻结来源人员或时窗不符')
    if view.get('model_view')!=manifest(unit):raise ValueError('模型视图未绑定完整原生材料及覆盖分母')
    if {k:view.get(k) for k in FIELDS}!={k:unit.get(k) for k in FIELDS}:
        raise ValueError('模型视图改变人员、时窗或计分范围')
    native={e['event']:e for e in unit['events']};ids=set()
    if len(native)!=len(unit['events']):raise ValueError('原生材料标识重复')
    for event in view['events']:
        eid=event['event']
        if eid in ids:raise ValueError('模型视图事件重复')
        ids.add(eid)
        if eid in native:
            if native[eid]!=event:raise ValueError('模型视图改写已核原文或本人归属')
        else:background(packet,event)
    expected=copy.deepcopy(view);refresh(unit,expected)
    if any(view[k]!=expected[k] for k in ('person_events','source_units','source_keys')):
        raise ValueError('模型视图改变已提供材料的本人或目标映射')
    if raw_size(view)>INPUT_BYTES:raise ValueError('模型视图超过有界读取容量')


def expose(unit, view, value):
    """节点补读同时提供重叠的已核本人原句；不凭来源user角色认领。"""
    root=value['event_id'];a,b=value['start'],value['end']
    existing={e['event']:e for e in view['events']};own=set(unit['person_events'])
    for event in unit['events']:
        if event['event'] not in own:continue
        parent=event.get('source_original_event',event.get('source_event',event['event']))
        span=event.get('source_original_span',event.get('source_span',[0,len(event['text'])]))
        if parent==root and span[0]<b and a<span[1] and event['event'] not in existing:
            view['events'].append(copy.deepcopy(event));existing[event['event']]=event
    refresh(unit,view)


def hydrate(unit, view, packet):
    """仅把已提供的冻结背景加入原生校验材料；全量本人和目标集合保持不变。"""
    verify(unit,view,packet)
    result=copy.deepcopy(unit);ids={e['event'] for e in result['events']}
    for event in view['events']:
        if event['event'] not in ids:
            result['events'].append(copy.deepcopy(event));ids.add(event['event'])
    return result


def check_review(view, review):
    """模型不能引用仅在磁盘存在、实际未提供过的原文。"""
    events={e['event']:e for e in view['events']}
    for ref in review.get('evidence',[]):
        if (ref.get('event') not in events or not ref.get('quote')
                or ref['quote'] not in events[ref['event']]['text']):
            raise ValueError('评分引用未实际提供的模型视图原文')
    for part in review.get('period_reviews',[]):
        for cell in part.get('cells',[]):
            for obj in (cell,cell.get('quality',{})):
                if not set(obj.get('missing',{}).get('checked_events',[]))<=set(events):
                    raise ValueError('缺失检查声称读取未提供的原文')
