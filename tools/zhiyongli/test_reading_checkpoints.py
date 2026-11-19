"""续跑不能反复改写固定检查点或覆盖已损坏的来源证明；无真实模型请求。"""
import io
import json
import unittest
from unittest.mock import patch

from runtime import atomic
from model_config import ModelConfig
import test_reading_cache as fixtures


class CheckpointTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    client=fixtures.ReadingCacheTests.client
    packet=fixtures.ReadingCacheTests.packet
    read=fixtures.ReadingCacheTests.read

    def test_only_registered_disjoint_capture_page_can_be_excluded_without_replaying_unknown(self):
        from bounded_read import excluded_scope_page,read_all,plan
        from workday_scope import DEFAULT,window
        import os,copy
        path=self.root/'registry.json';registry=json.loads(DEFAULT.read_text())
        registry['weeks']['2026-11-02']['evaluation_dates']=['2026-11-04','2026-11-05','2026-11-06'];atomic(path,registry)
        with patch.dict(os.environ,{'ZHIYONGLI_WORKDAY_REGISTRY':str(path)}):
            scope=window('2026-11-02','2026-11-09')
            packet=self.packet(1);packet.update(workday_scope=scope,source_scope='registered_person_workdays')
            packet['events'][0].update(at='2026-11-04T12:00:00+08:00',timestamp_basis='audit_capture')
            old=copy.deepcopy(packet);old['events'][0].update(event='old',at='2026-11-02T12:00:00+08:00')
            previous=plan(old,include_text=False);base=self.root/'plans';receipt=base/'old/0.receipt.json'
            atomic(receipt,dict(status='unknown',request_sha256='old-request'))
            atomic(receipt.parent/'plan.json',previous);original=receipt.read_bytes()
            client=self.client('new');events={e['event']:e for e in packet['events']}
            self.assertTrue(excluded_scope_page(packet,previous,receipt,events))
            result=read_all(packet,base,client=client,limit=10)
            self.assertEqual('reading_complete_pending_reconciliation',result['state'])
            self.assertEqual(1,self.calls);self.assertEqual(original,receipt.read_bytes())
            self.assertEqual(1,len(result['excluded_scope_unknown']))
            for change in (dict(event_id='e0'),dict(at='2026-11-04T12:00:00+08:00'),dict(timestamp_basis=None)):
                changed=copy.deepcopy(previous);changed['pages'][0]['fragments'][0].update(change)
                self.assertFalse(excluded_scope_page(packet,changed,receipt,events))
            unregistered=copy.deepcopy(packet);unregistered['workday_scope']['evaluation_dates']=['2026-11-05']
            with self.assertRaises(ValueError):excluded_scope_page(unregistered,previous,receipt,events)
            full=dict(packet,source_scope='full_person_window')
            blocked=read_all(full,base,client=client,limit=10)
            self.assertEqual('reading_blocked',blocked['state']);self.assertEqual(1,self.calls)

    def test_resume_rechecks_but_does_not_rewrite_fixed_plan_and_completed_pages(self):
        packet=self.packet(49);client=self.client('review')
        first=self.read(packet,client)
        paths=[*client.root.rglob('plan.json'),*client.root.rglob('*.checked.json')]
        before={str(p):(p.stat().st_ino,p.stat().st_mtime_ns,p.read_bytes()) for p in paths}
        sent=self.calls
        with patch('bounded_read.atomic',wraps=atomic) as write:
            second=self.read(packet,client)
        self.assertEqual(first,second);self.assertEqual(sent,self.calls)
        self.assertFalse(any(c.args[0].name=='plan.json' or c.args[0].name.endswith('.checked.json') for c in write.call_args_list))
        self.assertEqual(before,{str(p):(p.stat().st_ino,p.stat().st_mtime_ns,p.read_bytes()) for p in paths})
        self.assertEqual(3,client.budget.used())

    def test_changed_plan_is_preserved_and_blocks_before_any_new_request(self):
        packet=self.packet(25);client=self.client('review');self.read(packet,client)
        path=next(client.root.rglob('plan.json'));value=json.loads(path.read_text())
        value['packet_sha256']='合成损坏';atomic(path,value);before=path.read_bytes();sent=self.calls
        with self.assertRaisesRegex(ValueError,'检查点内容变化'):self.read(packet,client)
        self.assertEqual(before,path.read_bytes());self.assertEqual(sent,self.calls)

    def test_changed_checked_evidence_is_not_overwritten_by_successful_revalidation(self):
        packet=self.packet();client=self.client('review');self.read(packet,client)
        path=next(client.root.rglob('*.checked.json'));value=json.loads(path.read_text())
        value['value']['notes'][next(iter(value['value']['notes']))]['summary']='合成篡改'
        atomic(path,value);before=path.read_bytes();sent=self.calls
        result=self.read(packet,client)
        self.assertEqual('reading_blocked',result['state'])
        self.assertEqual('invalid_reading',result['blocked'][0]['state'])
        self.assertEqual(before,path.read_bytes());self.assertEqual(sent,self.calls)

    def test_truncated_output_stops_after_bounded_repairs_and_resume_never_resends(self):
        response=dict(status='incomplete',incomplete_details=dict(reason='max_output_tokens'),
            output=[dict(content=[dict(type='output_text',text='不完整的评审输出')])])
        wire=('data: '+json.dumps(dict(type='response.incomplete',response=response))+'\n\n').encode()
        from unittest.mock import Mock
        opener=Mock();opener.open.side_effect=lambda *a,**kw:io.BytesIO(wire)
        client=self.client('incomplete');packet=self.packet()
        with patch.object(ModelConfig,'opener',return_value=opener):
            first=self.read(packet,client)
            original={str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')}
            second=self.read(packet,client)
        self.assertEqual('reading_blocked',first['state'])
        self.assertEqual('invalid_reading',first['blocked'][0]['state'])
        self.assertEqual([],first['received_pages'])
        self.assertEqual(first,second)
        self.assertEqual(3,opener.open.call_count)
        self.assertEqual(3,client.budget.used())
        self.assertEqual(original,{str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')})

    def test_nonempty_fragment_repairs_are_bounded_and_not_resent_on_resume(self):
        from unittest.mock import Mock
        def reply(request,**kwargs):
            body=json.loads(request.data)
            slots=body['text']['format']['schema']['properties']['notes']['properties']
            for slot in slots.values():
                self.assertEqual(1,slot['properties']['facts']['minItems'])
            page=json.loads(body['input'])
            notes={f['fragment_id']:dict(summary='合成非空来源',facts=[],limitations=[],open_questions=[])
                for f in page['fragments']}
            response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(dict(notes=notes)))])])
            return io.BytesIO(('data: '+json.dumps(dict(type='response.completed',response=response))+'\n\n').encode())
        opener=Mock();opener.open.side_effect=reply
        client=self.client('empty-facts');packet=self.packet(1)
        with patch.object(ModelConfig,'opener',return_value=opener):
            first=self.read(packet,client)
            original={str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')}
            second=self.read(packet,client)
        self.assertEqual('reading_blocked',first['state'])
        self.assertEqual('非空来源片段缺逐字阅读依据',first['blocked'][0]['error'])
        self.assertEqual(first,second)
        self.assertEqual(3,opener.open.call_count)
        self.assertEqual(3,client.budget.used())
        self.assertEqual(original,{str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')})

    def test_empty_or_whitespace_fragment_keeps_empty_facts_valid(self):
        from bounded_read import checked,schema
        for text in ('',' \n\t'):
            with self.subTest(text=text):
                page=dict(fragments=[dict(fragment_id='empty',text=text,start=0)])
                facts=schema(page)['properties']['notes']['properties']['empty']['properties']['facts']
                self.assertEqual(0,facts['maxItems'])
                self.assertNotIn('minItems',facts)
                value=dict(notes=dict(empty=dict(summary='合成空白来源',facts=[],limitations=[],open_questions=[])))
                self.assertEqual(value,checked(page,value))


if __name__=='__main__':unittest.main()
