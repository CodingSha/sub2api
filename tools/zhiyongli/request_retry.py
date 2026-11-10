"""仅为明确HTTP429提供有界续跑；原回执与每次新发送独立留存。"""
import json
import os
from pathlib import Path

from runtime import atomic,digest

VERSION='bounded-http429-retry-20261109.1'


def attempt_path(base,index):
    return base.with_name(base.name.removesuffix('.receipt.json')+'.attempt-'+str(index)+'.receipt.json')


def pending_record(path,chain):
    """跨任务核对原发送账簿；无回执的已预留尝试不得重新发送。"""
    pending=chain.get('pending')
    if not pending:return None
    if pending.get('index')!=len(chain['attempts']):raise ValueError('429待发送序号不连续')
    budget=Path(pending['budget'])
    saved=json.loads(budget.read_text())
    if saved.get('version')!=1:raise ValueError('429原发送账簿格式无效')
    target=attempt_path(Path(path),pending['index'])
    entry=saved['entries'].get(str(target.resolve()))
    if entry and entry.get('request_sha256')!=chain['request_sha256']:
        raise ValueError('429原账簿请求签名变化')
    if target.exists():
        record=json.loads(target.read_text())
        if record.get('request_sha256')!=chain['request_sha256'] or not entry:
            raise ValueError('429待发送回执与原账簿不一致')
        return record
    if entry:return dict(status='unknown',request_sha256=chain['request_sha256'],error='missing_retry_receipt')
    return None # 仅链预记，尚未预留发送预算，可安全重新申请配额。


def read_receipt(path):
    """链入口仅存签名引用，模型原响应仍只存一份。"""
    raw=json.loads(Path(path).read_text())
    if raw.get('transport_version')!=VERSION:return raw
    attempts=raw.get('attempts')
    if not isinstance(attempts,list) or not attempts:raise ValueError('429续跑链缺原回执')
    record=None
    for i,ref in enumerate(attempts):
        target=attempt_path(Path(path),i)
        record=json.loads(target.read_text())
        if digest(record)!=ref.get('sha256') or record.get('request_sha256')!=raw['request_sha256']:
            raise ValueError('429续跑原回执或请求签名变化')
        if i<len(attempts)-1 and not (record.get('status')=='rejected' and record.get('error')=='http_429'):
            raise ValueError('仅明确429拒绝可以进入下次尝试')
    expected='retry_wait' if record.get('error')=='http_429' and not raw.get('exhausted') else record['status']
    if raw['status']!=expected:raise ValueError('429链入口与实际终态不一致')
    pending=pending_record(path,raw)
    if pending is not None:record=pending
    return dict(record,attempt_chain_sha256=digest(raw))


def origin_digest(path):
    raw=json.loads(Path(path).read_text())
    if raw.get('transport_version')==VERSION:return digest(json.loads(attempt_path(Path(path),0).read_text()))
    return digest(raw)


def call(client,payload,receipt,send):
    from pipeline import Paused
    receipt=Path(receipt)
    maximum=client.flow.config.get('max_429_retries',0) if client.flow else 0
    chain=json.loads(receipt.read_text()) if receipt.exists() else None
    if chain and chain.get('transport_version')==VERSION:
        prior=read_receipt(receipt)
        if chain['request_sha256']!=digest(dict(endpoint=client.config.responses_url,payload=payload)):
            raise ValueError('API请求签名漂移')
        if chain['status']!='retry_wait':return prior
        if chain.get('pending'):
            target=attempt_path(receipt,len(chain['attempts']))
            if prior['status'] in {'unknown','requesting'}:return prior
            if target.exists():
                # 发送落盘后、链更新前崩溃：只接回原回执，不额外请求。
                result=json.loads(target.read_text())
                return finish(receipt,chain,result,maximum)
            chain.pop('pending');atomic(receipt,chain)
    else:
        prior=send(payload,receipt)
        if not maximum or prior.get('status')!='rejected' or prior.get('error')!='http_429':return prior
        original=attempt_path(receipt,0)
        if original.exists():raise ValueError('429原回执目标已存在，须核对而非覆盖')
        os.link(receipt,original)
        chain=dict(transport_version=VERSION,request_sha256=prior['request_sha256'],status='retry_wait',
            attempts=[dict(sha256=digest(prior))],exhausted=False)
        atomic(receipt,chain)
    index=len(chain['attempts'])
    if index>maximum:
        chain.update(status='rejected',exhausted=True);atomic(receipt,chain)
        return read_receipt(receipt)
    wait_until=max(prior.get('retry_after_at',0),prior.get('sent_at',0)+client.flow.config['cooldown_seconds'])
    if client.flow.now()<wait_until:raise Paused('queued','模型明确限流，已保留原回执；冷却后在累计预算内进行有界续跑')
    target=attempt_path(receipt,index)
    if target.exists():raise ValueError('429存在未入链尝试，须核对而非覆盖')
    # 先记录原账簿身份，覆盖预算预留后、回执写入前的跨任务崩溃窗口。
    chain['pending']=dict(index=index,budget=str(client.budget.path.resolve()))
    atomic(receipt,chain)
    result=send(payload,target)
    return finish(receipt,chain,result,maximum)


def finish(receipt,chain,result,maximum):
    from pipeline import Paused
    index=len(chain['attempts'])
    chain['attempts'].append(dict(sha256=digest(result)))
    chain.pop('pending',None)
    limited=result.get('status')=='rejected' and result.get('error')=='http_429'
    exhausted=limited and index>=maximum
    chain.update(status='retry_wait' if limited and not exhausted else result['status'],exhausted=exhausted)
    atomic(receipt,chain)
    if chain['status']=='retry_wait':raise Paused('queued','模型再次明确限流，等待受控冷却；原尝试与累计预算保留')
    return read_receipt(receipt)
