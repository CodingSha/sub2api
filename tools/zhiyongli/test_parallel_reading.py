"""合成响应验证单人页并发、在途落盘及原额度边界，不调用真实模型。"""
from concurrent.futures import ThreadPoolExecutor
import io
import json
import threading
import time
import unittest
from unittest.mock import patch

from bounded_read import read_all
from model_config import ModelConfig
from pipeline import Paused
import test_reading_cache as fixtures


class ParallelReadingTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    client=fixtures.ReadingCacheTests.client
    packet=fixtures.ReadingCacheTests.packet

    def read(self,packet,client,progress=None):
        return read_all(packet,client.root/'reading',client=client,limit=None,on_page=progress)

    def test_two_people_four_pages_each_share_eight_global_slots_and_keep_budgets(self):
        from model_flow import ModelFlow
        flow=ModelFlow(self.root/'flow',dict(version=1,budget_key='synthetic',approved_by='合成确认',reason='并行验证',
            max_concurrency=8,requests_per_minute=100,reserved_tokens_per_minute=10000000,
            max_requests=100,transient_failure_limit=2,cooldown_seconds=60))
        clients=[self.client('person'+str(i)) for i in range(2)]
        for client in clients:client.flow=flow;client.reading_concurrency=4
        packets=[dict(self.packet(73),user_id=str(i+1)) for i in range(2)]
        original=ModelConfig.opener(self.config);barrier=threading.Barrier(8)
        def send(request,**kwargs):
            barrier.wait(timeout=5)
            self.assertEqual(8,next(s['count'] for s in flow.snapshot()['states'] if s['status']=='active'))
            barrier.wait(timeout=5)
            return original.open(request,**kwargs)
        class Opener:
            open=staticmethod(send)
        with patch.object(ModelConfig,'opener',return_value=Opener()):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda pair:self.read(*pair),zip(packets,clients)))
        self.assertEqual([[0,1,2,3]]*2,[r['received_pages'] for r in results])
        self.assertEqual([4,4],[c.budget.used() for c in clients])
        self.assertEqual(8,flow.snapshot()['request_budget']['used'])
        self.assertEqual('received',flow.snapshot()['states'][0]['status'])

    def test_overlapping_pages_keep_original_proofs_order_and_resume_without_new_send(self):
        client=self.client('parallel');client.reading_concurrency=2
        original=ModelConfig.opener(self.config);barrier=threading.Barrier(2)
        active=0;peak=0;lock=threading.Lock();main=threading.get_ident();progress=[]
        def send(request,**kwargs):
            nonlocal active,peak
            with lock:active+=1;peak=max(active,peak)
            page=json.loads(json.loads(request.data)['input'])
            try:
                if page['fragments'][0]['event_id'] in {'e0','e24'}:barrier.wait(timeout=3)
                return original.open(request,**kwargs)
            finally:
                with lock:active-=1
        class Opener:
            open=staticmethod(send)
        def report(done,total):
            self.assertEqual(main,threading.get_ident());progress.append((done,total))
        with patch.object(ModelConfig,'opener',return_value=Opener()):
            result=self.read(self.packet(49),client,report)
        self.assertEqual(2,peak);self.assertEqual([0,1,2],result['received_pages'])
        self.assertEqual('reading_complete_pending_reconciliation',result['state'])
        self.assertEqual([(0,3),(1,3),(2,3),(3,3)],progress)
        proofs={str(p):p.read_bytes() for p in client.root.rglob('*.checked.json')}
        self.assertEqual(3,client.budget.used());sent=self.calls
        self.assertEqual(result,self.read(self.packet(49),client))
        self.assertEqual(sent,self.calls)
        self.assertEqual(proofs,{str(p):p.read_bytes() for p in client.root.rglob('*.checked.json')})

    def test_failed_page_stops_new_dispatch_and_drains_successful_peer(self):
        client=self.client('drain');client.reading_concurrency=2
        original=ModelConfig.opener(self.config);barrier=threading.Barrier(2)
        def send(request,**kwargs):
            page=json.loads(json.loads(request.data)['input']);first=page['fragments'][0]['event_id']
            barrier.wait(timeout=3)
            if first=='e0':
                return io.BytesIO(b'data: {"type":"response.failed","response":{"status":"failed","error":{"code":"synthetic"}}}\n\n')
            time.sleep(.1)
            return original.open(request,**kwargs)
        class Opener:
            open=staticmethod(send)
        with patch.object(ModelConfig,'opener',return_value=Opener()):result=self.read(self.packet(49),client)
        self.assertEqual('reading_blocked',result['state']);self.assertEqual([1],result['received_pages'])
        self.assertEqual(0,result['blocked'][0]['page'])
        self.assertEqual(2,client.budget.used())
        self.assertEqual(['1.checked.json'],[p.name for p in client.root.rglob('*.checked.json')])
        self.assertFalse(any(p.name=='2.receipt.json' for p in client.root.rglob('*.receipt.json')))

    def test_parallel_turn_reservations_cannot_exceed_one_and_peer_is_preserved(self):
        client=self.client('turn',turn=1);client.reading_concurrency=2
        barrier=threading.Barrier(2);original=client.budget.check
        def check(receipt):
            original(receipt);barrier.wait(timeout=3)
        with patch.object(client.budget,'check',side_effect=check):
            with self.assertRaises(Paused) as error:self.read(self.packet(25),client)
        self.assertEqual('queued',error.exception.state)
        self.assertEqual((1,1,1),(self.calls,client.budget.used(),client.budget.turn_used))
        self.assertEqual(1,len(list(client.root.rglob('*.checked.json'))))
        client.budget.turn_used=0
        result=self.read(self.packet(25),client)
        self.assertEqual([0,1],result['received_pages']);self.assertEqual(2,self.calls)

    def test_many_reservations_share_personal_and_turn_limit(self):
        client=self.client('budget',limit=7,turn=3)
        def reserve(index):
            try:client.budget.reserve(client.root/(str(index)+'.receipt.json'),'synthetic');return True
            except Paused:return False
        with ThreadPoolExecutor(max_workers=12) as pool:success=list(pool.map(reserve,range(20)))
        self.assertEqual(3,sum(success));self.assertEqual((3,3),(client.budget.used(),client.budget.turn_used))


if __name__=='__main__':unittest.main()
