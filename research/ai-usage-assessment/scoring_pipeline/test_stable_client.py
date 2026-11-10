"""固定公司渠道、拒绝处理、完整回执和传输恢复。"""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scoring_pipeline import stable_client as c
from scoring_pipeline.batch_client import ModelError


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'.codex').mkdir();(self.root/'.codex/auth.json').write_text(json.dumps({'OPENAI_API_KEY':'unit-test-only'}))
        self.model=patch.object(c,'configured_model',return_value='test');self.model.start()
        self.home=patch.object(c.Path,'home',return_value=self.root);self.home.start()
        self.response=dict(id='response',status='completed',output=[dict(content=[dict(type='output_text',text='{"ok":true}')])])
    def tearDown(self):self.home.stop();self.model.stop();self.tmp.cleanup()
    def call(self,client):return client.call('固定判据',{'task':'合成测试'},self.root/'cache','test-run')
    def test_only_company_and_no_storage(self):
        def opener(request,timeout):
            self.assertEqual(request.full_url,'https://ai.apsatcom.com/responses')
            body=json.loads(request.data);self.assertFalse(body['store']);self.assertFalse(body['stream'])
            return io.BytesIO(json.dumps(self.response).encode())
        self.assertEqual(json.loads(self.call(c.JSONClient('test',opener=opener))['text']),{'ok':True})
    def test_refusal_not_retried(self):
        self.response['output'][0]['content']=[dict(type='refusal',refusal='不可处理')];calls=[]
        def opener(*args,**kwargs):calls.append(1);return io.BytesIO(json.dumps(self.response).encode())
        with self.assertRaisesRegex(ModelError,'model_refusal'):self.call(c.JSONClient('test',opener=opener,sleep=lambda _:None))
        self.assertEqual(len(calls),1)
    def test_incomplete_not_success(self):
        self.response['status']='incomplete';self.response['incomplete_details']={'reason':'max_output_tokens'}
        with self.assertRaisesRegex(ModelError,'response_max_output_tokens'):
            self.call(c.JSONClient('test',opener=lambda *a,**k:io.BytesIO(json.dumps(self.response).encode())))
    def test_retry_bounded_and_history_preserved(self):
        calls=[]
        def opener(*a,**k):
            calls.append(1)
            if len(calls)==1:raise TimeoutError()
            return io.BytesIO(json.dumps(self.response).encode())
        result=self.call(c.JSONClient('test',opener=opener,sleep=lambda _:None))
        self.assertEqual(len(result['attempts']),2);self.assertEqual(result['attempts'][0]['code'],'transport_TimeoutError')
        self.assertTrue(list((self.root/'cache').glob('*.failure.json')))
    def test_corrupted_cache_never_reused(self):
        client=c.JSONClient('test',opener=lambda *a,**k:io.BytesIO(json.dumps(self.response).encode()))
        self.call(client);p=next((self.root/'cache').glob('*.json'));v=json.loads(p.read_text());v['text']='篡改';p.write_text(json.dumps(v))
        with self.assertRaisesRegex(ModelError,'response_cache_corrupted'):self.call(client)
    def test_repeated_failed_request_preserves_each_invocation(self):
        def opener(*a,**k):raise TimeoutError()
        client=c.JSONClient('test',attempts=1,opener=opener)
        for _ in range(2):
            with self.assertRaises(ModelError):self.call(client)
        failures=list((self.root/'cache'/'failures').glob('*.json'))
        self.assertEqual(len(failures),2)
        self.assertEqual(len({json.loads(p.read_text())['invocation_id'] for p in failures}),2)
    def test_model_change_stops_before_send(self):
        with patch.object(c,'configured_model',return_value='other'):
            with self.assertRaisesRegex(ModelError,'model_configuration_changed'):self.call(c.JSONClient('test',opener=lambda *a,**k:self.fail('不应发送')))


if __name__=='__main__':unittest.main()
