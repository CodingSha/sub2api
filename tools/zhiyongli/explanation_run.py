"""同一受管共享来源域的说明执行归属；不迁移、不重置旧回执。"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path

from runtime import digest,atomic
from shared_weeks import read_ref
from pipeline import Paused


class Ownership:
    """逻辑调用意图独立于输出目录持久保存，不替代模型预算或重试链。"""
    def __init__(self,directory,identity,read_only):
        self.directory=directory;self.identity=identity;self.read_only=read_only
        self.path=directory/'requests.json'

    def load(self):
        value=json.loads(self.path.read_text())
        if (not isinstance(value,dict) or set(value)!={'identity','entries','sha256'}
                or value['identity']!=self.identity or not isinstance(value['entries'],dict)
                or value['sha256']!=digest({k:v for k,v in value.items() if k!='sha256'})):
            raise ValueError('说明调用意图账损坏或归属变化，禁止归零')
        for name,item in value['entries'].items():
            if (not isinstance(name,str) or not Path(name).is_absolute()
                    or Path(self.identity['root']) not in Path(name).parents
                    or not isinstance(item,dict) or set(item)!={'request_sha256'}
                    or not isinstance(item['request_sha256'],str) or len(item['request_sha256'])!=64):
                raise ValueError('说明调用意图条目无效')
        return value

    def call(self,client,payload,receipt):
        value=self.load();name=str(Path(receipt).resolve())
        if Path(self.identity['root']) not in Path(name).parents:
            raise ValueError('说明回执路径越出登记输出目录')
        expected=dict(request_sha256=digest(dict(endpoint=client.config.responses_url,payload=payload)))
        prior=value['entries'].get(name)
        if prior is not None:
            if prior!=expected:raise ValueError('说明调用意图的实际输入变化，禁止换合同重发')
            if not Path(name).is_file():raise Paused('unknown','说明已有调用意图但回执丢失，禁止同目录重新发送')
        else:
            if Path(name).exists():raise ValueError('说明回执缺少原调用意图，禁止补登记放行')
            if self.read_only:raise ValueError('只读说明核验缺调用意图，不登记或发送')
            def register():
                value['entries'][name]=expected
                value['sha256']=digest({k:v for k,v in value.items() if k!='sha256'})
                atomic(self.path,value)
            from worker import JobClient
            if type(client) is JobClient:
                # 明确通过预算及全局发送槽后，才登记说明意图；失败不删除任何已有记录。
                return client._call(payload,Path(name),before_send=register)
            register()
        return client._call(payload,Path(name))


@contextmanager
def claim(entry_ref,root,*,read_only=False):
    entry=read_ref(entry_ref);path=Path(entry_ref['path']).resolve()
    mode=entry.get('page_navigation_mode');policy=entry.get('policy_sha256')
    key=digest(dict(policy_sha256=policy,page_navigation_mode=mode)) if mode else policy
    if path.parent.name!=entry.get('source_identity') or path.stem!=key or path.suffix!='.json':
        raise ValueError('说明须绑定规范共享来源索引，不接受复制或另造的索引路径')
    directory=path.parent/'explanation-run';root=Path(root).resolve()
    created=False
    try:
        if not read_only:directory.mkdir(mode=0o700);created=True
    except FileExistsError:pass
    if not directory.is_dir():raise ValueError('说明执行登记缺失，只读不得创建')
    owner=directory/'owner.json';lock_path=directory/'run.lock';ledger=directory/'requests.json'
    if not created and (not owner.is_file() or not lock_path.is_file() or not ledger.is_file()):
        raise ValueError('说明执行登记或锁丢失，保留现场，禁止重建归属')
    descriptor=os.open(lock_path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600) if created else os.open(lock_path,os.O_RDONLY)
    with os.fdopen(descriptor,'r') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Paused('queued','相同来源的说明正在执行，等待原执行落盘') from None
        expected=dict(version=1,source_identity=entry['source_identity'],root=str(root))
        expected['sha256']=digest(expected)
        if created:
            atomic(owner,expected)
            initial=dict(identity={k:v for k,v in expected.items() if k!='sha256'},entries={})
            initial['sha256']=digest(initial);atomic(ledger,initial)
        else:
            value=json.loads(owner.read_text())
            if not isinstance(value,dict) or set(value)!=set(expected) or value.get('version')!=1 or value.get('source_identity')!=entry['source_identity'] or not isinstance(value.get('root'),str) or not Path(value['root']).is_absolute() or value.get('sha256')!=digest({k:v for k,v in value.items() if k!='sha256'}):
                raise ValueError('说明执行归属登记损坏，保留原文件待核')
            if value!=expected:
                from request_retry import blocking_status
                original=Path(value['root'])
                registered=Ownership(directory,{k:v for k,v in value.items() if k!='sha256'},read_only).load()
                unknown=(any(not Path(name).is_file() for name in registered['entries'])
                    or any(blocking_status(p) in {'unknown','requesting'} for p in original.rglob('*.receipt.json')))
                raise Paused('unknown' if unknown else 'needs_review',
                    '原说明存在结果不明请求，禁止换目录重发' if unknown else '该来源已登记说明执行，须复用原输出目录，不自动迁移合同')
        ownership=Ownership(directory,{k:v for k,v in expected.items() if k!='sha256'},read_only)
        value=ownership.load()
        for name in value['entries']:
            if not Path(name).is_file():raise Paused('unknown','说明已有调用意图但回执丢失，禁止重新发送')
        yield ownership
