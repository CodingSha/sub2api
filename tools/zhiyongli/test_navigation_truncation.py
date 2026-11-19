"""导航6000token明确截断的有界恢复；只用合成SSE及真实本地回执账簿。"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from context_navigation import Navigation,FIELDS,ContractError
from model_config import ModelConfig,ModelError
from pipeline import received,Paused
from runtime import digest
from worker import JobClient


class NavigationTruncationTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        key=self.root/'key';key.write_text('synthetic-navigation-key');key.chmod(0o600)
        self.config=ModelConfig('https://ai.apsatcom.com',key);self.requests=[];self.responses=[]
        self.packet=dict(user_id='synthetic',user_email='synthetic@example.test',since='2026-09-07',until='2026-09-14',events=[])
        self.data=dict(children=[dict(node_id='a',counterevidence='明确失败'),dict(node_id='b',unresolved='尚未解决')])
        self.good=dict({k:'合成要求、失败反证及未决保留。' for k in FIELDS},covered_child_ids=['a','b'])
        test=self
        class Opener:
            def open(self,request,**kwargs):
                payload=json.loads(request.data);test.requests.append(payload)
                response=test.responses.pop(0) if test.responses else test.complete()
                event='response.completed' if response['status']=='completed' else 'response.failed' if response['status']=='failed' else 'response.incomplete'
                return io.BytesIO(('data: '+json.dumps(dict(type=event,response=response))+'\n\n').encode())
        patcher=patch.object(ModelConfig,'opener',return_value=Opener());patcher.start();self.addCleanup(patcher.stop)

    def complete(self,**changes):
        return dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(dict(self.good,**changes)))])])

    def truncated(self):
        return dict(status='incomplete',incomplete_details=dict(reason='max_output_tokens'),output=[])

    def client(self,name='first',shared=False):
        client=JobClient(self.config,self.root/name,20,20)
        if shared:client.navigation_cache_root=self.root/'shared'
        return client

    def test_explicit_truncation_uses_12000_then_reuses_local_and_shared_without_new_send(self):
        self.responses=[self.truncated()];client=self.client(shared=True);nav=Navigation(self.root/'nav',self.packet)
        result=nav.summarize(self.data,client,received)
        self.assertEqual(self.good,result)
        self.assertEqual([6000,12000],[r['max_output_tokens'] for r in self.requests])
        self.assertEqual(2,client.budget.used())
        self.assertTrue(all(json.loads(r['input'])['children']==self.data['children'] for r in self.requests))
        before={str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')}
        self.assertEqual(self.good,nav.summarize(self.data,client,received))
        second=self.client('second',shared=True)
        self.assertEqual(self.good,Navigation(self.root/'other-nav',self.packet).summarize(self.data,second,received))
        self.assertEqual((2,0),(len(self.requests),second.budget.used()))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')})
        self.assertEqual(2,len(list((self.root/'shared').rglob('*.seal.json'))))

    def test_truncation_and_format_share_original_two_repair_slots_and_size_limit(self):
        for name,outputs,tokens in (
            ('truncated-format-ok',[self.truncated(),self.complete(covered_child_ids=[])],[6000,12000,12000]),
            ('format-truncated-ok',[self.complete(covered_child_ids=[]),self.truncated()],[6000,6000,12000]),
            ('all-truncated',[self.truncated() for _ in range(3)],[6000,12000,12000]),
            ('oversized',[self.truncated()]+[self.complete(summary='长'*5000) for _ in range(2)],[6000,12000,12000]),
        ):
            with self.subTest(name=name):
                self.responses=outputs;self.requests=[];client=self.client(name);nav=Navigation(self.root/name/'nav',self.packet)
                if name in {'all-truncated','oversized'}:
                    with self.assertRaises(ContractError):nav.summarize(self.data,client,received)
                else:self.assertEqual(self.good,nav.summarize(self.data,client,received))
                self.assertEqual(tokens,[r['max_output_tokens'] for r in self.requests])
                self.assertEqual(3,client.budget.used())
                before={str(p):p.read_bytes() for p in client.root.rglob('*.receipt.json')}
                if name in {'all-truncated','oversized'}:
                    with self.assertRaises(ContractError):nav.summarize(self.data,client,received)
                else:nav.summarize(self.data,client,received)
                self.assertEqual(3,len(self.requests))
                self.assertEqual(before,{str(p):p.read_bytes() for p in client.root.rglob('*.receipt.json')})

    def test_other_terminal_reasons_and_unknown_never_repair(self):
        from runtime import atomic
        for name,response in (
            ('content-filter',dict(status='incomplete',incomplete_details=dict(reason='content_filter'),output=[])),
            ('failed',dict(status='failed',error=dict(code='server_error'),output=[])),
        ):
            with self.subTest(name=name):
                self.responses=[response];self.requests=[];client=self.client(name);nav=Navigation(self.root/name/'nav',self.packet)
                with self.assertRaises(ModelError):nav.summarize(self.data,client,received)
                with self.assertRaises(ModelError):nav.summarize(self.data,client,received)
                self.assertEqual((1,1),(len(self.requests),client.budget.used()))
        for state in ('unknown','requesting'):
            with self.subTest(state=state):
                client=self.client(state);nav=Navigation(self.root/state/'nav',self.packet)
                payload=client.navigation_payload(self.data,__import__('context_navigation').schema(self.data))
                path=nav.root/(digest(self.data)+'.receipt.json')
                atomic(path,dict(status=state,request_sha256=digest(dict(endpoint=self.config.responses_url,payload=payload))))
                before=path.read_bytes();calls=len(self.requests)
                with self.assertRaises(Paused):nav.summarize(self.data,client,received)
                self.assertEqual((before,calls,0),(path.read_bytes(),len(self.requests),client.budget.used()))

    def test_client_without_explicit_capability_does_not_change_output_budget(self):
        self.responses=[self.truncated()];client=self.client();client.supports_navigation_output_repair=False
        with self.assertRaises(ModelError):Navigation(self.root/'nav',self.packet).summarize(self.data,client,received)
        self.assertEqual([6000],[r['max_output_tokens'] for r in self.requests])

    def test_original_truncated_shared_receipt_still_rejects_corruption_after_recovery(self):
        from runtime import atomic
        self.responses=[self.truncated()];client=self.client(shared=True);nav=Navigation(self.root/'nav',self.packet)
        nav.summarize(self.data,client,received)
        original=next(p for p in (self.root/'shared').rglob('*.receipt.json')
            if json.loads(p.read_text()).get('response',{}).get('status')=='incomplete')
        value=json.loads(original.read_text());value['response']['output']=[dict(changed=True)];atomic(original,value)
        corrupted=original.read_bytes()
        with self.assertRaisesRegex(ValueError,'签名变化'):nav.summarize(self.data,client,received)
        self.assertEqual((2,corrupted),(len(self.requests),original.read_bytes()))

    def test_repair_respects_existing_budget_and_never_resets_original_receipt(self):
        self.responses=[self.truncated()];client=self.client(shared=True);client.limit=client.budget.limit=1
        nav=Navigation(self.root/'nav',self.packet)
        with self.assertRaises(Paused) as caught:nav.summarize(self.data,client,received)
        self.assertEqual('needs_review',caught.exception.state)
        before={str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')}
        with self.assertRaises(Paused):nav.summarize(self.data,client,received)
        self.assertEqual((1,1),(len(self.requests),client.budget.used()))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')})

    def test_partial_truncated_json_is_preserved_but_never_parsed_as_navigation(self):
        partial='{"covered_child_ids":["a"],"summary":"不完整且不可采用'
        response=self.truncated();response['output']=[dict(content=[dict(type='output_text',text=partial)])]
        self.responses=[response];client=self.client();nav=Navigation(self.root/'nav',self.packet)
        self.assertEqual(self.good,nav.summarize(self.data,client,received))
        first=nav.root/(digest(self.data)+'.receipt.json')
        self.assertEqual(partial,json.loads(first.read_text())['response']['output'][0]['content'][0]['text'])
        repair=json.loads(self.requests[1]['input'])
        self.assertEqual(self.data['children'],repair['children'])
        self.assertNotIn(partial,json.dumps(repair,ensure_ascii=False))
        self.assertIn('previous_receipt_sha256',repair['navigation_feedback'])
        before=first.read_bytes();nav.summarize(self.data,client,received)
        self.assertEqual((before,2,2),(first.read_bytes(),len(self.requests),client.budget.used()))

    def test_runtime_output_tokens_are_strict_and_default_payload_is_unchanged(self):
        from context_navigation import schema
        client=self.client();shape=schema(self.data)
        default=client.navigation_payload(self.data,shape)
        self.assertEqual(default,client.navigation_payload(self.data,shape,max_output_tokens=6000))
        expanded=client.navigation_payload(self.data,shape,max_output_tokens=12000)
        self.assertEqual(dict(default,max_output_tokens=12000),expanded)
        self.assertEqual(self.data,json.loads(default['input']))
        for bad in (True,6000.0,24000,None,'12000'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):client.navigation_payload(self.data,shape,max_output_tokens=bad)
                with self.assertRaises(ValueError):client.navigate_context(self.data,shape,self.root/'invalid.receipt.json',max_output_tokens=bad)
        self.assertEqual([],self.requests)
        self.assertEqual(0,client.budget.used())
        self.assertFalse((self.root/'invalid.receipt.json').exists())


if __name__=='__main__':unittest.main()
