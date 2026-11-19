"""长来源自动接入分层导航；原文保留于冻结包及最终原生材料。"""
import copy
from collections import Counter

from runtime import digest


def supported(client):
    return all(callable(getattr(type(client),name,None)) for name in ('navigate_context','merge_goals'))


def compact(packet, descriptor, provenance):
    from bounded_read import raw_size
    large_issues=raw_size(packet.get('issues',[]))>12_000
    excluded={'events','reading_notes','reading_provenance'} | ({'issues'} if large_issues else set())
    value={k:copy.deepcopy(v) for k,v in packet.items() if k not in excluded}
    value['events']=[copy.deepcopy(e) for e in packet['events'] if e['actor_class']=='candidate_user']
    value['reading_notes']=[]
    if large_issues:
        issues=packet['issues']
        value['source_issue_summary']=dict(count=len(issues),sha256=digest(issues),
            codes=dict(Counter(i['code'] for i in issues)),
            limitation='来源缺陷完整保存在冻结包及原生材料；类别均未消除，局部归属不能据此宣布来源完整。')
        value['issues']=[]
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


def navigation_notes(packet,status):
    """仅从本轮完整首读的原计划及已核检查点装配，与reading_view使用相同字段和顺序。"""
    import json
    from pathlib import Path
    if 'page_navigation_mode' in status:
        from page_navigation import inputs
        return inputs(packet,status)[0]
    if (status.get('state')!='reading_complete_pending_reconciliation'
            or status.get('packet_sha256')!=digest(packet)
            or status.get('received_pages')!=list(range(status['pages']))):
        raise ValueError('导航前缀须先完成当前来源全部阅读校验')
    directory=Path(status['evidence_directory'])
    manifest=json.loads((directory/'plan.json').read_text())
    if manifest['packet_sha256']!=digest(packet) or len(manifest['pages'])!=status['pages']:
        raise ValueError('导航前缀阅读计划与当前来源不符')
    events={e['event']:e for e in packet['events']};notes=[]
    for index,page in enumerate(manifest['pages']):
        checked=json.loads((directory/(str(index)+'.checked.json')).read_text())['value']['notes']
        for fragment in page['fragments']:
            note=checked[fragment['fragment_id']];event=events[fragment['event_id']]
            notes.append(dict(event_id=event['event'],start=fragment['start'],end=fragment['end'],
                summary=note['summary'],limitations=note['limitations'],open_questions=note['open_questions']))
    if 'reading_strategy' in manifest:
        from reading_efficiency import procedural_notes
        notes.extend(procedural_notes(packet,manifest,notes))
    return notes


def build(packet,notes,status,root,client,receive):
    from context_navigation import Navigation
    page_groups=None
    if 'page_navigation_mode' in status:
        from page_navigation import inputs
        notes,page_groups=inputs(packet,status)
    nav=Navigation(root/'context-navigation',packet,page_navigation=page_groups is not None)
    tree=nav.build(notes,client,receive,page_groups=page_groups)
    scope='完整来源逐页阅读后分层索引；候选原文保留，背景通过受控导航补读，不按页或会话拆任务'
    if status.get('reading_strategy'):
        scope='唯一非空背景正文及全部候选输入已逐页阅读；零字符及同文背景逐次程序留痕，原文均可补读；索引排列不证明实际先后，正文复用不继承归属、因果或结果判断'
    provenance=dict(original_packet_sha256=digest(packet),
        reading_status_sha256=digest(status),navigation_tree_sha256=digest(tree),
        scope=scope)
    if status.get('reading_strategy'):provenance['reading_strategy']=status['reading_strategy']
    if 'page_navigation_mode' in status:provenance['page_navigation_mode']=status['page_navigation_mode']
    return compact(packet,nav.descriptor(tree['root']),provenance)


def complete_native(unit,packet):
    """完整原文留作本地校验，不意味着模型每次收到全部原文。"""
    result=copy.deepcopy(unit)
    result['source_issues']=copy.deepcopy(packet.get('issues',[]))
    ids={e['event'] for e in unit['events']}
    for original in packet['events']:
        if original['event'] in ids:continue
        event=copy.deepcopy(original);event.update(event_id=event['event'],actor_class='context',human_spans=[])
        result['events'].append(event)
    result['full_source_provenance']=dict(packet_sha256=digest(packet),event_count=len(packet['events']))
    return result
