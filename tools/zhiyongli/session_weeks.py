"""会话周评的独立共享入口；同源同合同复用原回执，不混用旧首读缓存。"""
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace

from navigation_access import freeze
from runtime import digest,policy
from shared_weeks import file_digest,source_identity


def resolve(directory):
    root=Path(directory)
    pointer=root/'shared-session-week.json'
    if not pointer.exists():return root
    ref=json.loads(pointer.read_text())
    path=Path(ref['path'])
    if file_digest(path)!=ref['sha256']:raise ValueError('共享会话周评索引签名变化')
    entry=json.loads(path.read_text())
    if entry['state']!='completed':raise ValueError('共享会话周评尚未完成')
    verify(entry)
    return Path(entry['directory'])


def verify(entry):
    """原始动作逐条重放；校验快照不能替代实际评分与原件核验。"""
    from request_retry import read_receipt
    from session_review import evaluate
    if entry['policy_sha256']!=policy():raise ValueError('共享会话周评执行合同变化')
    root=Path(entry['directory'])
    for ref in entry['files']:
        if file_digest(ref['path'])!=ref['sha256']:raise ValueError('共享会话周评原文件签名变化')
    packet=json.loads((root/'packet.json').read_text())
    if source_identity(packet)!=entry['source_identity']:raise ValueError('共享会话周评来源不一致')
    class Receipts:
        config=SimpleNamespace(model=entry['model'],responses_url=entry['endpoint'])
        def _call(self,payload,target):
            if not target.exists():raise ValueError('共享会话周评缺原回执，禁止重新请求模型')
            record=read_receipt(target)
            if record.get('request_sha256')!=digest(dict(endpoint=self.config.responses_url,payload=payload)):
                raise ValueError('共享会话周评实际请求签名变化')
            return record
    client=Receipts()
    seed=root/'seed-input.json'
    if seed.exists():
        ref=json.loads(seed.read_text());client.session_seed_from=ref['source']
        client.session_seed_reading_only=ref['reading_only'];client.session_seed_allow_terminal=ref.get('allow_terminal',False)
    result=evaluate(packet,root,client)
    if digest(result)!=entry['bundle_sha256']:raise ValueError('共享会话周评分数或说明与原始回执不一致')
    return result


def evaluate(packet,directory,client,progress,shared_root):
    """未完成所有者保留原执行目录；其他任务等待，不重复发送其未知请求。"""
    from session_review import evaluate as run
    from pipeline import Paused
    root=Path(directory);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    identity=source_identity(packet);shared=Path(shared_root)/identity
    shared.mkdir(parents=True,exist_ok=True,mode=0o700)
    entry_path=shared/(digest(dict(policy=policy(),model=client.config.model,endpoint=client.config.responses_url))+'.json')
    with (shared/'week.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Paused('queued','同来源会话周评正在执行，等待原回执') from None
        if entry_path.exists():
            entry=json.loads(entry_path.read_text())
            if entry['state']=='completed':
                if source_identity(packet)!=entry['source_identity']:raise ValueError('共享会话周评范围变化')
                result=verify(entry)
                if Path(entry['directory'])!=root.resolve():
                    freeze(root/'shared-session-week.json',dict(path=str(entry_path.resolve()),sha256=file_digest(entry_path),
                        packet_sha256=digest(packet),source_identity=identity))
                return result
            if entry['directory']!=str(root.resolve()):
                raise Paused('queued','同来源会话周评尚未完成，沿原任务接续，不另起重复评审')
        else:
            freeze(entry_path,dict(state='running',source_identity=identity,directory=str(root.resolve()),policy_sha256=policy()))
        result=run(packet,root,client,progress)
        # 索引只登记完整结果；运行状态不被解释为语义复核通过。
        from runtime import atomic
        files=[dict(path=str(path.resolve()),sha256=file_digest(path)) for path in sorted(root.rglob('*'))
            if path.is_file() and path.suffix in {'.json','.sqlite'} and path.name!='request-budget.json']
        entry=dict(state='completed',source_identity=identity,directory=str(root.resolve()),policy_sha256=policy(),
            bundle_sha256=digest(result),model=client.config.model,endpoint=client.config.responses_url,files=files)
        atomic(entry_path,entry)
        return result
