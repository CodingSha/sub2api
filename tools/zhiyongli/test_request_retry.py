"""明确429、跨任务崩溃与回执签名回归，全部使用合成端点和时钟。"""
import json
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
        value=fixtures.ReadingCacheTests.read(self,self.page,other)
        self.assertEqual('unknown',value['blocked'][0]['state']);self.assertEqual((1,0),(self.sends,other.budget.used()))


if __name__=='__main__':unittest.main()
