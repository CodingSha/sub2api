"""为明确HTTP429和传输中断提供有界续发；原回执与每次新发送独立留存。"""
import json
import os
from pathlib import Path
import re

from runtime import atomic,digest

VERSION='bounded-transport-retry-20261110.1'
LEGACY='bounded-http429-retry-20261109.1'
VERSIONS={VERSION,LEGACY}
ATTEMPT=re.compile(r'^(.*)\.attempt-(\d+)\.receipt\.json$')
# 客户端可确定没有取得终态的传输层失败；进程崩溃遗留的requesting及缺回执预留不在此列。
INTERRUPTED_CODES={'stream_ended_without_terminal','stream_timeout'}
INTERRUPTED_ERRORS={'URLError','ConnectionResetError','ConnectionAbortedError','ConnectionRefusedError','BrokenPipeError',
    'RemoteDisconnected','IncompleteRead','TimeoutError','timeout','SSLEOFError','SSLZeroReturnError',
    'http_408','http_500','http_502','http_503','http_504'}


def retry_kind(record,version=VERSION):
    """可续发的失败类型。请求为store=false的无状态推理，重复发送没有服务端副作用；
    仍只在显式配置的次数内续发，原回执不改写，每次尝试都计入任务与全局预算。"""
    if record.get('status')=='rejected' and record.get('error')=='http_429':return 'http_429'
    if (version==VERSION and record.get('status')=='unknown'
            and (record.get('error_code') in INTERRUPTED_CODES or record.get('error') in INTERRUPTED_ERRORS)):
        return 'interrupted'
    # 回执路径由任务锁、共享页锁和Worker独占锁保护：持锁方读到requesting，只能是已退出进程留下的
    # 未完成发送，客户端同样没有终态。
    if version==VERSION and record.get('status')=='requesting':return 'interrupted'
    return None


def limits(client):
    config=client.flow.config if getattr(client,'flow',None) else {}
    return {'http_429':config.get('max_429_retries',0),'interrupted':config.get('max_interrupted_retries',0)}


def attempt_path(base,index):
    return base.with_name(base.name.removesuffix('.receipt.json')+'.attempt-'+str(index)+'.receipt.json')


def used(receipt,chain,kind):
    """同链内该类失败已出现的次数；允许的总发送数为1+上限。"""
    return sum(retry_kind(json.loads(attempt_path(Path(receipt),i).read_text()),chain['transport_version'])==kind
        for i in range(len(chain['attempts'])))


def pending_record(path,chain):
    """跨任务核对原发送账簿；无回执的已预留尝试不得重新发送。"""
    pending=chain.get('pending')
    if not pending:return None
    if pending.get('index')!=len(chain['attempts']):raise ValueError('续发待发送序号不连续')
    budget=Path(pending['budget'])
    saved=json.loads(budget.read_text())
    if saved.get('version')!=1:raise ValueError('续发原发送账簿格式无效')
    target=attempt_path(Path(path),pending['index'])
    entry=saved['entries'].get(str(target.resolve()))
    if entry and entry.get('request_sha256')!=chain['request_sha256']:
        raise ValueError('续发原账簿请求签名变化')
    if target.exists():
        record=json.loads(target.read_text())
        if record.get('request_sha256')!=chain['request_sha256'] or not entry:
            raise ValueError('续发待发送回执与原账簿不一致')
        return record
    if entry:return dict(status='unknown',request_sha256=chain['request_sha256'],error='missing_retry_receipt')
    return None # 仅链预记，尚未预留发送预算，可安全重新申请配额。


def read_receipt(path):
    """链入口仅存签名引用，模型原响应仍只存一份。"""
    raw=json.loads(Path(path).read_text())
    if raw.get('transport_version') not in VERSIONS:return raw
    attempts=raw.get('attempts')
    if not isinstance(attempts,list) or not attempts:raise ValueError('续发链缺原回执')
    record=None
    for i,ref in enumerate(attempts):
        target=attempt_path(Path(path),i)
        record=json.loads(target.read_text())
        if digest(record)!=ref.get('sha256') or record.get('request_sha256')!=raw['request_sha256']:
            raise ValueError('续发原回执或请求签名变化')
        if i<len(attempts)-1 and not retry_kind(record,raw['transport_version']):
            raise ValueError('仅明确限流或传输中断可以进入下次尝试')
    expected='retry_wait' if retry_kind(record,raw['transport_version']) and not raw.get('exhausted') else record['status']
    if raw['status']!=expected:raise ValueError('续发链入口与实际终态不一致')
    pending=pending_record(path,raw)
    if pending is not None:record=pending
    return dict(record,attempt_chain_sha256=digest(raw))


def blocking_status(path):
    """目录扫描用的有效状态：已入链的单次尝试由链入口代表，等待有界续发不算结果不明。"""
    path=Path(path);raw=json.loads(path.read_text())
    if raw.get('transport_version') in VERSIONS:
        record=read_receipt(path)
        if raw['status']=='retry_wait' and record.get('error')!='missing_retry_receipt':
            return 'retry_wait'
        return record.get('status')
    match=ATTEMPT.match(path.name)
    if match:
        entry=path.with_name(match.group(1)+'.receipt.json')
        chain=json.loads(entry.read_text()) if entry.exists() else {}
        if chain.get('transport_version') in VERSIONS and (int(match.group(2))<len(chain.get('attempts',[]))
                or (chain.get('pending') or {}).get('index')==int(match.group(2))):
            return 'chained'
    # 尚未入链的传输中断是已确定没有终态的失败，由同一回执路径按配置原位续发；未配置续发时调用处仍得到unknown。
    if retry_kind(raw)=='interrupted':return 'interrupted'
    return raw.get('status')


def retry_pending(path):
    path=Path(path)
    return path.exists() and blocking_status(path) in {'retry_wait','interrupted'}


def origin_digest(path):
    raw=json.loads(Path(path).read_text())
    if raw.get('transport_version') in VERSIONS:return digest(json.loads(attempt_path(Path(path),0).read_text()))
    return digest(raw)


def call(client,payload,receipt,send):
    from pipeline import Paused
    receipt=Path(receipt)
    maximum=limits(client)
    chain=json.loads(receipt.read_text()) if receipt.exists() else None
    if chain and chain.get('transport_version') in VERSIONS:
        prior=read_receipt(receipt)
        if chain['request_sha256']!=digest(dict(endpoint=client.config.responses_url,payload=payload)):
            raise ValueError('API请求签名漂移')
        if chain['status']!='retry_wait':return prior
        if chain.get('pending'):
            pending=pending_record(receipt,chain)
            if pending is not None:
                # 发送落盘后、链更新前崩溃：先接回原回执，不额外请求；预留后连回执都没有的仍为结果不明。
                if pending.get('error')=='missing_retry_receipt':return prior
                return finish(receipt,chain,pending,maximum)
            chain.pop('pending');atomic(receipt,chain)
        prior=json.loads(attempt_path(receipt,len(chain['attempts'])-1).read_text())
    else:
        prior=send(payload,receipt)
        kind=retry_kind(prior)
        if not kind or not maximum[kind]:return prior
        original=attempt_path(receipt,0)
        if original.exists():raise ValueError('续发原回执目标已存在，须核对而非覆盖')
        os.link(receipt,original)
        chain=dict(transport_version=VERSION,request_sha256=prior['request_sha256'],status='retry_wait',
            attempts=[dict(sha256=digest(prior))],exhausted=False)
        atomic(receipt,chain)
    kind=retry_kind(prior,chain['transport_version'])
    if used(receipt,chain,kind)>maximum[kind]:
        chain.update(status=prior['status'],exhausted=True);atomic(receipt,chain)
        return read_receipt(receipt)
    if kind=='http_429':
        wait_until=max(prior.get('retry_after_at',0),prior.get('sent_at',0)+client.flow.config['cooldown_seconds'])
        if client.flow.now()<wait_until:raise Paused('queued','模型明确限流，已保留原回执；冷却后在累计预算内进行有界续跑')
    index=len(chain['attempts'])
    target=attempt_path(receipt,index)
    if target.exists():raise ValueError('存在未入链尝试，须核对而非覆盖')
    # 先记录原账簿身份，覆盖预算预留后、回执写入前的跨任务崩溃窗口。
    chain['pending']=dict(index=index,budget=str(client.budget.path.resolve()))
    atomic(receipt,chain)
    result=send(payload,target)
    return finish(receipt,chain,result,maximum)


def finish(receipt,chain,result,maximum):
    from pipeline import Paused
    chain['attempts'].append(dict(sha256=digest(result)))
    chain.pop('pending',None)
    kind=retry_kind(result,chain['transport_version'])
    exhausted=bool(kind) and used(receipt,chain,kind)>maximum[kind]
    retry=bool(kind) and not exhausted
    chain.update(status='retry_wait' if retry else result['status'],exhausted=exhausted)
    atomic(receipt,chain)
    if retry:
        raise Paused('queued','模型再次明确限流，等待受控冷却；原尝试与累计预算保留' if kind=='http_429'
            else '模型请求传输中断，未取得终态；原尝试保留，在累计预算内有界续发')
    return read_receipt(receipt)
