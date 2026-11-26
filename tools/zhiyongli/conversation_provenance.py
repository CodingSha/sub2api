"""从真实请求和完整回执重建连续评审结果，拒绝只写通过标记或手改分数。"""
import hashlib
import json
import re
from pathlib import Path

import jsonschema

from conversation_transport import decode
from conversation_attribution import annotate,background_only_result
from daily_behaviors import catalog
from model_config import response_text
from request_retry import read_receipt
from runtime import digest
from task_evidence_flow import no_observation
from task_review_policy import RUBRIC


def load(path):return json.loads(Path(path).read_text())


def verify_background(root, tasks, originals, attribution):
    """重建来源分流结论；空分本身、手写状态或请求失败都不能证明纯背景。"""
    root=Path(root);contract=load(root/'conversation-contract.json');quality=load(root/'quality.json')
    expected=background_only_result(tasks,originals,attribution);saved=load(root/'conversation-result.json')
    source_sha=digest(dict(tasks=tasks,originals=originals))
    if (expected is None or quality.get('quality_status')!='source_checked'
            or contract['source_sha256']!=source_sha or quality.get('source_sha256')!=source_sha
            or saved.get('origin')!='verified_background_source' or saved.get('raw') is not None
            or saved['value']!=expected or quality['result_sha256']!=digest(expected)):
        raise ValueError('纯背景结论与实际来源、归属或程序重建结果不符')
    return dict(origin='verified_background_source',source_sha256=source_sha,
        implementation_sha256=contract['implementation_sha256'],result_sha256=digest(expected),receipt_hashes=[])


def stage(root, name):
    root=Path(root);intent=load(root/(name+'.intent.json'));payload=load(root/(name+'.input.json'))
    path=root/(name+'.receipt.json')
    if not path.exists():
        ref=load(root/(name+'.reused.json'));path=Path(ref['path'])
        if digest(read_receipt(path))!=ref['receipt_sha256']:raise ValueError('复用回执签名变化')
    receipt=read_receipt(path)
    if (payload!=intent['payload'] or json.loads(payload['input'])!=intent['data']
            or receipt.get('request_sha256')!=digest(dict(endpoint=intent['endpoint'],payload=payload))
            or receipt.get('wire_body_sha256')!=hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest()
            or receipt.get('model')!=intent['model'] or receipt.get('endpoint')!=intent['endpoint']
            or receipt.get('status')!='received'):
        raise ValueError('阶段请求、来源端点或回执签名不符')
    correction=root/(name+'.format-correction.json')
    if correction.exists():
        from conversation_transport import format_correction
        from conversation_review import received_stage,JSON_ONLY
        proof=load(correction);parent=proof['parent_stage'];original=received_stage(type('Stored',(),{})(),root,parent)
        parent_intent=load(root/(parent+'.intent.json'))
        if original.get('status')!='received' or (original.get('response') or {}).get('status')!='completed':
            raise ValueError('格式纠正的原请求终态不明')
        raw=json.loads(response_text(original['response']))
        resumed,instructions,expected=format_correction(parent,parent_intent['instructions'][:-len(JSON_ONLY)],raw,parent_intent['schema'],digest(original))
        if (resumed!=name or proof!=expected or intent['data']!=parent_intent['data']
                or intent['schema']!=parent_intent['schema'] or intent['instructions']!=instructions+JSON_ONLY
                or original.get('request_sha256')!=digest(dict(endpoint=parent_intent['endpoint'],payload=parent_intent['payload']))):
            raise ValueError('格式纠正没有绑定原始材料、完整回执和原合同')
    value=json.loads(response_text(receipt['response']))
    normalization=root/(name+'.normalization.json')
    if normalization.exists():
        from conversation_transport import normalize_terminal
        normalized,moves=normalize_terminal(value,intent['data'].get('records',[]));proof=load(normalization)
        if (not moves or proof['moves']!=moves or proof['original_sha256']!=digest(value)
                or proof['result_sha256']!=digest(normalized) or proof['receipt_sha256']!=digest(receipt)):
            raise ValueError('终判导航字段归位记录与真实模型正文不符')
        value=normalized
    jsonschema.validate(value,intent['schema'])
    if value!=load(root/(name+'.json')):raise ValueError('落盘判断与真实模型正文不符')
    return value,intent,digest(receipt)


def candidate(root,name,seen=None,source=None):
    """逐层重建父判断和模型局部修正，不能把程序拼装正文冒充单次模型回复。"""
    root=Path(root);seen=set() if seen is None else seen
    if name in seen:raise ValueError('判断修正链出现循环')
    seen.add(name)
    value,intent,sha=stage(root,name)
    if not name.startswith('repair-'):return value,intent,[sha]
    from conversation_repair import package,apply,source_data
    assembly=load(root/(name+'.assembly.json'))
    parent,base,proofs=candidate(root,assembly['parent_stage'],seen,source)
    correction=dict(intent['data']['correction'],previous_invalid=parent)
    if intent['data']['candidate']!=parent:raise ValueError('局部修正的父判断与真实模型正文不符')
    if assembly.get('context_refs'):
        if source is None:raise ValueError('补回语境必须绑定冻结原文')
        from conversation_repair import with_context
        data,schema,_,_,refs=with_context(source_data(base['data']),base['schema'],*source,correction)
        if refs!=assembly['context_refs']:raise ValueError('修正语境不同于原引用的紧邻来源')
        base=dict(base,data=data,schema=schema)
    packet,_=package(source_data(base['data']),base['schema'],correction)
    if source_data(intent['data'])!=packet:raise ValueError('局部修正材料与原综合判断来源不符')
    merged=apply(parent,value,packet)
    if (assembly['original_data_sha256']!=packet['original_data_sha256']
            or assembly['result_sha256']!=digest(merged)
            or load(root/(name+'.assembled.json'))!=merged):
        raise ValueError('局部修正合并结果被改写')
    return merged,base,proofs+[sha]


def verify(root, tasks, originals):
    root=Path(root);contract=load(root/'conversation-contract.json');quality=load(root/'quality.json')
    tasks,originals,attribution=annotate(tasks,originals)
    if load(root/'source-attribution.json')!=attribution:raise ValueError('来源归属标记与逐字派发证据不符')
    if contract['source_sha256']!=digest(dict(tasks=tasks,originals=originals)):
        raise ValueError('评审与冻结原文不符')
    if quality.get('quality_status')=='source_checked':
        return verify_background(root,tasks,originals,attribution)
    if quality.get('quality_status')!='model_checked':raise ValueError('综合评审尚未通过模型事实检查')
    saved=load(root/'conversation-result.json');matches=[];proofs=[]
    for path in root.glob('synthesis-*.json'):
        if path.name.endswith(('.intent.json','.input.json','.receipt.json','.reused.json')):continue
        if digest(load(path))==quality['candidate_sha256']:matches.append(path.stem)
    name=quality.get('candidate_stage')
    if not name:
        if len(matches)!=1:raise ValueError('最终模型判断不能唯一定位')
        name=matches[0]
    wire,intent,candidate_proofs=candidate(root,name,source=(tasks,originals));proofs.extend(candidate_proofs)
    if digest(wire)!=quality['candidate_sha256']:raise ValueError('最终模型判断签名不符')
    match=re.search(r'-(\d+)(?:-effort-(?:low|medium|high|none))?(?:-without-reasoning)?(?:-service-retry-\d+)?$',name)
    if not match:raise ValueError('最终综合判断阶段编号不符')
    attempt=int(match[1]);data=intent['data']
    aliases={key:'r'+str(index+1) for index,key in enumerate(originals)}
    reverse={alias:key for key,alias in aliases.items()};fragments={}
    for row in data['records']:
        ref=reverse[row['id']];source=originals[ref]
        text=''.join(piece for _,piece in row['text_parts']) if 'text_parts' in row else row['text']
        if text!=source['text'][row['start']:row['end']]:raise ValueError('实际综合判断原文与来源区间不符')
        if row['role']=='user' and (source['role']!='user' or source.get('boundary_context')):
            raise ValueError('综合判断输入把背景当作本人原话')
        for key,piece in row.get('text_parts',[]):
            if key:fragments[key]=[ref,piece]
    transport=load(root/('transport-'+str(attempt)+'.json'))
    if transport!=dict(aliases=aliases,fragments=fragments):raise ValueError('原文片段定位索引与实际交付不符')
    raw=decode(wire,transport)
    for summary in raw['task_summaries'].values():summary['user_actions']=[]
    if raw!=saved['raw']:raise ValueError('解析判断与模型正文不同')
    items={};behaviors=[]
    for code in RUBRIC['items']:
        cell=raw['items'].get(code,no_observation(code,'本范围未观察到本项可独立评价的本人行为。'))
        evidence=[]
        for ev in cell['evidence']:
            identity='b'+digest([ev['record_id'],ev['claim'].strip()])[:20]
            evidence.append(dict(record_id=ev['record_id'],quote=ev['quote'],origin=ev['origin'],behavior_id=identity))
            behaviors.append(dict(record_id=ev['record_id'],quote=ev['quote'],claim=ev['claim'],primary_item=code))
        items[code]=dict(band=cell['band'],state=cell['state'],basis=cell['reason'],
            next_band=cell['next_band'],negative=cell['negative'],evidence=evidence)
    expected=dict(needs_records=[],task_summaries=raw['task_summaries'],items=items,summary=raw['summary'],
        behaviors=catalog(behaviors,{r['id']:r for task in tasks for r in task['records']}) if behaviors else {})
    if expected!=saved['value'] or digest(expected)!=quality['result_sha256']:
        raise ValueError('最终分项、原话或理由不同于真实模型判断')
    if quality.get('audit_stages'):
        from conversation_quality import packets,SINGLETON_BYTES
        expected_packets=packets(data,wire,singleton_maximum=SINGLETON_BYTES)
        if len(expected_packets)!=len(quality['audit_stages']):raise ValueError('事实核查批次缺失')
        for audit_name,packet in zip(quality['audit_stages'],expected_packets):
            audit,audit_intent,audit_sha=stage(root,audit_name);proofs.append(audit_sha)
            if audit['issues'] or any(audit_intent['data'].get(key)!=value for key,value in packet.items()):
                raise ValueError('事实检查未通过或核查材料发生变化')
    else:
        audit,audit_intent,audit_sha=stage(root,quality['audit_stage']);proofs.append(audit_sha)
        if audit['issues'] or audit_intent['data']['candidate']!=wire:raise ValueError('事实检查未通过或核查了其他候选')
    coverage=[]
    for path in sorted(root.glob('reading-*.json')):
        if path.name.endswith(('.intent.json','.input.json','.receipt.json','.reused.json')) or path.name=='reading-coverage.json':continue
        if not path.with_name(path.stem+'.intent.json').exists():continue
        value,read_intent,read_sha=stage(root,path.stem)
        if not value['capacity_complete']:raise ValueError('原文阅读未完成')
        proofs.append(read_sha)
        for row in read_intent['data']['records']:
            source=originals[row['id']]
            if row['text']!=source['text'][row['start']:row['end']]:raise ValueError('阅读回执原文不同')
            coverage.append((row['id'],row['start'],row['end']))
    for task in tasks:
        for row in task['records']:
            end=0
            for _,lo,hi in sorted(r for r in coverage if r[0]==row['id']):
                if lo>end:break
                end=max(end,hi)
            if end!=len(row['text']):raise ValueError('原文连续阅读覆盖不完整')
    return dict(origin='verified_model_receipts',source_sha256=contract['source_sha256'],
        implementation_sha256=contract['implementation_sha256'],result_sha256=digest(expected),receipt_hashes=proofs)
