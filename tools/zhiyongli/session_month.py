"""月评消费同版会话周评和月内片段；只核必要缺口，不重复首读或重判月档。"""
import copy
import json
from pathlib import Path

from navigation_access import freeze
from runtime import digest,policy
from review_schema import obj,array,enum


def dispositions(periods,root,client):
    """引用选择绑定既有原文；缺决定性恢复事实给最终无评级，不转员工审核。"""
    from month_evidence import slots,validate,INSTRUCTIONS
    from session_review import check_schema,decode_record
    from pipeline import Paused,received
    from runtime import MAX_REQUEST_BYTES,atomic
    result=[]
    for key,slot in slots(periods).items():
        folder=Path(root)/digest(key);evidence={};views=[];visible=set()
        for period in periods:
            pid=period['row']['period_id'];ledger=period['review']['evidence']
            cells=[c for c in period['row']['cells'] if c['item']==slot['item']]
            def refs(value):
                if isinstance(value,dict):return set(value.get('refs',[]))|set().union(*(refs(v) for v in value.values()))
                if isinstance(value,list):return set().union(*(refs(v) for v in value))
                return set()
            relevant=refs(cells)
            for proof in ledger:
                evidence[pid+'/'+proof['id']]=dict(period_id=pid,**proof)
                if proof['id'] in relevant:visible.add(pid+'/'+proof['id'])
            views.append(dict(period_id=pid,window=period['row']['window'],cells=cells,
                evidence_index=[dict(reference=pid+'/'+e['id'],characters=len(e['quote'])) for e in ledger]))
        text=dict(type='string',minLength=1,maxLength=1200)
        reference=enum(*evidence)
        schema=obj(dict(action=dict(anyOf=[
            obj(dict(operation=enum('read_evidence'),references=dict(array(reference),minItems=1,maxItems=20))),
            obj(dict(operation=enum('decide'),status=enum('open','recovered','unresolved'),reason=text,
                source_refs=dict(array(reference),minItems=1,maxItems=30),
                recovery_period=dict(type=['string','null']),recovery_refs=array(dict(type='string'))))])))
        freeze(folder/'input.json',dict(periods_sha256=digest(periods),slot=key,policy_sha256=policy()))
        feedback=None;previous=None;errors=0
        for turn in range(24):
            data=dict(slot=slot,periods=views,evidence=[dict(reference=r,**evidence[r]) for r in sorted(visible)],
                contract_feedback=feedback,previous_response=previous,
                limitation='已评分期间的事实与证据复用，未展示引文可read_evidence；不得把未展示当没有恢复。只判月末状态，不重新评分。')
            payload=dict(model=client.config.model,instructions=INSTRUCTIONS+'\n用source_refs选择实际交付的reference，程序提取原文，不自行抄引文。decide是本次最终处置；unresolved明确缺什么及影响，后续不需要员工审核，也不生成完整月分。',
                input=json.dumps(data,ensure_ascii=False,separators=(',',':')),stream=True,store=False,max_output_tokens=2400,
                text=dict(format=dict(type='json_schema',name='zhiyongli_session_month_status',strict=True,schema=schema)),
                metadata=dict(purpose='zhiyongli_session_month_status'))
            if len(json.dumps(payload,ensure_ascii=False).encode())>MAX_REQUEST_BYTES:
                raise Paused('failed','月末缺口工作集超过容量，原周评保留',reason_code='request_capacity_exceeded')
            target=folder/str(turn)/'model.receipt.json'
            freeze(target.parent/'request.json',dict(payload_sha256=digest(payload)))
            record=client._call(payload,target)
            if record['status']!='received':received(record)
            try:
                value=decode_record(record);check_schema(value,schema);action=value['action']
                if action['operation']=='read_evidence':
                    requested=set(action['references'])
                    if requested<=visible:raise ValueError('所请求引文已经实际交付，无需重复读取')
                    visible.update(requested)
                else:
                    if not set(action['source_refs'])<=visible:raise ValueError('月末结论引用尚未交付的原文')
                    quotes=[dict(period_id=evidence[r]['period_id'],event=evidence[r]['event'],quote=evidence[r]['quote'])
                        for r in action['source_refs']]
                    decision={k:action[k] for k in ('status','reason','recovery_period','recovery_refs')}
                    decision['source_quotes']=quotes
                    checked=validate(periods,dict(slots={key:decision}),only_keys=[key],allow_unresolved=True)
                    freeze(folder/'result.json',dict(value=decision,receipt_sha256=digest(record)))
                    result.extend(checked);break
                freeze(target.parent/'checked.json',dict(action_sha256=digest(action)))
                feedback=None;previous=None;errors=0
            except (ValueError,KeyError,TypeError) as ex:
                if hasattr(ex,'state'):raise
                feedback=dict(error=str(ex)[:1500]);errors+=1
                try:previous=decode_record(record)
                except (ValueError,KeyError,TypeError):previous=None
                atomic(target.parent/'contract-error.json',feedback)
                if errors>=3:raise Paused('failed','月末缺口回复未过合同，原评分保留',reason_code='model_contract_invalid') from None
        else:raise Paused('failed','月末必要缺口取证达到有界轮数',reason_code='request_capacity_exceeded')
    return result


def evaluate(source_root,user_id,start,end,directory,client,progress,shared_root,workday_scope):
    from datetime import timedelta
    from runtime import moment,atomic,validate_review,summarize_month
    from pipeline import prepare,Paused
    from month_evidence import freeze_empty,audited_empty
    from session_weeks import resolve
    from session_review import evaluate as run
    from export_results import convert
    from qualify_results import qualify
    from release_scope import signature
    root=Path(directory);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    a,b=moment(start),moment(end);cursor=a-timedelta(days=a.weekday());weeks=[];native=[];references=[];values={}
    freeze(root/'session-month-input.json',dict(user_id=str(user_id),start=start,end=end,policy_sha256=policy(),workday_scope=workday_scope))
    def read(path):return json.loads(path.read_text())
    def source(lo,hi):
        extra=dict(session_review=True,metadata_cache=root/'source-frames')
        if workday_scope is not None:
            from workday_scope import window,load
            extra['workday_scope']=window(lo.date().isoformat(),hi.date().isoformat(),load(expected=workday_scope['registry_sha256']))
        return prepare(source_root,user_id,lo.isoformat(),hi.isoformat(),**extra)
    while cursor<b:
        next_week=cursor+timedelta(days=7);week=root/'weeks'/cursor.date().isoformat()
        packet=source(cursor,next_week)
        try:
            from pipeline import evaluate_week
            evaluate_week(packet,week,client,progress,shared_root=shared_root)
        except Paused as ex:
            if ex.state!='no_activity':raise
            freeze_empty(packet,week)
        else:
            week=resolve(week)
            # 从原生unit/review生成1.3来源，再按现有025资格化；不改现有1.6版本号伪装旧源。
            entry=dict(user_email=packet['user_email'],source_user_id=str(user_id),period_type='week',
                period_start=cursor.date().isoformat(),period_end=next_week.date().isoformat(),evidence_checked=True,
                periods=[dict(unit=dict(key='u'),review=dict(key='r'))])
            data={'u':read(week/'unit.json'),'r':read(week/'review.json')['value']}
            weeks.append(convert(root,entry,lambda ref:data[ref['key']])[0])
        lo,hi=max(a,cursor),min(b,next_week);target=week;fragment=packet
        if (lo,hi)!=(cursor,next_week):
            target=root/'fragments'/lo.date().isoformat();fragment=source(lo,hi)
        if (not fragment['events'] and not fragment['issues']) or fragment.get('workday_scope') is not None and not fragment['workday_scope']['working_dates']:
            empty=freeze_empty(fragment,target);u,v,row=audited_empty(empty)
            key=str(len(references));values[key+'e']=empty;references.append(dict(empty=dict(key=key+'e')))
        else:
            if target!=week:run(fragment,target,client,progress)
            u,v=read(target/'unit.json'),read(target/'review.json')['value']
            if u.get('source_issues') or u.get('source_gaps'):
                raise Paused('failed','月度来源存在技术缺失，不能转成员工证据不足',reason_code='source_incomplete')
            row=validate_review(u,v)[0];row['evidence_checked']=True
            key=str(len(references));values.update({key+'u':u,key+'r':v})
            references.append(dict(unit=dict(key=key+'u'),review=dict(key=key+'r')))
        native.append(dict(unit=u,review=v,row=row));cursor=next_week
    if not weeks:raise Paused('no_activity','全月冻结来源无活动，不生成月评级')
    decisions=dispositions(native,root/'month-status',client)
    computed=summarize_month([p['row'] for p in native],start=start,end=end,gap_dispositions=decisions)
    atomic(root/'month-computed.json',computed)
    entry=dict(user_email=weeks[0]['user_email'],source_user_id=str(user_id),period_type='month',
        period_start=start[:10],period_end=end[:10],evidence_checked=True,periods=references,
        gap_dispositions=decisions,final_observation=True)
    if workday_scope is not None:entry['workday_scope']=copy.deepcopy(workday_scope)
    if computed['source_gaps']:
        entry['scope_decisions']=[dict(id=signature(g),source=g,status='blocking',basis='已完成期间的原生分项及月末证据',
            reason='本次已完成评判，但记录中的该项事实不足以形成完整月评级：'+str(g)) for g in computed['source_gaps']]
        entry['scope_eligibility']=dict(eligible=sum(qualify(w)['score_status']=='scored' for w in weeks)>=3)
    report,_=convert(root,entry,lambda ref:values[ref['key']])
    result=dict(schema_version=1,source_name='会话月汇总:'+str(user_id)+':'+start[:10],reports=[qualify(report,weeks)])
    atomic(root/'bundle.json',result);return result
