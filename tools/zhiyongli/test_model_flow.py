"""全局配额、限流和崩溃恢复；合成时钟与回执，不访问模型端点。"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest

from model_flow import ModelFlow
from pipeline import Paused
from runtime import atomic


class ModelFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=10000
        self.config=dict(version=1,budget_key='synthetic-cycle',approved_by='合成确认人',reason='合成测试',
            max_concurrency=2,requests_per_minute=10,reserved_tokens_per_minute=1000,max_requests=100,
            transient_failure_limit=2,cooldown_seconds=120)

    def flow(self,**change):return ModelFlow(self.root/'flow',dict(self.config,**change),now=lambda:self.now)

    def call(self,flow,name,status='received',**record):
        path=self.root/(name+'.receipt.json')
        with flow.slot(name,path,b'12345',5):atomic(path,dict(status=status,**record))

    def test_concurrency_is_global_and_terminal_receipt_releases_slot(self):
        flow=self.flow(max_concurrency=1);first=self.root/'first.receipt.json'
        with flow.slot('first',first,b'x',1):
            with self.assertRaises(Paused) as ex:self.call(self.flow(max_concurrency=1),'second')
            self.assertEqual('queued',ex.exception.state)
            self.assertFalse((self.root/'second.receipt.json').exists())
            atomic(first,dict(status='received'))
        self.call(flow,'second')
        self.assertEqual([dict(status='received',count=2)],flow.snapshot()['states'])

    def test_rate_and_token_reservations_survive_restart(self):
        flow=self.flow(requests_per_minute=1)
        self.call(flow,'first')
        with self.assertRaises(Paused):self.call(self.flow(requests_per_minute=1),'second')
        self.now+=61;self.call(flow,'second')
        with self.assertRaises(Paused) as ex:
            with flow.slot('oversize',self.root/'large',b'x'*1001,0):pass
        self.assertEqual('needs_review',ex.exception.state)

    def test_total_budget_is_not_reset_by_restart_or_minute_window(self):
        self.call(self.flow(max_requests=1),'first');self.now+=61
        with self.assertRaises(Paused) as ex:self.call(self.flow(max_requests=1),'second')
        self.assertEqual('needs_review',ex.exception.state)

    def test_429_honors_server_cooldown_and_does_not_retry_original(self):
        flow=self.flow();self.call(flow,'limited','rejected',error='http_429',retry_after_at=self.now+300)
        self.now+=121
        with self.assertRaises(Paused):self.call(flow,'other')
        self.now+=180;self.call(flow,'other')
        self.assertEqual('rejected',json.loads((self.root/'limited.receipt.json').read_text())['status'])

    def test_unknown_circuit_and_orphan_reservation_are_persistent(self):
        flow=self.flow();self.call(flow,'unknown1','unknown');self.call(flow,'unknown2','unknown')
        with self.assertRaises(Paused):self.call(self.flow(),'third')
        self.now+=121;self.call(flow,'third')
        # 模拟预留后进程退出，未进入Python finally；超时只释放并发，仍计总预算。
        with flow.connect() as db:
            db.execute("INSERT INTO attempts VALUES('orphan','synthetic-cycle','sha',?, ?,10,'active',NULL,NULL,NULL,2147483647)",
                (str(self.root/'missing'),self.now-1861))
        self.call(flow,'fourth')
        states={v['status']:v['count'] for v in flow.snapshot()['states']}
        self.assertEqual(3,states['unknown'])

    def test_slow_request_in_live_process_never_releases_concurrency_by_age(self):
        flow=self.flow(max_concurrency=1)
        with flow.slot('slow',self.root/'slow.receipt.json',b'x',1):
            self.now+=4000
            with self.assertRaises(Paused):self.call(flow,'other')
            self.assertEqual('concurrency_full',flow.blocked())
            atomic(self.root/'slow.receipt.json',dict(status='unknown'))

    def test_known_local_budget_refusal_releases_global_reservation(self):
        flow=self.flow(max_requests=1)
        with self.assertRaises(Paused):
            with flow.slot('local',self.root/'not-sent',b'x',1):raise Paused('needs_review','合成本地预算拒绝')
        self.call(flow,'actual')
        self.assertEqual(0,flow.snapshot()['measured_usage_requests'])

    def test_operations_snapshot_uses_read_only_connection(self):
        flow=self.flow();self.call(flow,'first',response=dict(status='completed',usage=dict(total_tokens=8)))
        readonly=ModelFlow(self.root/'flow',self.config,read_only=True)
        self.assertEqual(8,readonly.snapshot()['actual_tokens'])
        import sqlite3
        with self.assertRaises(sqlite3.OperationalError):
            with readonly.connect() as db:db.execute('DELETE FROM attempts')
        self.assertEqual(1,flow.snapshot()['measured_usage_requests'])

    def test_startup_reconciles_reused_live_pid_and_preserves_unknown_budget(self):
        import os
        flow=self.flow(max_concurrency=1,max_requests=2)
        paths=[self.root/'missing.receipt.json',self.root/'requesting.receipt.json']
        atomic(paths[1],dict(status='requesting',request_sha256='original'))
        with flow.connect() as db:
            for i,path in enumerate(paths):
                db.execute("INSERT INTO attempts VALUES(?,?,?, ?,?,10,'active',NULL,NULL,NULL,?)",
                    (str(i),'synthetic-cycle','original',str(path),self.now,os.getpid()))
        with (self.root/'worker.lock').open('a') as lock:flow.reconcile_startup(lock)
        self.assertEqual([dict(status='unknown',count=2)],flow.snapshot()['states'])
        self.assertEqual('global_budget_exhausted',flow.blocked())
        self.assertFalse(paths[0].exists())
        self.assertEqual('requesting',json.loads(paths[1].read_text())['status'])
        with (self.root/'worker.lock').open('a') as lock:flow.reconcile_startup(lock)
        self.assertEqual([dict(status='unknown',count=2)],flow.snapshot()['states'])

    def test_startup_recovers_terminal_receipt_and_requires_exclusive_worker_lock(self):
        import fcntl
        flow=self.flow();path=self.root/'done.receipt.json'
        atomic(path,dict(status='received',response=dict(usage=dict(total_tokens=7))))
        with flow.connect() as db:
            db.execute("INSERT INTO attempts VALUES('done','synthetic-cycle','sha',?,?,10,'active',NULL,NULL,NULL,1)",(str(path),self.now))
        worker=self.root/'worker.lock'
        with worker.open('a') as held,worker.open('a') as contender:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):flow.reconcile_startup(contender)
            self.assertEqual([dict(status='active',count=1)],flow.snapshot()['states'])
            flow.reconcile_startup(held)
        self.assertEqual((7,[dict(status='received',count=1)]),(flow.snapshot()['actual_tokens'],flow.snapshot()['states']))
        with (self.root/'other.lock').open('a') as wrong:
            with self.assertRaises(ValueError):flow.reconcile_startup(wrong)

    def test_hundred_concurrent_claims_cannot_exceed_shared_concurrency(self):
        flow=self.flow(max_concurrency=3,requests_per_minute=100)
        release=threading.Event();ready=threading.Event();mutex=threading.Lock();entered=[];refused=[]
        def work(n):
            try:
                path=self.root/(str(n)+'.receipt.json')
                with flow.slot(str(n),path,b'x',1):
                    with mutex:
                        entered.append(n)
                        if len(entered)==3:ready.set()
                    release.wait(5);atomic(path,dict(status='received'))
            except Paused:
                with mutex:refused.append(n)
        with ThreadPoolExecutor(max_workers=100) as pool:
            futures=[pool.submit(work,n) for n in range(100)]
            self.assertTrue(ready.wait(5))
            # 后续申请必须全部判定完，之后才释放前三个，避免把并发测成总数。
            for future in futures[3:]:
                # 抢到配额的不一定是最先提交的三个，故不按序等待。
                if future.done():future.result()
            import time
            deadline=time.monotonic()+5
            while len(refused)<97 and time.monotonic()<deadline:time.sleep(.01)
            release.set()
            for future in futures:future.result()
        self.assertEqual((3,97),(len(entered),len(refused)))


if __name__=='__main__':unittest.main()
