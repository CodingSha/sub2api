"""使用合成端点验证增量复用、跨任务预算与崩溃边界；不发真实API。"""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bounded_read import plan,read_all
from model_config import ModelConfig
from pipeline import Paused
from reading_cache import ReadingCache
from runtime import atomic,digest
from worker import JobClient


class ReadingCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);key=self.root/'key';key.write_text('synthetic-key');key.chmod(0o600)
        self.config=ModelConfig('https://ai.apsatcom.com',key);self.calls=0
        test=self
        class Opener:
            def open(self,request,**kwargs):
                test.calls+=1
                payload=json.loads(request.data);page=json.loads(payload['input'])
                if 'text' in payload and payload['text']['format']['name']=='zhiyongli_context_navigation':
                    from context_navigation import FIELDS
                    value=dict({k:'合成局部事实，未核其他分支及后续状态。' for k in FIELDS},
                        covered_child_ids=[c['node_id'] for c in page['children']])
                    response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])])
                    return io.BytesIO(('data: '+json.dumps(dict(type='response.completed',response=response))+'\n\n').encode())
                notes={f['fragment_id']:dict(summary='合成页主要事实',
                    facts=[dict(anchor_id=f['segments'][0]['anchor_id'],fact='合成依据')],
                    limitations=[],open_questions=[]) for f in page['fragments']}
                response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(dict(notes=notes)))])])
                return io.BytesIO(('data: '+json.dumps(dict(type='response.completed',response=response))+'\n\n').encode())
        patcher=patch.object(ModelConfig,'opener',return_value=Opener());patcher.start();self.addCleanup(patcher.stop)

    def client(self,name,limit=100,turn=100):
        client=JobClient(self.config,self.root/name,limit,turn)
        client.reading_cache_root=self.root/'shared'
        return client

    def packet(self,count=24):
        return dict(user_id='1',user_email='synthetic@example.test',since='2026-11-02',until='2026-11-09',issues=[],
            events=[dict(event='e'+str(i),text='合成来源 '+str(i),actor_class='context',at='2026-11-03T12:00:00+08:00') for i in range(count)])

    def read(self,packet,client):
        return read_all(packet,client.root/'reading',client=client,limit=100)

    def test_appended_page_reuses_stable_prefix_without_spending_new_job_budget(self):
        first=self.client('a');self.read(self.packet(),first)
        self.assertEqual((1,1),(self.calls,first.budget.used()))
        second=self.client('b',limit=1);value=self.read(self.packet(25),second)
        self.assertEqual('reading_complete_pending_reconciliation',value['state'])
        self.assertEqual((2,1),(self.calls,second.budget.used()))
        # 封口时刻、快照变化不重复阅读稳定单页，仍需后续全局归并。
        packet=self.packet(25);packet['source_snapshot']='new';packet['until']='2026-11-10'
        third=self.client('c',limit=1);self.read(packet,third)
        self.assertEqual((2,0),(self.calls,third.budget.used()))

    def test_changed_page_and_identity_never_reuse(self):
        packet=self.packet();self.read(packet,self.client('a'))
        changed=copy.deepcopy(packet);changed['events'][0]['text']='变化的工具反证'
        self.read(changed,self.client('b'));self.assertEqual(2,self.calls)
        changed['user_id']='2';self.read(changed,self.client('c'));self.assertEqual(3,self.calls)
        changed['events'][0]['actor_class']='candidate_user'
        self.read(changed,self.client('d'));self.assertEqual(4,self.calls)

    def test_unknown_shared_receipt_blocks_other_jobs_without_charging_them(self):
        self.read(self.packet(),self.client('a'))
        receipt=next((self.root/'shared').rglob('*.receipt.json'))
        record=json.loads(receipt.read_text());record['status']='unknown';atomic(receipt,record)
        second=self.client('b')
        with self.assertRaises(Paused) as ex:self.read(self.packet(),second)
        self.assertEqual('unknown',ex.exception.state)
        self.assertEqual((1,0),(self.calls,second.budget.used()))

    def test_crash_between_budget_and_receipt_blocks_cross_job_replay(self):
        first=self.client('a');packet=self.packet();page=plan(packet)['pages'][0]
        cache=ReadingCache(first.reading_cache_root,dict(user_id='1',user_email=packet['user_email']),first)
        with cache.page(page) as receipt:first.budget.reserve(receipt,'synthetic-request')
        second=self.client('b')
        with self.assertRaises(Paused) as ex:self.read(packet,second)
        self.assertEqual('unknown',ex.exception.state)
        self.assertEqual((0,0),(self.calls,second.budget.used()))

    def test_corrupt_shared_receipt_is_not_a_cache_miss(self):
        first=self.client('a');self.read(self.packet(),first)
        receipt=next((self.root/'shared').rglob('*.receipt.json'))
        record=json.loads(receipt.read_text());record['response']['changed']=True;atomic(receipt,record)
        with self.assertRaisesRegex(ValueError,'签名变化'):self.read(self.packet(),first)
        self.assertEqual(1,self.calls)

    def test_crashed_requesting_without_seal_blocks_as_unknown_instead_of_signature_failure(self):
        first=self.client('a');page=plan(self.packet())['pages'][0]
        cache=ReadingCache(first.reading_cache_root,dict(user_id='1',user_email='synthetic@example.test'),first)
        source=cache.root/digest(page)
        key=digest(dict(endpoint=first.config.responses_url,payload=first.reading_payload(page)))
        receipt=source/(key+'.receipt.json')
        atomic(receipt,dict(status='requesting',request_sha256=key))
        with self.assertRaises(Paused) as error:
            with cache.page(page):self.fail('不得越过原发送意图')
        self.assertEqual('unknown',error.exception.state)
        self.assertFalse(receipt.with_name(key+'.seal.json').exists())
        self.assertEqual(0,self.calls)

    def test_failed_preflight_does_not_consume_budget_and_missing_receipt_does(self):
        client=self.client('a')
        with self.assertRaisesRegex(ValueError,'上下文预算'):
            client._call(dict(input='x'*200001),client.root/'large.receipt.json')
        self.assertEqual(0,client.budget.used())
        missing=client.root/'missing.receipt.json';client.budget.reserve(missing,'reserved')
        restarted=self.client('a')
        with self.assertRaises(Paused) as ex:restarted._call(dict(input='small'),missing)
        self.assertEqual('unknown',ex.exception.state);self.assertEqual(0,self.calls)

    def test_global_budget_exhaustion_still_allows_cache_validation(self):
        from model_flow import ModelFlow
        config=dict(version=1,budget_key='synthetic-cycle',approved_by='合成确认人',reason='合成测试',
            max_concurrency=1,requests_per_minute=100,reserved_tokens_per_minute=1000000,max_requests=1,
            transient_failure_limit=2,cooldown_seconds=60)
        flow=ModelFlow(self.root/'flow',config)
        first=self.client('a');first.flow=flow;self.read(self.packet(),first)
        self.assertEqual('global_budget_exhausted',flow.blocked())
        second=self.client('b');second.flow=flow
        self.assertEqual('reading_complete_pending_reconciliation',self.read(self.packet(),second)['state'])
        with self.assertRaises(Paused) as ex:self.read(self.packet(25),second)
        self.assertEqual('needs_review',ex.exception.state)
        self.assertEqual((1,0),(self.calls,second.budget.used()))


if __name__=='__main__':unittest.main()
