"""从实际回放回执重建候选，拒绝仅声明哈希或手写候选的质量证明。"""
import hashlib
import json
from pathlib import Path
import jsonschema

from evidence_review import validate
from evidence_requests import ITEMS
from model_config import response_text
from request_retry import read_receipt
from runtime import digest


def load(path):
    return json.loads(Path(path).read_text())


def verified_stage(root, name):
    """校验请求、响应终态、响应正文和落盘结果的完整对应。"""
    root=Path(root);intent=load(root/(name+'.intent.json'))
    payload=load(root/(name+'.input.json'));receipt=read_receipt(root/(name+'.receipt.json'))
    if payload!=intent['payload'] or json.loads(payload['input'])!=intent['data']:
        raise ValueError('回放请求与冻结阶段输入不同')
    if receipt.get('request_sha256')!=digest(dict(endpoint=intent['endpoint'],payload=payload)):
        raise ValueError('模型回执请求签名不符')
    body=json.dumps(payload,ensure_ascii=False).encode()
    if receipt.get('wire_body_sha256')!=hashlib.sha256(body).hexdigest():
        raise ValueError('模型回执实际发送正文签名不符')
    if receipt.get('model')!=intent['model'] or receipt.get('endpoint')!=intent['endpoint']:
        raise ValueError('模型回执来源不同')
    if receipt.get('status')!='received':raise ValueError('模型回执未取得终态')
    value=json.loads(response_text(receipt['response']))
    jsonschema.validate(value,intent['schema'])
    if value!=load(root/(name+'.json')):raise ValueError('落盘结果不同于模型真实正文')
    return value,digest(receipt),intent


def candidate_from_replay(root, reference):
    """只从回执提取评分；检查来源、规则、参考冻结合同和最终候选的绑定。"""
    root=Path(root);contract=load(root/'replay-contract.json');source=load(root/'frozen-source.json')
    if contract['reference_sha256']!=digest(reference):raise ValueError('参考不是回放前冻结的版本')
    if contract['source_sha256']!=reference['source_sha256'] or contract['source_payload_sha256']!=digest(source):
        raise ValueError('回放来源与参考不同')
    flow=root/'candidate';run=load(flow/'result.json');frozen=load(flow/'contract.json')
    if (frozen['tasks_sha256']!=digest(source['tasks']) or frozen['originals_sha256']!=digest(source['originals'])
            or frozen['rubric_sha256']!=reference['rubric_sha256']):
        raise ValueError('候选运行来源或规则不符')
    if run['status']!='candidate' or run['failures']:raise ValueError('候选回放尚未完整完成')
    if [r['task_id'] for r in run['results']]!=[t['id'] for t in source['tasks']]:
        raise ValueError('候选缺少冻结任务或任务次序漂移')
    cases=[];receipts=[]
    for index,(task,result) in enumerate(zip(source['tasks'],run['results'])):
        folder=flow/('task-'+str(index));task_source=load(folder/'source.json')
        local={k:r for k,r in source['originals'].items() if r.get('task_id')==task['id'] or k in {x['id'] for x in task['records']}}
        if task_source['task']!=task or task_source['originals']!=local:
            raise ValueError('单任务冻结原文不一致')
        if task_source['implementation_sha256']!=frozen['implementation_sha256']:
            raise ValueError('候选运行实现版本不同')
        stages={}
        for intent_path in sorted(folder.rglob('*.intent.json')):
            name=intent_path.name.removesuffix('.intent.json')
            value,sha,intent=verified_stage(intent_path.parent,name)
            if intent['model']!=contract['model'] or intent['endpoint']!=contract['endpoint']:
                raise ValueError('阶段模型端点与回放不同')
            stages[str(intent_path.relative_to(folder)).removesuffix('.intent.json')]=(value,intent)
            receipts.append(sha)
        if stages['task-card'][0]!=result['task_card'] or stages['audit'][0]!=result['audit']:
            raise ValueError('任务理解或复核不同于模型回执')
        if set(result['items'])!=set(ITEMS):raise ValueError('候选未处置全部十二项')
        stages['evidence-plan'] # 规划阶段同样必须有实际回执。
        for code,item in result['items'].items():
            terminal=sorted(k for k in stages if k.startswith(code+'/judgment-'))[-1]
            value,intent=stages[terminal]
            if value['requests'] or value['queries'] or value['result']!=item:
                raise ValueError('候选评分不同于最终分项模型回执')
            validate(item,code,intent['data']['records'])
        cases.append(dict(case_id=task['id'],items=result['items'],source_records=local))
    return dict(source_sha256=reference['source_sha256'],rubric_sha256=reference['rubric_sha256'],
        origin='verified_model_receipts',implementation_sha256=frozen['implementation_sha256'],receipt_hashes=receipts,cases=cases)
