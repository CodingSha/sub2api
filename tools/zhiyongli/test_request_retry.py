"""明确429、跨任务崩溃与回执签名回归，全部使用合成端点和时钟。"""
import json
import io
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from model_config import ModelConfig
from model_flow import ModelFlow
from pipeline import Paused
from request_retry import attempt_path,read_receipt
from runtime import atomic,digest
from worker import JobClient
import test_reading_cache as fixtures


class RetryTests(unittest.TestCase):
    def setUp(self):
        fixtures.ReadingCacheTests.setUp(self)
        self.now=time.time();self.errors=[429];self.sends=0
        original=self.config.opener();test=self
        class Opener:
            def open(self,request,**kw):
                test.sends+=1
                error=test.errors.pop(0) if test.errors else None
                if isinstance(error,dict):
                    return io.BytesIO(('data: '+json.dumps(dict(type='response.failed',response=error))+'\n\n').encode())
                if error=='cut':raise ConnectionResetError('合成传输中断')
                if error:raise HTTPError(request.full_url,error,'synthetic',{'Retry-After':'180'},None)
                return original.open(request,**kw)
        patcher=patch.object(ModelConfig,'opener',return_value=Opener());patcher.start();self.addCleanup(patcher.stop)
        self.flow=ModelFlow(self.root/'flow',dict(version=1,budget_key='synthetic',approved_by='合成',reason='回归',
            max_concurrency=1,requests_per_minute=100,reserved_tokens_per_minute=1000000,max_requests=100,
            transient_failure_limit=3,cooldown_seconds=120,max_429_retries=2),now=lambda:self.now)
        self.page=fixtures.ReadingCacheTests.packet(self)
        self.payload=dict(input=json.dumps(dict(fragments=[])))
        self.receipt=self.root/'review.receipt.json'

    def client(self,name='a',limit=100,turn=100):return JobClient(self.config,self.root/name,limit,turn,self.flow)

    def start(self,client=None):
        client=client or self.client()
        with self.assertRaises(Paused) as ex:client._call(self.payload,self.receipt)
        self.assertEqual('queued',ex.exception.state)
        return client

    def test_retry_after_restart_preserves_original_and_uses_both_budgets(self):
        first=self.start();self.assertEqual(1,self.sends)
        self.now+=121
        with self.assertRaises(Paused):self.client()._call(self.payload,self.receipt)
        self.assertEqual(1,self.sends)
        self.now+=61
        result=self.client()._call(self.payload,self.receipt)
        self.assertEqual('received',result['status']);self.assertEqual((2,2),(self.sends,first.budget.used()))
        self.assertEqual('http_429',json.loads(attempt_path(self.receipt,0).read_text())['error'])
        self.assertEqual(2,sum(v['count'] for v in self.flow.snapshot()['states']))
        self.client()._call(self.payload,self.receipt);self.assertEqual(2,self.sends)

    def test_attempt_identity_is_durable_before_send_and_new_only_for_new_attempt(self):
        import hashlib
        seen=[]
        class Opener:
            def open(inner,request,**kwargs):
                paths=list(self.root.rglob('*.receipt.json'))
                stored=[json.loads(p.read_text()) for p in paths]
                saved=next(v for v in stored if v.get('client_request_id')==request.get_header('X-client-request-id'))
                self.assertEqual('requesting',saved['status'])
                self.assertEqual(hashlib.sha256(request.data).hexdigest(),saved['wire_body_sha256'])
                seen.append(saved['client_request_id'])
                if len(seen)==1:raise HTTPError(request.full_url,429,'synthetic',{'Retry-After':'180'},None)
                return io.BytesIO(b'data: {"type":"response.completed","response":{"status":"completed","output":[]}}\n\n')
        with patch.object(ModelConfig,'opener',return_value=Opener()):
            self.start();self.now+=181
            self.client()._call(self.payload,self.receipt)
            self.client()._call(self.payload,self.receipt)
        self.assertEqual(2,len(seen));self.assertNotEqual(seen[0],seen[1])
        self.assertEqual(seen[1],read_receipt(self.receipt)['client_request_id'])

    def test_repeated_429_exhausts_without_resetting_across_jobs(self):
        self.errors=[429,429,429];self.start();self.now+=181
        with self.assertRaises(Paused):self.client('b')._call(self.payload,self.receipt)
        self.now+=181
        self.assertEqual('rejected',self.client('c')._call(self.payload,self.receipt)['status'])
        self.now+=181;self.client('d')._call(self.payload,self.receipt)
        self.assertEqual(3,self.sends)

    def test_unknown_and_non429_never_retry(self):
        for code in (500,400,408):
            self.errors=[code];path=self.root/(str(code)+'.receipt.json');before=self.sends
            client=self.client(str(code));client._call(self.payload,path);self.now+=181
            client._call(self.payload,path);self.assertEqual(before+1,self.sends)

    def interrupted_flow(self):
        self.flow=ModelFlow(self.root/'flow-cut',dict(self.flow.config,max_interrupted_retries=2),now=lambda:self.now)

    def test_interrupted_transport_resends_within_limit_and_keeps_original(self):
        from request_retry import blocking_status
        self.interrupted_flow();self.errors=['cut'];client=self.client()
        result=client._call(self.payload,self.receipt)
        self.assertEqual('received',result['status']);self.assertEqual((2,2),(self.sends,client.budget.used()))
        original=json.loads(attempt_path(self.receipt,0).read_text())
        self.assertEqual(('unknown','ConnectionResetError'),(original['status'],original['error']))
        self.assertEqual('chained',blocking_status(attempt_path(self.receipt,0)))
        self.assertEqual('received',blocking_status(self.receipt))
        self.client('b')._call(self.payload,self.receipt);self.assertEqual(2,self.sends)

    def test_repeated_interruption_exhausts_as_unknown_without_more_sends(self):
        from request_retry import blocking_status
        self.interrupted_flow();self.errors=['cut','cut','cut','cut']
        with self.assertRaises(Paused) as ex:self.client()._call(self.payload,self.receipt)
        self.assertEqual(('queued',2),(ex.exception.state,self.sends))
        self.assertEqual('retry_wait',blocking_status(self.receipt))
        self.assertEqual('unknown',self.client('b')._call(self.payload,self.receipt)['status'])
        self.assertEqual('unknown',self.client('c')._call(self.payload,self.receipt)['status'])
        self.assertEqual(3,self.sends);self.assertEqual('unknown',blocking_status(self.receipt))

    def test_orphaned_requesting_receipt_never_resends_even_with_transport_retries(self):
        from request_retry import blocking_status
        self.errors=[]
        atomic(self.receipt,dict(request_sha256=digest(dict(endpoint=self.config.responses_url,payload=self.payload)),
            status='requesting',sent_at=self.now,response_id=None))
        self.assertEqual(('requesting',0),(self.client()._call(self.payload,self.receipt)['status'],self.sends))
        self.interrupted_flow()
        self.assertEqual(('requesting',0),(self.client('b')._call(self.payload,self.receipt)['status'],self.sends))
        self.assertEqual('requesting',blocking_status(self.receipt))
        self.assertFalse(attempt_path(self.receipt,0).exists())

    def test_budget_refusal_keeps_pending_but_can_resume_without_new_send_intent(self):
        first=self.start(self.client(limit=1));self.now+=181
        with self.assertRaises(Paused) as ex:first._call(self.payload,self.receipt)
        self.assertEqual('needs_review',ex.exception.state);self.assertEqual(1,self.sends)
        self.assertEqual('received',self.client('b')._call(self.payload,self.receipt)['status'])
        self.assertEqual(2,self.sends)

    def test_pre_receipt_crash_blocks_other_job_and_shared_page(self):
        first=self.start();self.now+=181
        reserve=first.budget.reserve
        def crash(*args):reserve(*args);raise RuntimeError('合成发送前进程中断')
        with patch.object(first.budget,'reserve',side_effect=crash):
            with self.assertRaises(RuntimeError):first._call(self.payload,self.receipt)
        self.assertEqual('unknown',self.client('b')._call(self.payload,self.receipt)['status'])
        self.assertEqual(1,self.sends)
        self.assertEqual('unknown',read_receipt(self.receipt)['status'])

    def test_crash_after_receipt_reconciles_without_resending_and_corruption_fails(self):
        first=self.start();self.now+=181
        with patch('request_retry.finish',side_effect=RuntimeError('合成链更新前中断')):
            with self.assertRaises(RuntimeError):first._call(self.payload,self.receipt)
        self.assertEqual('received',self.client('b')._call(self.payload,self.receipt)['status'])
        self.assertEqual(2,self.sends)
        original=attempt_path(self.receipt,0);value=json.loads(original.read_text());value['error']='changed';atomic(original,value)
        with self.assertRaisesRegex(ValueError,'签名变化'):self.client('c')._call(self.payload,self.receipt)
        self.assertEqual(2,self.sends)

    def test_shared_reading_retries_then_other_job_reuses_verified_chain(self):
        first=self.client();first.reading_cache_root=self.root/'shared'
        with self.assertRaises(Paused):fixtures.ReadingCacheTests.read(self,self.page,first)
        self.now+=181
        self.assertEqual('reading_complete_pending_reconciliation',fixtures.ReadingCacheTests.read(self,self.page,first)['state'])
        other=self.client('b');other.reading_cache_root=self.root/'shared'
        self.assertEqual('reading_complete_pending_reconciliation',fixtures.ReadingCacheTests.read(self,self.page,other)['state'])
        self.assertEqual((2,0),(self.sends,other.budget.used()))

    def test_shared_retry_missing_receipt_is_unknown_cross_job(self):
        first=self.client();first.reading_cache_root=self.root/'shared'
        with self.assertRaises(Paused):fixtures.ReadingCacheTests.read(self,self.page,first)
        self.now+=181;reserve=first.budget.reserve
        def crash(*args):reserve(*args);raise RuntimeError('合成发送前中断')
        with patch.object(first.budget,'reserve',side_effect=crash):
            with self.assertRaises(RuntimeError):fixtures.ReadingCacheTests.read(self,self.page,first)
        other=self.client('b');other.reading_cache_root=self.root/'shared'
        # 共享页扫描按链的实际待发送状态判断，缺回执的预留在入口处即按结果不明拦住。
        with self.assertRaises(Paused) as ex:fixtures.ReadingCacheTests.read(self,self.page,other)
        self.assertEqual('unknown',ex.exception.state);self.assertEqual((1,0),(self.sends,other.budget.used()))

    def gateway_failure(self,**change):
        return dict(dict(status='failed',output=[],error=dict(code='gateway_concurrency_limit',
            message='Concurrency limit exceeded for account, please retry later')),**change)

    def test_gateway_terminal_retries_with_original_and_both_budgets_preserved(self):
        self.errors=[self.gateway_failure()];first=self.start()
        original=attempt_path(self.receipt,0).read_bytes()
        with self.assertRaises(Paused):self.client()._call(self.payload,self.receipt)
        self.assertEqual(1,self.sends)
        self.now+=121
        self.assertEqual('completed',self.client()._call(self.payload,self.receipt)['response']['status'])
        self.assertEqual((2,2,2),(self.sends,first.budget.used(),self.flow.snapshot()['request_budget']['used']))
        self.assertEqual(original,attempt_path(self.receipt,0).read_bytes())
        self.client('b')._call(self.payload,self.receipt);self.assertEqual(2,self.sends)

    def test_gateway_retry_exhaustion_survives_restart_and_uses_increasing_backoff(self):
        self.errors=[self.gateway_failure() for _ in range(3)]
        self.start();self.now+=121
        with self.assertRaises(Paused):self.client('b')._call(self.payload,self.receipt)
        # 第二次失败按两倍冷却，不能在同一时刻连续重发。
        with self.assertRaises(Paused):self.client('c')._call(self.payload,self.receipt)
        self.assertEqual(2,self.sends)
        self.now+=241
        result=self.client('c')._call(self.payload,self.receipt)
        self.assertEqual('failed',result['response']['status'])
        self.client('d')._call(self.payload,self.receipt);self.assertEqual(3,self.sends)

    def test_non_gateway_output_and_disabled_limit_never_retry(self):
        failures=[self.gateway_failure(error=dict(code='server_error',message='合成其他失败')),
            self.gateway_failure(output=[dict(type='message')]),self.gateway_failure(error=dict(code='gateway_concurrency_limit',message='无法确认槽位层级'))]
        for i,failure in enumerate(failures):
            self.errors=[failure];path=self.root/(str(i)+'.receipt.json');before=self.sends
            self.client()._call(self.payload,path);self.client()._call(self.payload,path)
            self.assertEqual(before+1,self.sends)
        self.flow.config['max_429_retries']=0;self.errors=[self.gateway_failure()]
        self.client()._call(self.payload,self.receipt);self.client()._call(self.payload,self.receipt)
        self.assertFalse(attempt_path(self.receipt,0).exists())

    def test_existing_shared_gateway_failure_resumes_under_page_lock(self):
        self.flow.config['max_429_retries']=0;self.errors=[self.gateway_failure()]
        first=self.client();first.reading_cache_root=self.root/'shared'
        self.assertEqual('reading_blocked',fixtures.ReadingCacheTests.read(self,self.page,first)['state'])
        original=next(first.reading_cache_root.rglob('*.receipt.json')).read_bytes()
        self.flow.config['max_429_retries']=2
        with self.assertRaises(Paused):fixtures.ReadingCacheTests.read(self,self.page,first)
        self.now+=121
        self.assertEqual('reading_complete_pending_reconciliation',fixtures.ReadingCacheTests.read(self,self.page,first)['state'])
        other=self.client('b');other.reading_cache_root=first.reading_cache_root
        self.assertEqual('reading_complete_pending_reconciliation',fixtures.ReadingCacheTests.read(self,self.page,other)['state'])
        self.assertEqual((2,0),(self.sends,other.budget.used()))
        self.assertEqual(original,next(first.reading_cache_root.rglob('*.attempt-0.receipt.json')).read_bytes())

    def test_legacy_transport_chain_retains_terminal_gateway_failure(self):
        from request_retry import TRANSPORT,retry_kind
        failure=dict(status='received',response=self.gateway_failure(),request_sha256='synthetic')
        atomic(attempt_path(self.receipt,0),failure)
        atomic(self.receipt,dict(transport_version=TRANSPORT,request_sha256='synthetic',status='received',attempts=[dict(sha256=digest(failure))],exhausted=False))
        self.assertIsNone(retry_kind(failure,TRANSPORT))
        self.assertEqual('failed',read_receipt(self.receipt)['response']['status'])

    def test_existing_shared_gateway_retry_crash_keeps_unknown_and_pointer_protection(self):
        self.flow.config['max_429_retries']=0;self.errors=[self.gateway_failure()]
        first=self.client();first.reading_cache_root=self.root/'shared'
        fixtures.ReadingCacheTests.read(self,self.page,first)
        self.flow.config['max_429_retries']=2
        with self.assertRaises(Paused):fixtures.ReadingCacheTests.read(self,self.page,first)
        self.now+=121;reserve=first.budget.reserve
        def crash(*args):reserve(*args);raise RuntimeError('合成续发预留后中断')
        with patch.object(first.budget,'reserve',side_effect=crash):
            with self.assertRaises(RuntimeError):fixtures.ReadingCacheTests.read(self,self.page,first)
        other=self.client('b');other.reading_cache_root=first.reading_cache_root
        with self.assertRaises(Paused) as error:fixtures.ReadingCacheTests.read(self,self.page,other)
        self.assertEqual(('unknown',1,0),(error.exception.state,self.sends,other.budget.used()))


if __name__=='__main__':unittest.main()
