"""跨任务复用独立单页阅读；完整上下文归并与评分每次另行核对。"""
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path

from runtime import atomic,digest


class ReadingCache:
    def __init__(self,root,identity,client):
        from dsh_client import DSHClient
        if isinstance(client,DSHClient):raise ValueError('DSH尚未接入共享阅读合同')
        if not identity.get('user_id') or not identity.get('user_email'):
            raise ValueError('共享阅读必须绑定来源账户ID和邮箱')
        self.root=Path(root)/digest(identity);self.client=client
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)

    @contextmanager
    def page(self,page):
        with self.request(page,self.client.reading_payload(page)) as receipt:
            yield receipt

    @contextmanager
    def request(self,item,payload):
        """按完整局部材料及实际请求合同复用；导航与阅读使用不同根目录。"""
        from pipeline import Paused
        from request_retry import read_receipt,origin_digest
        source=self.root/digest(item);source.mkdir(parents=True,exist_ok=True,mode=0o700)
        key=digest(dict(endpoint=self.client.config.responses_url,payload=payload))
        with (source/'page.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise Paused('queued','相同来源页正在读取，等待原请求落盘后复用') from None
            for receipt in source.glob('*.receipt.json'):
                record=json.loads(receipt.read_text())
                if record.get('status') in {'unknown','requesting'}:
                    raise Paused('unknown','同一来源页已有结果不明请求，禁止跨任务或换合同重发')
            reservation=source/'reservation.json'
            if reservation.exists():
                prior=json.loads(reservation.read_text());budget=Path(prior['budget'])
                # 预留和回执间崩溃时，跨任务也须查原账簿，不能绕过未知发送。
                if not budget.exists():raise ValueError('共享页发送账簿丢失')
                entries=json.loads(budget.read_text())['entries']
                if prior['receipt'] in entries and not Path(prior['receipt']).exists():
                    raise Paused('unknown','共享页已有发送前账簿但回执缺失，须核账，禁止跨任务重发')
            receipt=source/(key+'.receipt.json')
            seal=source/(key+'.seal.json')
            if receipt.exists():
                record=read_receipt(receipt)
                signed=json.loads(seal.read_text()) if seal.exists() else {}
                if (signed.get('origin_sha256')!=origin_digest(receipt) or signed.get('request_sha256')!=key
                    or signed.get('terminal_sha256') is not None and signed['terminal_sha256']!=digest(record)):
                    raise ValueError('共享阅读回执缺完成签名或签名变化，须核原执行记录')
            atomic(reservation,dict(budget=str(self.client.budget.path.resolve()),receipt=str(receipt.resolve())))
            try:yield receipt
            finally:
                if receipt.exists():
                    record=read_receipt(receipt);raw=json.loads(receipt.read_text())
                    atomic(seal,dict(origin_sha256=origin_digest(receipt),request_sha256=key,
                        terminal_sha256=None if raw.get('status')=='retry_wait' else digest(record)))
