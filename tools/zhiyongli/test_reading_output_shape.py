"""已收到的错误JSON形状应走原有有界修订，不能崩溃或冒充合法阅读。"""
import copy
import io
import json
import unittest
from unittest.mock import patch

from model_config import ModelConfig
import test_reading_cache as fixtures


class ReadingOutputShapeTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    client=fixtures.ReadingCacheTests.client
    packet=fixtures.ReadingCacheTests.packet
    read=fixtures.ReadingCacheTests.read

    def test_invalid_json_shapes_are_repaired_without_bypassing_validation(self):
        for case in ('top_array','note_array','fact_array','fact_number','fact_blank'):
            with self.subTest(case=case):
                calls=[]
                def reply(request,**kwargs):
                    payload=json.loads(request.data);calls.append(payload)
                    page=json.loads(payload['input']);fragment=page['fragments'][0];key=fragment['fragment_id']
                    note=dict(summary='合成主要内容',facts=[dict(anchor_id='0',fact='合成原文依据')],limitations=[],open_questions=[])
                    value=dict(notes={key:copy.deepcopy(note)})
                    if 'contract_feedback' not in page:
                        if case=='top_array':value=[]
                        elif case=='note_array':value['notes'][key]=[]
                        elif case=='fact_array':value['notes'][key]['facts']=[[]]
                        elif case=='fact_number':value['notes'][key]['facts'][0]['fact']=123
                        else:value['notes'][key]['facts'][0]['fact']='   '
                    response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])])
                    return io.BytesIO(('data: '+json.dumps(dict(type='response.completed',response=response))+'\n\n').encode())
                client=self.client(case);client.reading_cache_root=self.root/('shared-'+case)
                with patch.object(ModelConfig,'opener') as opened:
                    opened.return_value.open.side_effect=reply
                    result=self.read(self.packet(1),client)
                self.assertEqual('reading_complete_pending_reconciliation',result['state'])
                self.assertEqual((2,2),(len(calls),client.budget.used()))
                self.assertIn('contract_feedback',json.loads(calls[1]['input']))

    def test_persistently_invalid_shape_stops_at_original_limit_without_overwriting(self):
        response=dict(status='completed',output=[dict(content=[dict(type='output_text',text='[]')])])
        wire=('data: '+json.dumps(dict(type='response.completed',response=response))+'\n\n').encode()
        client=self.client('persistently-invalid')
        with patch.object(ModelConfig,'opener') as opened:
            opened.return_value.open.side_effect=lambda *args,**kwargs:io.BytesIO(wire)
            result=self.read(self.packet(1),client)
            before={str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')}
            self.assertEqual(result,self.read(self.packet(1),client))
            self.assertEqual(3,opened.return_value.open.call_count)
        self.assertEqual('reading_blocked',result['state'])
        self.assertEqual('invalid_reading',result['blocked'][0]['state'])
        self.assertEqual(3,client.budget.used())
        self.assertFalse(list(client.root.rglob('*.checked.json')))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')})


if __name__=='__main__':
    unittest.main()
