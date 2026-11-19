"""任务发送前账簿；共享回执复用不计新发送，崩溃预留不得绕路重发。"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import time

from runtime import atomic


class RequestBudget:
    def __init__(self,root,limit,turn_limit):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'request-budget.json'
        self.initialization=self.root/'request-budget-initialization.json'
        self.limit=limit;self.turn_limit=turn_limit;self.turn_used=0
        with self.lock(create=True) as entries:
            if not self.path.exists():
                # 旧任务保守继承原回执；不能因升级账簿重置预算。
                for path in self.root.rglob('*.receipt.json'):
                    value=json.loads(path.read_text())
                    if value.get('shared_reading_receipt'):continue
                    entries[str(path.resolve())]=dict(request_sha256=value.get('request_sha256'),
                        accounting='legacy_receipt',at=value.get('sent_at'))

    @contextmanager
    def lock(self, *, write=False, create=False):
        lock_path=self.root/'request-budget.lock'
        try:
            descriptor=os.open(lock_path,os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600);new_lock=True
        except FileExistsError:
            descriptor=os.open(lock_path,os.O_RDWR);new_lock=False
        with os.fdopen(descriptor,'a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            exists=self.path.exists()
            marker=json.loads(self.initialization.read_text()) if self.initialization.exists() else None
            if marker is not None and marker not in (dict(version=1,state='initializing'),dict(version=1,state='ready')):
                raise ValueError('任务请求账簿初始化标记无效，禁止重置')
            if not exists:
                if (not create or marker==dict(version=1,state='ready')
                        or marker is None and not new_lock):
                    raise ValueError('任务请求账簿缺失，禁止重置；旧锁无账簿须先核账')
                # initializing只允许构造期恢复；尚未完成构造，任何发送都未获授权。
                if marker is None:atomic(self.initialization,dict(version=1,state='initializing'))
            saved=json.loads(self.path.read_text()) if exists else dict(version=1,entries={})
            if not isinstance(saved,dict) or saved.get('version')!=1 or not isinstance(saved.get('entries'),dict):
                raise ValueError('任务请求账簿格式无效，禁止重置')
            yield saved['entries']
            if write or not exists:atomic(self.path,saved)
            # 必须先持久化账簿，再标记完成；失败可恢复初始化，不能抹掉已完成的发送意图。
            if create and marker!=dict(version=1,state='ready'):
                atomic(self.initialization,dict(version=1,state='ready'))

    def used(self):
        with self.lock() as entries:return len(entries)

    def check_entries(self,entries,receipt):
        from pipeline import Paused
        name=str(Path(receipt).resolve())
        if name in entries:
            raise Paused('unknown','已有发送前预算记录但回执缺失，须先核账，禁止自动重发')
        if len(entries)>=self.limit:
            raise Paused('needs_review','达到本任务模型请求预算，已保留完成阶段；核对阅读规模后再决定继续',reason_code='request_budget_exhausted')
        if self.turn_used>=self.turn_limit:
            raise Paused('queued','当前批次已保存，等待轮转继续；未重发已有请求')

    def check(self,receipt):
        with self.lock() as entries:self.check_entries(entries,receipt)

    def reserve(self,receipt,request_hash):
        with self.lock(write=True) as entries:
            self.check_entries(entries,receipt)
            name=str(Path(receipt).resolve())
            entries[name]=dict(request_sha256=request_hash,accounting='send_intent',at=time.time())
            # 同任务并行阅读共用本轮额度，检查和递增必须在同一把锁内。
            self.turn_used+=1
