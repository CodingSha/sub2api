"""超长候选输入的连续原文分段；执行片段不是新消息、目标或本人结论。"""
import copy
import hashlib

from bounded_read import raw_size
from runtime import digest

VERSION='candidate-segments-20261109.1'
EVENT_BYTES=24_000


def budget(packet):
    if not any(e['actor_class']=='candidate_user' and raw_size(e)>EVENT_BYTES for e in packet['events']):
        return EVENT_BYTES
    base={k:v for k,v in packet.items() if k not in {'events','reading_provenance'}}
    # 给分段说明及归属投影留空间；两份完整表达加同一份导航须能进入85KB归并请求。
    available=(85_000-raw_size(base)-2_000)//2-2_000
    if available<4096:raise ValueError('固定导航无法容纳完整候选分段及跨批比较，须先完成分层归属')
    return min(EVENT_BYTES,available)


def fragment(original,start,end,index,source_sha256):
    value=copy.deepcopy(original)
    value.update(event=original['event']+'-part-'+str(start)+'-'+str(end),
        text=original['text'][start:end],source_segment=dict(
            original_event=original['event'],source_sha256=source_sha256,
            start=start,end=end,total_characters=len(original['text']),part_index=index))
    value['text_sha256']=hashlib.sha256(value['text'].encode()).hexdigest()
    return value


def parts(event,max_bytes):
    if raw_size(event)<=max_bytes:return [copy.deepcopy(event)]
    text=event['text'];source_sha256=digest(event);start=0;result=[]
    while start<len(text):
        low=start;high=min(len(text),start+max_bytes)
        while low<high:
            stop=(low+high+1)//2
            if raw_size(fragment(event,start,stop,len(result),source_sha256))<=max_bytes:low=stop
            else:high=stop-1
        if low==start:raise ValueError('候选分段的来源元数据超出单段容量，不能裁切标识或跳过原文')
        # 优先在后半段的换行结束，避免无必要地切断句子；无换行仍保留全部字符。
        newline=text.rfind('\n',start+(low-start)//2,low)
        end=newline+1 if low<len(text) and newline>=start else low
        result.append(fragment(event,start,end,len(result),source_sha256));start=end
    if not result:raise ValueError('空文本的元数据超出候选分段容量，不能生成虚构片段')
    return result


def split(packet,max_bytes=None):
    """保持未分段事件完全相同；长候选的每个字符须且仅须出现一次。"""
    if max_bytes is None:max_bytes=budget(packet)
    if type(max_bytes) is not int or max_bytes<4096:raise ValueError('候选分段容量须至少4096字节')
    if packet.get('candidate_segmentation') or any('source_segment' in e for e in packet['events']):
        raise ValueError('候选分段必须从原始冻结阅读材料生成，禁止重复分段漂移来源')
    ids=[e['event'] for e in packet['events']]
    if len(ids)!=len(set(ids)):raise ValueError('候选分段的原消息标识重复')
    result=copy.deepcopy(packet);events=[];changed=[]
    for event in packet['events']:
        values=parts(event,max_bytes) if event['actor_class']=='candidate_user' else [copy.deepcopy(event)]
        events.extend(values)
        if len(values)>1:
            changed.append(dict(original_event=event['event'],source_sha256=digest(event),
                segments=[dict(event=e['event'],**e['source_segment']) for e in values]))
    if not changed:return result
    result['events']=events
    result['candidate_segmentation']=dict(version=VERSION,packet_sha256=digest(packet),
        max_event_bytes=max_bytes,original_candidate_messages=sum(e['actor_class']=='candidate_user' for e in packet['events']),
        candidate_fragments=sum(e['actor_class']=='candidate_user' for e in events),
        changed_source_messages=len(changed),segments_sha256=digest(changed),
        limitation='执行分段不是消息或任务数量；原角色不是本人结论，同一消息也可能含多个实际目标。必须结合其他片段及上下文核归属，切口不能产生缺陷或新目标。')
    verify(packet,result)
    return result


def verify(original,segmented):
    """检查每个事件及区间；不以校验通过宣称归属或任务语义正确。"""
    provenance=segmented.get('candidate_segmentation',{})
    if provenance.get('version')!=VERSION or provenance.get('packet_sha256')!=digest(original):
        raise ValueError('候选分段未绑定实际原始材料和合同')
    if ({k:v for k,v in segmented.items() if k not in {'events','candidate_segmentation'}}
            !={k:v for k,v in original.items() if k!='events'}):
        raise ValueError('候选分段改变人员、时窗或其他冻结上下文')
    max_bytes=provenance.get('max_event_bytes')
    if type(max_bytes) is not int or max_bytes<4096:raise ValueError('候选分段容量声明无效')
    expected={e['event']:e for e in original['events']};groups={};ids=set();source_order=[];changed=[]
    for event in segmented['events']:
        if event['event'] in ids:raise ValueError('候选分段标识重复或与原消息冲突')
        if event['actor_class']=='candidate_user' and raw_size(event)>max_bytes:
            raise ValueError('候选原文分段超过声明容量')
        ids.add(event['event']);span=event.get('source_segment')
        parent=span.get('original_event') if span else event['event']
        if parent not in expected:raise ValueError('候选分段引用其他来源')
        if not source_order or source_order[-1]!=parent:source_order.append(parent)
        groups.setdefault(parent,[]).append(event)
    if set(groups)!=set(expected):raise ValueError('候选分段遗漏原始事件')
    if source_order!=list(expected):raise ValueError('候选分段改变原消息顺序或交错来源')
    for key,source in expected.items():
        values=groups[key]
        if len(values)==1 and not values[0].get('source_segment'):
            if values[0]!=source:raise ValueError('未分段的来源被修改')
            continue
        if source['actor_class']!='candidate_user':raise ValueError('禁止将背景改为候选分段')
        offset=0;sha=digest(source)
        for index,event in enumerate(values):
            span=event.get('source_segment',{});end=span.get('end')
            if (type(end) is not int or not offset<end<=len(source['text'])
                    or event!=fragment(source,offset,end,index,sha)):
                raise ValueError('候选分段存在缺口、重叠、顺序或原文/角色变化')
            offset=end
        if offset!=len(source['text']):raise ValueError('候选分段未覆盖原消息结尾')
        changed.append(dict(original_event=source['event'],source_sha256=sha,
            segments=[dict(event=e['event'],**e['source_segment']) for e in values]))
    if (provenance.get('segments_sha256')!=digest(changed)
            or provenance.get('original_candidate_messages')!=sum(e['actor_class']=='candidate_user' for e in original['events'])
            or provenance.get('candidate_fragments')!=sum(e['actor_class']=='candidate_user' for e in segmented['events'])
            or provenance.get('changed_source_messages')!=len(changed)):
        raise ValueError('候选分段的原消息数量或区间清单签名不符')
    return True
