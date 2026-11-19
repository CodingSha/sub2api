"""自动修订只停止无进展重评；不替代异议处置或改变评分。"""
import json
from pathlib import Path

from runtime import digest,policy,response_value,response_text,validate_review,RUBRIC
from request_retry import read_receipt,blocking_status
from review_control import model_receipt,verify_files
from audit_readback import context,receipt
from semantic_audit import validate


def _read(path):
    return json.loads(path.read_text())


def evidence_followup(packet,current,issues):
    """只记录本次复查的原文覆盖；未提供不等于来源缺失，不猜语义所需事件。"""
    events={e['event']:e for e in packet['events']};spans={}
    for event in current['events']:
        key=event.get('source_event',event['event']);original=events.get(key)
        if original is None:continue
        if event['event']==key and event['text']==original['text']:
            span=(0,len(original['text']))
        else:
            span=event.get('source_span',[])
            if (len(span)!=2 or any(type(v) is not int for v in span)
                    or not 0<=span[0]<=span[1]<=len(original['text'])
                    or event['text']!=original['text'][span[0]:span[1]]):continue
        spans.setdefault(key,[]).append(tuple(span))
    unseen=[];partial=[]
    for key,event in events.items():
        ranges=sorted(spans.get(key,[]));stop=0
        for start,end in ranges:
            if start>stop:break
            stop=max(stop,end)
        if not ranges or stop<len(event['text']):
            unseen.append(key)
            if ranges:partial.append(key)
    access=current.get('context_access',{})
    available=set(access.get('available_node_ids',[]))
    leaves=set(access.get('leaf_node_ids',[]))
    raw=available & leaves-set(access.get('raw_node_ids',[]))
    branches=available-leaves-set(access.get('inspected_node_ids',[]))
    return dict(version=1,required=True,unresolved_items=sorted(k for k,v in issues['items'].items() if v['verdict']=='unresolved'),
        next_action='locate_frozen_evidence' if unseen else 'adjudicate_existing_evidence',
        frozen_event_count=len(events),not_fully_provided_events=len(unseen),partially_provided_events=len(partial),
        not_fully_provided_ids_sha256=digest(unseen),available_raw_nodes=len(raw),available_branches=len(branches),
        source_issue_count=len(packet.get('issues',[])),source_missing_established=False,
        limitation='覆盖仅指本次复查实际提供的原文；先前阅读摘要不等于本次已核。未读范围不是必要补证清单，须定位能改变判断的材料；'
            '全部原文已提供也不证明语义判断正确。停止重复评分不代表证据补核完成。')


def _snapshot(root):
    if (root/'contract-error.json').exists() or not (root/'model-audit.json').exists():return None
    stamp=_read(root/'input.json');packet=_read(root/'packet.json')
    if stamp.get('packet_sha256')!=digest(packet):raise ValueError('收敛核对冻结来源签名不符')
    if stamp.get('policy_sha256')!=policy():return None
    unit=_read(root/'unit.json')
    candidate=read_receipt(model_receipt(root,unit))
    if candidate.get('status')!='received':raise ValueError('收敛核对不能沿用未知评分回执')
    review=response_value(unit,candidate)[0];validate_review(unit,review)
    if _read(root/'model-review.json')['value']!=review:raise ValueError('收敛核对候选与实际回执不符')
    actual_context=context(root,unit);audit_record=read_receipt(receipt(root,unit))
    if audit_record.get('status')!='received':raise ValueError('收敛核对不能沿用未知复查回执')
    value=json.loads(response_text(audit_record['response']))
    # 单槽引文修复须另验完整修复链；本守卫保守保留原有界修订，不信派生结论。
    if isinstance(value,dict) and set(value)=={'event','quote'}:return None
    audit=validate(unit,review,value,RUBRIC['items'],context=actual_context,require_facts=True)
    if _read(root/'model-audit.json')!=audit:raise ValueError('收敛核对复查与实际回执不符')
    issues=dict(items={k:v for k,v in value['items'].items() if v['verdict']!='supported'},
        global_issues=value['global_issues'],state_mismatches=audit.get('state_mismatches',[]),
        claim_conflicts=audit.get('claim_conflicts',[]))
    signature=dict(review_sha256=digest(review),issues_sha256=digest(issues),unit_sha256=digest(unit),
        context_sha256=digest(actual_context),policy_sha256=policy())
    unresolved=bool(issues['items']) and all(v['verdict']=='unresolved' for v in issues['items'].values())
    unresolved=unresolved and not any(issues[k] for k in ('global_issues','state_mismatches','claim_conflicts'))
    return dict(signature=signature,unresolved_only=unresolved,followup=evidence_followup(packet,actual_context,issues))


def _unresolved_requests(root):
    # 祖先文件签名只能证明原文件未变，新增未决请求也必须明确阻断。
    for path in root.rglob('*.receipt.json'):
        if 'revisions' in path.relative_to(root).parts:continue
        if blocking_status(path) in {'unknown','requesting','budget_paused','retry_wait','interrupted'}:
            raise ValueError('收敛核对存在未决请求，须先核对原回执')


def decision(root):
    """返回是否允许原有自动修订；拒绝只保留needs_review，绝不放行。"""
    root=Path(root).resolve()
    _unresolved_requests(root)
    ancestors=[];cursor=root;seen={root}
    while (cursor/'revision.json').exists():
        revision=_read(cursor/'revision.json')
        if not revision.get('parent_files'):raise ValueError('自动修订缺少前序文件签名')
        verify_files(revision['parent_files'])
        parent=Path(revision['parent_directory']).resolve()
        if not parent.is_dir():raise ValueError('自动修订父目录缺失')
        if parent in seen:raise ValueError('自动修订父链循环')
        seen.add(parent)
        if not revision.get('automatic_round') or revision.get('policy_sha256')!=policy():break
        _unresolved_requests(parent)
        ancestors.append(parent);cursor=parent
    current=_snapshot(root)
    if current is None:return dict(allow=True,reason='原合同修订或未支持的复查链维持原流程',proof={})
    proof=dict(current=current['signature'],evidence_followup=current['followup'])
    if current['unresolved_only']:
        action='先定位冻结来源中尚未提供的必要原文' if current['followup']['not_fully_provided_events'] else '核对已提供原文及未决判断'
        return dict(allow=False,reason='语义复查仍有未决缺口，停止重复评分；'+action+'，证据补核未完成，异议保留待核',proof=proof)
    for parent in ancestors:
        previous=_snapshot(parent)
        if previous is not None and previous['signature']==current['signature']:
            proof['ancestor']=str(parent)
            return dict(allow=False,reason='相同材料、候选与异议已出现，停止重复自动评分和复查；证据补核与异议处置仍待完成',proof=proof)
    return dict(allow=True,reason='存在可修订异议，维持原有界修订',proof=proof)
