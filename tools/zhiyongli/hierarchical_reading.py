"""长来源自动接入分层导航；原文保留于冻结包及最终原生材料。"""
import copy

from runtime import digest


def supported(client):
    return all(callable(getattr(type(client),name,None)) for name in ('navigate_context','merge_goals'))


def compact(packet, descriptor, provenance):
    value={k:copy.deepcopy(v) for k,v in packet.items() if k not in {'events','reading_notes','reading_provenance'}}
    value['events']=[copy.deepcopy(e) for e in packet['events'] if e['actor_class']=='candidate_user']
    value['reading_notes']=[]
    value['context_navigation']=dict(root=descriptor,packet_sha256=digest(packet),
        event_count=len(packet['events']),limitation='完整来源均已建导航；摘要不作逐字依据，需沿已展示节点补读必要原文及反证。')
    value['reading_provenance']=provenance
    return value


def preflight(packet):
    from candidate_segments import split
    from goal_batches import plan
    # 导航输出上限12000字节；先用该上限检查确定性的候选分段与批次装配。
    descriptor=dict(node_id='0'*64,summary='x'*12_000)
    view=compact(packet,descriptor,dict(original_packet_sha256=digest(packet)))
    return plan(split(view))


def build(packet,notes,status,root,client,receive):
    from context_navigation import Navigation
    nav=Navigation(root/'context-navigation',packet);tree=nav.build(notes,client,receive)
    return compact(packet,nav.descriptor(tree['root']),dict(original_packet_sha256=digest(packet),
        reading_status_sha256=digest(status),navigation_tree_sha256=digest(tree),
        scope='完整来源逐页阅读后分层索引；候选原文保留，背景通过受控导航补读，不按页或会话拆任务'))


def complete_native(unit,packet):
    """完整原文留作本地校验，不意味着模型每次收到全部原文。"""
    result=copy.deepcopy(unit);ids={e['event'] for e in unit['events']}
    for original in packet['events']:
        if original['event'] in ids:continue
        event=copy.deepcopy(original);event.update(event_id=event['event'],actor_class='context',human_spans=[])
        result['events'].append(event)
    result['full_source_provenance']=dict(packet_sha256=digest(packet),event_count=len(packet['events']))
    return result
