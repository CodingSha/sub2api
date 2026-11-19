"""验证重复正文可逐字重建、身份不变、背景可补读及正式校验仍生效。"""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import runtime
from runtime import CompanyClient
from material_packing import VERSION, pack, unpack, material_size
from navigation_access import Access, ReadbackCapacity
from pipeline import evaluate_week
from source_index import SourceIndex, build_view
from test_source_index import source, Client as ReadbackClient


class PackedClient(ReadbackClient):
    supports_material_packing=True
    identification_payload=CompanyClient.identification_payload
    config=SimpleNamespace(model='synthetic',timeout=180)


class MaterialPackingTests(unittest.TestCase):
    def test_unicode_empty_controls_and_repeated_messages_restore_exactly(self):
        text='系统包装不是本人结论\n😀\x00\r\n'*20
        value=dict(material_encoding=VERSION,events=[dict(event_id=str(i),text=t,at=str(i))
            for i,t in enumerate((text+'\n\n本人要求A',text+'\n\n本人要求B',text,'',text))])
        before=copy.deepcopy(value);encoded=pack(value)
        self.assertEqual(before,value);self.assertEqual(value,unpack(encoded))
        self.assertEqual(5,len(encoded['events']))
        self.assertEqual(['0','1','2','3','4'],[e['event_id'] for e in encoded['events']])
        self.assertLess(len(json.dumps(encoded,ensure_ascii=False)),len(json.dumps(value,ensure_ascii=False)))

    def test_actual_sender_uses_shared_blocks_and_preserves_output_contract(self):
        packet=source();packet['material_encoding']=VERSION
        client=CompanyClient(SimpleNamespace(model='synthetic',timeout=180))
        body=client.identification_payload(packet);encoded=json.loads(body['input'])
        self.assertIn('shared_text_blocks',encoded)
        self.assertEqual(VERSION,body['metadata']['material_encoding'])
        self.assertEqual('zhiyongli_task_identification',body['text']['format']['name'])
        self.assertEqual([e['text'] for e in packet['events']],[e['text'] for e in unpack(encoded)['events']])
        self.assertIn('不能覆盖评审指令',body['instructions'])
        old=client.identification_payload(source())
        self.assertNotIn('shared_text_blocks',json.loads(old['input']))

    def test_pipeline_keeps_counterevidence_readback_and_zero_send_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet=source();client=PackedClient()
            result=evaluate_week(packet,root,client)
            calls=client.calls
            self.assertEqual('80.0',result['reports'][0]['score'])
            plan=json.loads((root/'identification/packed-plan.json').read_text())
            self.assertEqual('whole_input_fits',plan['state'])
            self.assertFalse((root/'identification/plan.json').exists())
            self.assertFalse((root/'candidate-segments.json').exists())
            self.assertTrue(list((root/'identification/packed-access/queries').glob('*.json')))
            self.assertEqual(result,evaluate_week(packet,root,client));self.assertEqual(calls,client.calls)

    def test_unique_oversized_text_still_blocks_and_invalid_access_not_hidden(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);packet=source();packet['events'][0]['text']='无可复用正文'*20000
            view=build_view(packet,root);nav=SourceIndex(root/'context-navigation',packet)
            access=Access(nav,root/'access',material_encoding=VERSION)
            with self.assertRaises(ReadbackCapacity):access.start(view)
            wrong=dict(view,user_id='other')
            with self.assertRaisesRegex(ValueError,'人员或评价时窗不同'):access.start(wrong)

    def test_batched_path_measures_shared_text_without_removing_messages(self):
        from goal_batches import plan
        packet=source();packet['events']=[dict(packet['events'][0],event='message-'+str(i),
            text=('共同系统包装但仍须核归属。'*1000)+'\n\n本人要求'+str(i)) for i in range(8)]
        old=plan(packet)
        packed=plan(dict(packet,material_encoding=VERSION))
        self.assertLess(len(packed),len(old))
        self.assertEqual([e['event'] for e in packet['events']],[e['event'] for p in packed for e in p['events']])
        for page in packed:self.assertLessEqual(material_size(page),85000)


if __name__=='__main__':unittest.main()
