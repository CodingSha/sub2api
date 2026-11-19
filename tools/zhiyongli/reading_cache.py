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

    def inspect(self,item,payload):
        """严格只读检查现有共享页；不能把损坏或结果不明当成缓存未命中。"""
        from pipeline import Paused
        from request_retry import read_receipt,origin_digest,blocking_status,VERSIONS,ATTEMPT,retry_kind
        source=self.root/digest(item)
        if not source.exists():return None
        lock_path=source/'page.lock'
        try:lock=lock_path.open('rb')
        except FileNotFoundError:
            if any(source.iterdir()):raise ValueError('共享页已有证据但来源锁缺失，须核原执行记录') from None
            return None
        with lock:
            try:fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
            except BlockingIOError:
                raise Paused('queued','相同来源页正在读取，等待原请求落盘后检查') from None
            key=digest(dict(endpoint=self.client.config.responses_url,payload=payload))
            expected=source/(key+'.receipt.json')
            receipts=[]
            for path in source.glob('*.receipt.json'):
                raw=json.loads(path.read_text())
                # 普通回执在本次共享锁内只读一份；重试链沿用完整原核验路径。
                plain=raw.get('transport_version') not in VERSIONS and not ATTEMPT.match(path.name)
                status=(('interrupted' if retry_kind(raw)=='interrupted' else raw.get('status'))
                    if plain else blocking_status(path))
                if plain and raw.get('status')=='requesting':status='requesting'
                if status in {'unknown','requesting'}:
                    raise Paused('unknown','同一来源页已有结果不明请求，禁止跨任务或换合同重发')
                if status!='chained':
                    summary=(dict(origin=digest(raw),request_sha256=raw.get('request_sha256'),
                        record=raw if path==expected else None) if plain else None)
                    receipts.append((path,summary))
                del raw
            for seal in source.glob('*.seal.json'):
                if not seal.with_name(seal.name.replace('.seal.json','.receipt.json')).exists():
                    raise ValueError('共享页完成签名对应回执缺失')
            reservation=source/'reservation.json'
            if reservation.exists():
                prior=json.loads(reservation.read_text())
                if not isinstance(prior,dict) or not isinstance(prior.get('budget'),str) or not isinstance(prior.get('receipt'),str):
                    raise ValueError('共享页发送预约格式无效')
                budget=Path(prior['budget']);reserved=Path(prior['receipt'])
                if not budget.exists():raise ValueError('共享页发送账簿丢失')
                saved=json.loads(budget.read_text())
                if not isinstance(saved,dict) or saved.get('version')!=1 or not isinstance(saved.get('entries'),dict):
                    raise ValueError('共享页发送账簿格式无效')
                if any(not isinstance(name,str) or not isinstance(entry,dict) for name,entry in saved['entries'].items()):
                    raise ValueError('共享页发送账簿条目无效')
                if reserved.parent.resolve()!=source.resolve():raise ValueError('共享页发送预约引用越界')
                if prior['receipt'] in saved['entries'] and not reserved.exists():
                    raise Paused('unknown','共享页已有发送前账簿但回执缺失，须核账，禁止跨任务重发')
            found=None
            for path,summary in receipts:
                record=read_receipt(path) if summary is None else summary['record']
                origin=origin_digest(path) if summary is None else summary['origin']
                request_sha=record.get('request_sha256') if summary is None else summary['request_sha256']
                terminal=digest(record) if summary is None else origin
                seal=path.with_name(path.name.replace('.receipt.json','.seal.json'))
                signed=json.loads(seal.read_text()) if seal.exists() else {}
                request=path.name.removesuffix('.receipt.json')
                if (not isinstance(signed,dict) or signed.get('origin_sha256')!=origin
                        or signed.get('request_sha256')!=request
                        or request_sha!=request
                        or signed.get('terminal_sha256') is not None and signed['terminal_sha256']!=terminal):
                    raise ValueError('共享阅读回执缺完成签名或签名变化，须核原执行记录')
                if path==expected:found=(path,record)
            return found

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
            from request_retry import blocking_status
            for receipt in source.glob('*.receipt.json'):
                if blocking_status(receipt) in {'unknown','requesting'}:
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
