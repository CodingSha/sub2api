"""任务发送前账簿；共享回执复用不计新发送，崩溃预留不得绕路重发。"""
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import time

from runtime import atomic


class RequestBudget:
    def __init__(self,root,limit,turn_limit):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/'request-budget.json'
        self.limit=limit;self.turn_limit=turn_limit;self.turn_used=0
        with self.lock() as entries:
            if not self.path.exists():
                # 旧任务保守继承原回执；不能因升级账簿重置预算。
                for path in self.root.rglob('*.receipt.json'):
                    value=json.loads(path.read_text())
                    if value.get('shared_reading_receipt'):continue
                    entries[str(path.resolve())]=dict(request_sha256=value.get('request_sha256'),
                        accounting='legacy_receipt',at=value.get('sent_at'))

    @contextmanager
    def lock(self):
        with (self.root/'request-budget.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            saved=json.loads(self.path.read_text()) if self.path.exists() else dict(version=1,entries={})
            if saved.get('version')!=1 or not isinstance(saved.get('entries'),dict):
                raise ValueError('任务请求账簿格式无效，禁止重置')
            yield saved['entries']
            atomic(self.path,saved)

    def used(self):
        with self.lock() as entries:return len(entries)

    def check_entries(self,entries,receipt):
        from pipeline import Paused
        name=str(Path(receipt).resolve())
        if name in entries:
            raise Paused('unknown','已有发送前预算记录但回执缺失，须先核账，禁止自动重发')
        if len(entries)>=self.limit:
            raise Paused('needs_review','达到本任务模型请求预算，已保留完成阶段；核对阅读规模后再决定继续')
        if self.turn_used>=self.turn_limit:
            raise Paused('queued','当前批次已保存，等待轮转继续；未重发已有请求')

    def check(self,receipt):
        with self.lock() as entries:self.check_entries(entries,receipt)

    def reserve(self,receipt,request_hash):
        with self.lock() as entries:
            self.check_entries(entries,receipt)
            name=str(Path(receipt).resolve())
            entries[name]=dict(request_sha256=request_hash,accounting='send_intent',at=time.time())
        self.turn_used+=1
