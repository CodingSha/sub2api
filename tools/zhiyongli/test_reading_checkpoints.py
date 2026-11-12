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

    def test_incomplete_model_output_blocks_reading_and_is_not_resent_on_resume(self):
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
        self.assertEqual(1,opener.open.call_count)
        self.assertEqual(1,client.budget.used())
        self.assertEqual(original,{str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')})

    def test_nonempty_fragment_requires_facts_and_invalid_receipt_is_not_resent(self):
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
        self.assertEqual(1,opener.open.call_count)
        self.assertEqual(1,client.budget.used())
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
