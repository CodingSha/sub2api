"""完整自然周评审编排；每阶段持久化，来源/判断问题不得转换为低分。"""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from runtime import atomic, digest, response_text, response_value, validate_review, summarize_month, VERSION, RUBRIC, policy, moment
from source_frames import frames, read_signed
from protocol_events import extract
from task_identification import canonical, validate as validate_identified
from export_results import convert
from qualify_results import qualify_bundle

TZ=timezone(timedelta(hours=8))
# 工程请求预算，非评分门槛。真实262144-token端点曾拒绝约75万token输入。
DIRECT_BYTES=120_000
# 复查分歧后按逐项意见自动修订的轮数上限；用尽仍有分歧则保留待人工核对。属执行上限，不是评分门槛。
AUTO_REVISIONS=2

class Paused(ValueError):
    def __init__(self, state, message):
        super().__init__(message); self.state=state

def received(record):
    if record['status']=='budget_paused':
        raise Paused('needs_review','DSH在发送前达到请求预算；原会话和回执已保留，须显式核预算续接，页面恢复不会重新发请求')
    if record['status'] in {'requesting','unknown'}:
        raise Paused('unknown','模型请求结果不明，已保留回执；需核对后处理，不自动重发')
    if record['status']!='received':
        raise Paused('needs_review','模型请求被明确拒绝；已保留回执，不自动重发')
    return json.loads(response_text(record['response']))

def source_display_order(event):
    """同采集时刻按审计行及请求组织展示；同帧内部保留协议数组顺序。"""
    frame=event['source']['frame'].split(':')
    # 非空请求标识才能配对；空标识保留独立帧，不能合成一次调用。
    request=event.get('request_id') or ':'.join(frame[2:])
    return moment(event['at']),int(frame[1]),request,frame[2]=='response'

def prepare(source_root, user_id, start, end,workday_scope=None,preparation=False,preparation_until=None):
    latest=json.loads((Path(source_root)/'latest.json').read_text())
    snapshot=read_signed(Path(source_root),latest)
    user=next((u for u in snapshot['users'] if u['id']==user_id),None)
    if not user: raise Paused('failed','来源账户不存在或已排除审计')
    if workday_scope is not None:
        from workday_scope import validate
        validate(workday_scope)
        if (workday_scope['period_start'],workday_scope['period_end'])!=(start[:10],end[:10]):raise ValueError('工作日范围与来源时窗不同')
    required=workday_scope['freeze_at'] if workday_scope else end
    cutoff=workday_scope['observed_until'] if workday_scope else end
    if preparation:
        if workday_scope is None or not preparation_until:raise ValueError('日常准备须有冻结范围和实际已结束的日期')
        cutoff=min(moment(cutoff),moment(preparation_until),moment(snapshot['snapshot_at'])).isoformat()
    if not preparation and moment(snapshot['snapshot_at'])<moment(required):
        raise Paused('failed','来源快照尚未覆盖评价截止时刻')
    if snapshot.get('since') and moment(snapshot['since'])>moment(start):
        raise Paused('needs_review','来源缓存的采集起点晚于本期开始，缺失日期不能冒称完整周材料')
    if snapshot.get('until') and moment(snapshot['until'])<moment(cutoff):
        raise Paused('needs_review','来源缓存的采集终点早于本期截止，快照保存时间不能替代采集范围')
    own=copy.deepcopy(snapshot)
    # 先按签名查所属人员；不会把其他人员正文装配入模型。
    own['rows']=[r for r in snapshot['rows'] if read_signed(Path(source_root),r)['user_id']==user_id]
    framed=frames(source_root,own)
    def group(frame):
        return frame.get('session_id') or 'audit:'+frame['frame'].split(':')[1]
    active={group(f) for f in framed['frames'] if moment(start)<=moment(f['at'])<moment(cutoff)}
    framed['frames']=[f for f in framed['frames'] if group(f) in active and moment(f['at'])<moment(cutoff)]
    frame_ids={f['frame'] for f in framed['frames']}
    framed['issues']=[i for i in framed['issues'] if i['frame'] in frame_ids]
    packet=extract(framed)
    if workday_scope is not None:packet['workday_scope']=copy.deepcopy(workday_scope)
    if preparation:packet.update(preparation_only=True,preparation_observed_until=cutoff)
    for event in packet['events']:
        # 产品来源标识只允许字母、数字、冒号、下划线和连字符；原指针另存。
        event['source']['original_event']=event['event']
        event['event']='audit-'+digest(event['event'])[:32]
    # 历史同文只去掉时间未知的副本，真实再次发送不去重。
    known={(e['session_id'],e['text']) for e in packet['events'] if e['actor_class']=='candidate_user' and not e['timestamp_unknown']}
    for e in packet['events']:
        if e['actor_class']=='candidate_user' and not e['timestamp_unknown'] and moment(e['at'])<moment(start):
            e['actor_class']='context'
        if e['actor_class']=='candidate_user' and e['timestamp_unknown'] and (e['session_id'],e['text']) in known:
            e['actor_class']='context'
        if (workday_scope is not None and e['actor_class']=='candidate_user' and not e['timestamp_unknown']
            and moment(e['at']).astimezone(TZ).date().isoformat() not in workday_scope['working_dates']):
            e.update(actor_class='context',workday_exclusion='non_workday_context_only')
    # 完整provider历史可能随每次请求重复携带；仅合并时间未知的同会话、同角色、同文背景。
    # 本次真实提交（时间已知）无论同文多少次均保留。
    history={};retained=[]
    for e in packet['events']:
        if e['timestamp_unknown']:
            key=(e['session_id'] or e['source']['frame'].split(':')[1],e['protocol_role'],e['text'])
            if key in history:
                history[key].setdefault('duplicate_source_events',[]).append(e['event']);continue
            history[key]=e
        retained.append(e)
    packet['events']=retained
    packet.update(user_id=str(user_id),user_email=user['email'],since=start,until=end,
        source_scope='full_person_window',source_snapshot=latest)
    # 稳定排序不能用哈希事件ID打散同请求的输入/返回或协议内原有顺序。
    # 这只是展示顺序，不能据此推断耗时、历史发送先后或跨请求的因果关系。
    packet['events'].sort(key=source_display_order)
    return packet

def reading_preflight(packet,client):
    """日常准备与封口共用容量检查；已知下游不能装配时均不得先付费阅读。"""
    from bounded_read import raw_size,plan
    from hierarchical_reading import supported, preflight
    if supported(client) and (raw_size(packet)>DIRECT_BYTES
            or any(raw_size(e)>85_000 for e in packet['events'] if e['actor_class']=='candidate_user')):
        try:preflight(packet)
        except ValueError as ex:raise Paused('needs_review','分层归属的确定性装配超容量，本次未发送阅读请求') from ex
        return
    candidates=[e for e in packet['events'] if e['actor_class']=='candidate_user']
    if raw_size(packet)<=DIRECT_BYTES:
        if any(raw_size(e)>85_000 for e in candidates):
            raise Paused('needs_review','单条候选输入的分段归属已有合同，但整周有界综合尚未接通；本次未发送模型请求，原文完整保留')
        return
    if raw_size(dict(events=candidates))>DIRECT_BYTES and not callable(getattr(client,'merge_goals',None)):
        # 当前尚无分批归属/全局归并执行器，先拦住已知必然失败的后续整包识别。
        raise Paused('needs_review','完整候选本人输入单独已超过单次容量，须先分批核归属及跨页归并；原文已保留，本次未发送长材料阅读请求')
    # 当前分批合同仍要求完整候选事件不切断；已知单条不可能装入时不要先付费读背景。
    from goal_batches import plan as identification_plan
    try:identification_plan({k:v for k,v in dict(packet,events=candidates).items() if k not in {'reading_notes','reading_provenance'}})
    except ValueError as ex:
        raise Paused('needs_review','单条候选输入超出当前完整归属容量；须补分段本人归属合同，本次未发送阅读请求，原文未截断') from ex
    reading=plan(packet);pages=len(reading['pages'])
    # 即使摘要为空，固定来源索引也可能超过后续全局归属合同；先查确定性下界，
    # 不能先付费读完几千页，之后才发现下一阶段无论如何都无法装配。
    minimum_notes=[dict(event_id=f['event_id'],start=f['start'],end=f['end'],summary='',limitations=[],open_questions=[])
        for page in reading['pages'] for f in page['fragments']]
    minimum_view={k:v for k,v in dict(packet,events=candidates,reading_notes=minimum_notes).items() if k!='reading_provenance'}
    try:identification_plan(minimum_view)
    except ValueError as ex:
        raise Paused('needs_review','全局阅读导航的固定来源索引已超容量，须先完成分层归并合同；本次未发送阅读请求，原文全部保留') from ex


def reading_view(packet,root,client,progress,force_hierarchical=False):
    """长来源逐页全读；保留全部候选输入、各页摘要和逐字依据，原文仍在冻结包。"""
    from bounded_read import raw_size,read_all,plan
    from hierarchical_reading import supported
    hierarchical=supported(client) and (force_hierarchical or raw_size(packet)>DIRECT_BYTES
        or any(raw_size(e)>85_000 for e in packet['events'] if e['actor_class']=='candidate_user'))
    if raw_size(packet)<=DIRECT_BYTES and not hierarchical:return packet
    reading_preflight(packet,client)
    pages=len(plan(packet)['pages'])
    progress('reading',0,pages,'逐页读取全部已保存内容；不裁掉长对话')
    status=read_all(packet,root/'reading',client=client,limit=pages,
        on_page=lambda done,total:progress('reading',done,total,'阅读页已落盘并核对引文，原文完整保留'))
    progress('reading',len(status['received_pages']),pages,'已收到的阅读页已逐字验引文')
    if status['state']=='reading_blocked':
        if any(b['state']=='unknown' for b in status['blocked']):raise Paused('unknown','来源阅读请求结果不明；保留已完成页，不自动重发')
        raise Paused('needs_review','部分来源阅读未通过校验，已完成页保留')
    if len(status['received_pages'])!=pages:raise Paused('needs_review','来源阅读未完成，不能进入整周评审')
    events={e['event']:e for e in packet['events']};notes=[];excerpts={}
    directory=Path(status['evidence_directory']);manifest=json.loads((directory/'plan.json').read_text())
    for index,page in enumerate(manifest['pages']):
        checked=json.loads((directory/(str(index)+'.checked.json')).read_text())['value']['notes']
        for fragment in page['fragments']:
            note=checked[fragment['fragment_id']];event=events[fragment['event_id']]
            notes.append(dict(event_id=event['event'],start=fragment['start'],end=fragment['end'],
                summary=note['summary'],limitations=note['limitations'],open_questions=note['open_questions']))
            if hierarchical or event['actor_class']=='candidate_user':continue
            for fact in note['facts']:
                a,b=fact['source_start'],fact['source_end'];eid=event['event']+'-span-'+str(a)+'-'+str(b)
                excerpt=copy.deepcopy(event);excerpt.update(event=eid,text=event['text'][a:b],
                    source_event=event['event'],source_span=[a,b]);excerpts[eid]=excerpt
    if hierarchical:
        from hierarchical_reading import build
        view=build(packet,notes,status,root,client,received)
        atomic(root/'reading-view.json',view)
        return view
    view=copy.deepcopy(packet)
    view['events']=[e for e in packet['events'] if e['actor_class']=='candidate_user']+list(excerpts.values())
    view['reading_notes']=notes
    view['reading_provenance']=dict(original_packet_sha256=digest(packet),reading_status=status,
        scope='逐页全读后的上下文摘要与逐字原文片段；候选用户输入完整保留，摘要不冒充原文证据')
    atomic(root/'reading-view.json',view)
    if raw_size(view)>DIRECT_BYTES and not callable(getattr(client,'merge_goals',None)):
        raise Paused('needs_review','全部来源已分段阅读，但完整候选本人输入及阅读记录仍超过单次容量；须先分批核归属和跨页归并，不裁掉输入或强发整周请求')
    return view

def assemble(packet, identified):
    checked=validate_identified(packet,identified)
    events=[];personal={}
    lo,hi=moment(packet['since']),moment(packet['until'])
    for original in packet['events']:
        event=copy.deepcopy(original); event.update(event_id=original['event'],actor_class='context',human_spans=[]); events.append(event)
    material={m['event_id']:m for m in checked['materials']}
    for original in packet['events']:
        m=material.get(original['event'])
        if not m or m['classification']!='human' or original['timestamp_unknown'] or not lo<=moment(original['at'])<hi: continue
        personal[original['event']]=[]
        for index,(a,b) in enumerate(m['human_spans']):
            event=copy.deepcopy(original); eid=original['event']+'-human-'+str(index)
            event.update(event=eid,event_id=eid,text=original['text'][a:b],actor_class='human',human_spans=[[0,b-a]],
                source_event=original['event'],source_span=[a,b])
            if original.get('source_segment'):
                segment=original['source_segment']
                event.update(source_original_event=segment['original_event'],
                    source_original_span=[segment['start']+a,segment['start']+b],source_original_sha256=segment['source_sha256'])
            events.append(event);personal[original['event']].append(eid)
    period=dict(period_id='week-'+lo.date().isoformat(),start=lo.isoformat(),end=hi.isoformat(),kind='weekly')
    sources=[];assigned=set()
    for goal in checked['goals']:
        ids=[p for eid in goal['person_events'] for p in personal.get(eid,[])]
        if not ids: continue
        assigned.update(ids)
        sources.append(dict(key=packet['user_id']+':'+period['period_id']+':goal-'+digest(goal['goal_id'])[:16],
            title=goal['title'],purpose=goal['purpose'],boundary=goal['boundary'],boundary_reason=goal['boundary_reason'],
            source_kind='task' if goal['boundary']=='confirmed' else 'local_behavior',
            person_events=ids,source_gaps=[],periods=[period]))
    # 不能识别父目标、非任务交流均保留；局部本人表达仍可评，不设置任务数门槛。
    remaining=sorted(p for ids in personal.values() for p in ids if p not in assigned)
    if remaining:
        sources.append(dict(key=packet['user_id']+':'+period['period_id']+':local',title='保留的局部本人表达',
            purpose='未关联完整目标的表达按上下文判断，不自动扣分',boundary='unresolved',source_kind='local_behavior',person_events=remaining,source_gaps=[],periods=[period]))
    if not sources: raise Paused('needs_review','本期没有已确认的本人输入；需核对非任务交流、归属未知和无来源状态')
    by_id={e['event']:e for e in packet['events']}
    # 手册1.6：归属未决与表现分开，已核可评范围照常评审，未决部分不计分、不补数，在报告中披露。
    unresolved=[dict(event_id=m['event_id'],reason=m['reason']) for m in checked['materials']
        if m['classification']=='unresolved' and by_id[m['event_id']]['actor_class']=='candidate_user'
        and not by_id[m['event_id']]['timestamp_unknown'] and lo<=moment(by_id[m['event_id']]['at'])<hi]
    scope='person_week' if hi-lo==timedelta(days=7) and lo.weekday()==0 else 'person_week_fragment'
    if scope=='person_week_fragment': period['period_id']='fragment-'+lo.date().isoformat()
    result=dict(version=VERSION,key=packet['user_id']+':'+period['period_id'],user_id=packet['user_id'],
        title='自然周上下文评审' if scope=='person_week' else '月内自然周片段评审',evaluation_scope=scope,periods=[period],events=events,
        person_events=sorted(p for ids in personal.values() for p in ids),source_units=sources,
        source_keys=[s['key'] for s in sources],source_gaps=[],
        source_issues=packet['issues'],reading_notes=packet.get('reading_notes',[]),
        reading_provenance=packet.get('reading_provenance'),
        identification_provenance=dict(packet_sha256=digest(packet),identified_sha256=digest(identified)))
    if packet.get('workday_scope') is not None:result['workday_scope']=copy.deepcopy(packet['workday_scope'])
    if unresolved:result['attribution_unresolved']=unresolved
    if packet.get('candidate_segmentation') is not None:
        result['candidate_segmentation']=copy.deepcopy(packet['candidate_segmentation'])
    return result

class Disputed(Paused):
    """候选未通过另次语义复查或程序校验；有界自动修订用尽后才交人工核对。"""
    def __init__(self,message='语义复查仍存在判断分歧，各轮候选与复查意见已保留；需实际核对，不自动改分'):
        super().__init__('needs_review',message)


def contract_failure(root,stage,ex,client):
    """评分或复查输出未过确定性校验：记下校验原因供修订轮改正；原回执不动。不支持修订的执行器维持原异常。"""
    if not getattr(client,'accepts_revision_feedback',False):raise ex
    atomic(Path(root)/'contract-error.json',dict(stage=stage,error=str(ex)[:2000]))
    raise Disputed(('评分' if stage=='review' else '语义复查')+'输出未通过程序校验：'+str(ex)[:300]+'；原回执保留，需实际核对') from None


def revision_round(root):
    path=Path(root)/'revision.json'
    return json.loads(path.read_text()).get('automatic_round',0) if path.exists() else 0


def revision_feedback(root):
    """自动修订轮把前一候选和逐项复查意见作为待核材料交给评分；人工发起的修订不带入。"""
    if not revision_round(root):return None
    parent=Path(json.loads((Path(root)/'revision.json').read_text())['parent_directory'])
    def load(name):
        path=parent/name
        return json.loads(path.read_text()) if path.exists() else None
    review,audit,error=load('model-review.json'),load('model-audit.json'),load('contract-error.json')
    # 复查已支持的分项不重复携带理由和引文，修订请求只带需要重新核对的意见；输出不可解析时没有前序候选。
    return dict(round=revision_round(root),
        previous_review=dict(evidence=review['value'].get('evidence',[]),period_reviews=review['value'].get('period_reviews',[])) if review else None,
        audit=dict(items={k:v for k,v in audit['value']['items'].items()
                if v['verdict']!='supported' or k in audit.get('state_mismatches',[])},
            global_issues=audit['value']['global_issues']+audit.get('claim_conflicts',[])) if audit else None,
        contract_error=error)


def auto_revise(base,current,client):
    """按另次复查的逐项意见开一轮新修订；不是换随机结果重评，原候选、复查回执和前序文件签名全部保留。"""
    done=revision_round(current)
    if done>=AUTO_REVISIONS or not getattr(client,'accepts_revision_feedback',False):return False
    from review_control import revise
    target=revise(base,'自动修订（同模型复查意见驱动）',
        '语义复查存在逐项分歧，按复查意见进行第'+str(done+1)+'轮有界修订；前序候选与复查回执保留',automatic_round=done+1)
    # 评分之前的目标识别按相同请求复用原回执，修订轮只重新评分和复查。
    import os
    current=Path(current).resolve()
    for source in sorted(current.rglob('*.receipt.json')):
        relative=source.relative_to(current)
        if relative.parts[0]=='identification' or len(relative.parts)==1 and relative.name.startswith('identify.'):
            (target/relative).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            os.link(source,target/relative)
    return True


def evaluate_week(packet, directory, client, progress=lambda *args:None,shared_root=None):
    from review_control import selected
    while True:
        current=selected(directory)
        try:return _shared_week(packet,current,client,progress,shared_root)
        except Disputed:
            if not auto_revise(directory,current,client):raise
            progress('reviewing',0,1,'按复查意见或程序校验结果自动修订；前序候选保留')


def _shared_week(packet, directory, client, progress,shared_root):
    if shared_root is None or not packet['events']:return _evaluate_week(packet,directory,client,progress)
    from shared_weeks import slot
    with slot(shared_root,packet,directory) as result:
        if result is not None:
            progress('checking',1,1,'已复用同来源、同合同的已核周评，未重复调用模型')
            return result
        return _evaluate_week(packet,directory,client,progress)


def _evaluate_week(packet, directory, client, progress):
    """真实模型识别、评分与另次语义复查；未核通过只留候选，不导入。"""
    root=Path(directory);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if packet.get('preparation_only'):raise Paused('needs_review','日常准备材料尚未封口，只可建立阅读导航，禁止生成周分')
    stamp=dict(packet_sha256=digest(packet),policy_sha256=policy())
    path=root/'input.json'
    if path.exists() and json.loads(path.read_text())!=stamp:
        raise Paused('needs_review','冻结来源或评审合同发生变化，旧回执不能自动升级；需明确修订')
    atomic(path,stamp); atomic(root/'packet.json',packet)
    current=[e for e in packet['events'] if e['actor_class']=='candidate_user' and not e['timestamp_unknown']
        and moment(packet['since'])<=moment(e['at'])<moment(packet['until'])]
    if not current:
        if packet.get('workday_scope') is not None and not packet['workday_scope']['working_dates']:
            raise Paused('no_activity','该期间为日历非计分期间；行为仅作背景，不计员工无活动或周分')
        uncertain=bool(packet['issues'] or packet['events'])
        raise Paused('needs_review' if uncertain else 'no_activity',
            '未找到本期可确认输入；已有上下文或来源问题需核对' if uncertain else '封口来源已核，本期未采集到可定位输入')
    reading_preflight(packet,client)
    if callable(getattr(client,'preflight_minimum',None)):
        from bounded_read import plan
        pages=len(plan(packet)['pages']) if len(json.dumps(packet,ensure_ascii=False).encode())>DIRECT_BYTES else 0
        client.preflight_minimum(pages+3)
    frozen_packet=packet
    packet=reading_view(packet,root,client,progress)
    from candidate_segments import split as split_candidates
    packet=split_candidates(packet)
    if packet.get('candidate_segmentation'):
        atomic(root/'candidate-segments.json',packet['candidate_segmentation'])
    progress('identifying',0,1,'结合完整来源识别目标和本人表达')
    from bounded_read import raw_size
    packet,identified=identify_packet(packet,frozen_packet,root,client,progress)
    atomic(root/'identified.json',identified)
    unit=assemble(packet,identified)
    if packet.get('context_navigation'):
        from hierarchical_reading import complete_native
        unit=complete_native(unit,frozen_packet)
    elif raw_size(unit)>DIRECT_BYTES:
        # 模型给出的归属元数据也可能使小来源装配变大；沿同一冻结来源补建导航。
        reading_view(frozen_packet,root,client,progress,force_hierarchical=True)
    atomic(root/'unit.json',unit)
    return evaluate_unit(frozen_packet,unit,root,client,progress)


def identify_packet(packet,frozen_packet,root,client,progress):
    from bounded_read import raw_size
    if packet.get('context_navigation'):
        from goal_batches import identify
        from context_navigation import Navigation
        from navigation_access import Access
        nav=Navigation(root/'context-navigation',frozen_packet)
        identified=identify(packet,root/'identification',client,received,progress,
            access_factory=lambda i:Access(nav,root/'identification/navigation-access'/str(i)))
        from model_view import background
        for event in identified.get('readback_context_events',[]):background(frozen_packet,event)
        packet=copy.deepcopy(packet);packet['events'].extend(identified.get('readback_context_events',[]))
    elif raw_size(packet)>85000:
        from goal_batches import identify
        identified=identify(packet,root/'identification',client,received,progress)
    else:
        from task_identification import checked
        identified=checked(client,packet,root/'identify.receipt.json',received,lambda value:canonical(packet,value))[2]
    return packet,identified

def review_materials(packet,unit,root,client,progress,feedback=None):
    """按完整材料签名建立模型视图，回填时仅增加冻结背景。"""
    from context_readback import complete
    from shared_weeks import reference
    from bounded_read import raw_size
    projected=raw_size(unit)>DIRECT_BYTES
    native=unit
    if projected:
        from model_view import create
        if not (root/'context-navigation/tree.json').exists():
            raise Paused('needs_review','完整材料超过单次容量且没有完整来源导航；本次不发送评分请求')
        from navigation_access import freeze
        unit=create(native)
    access=None
    if (root/'context-navigation/tree.json').exists():
        from context_navigation import Navigation
        from navigation_access import Access
        access=Access(Navigation(root/'context-navigation',packet),root/'navigation-access',native=native if projected else None)
    unit,record,receipt=complete(unit,packet,root,client,received,progress,navigation_access=access,feedback=feedback)
    view=unit
    if projected:
        from model_view import hydrate
        freeze(root/'model-view/score-context.json',view)
        unit=hydrate(native,view,packet)
        native_ids={e['event'] for e in native['events']}
        freeze(root/'model-view/native-input.json',dict(native_sha256=digest(native),
            appended_event_ids=[e['event'] for e in unit['events'] if e['event'] not in native_ids]))
    atomic(root/'unit.json',unit)
    pointer=dict(unit_sha256=digest(unit),receipt=reference(receipt))
    if projected:pointer.update(native_input=reference(root/'model-view/native-input.json'),
        model_context=reference(root/'model-view/score-context.json'))
    atomic(root/'current-model-review.json',pointer)
    return unit,view,record,projected,access


def audit_materials(packet,unit,view,review,root,client,progress,projected,access):
    from audit_readback import complete
    audit_view=None
    if projected:
        from model_view import create
        from navigation_access import Access
        from context_navigation import Navigation
        # 另次复查从相同原文起点独立选择补读，完整分母仍绑定最终原生材料。
        audit_view=create(unit,seeds=view['events'])
        access=Access(Navigation(root/'context-navigation',packet),root/'audit-navigation-access',native=unit)
    return complete(unit,review,packet,root,client,received,progress,access=access,model_context=audit_view)


def evaluate_unit(packet,unit,root,client,progress):
    """同一固定判据处理完整周或月内片段；不重新识别已核目标。"""
    progress('reviewing',0,1,'按1.6上下文合同评审12个分项')
    unit,view,record,projected,access=review_materials(packet,unit,root,client,progress,revision_feedback(root))
    from review_control import resolution
    resolved=resolution(root,unit)
    if resolved:
        review,audit,proof=resolved
        adaptation=dict(version='explicit-review-resolution',resolution=proof,changes_evidence=True,changes_scores=True)
    else:
        received(record)
        try:
            review,adaptation=response_value(unit,record)
            atomic(root/'model-review.json',dict(value=review,adaptation=adaptation))
            if projected:
                from model_view import check_review
                check_review(view,review)
            validate_review(unit,review)
        except (ValueError,KeyError,TypeError) as ex:contract_failure(root,'review',ex,client)
    rows=validate_review(unit,review)
    atomic(root/'review.json',dict(value=review,adaptation=adaptation,rows=rows))
    progress('checking',0,1,'核对引用、归属、算术与判断对应关系')
    if not resolved:
        from semantic_audit import ContractError
        try:audit=audit_materials(packet,unit,view,review,root,client,progress,projected,access)
        except ContractError as ex:
            # 复查格式失败不能触发重新评分，否则会丢失尚未形成有效回执的异议。
            atomic(root/'contract-error.json',dict(stage='audit',error=str(ex)[:2000]))
            raise Paused('needs_review','语义复查格式修复未通过；评分候选及全部异议保留，不自动重新评分：'+str(ex)[:300]) from None
        atomic(root/'model-audit.json',audit)
    atomic(root/'audit.json',audit)
    if not audit['supported']:
        raise Disputed()
    if unit['evaluation_scope']=='person_week_fragment':
        return None
    entry=dict(user_email=packet['user_email'],source_user_id=packet['user_id'],period_type='week',
        period_start=packet['since'][:10],period_end=packet['until'][:10],evidence_checked=True,
        periods=[dict(unit={'name':'unit'},review={'name':'review'})])
    report,_=convert(root,entry,lambda ref:unit if ref['name']=='unit' else review)
    report['limitations'].append(('模型候选经'+proof['reviewer_kind']+'有理由逐项处置，原模型与复查记录保留；'
        if resolved else 'AI自动评审并经同模型另次语义检查'+('，此前'+str(revision_round(root))+'轮候选已按复查意见自动修订，各轮记录保留；'
            if revision_round(root) else '；'))+'未经独立真人校准，发布前由管理员核对。'
        +('复查另附'+str(len(audit['value']['remarks']))+'条不要求改动候选的说明，见复查记录。' if not resolved and audit['value'].get('remarks') else ''))
    if unit.get('attribution_unresolved'):
        report['limitations'].append('本期有'+str(len(unit['attribution_unresolved']))+'段以用户身份提交的输入未能确认是否为本人表达'
            '（如打包材料、工具输出或编码数据），未计入评分、不补分；原文与判断理由保留，可在评审记录中核对。')
    atomic(root/'native-report.json',report)
    result=qualify_bundle(dict(schema_version=1,source_name='自动评审1.6:'+unit['key'],reports=[report]))
    atomic(root/'bundle.json',result)
    return result

def month_fragment(unit,start,end):
    """沿用已核整周的本人片段和目标身份；月外输入改为背景，月末之后完全不送入。"""
    result=copy.deepcopy(unit);lo,hi=moment(start),moment(end)
    # 整周导航摘要可能包含片段截止之后的内容；片段须从自己的冻结来源另建导航。
    result.pop('context_access',None)
    result.pop('candidate_segmentation',None)
    if unit.get('workday_scope') is not None:
        from workday_scope import window,load
        result['workday_scope']=window(start[:10],end[:10],load(expected=unit['workday_scope']['registry_sha256']))
    result['key']=unit['key']+':fragment-'+lo.date().isoformat()
    result['evaluation_scope']='person_week_fragment'
    result['periods']=[dict(period_id='fragment-'+lo.date().isoformat(),start=start,end=end,kind='weekly')]
    result['events']=[e for e in result['events'] if moment(e['at'])<hi]
    own=set()
    for event in result['events']:
        if event['actor_class']=='human':
            if lo<=moment(event['at']):own.add(event['event'])
            else:event.update(actor_class='context',human_spans=[])
    result['person_events']=sorted(own)
    for source in result['source_units']:
        source['person_events']=[eid for eid in source['person_events'] if eid in own]
        source['periods']=copy.deepcopy(result['periods'])
        # 完整周的文字概括可能含月末后的新要求；片段只能从当时原文重建。
        source.update(title='沿用目标来源索引',purpose='只按本片段截止前原文还原有效目标及阶段',
            boundary_reason='沿用整周归属标识；本片段目标范围须从截止前原文重新核对')
    result['source_units']=[s for s in result['source_units'] if s['person_events']]
    result['source_keys']=[s['key'] for s in result['source_units']]
    retained={e['event'] for e in result['events']}
    result['reading_notes']=[n for n in result.get('reading_notes',[]) if n['event_id'] in retained]
    frames={e.get('source',{}).get('frame') for e in result['events']}
    result['source_issues']=[i for i in result.get('source_issues',[]) if i['frame'] in frames]
    result['week_provenance']=dict(unit_sha256=digest(unit),range=[start,end])
    return result

def evaluate_month(source_root,user_id,start,end,directory,client,progress=lambda *args:None,shared_root=None,workday_scope=None):
    """完整周保证资格，首尾片段单独判断；月分只走024/025确定性计算。"""
    from month_evidence import freeze_empty,audited_empty
    root=Path(directory);a,b=moment(start),moment(end)
    cursor=a-timedelta(days=a.weekday());weeks=[];period_refs=[];period_values={};native=[];directories={}
    def read(path):return json.loads(path.read_text())
    while cursor<b:
        next_week=cursor+timedelta(days=7)
        week_root=root/'weeks'/cursor.date().isoformat()
        extra={}
        if workday_scope is not None:
            from workday_scope import window,load
            extra['workday_scope']=window(cursor.date().isoformat(),next_week.date().isoformat(),load(expected=workday_scope['registry_sha256']))
        packet=prepare(source_root,user_id,cursor.isoformat(),next_week.isoformat(),**extra)
        try:
            evaluate_week(packet,week_root,client,progress,shared_root=shared_root)
        except Paused as ex:
            if ex.state!='no_activity':raise
            # 真正空周已核封口，继续其他周；无本人归属/来源问题仍不能伪装为空。
            freeze_empty(packet,week_root)
        else:
            from shared_weeks import resolve
            week_root=resolve(week_root)
            weeks.append(read(week_root/'native-report.json'))
        lo,hi=max(a,cursor),min(b,next_week)
        target=week_root
        fragment=packet
        if (lo,hi)!=(cursor,next_week):
            target=root/'fragments'/lo.date().isoformat()
            if workday_scope is not None:
                extra['workday_scope']=window(lo.date().isoformat(),hi.date().isoformat(),load(expected=workday_scope['registry_sha256']))
            fragment=prepare(source_root,user_id,lo.isoformat(),hi.isoformat(),**extra)
        if (not fragment['events'] and not fragment['issues']) or fragment.get('workday_scope') is not None and not fragment['workday_scope']['working_dates']:
            empty=freeze_empty(fragment,target)
            key=str(len(period_refs));period_values[key+'e']=empty
            period_refs.append(dict(empty=dict(key=key+'e')))
            u,v,row=audited_empty(empty);native.append(dict(unit=u,review=v,row=row));directories[row['period_id']]=target
            cursor=next_week;continue
        if target!=week_root:
            target=evaluate_fragment(fragment,read(week_root/'unit.json'),lo,hi,target,client,progress)
        unit=read(target/'unit.json');review=read(target/'review.json')['value']
        key=str(len(period_refs));period_values[key+'u']=unit;period_values[key+'r']=review
        period_refs.append(dict(unit=dict(key=key+'u'),review=dict(key=key+'r')))
        row=validate_review(unit,review)[0];row['evidence_checked']=True
        native.append(dict(unit=unit,review=review,row=row));directories[row['period_id']]=target
        cursor=next_week
    if not weeks:
        raise Paused('no_activity','全月封口来源核查无可评周；保留逐期间空来源回执，不补零或生成月评级')
    dispositions=month_dispositions(native,directories,start,end,root,client,progress)
    return export_month(user_id,start,end,root,weeks,native,period_refs,period_values,dispositions,workday_scope)


def evaluate_fragment(fragment,unit,lo,hi,target,client,progress):
    from review_control import selected
    while True:
        current=selected(target)
        try:return _evaluate_fragment(fragment,unit,lo,hi,current,client,progress)
        except Disputed:
            if not auto_revise(target,current,client):raise


def _evaluate_fragment(fragment,unit,lo,hi,target,client,progress):
    def read(path):return json.loads(path.read_text())
    unit=month_fragment(unit,lo.isoformat(),hi.isoformat())
    target.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp=dict(packet_sha256=digest(fragment),unit_sha256=digest(unit),policy_sha256=policy())
    if (target/'input.json').exists() and read(target/'input.json')!=stamp:
        raise Paused('needs_review','月内片段输入或合同变化，原回执保留，须显式修订')
    atomic(target/'input.json',stamp);atomic(target/'packet.json',fragment);atomic(target/'unit.json',unit)
    if not unit['person_events']:raise Paused('needs_review','该月内片段没有本人输入，需核对空期间来源；不补造评分')
    from bounded_read import raw_size
    if raw_size(unit)>DIRECT_BYTES:
        # 片段只能建立自己的截止前导航；禁止继承完整周中的月末后摘要。
        reading_view(fragment,target,client,progress,force_hierarchical=True)
    evaluate_unit(fragment,unit,target,client,progress)
    return target


def month_dispositions(native,directories,start,end,root,client,progress):
    from month_evidence import slots,validate as validate_month
    def read(path):return json.loads(path.read_text())
    dispositions=[]
    if slots(native):
        progress('checking',0,len(slots(native)),'核对已证必要缺口截至月末的状态和实际后续依据')
        from bounded_read import raw_size
        if raw_size(native)>DIRECT_BYTES:
            if not callable(getattr(type(client),'month_status_view',None)):
                raise Paused('needs_review','长月末材料需要有界缺口核对执行器，原评分保留，本次不发送月末请求')
            for period in native:
                target=directories[period['row']['period_id']]
                if raw_size(period)>30_000 and period['unit']['events'] and not (target/'context-navigation/tree.json').exists():
                    reading_view(read(target/'packet.json'),target,client,progress,force_hierarchical=True)
            from month_readback import complete as month_complete
            candidate,audit,dispositions,audited=month_complete(native,directories,start,end,root/'month-readback',client,received)
        else:
            candidate=received(client.month_status(native,start,end,root/'month-status.receipt.json'))
            dispositions=validate_month(native,candidate)
            audit=received(client.month_status(native,start,end,root/'month-status-audit.receipt.json',previous=candidate))
            audited=validate_month(native,audit)
        def statuses(values):return {(d['period_id'],d['item']):(d['status'],d['recovery_period'],tuple(d['recovery_refs'])) for d in values}
        atomic(root/'month-status.json',dict(candidate=candidate,audit=audit,dispositions=dispositions))
        if statuses(dispositions)!=statuses(audited):raise Paused('needs_review','月末缺口处置的另次检查存在分歧，原判断保留，不自动解除缺口')
    return dispositions


def export_month(user_id,start,end,root,weeks,native,period_refs,period_values,dispositions,workday_scope):
    from qualify_results import qualify
    def read(path):return json.loads(path.read_text())
    entry=dict(user_email=weeks[0]['user_email'],source_user_id=str(user_id),period_type='month',
        period_start=start[:10],period_end=end[:10],evidence_checked=True,periods=period_refs,gap_dispositions=dispositions)
    if workday_scope is not None:entry['workday_scope']=workday_scope
    computed=summarize_month([p['row'] for p in native],start=start,end=end,gap_dispositions=dispositions)
    atomic(root/'month-computed.json',computed)
    if computed['source_gaps']:
        from release_scope import signature
        atomic(root/'scope-pending.json',dict(computed_sha256=digest(computed),policy_sha256=policy(),
            issues=[dict(id=signature(g),source=g) for g in computed['source_gaps']]))
        if not (root/'scope-decisions.json').exists():
            raise Paused('needs_review','月度局部未知或资料问题须逐条核处置；完整提示已落盘，不能默删未决期间')
        proof=read(root/'scope-decisions.json')
        if proof.get('computed_sha256')!=digest(computed) or not proof.get('reviewer') or not proof.get('reason'):
            raise Paused('needs_review','月度资料处置未绑定实际计算结果和核对者，不能复用')
        entry.update(scope_decisions=proof['decisions'],scope_eligibility=dict(eligible=sum(
            qualify(w)['score_status']=='scored' for w in weeks)>=3))
    # 必要缺口月末状态未知、关键资料或期间未决时convert明确阻断，不能自动补“已恢复”。
    report,_=convert(root,entry,lambda ref:period_values[ref['key']])
    result=dict(schema_version=1,source_name='自动月汇总1.6:'+str(user_id)+':'+start[:10],
        reports=[qualify(report,weeks)])
    atomic(root/'bundle.json',result)
    return result
