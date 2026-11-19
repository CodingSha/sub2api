"""真实完整wire准入及原覆盖合同；全合成，不证明模型语义质量。"""
import copy
import json
import unittest
from unittest.mock import patch

import task_interpretation as role
import week_basis as week
import test_week_basis as fixtures
from pipeline import Paused
from runtime import atomic


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.Cases();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)

    def longer(self,text):
        self.fixture.unit['events'][3]['text']=text

    def run_stage(self,client):
        f=self.fixture
        return week.enrich(f.bundle,f.unit,f.parent,f.root/'admission',client)

    def test_long_complete_sources_and_actual_audit_fit_without_truncation_or_score_change(self):
        self.longer('合成完整AI反馈。'+'x'*125000)
        f=self.fixture;before=copy.deepcopy((f.unit,f.bundle));parent=f.parent.read_bytes()
        client=fixtures.Client();result=self.run_stage(client)
        self.assertEqual('basis_checked',result['status']);self.assertEqual(2,client.calls)
        data=json.loads((f.root/'admission/input.json').read_text())['data']
        self.assertGreater(role.size(data),120000);self.assertEqual(4,len(data['records']))
        self.assertEqual(f.unit['events'][3]['text'],next(r['text'] for r in data['records'] if r['id']=='a2'))
        spec=json.loads((f.root/'admission/spec.json').read_text())
        generation=week.request(client,'zhiyongli_week_basis',week.GENERATE,data,week.schema(data))
        audit=week.request(client,'zhiyongli_week_basis_audit',week.AUDIT,
            dict(source=data,interpretation=spec),week.audit_schema(data,spec))
        self.assertLessEqual(role.request_size(generation),200000);self.assertLessEqual(role.request_size(audit),200000)
        self.assertEqual(before,(f.unit,f.bundle));self.assertEqual(parent,f.parent.read_bytes())
        snapshot={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in f.root.rglob('*') if p.is_file()}
        self.assertEqual(result,self.run_stage(client));self.assertEqual(2,client.calls)
        self.assertEqual(snapshot,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in f.root.rglob('*') if p.is_file()})

    def test_actual_audit_overflow_keeps_generation_and_never_regenerates(self):
        self.longer('x'*115000)
        def expanded(kind,value):
            if kind=='zhiyongli_week_basis':
                for item in value['items'].values():item.update(basis='x'*2400,higher_band='y'*2400)
            return value
        f=self.fixture;client=fixtures.Client(expanded)
        for _ in range(2):
            with self.assertRaises(Paused):self.run_stage(client)
            self.assertEqual(1,client.calls)
        root=f.root/'admission';self.assertTrue((root/'generation.receipt.json').exists())
        self.assertTrue((root/'spec.json').exists());self.assertFalse((root/'review.receipt.json').exists())
        self.assertFalse((root/'manifest.json').exists())
        cost=json.loads((root/'capacity.json').read_text());self.assertLessEqual(cost['audit_body_lower_bound_bytes'],200000)
        data=json.loads((root/'input.json').read_text())['data'];spec=json.loads((root/'spec.json').read_text())
        actual=week.request(client,'zhiyongli_week_basis_audit',week.AUDIT,dict(source=data,interpretation=spec),week.audit_schema(data,spec))
        self.assertGreater(role.request_size(actual),200000)

    def test_json_escaping_overflow_is_stopped_before_generation_despite_small_data(self):
        self.longer('\\'*50000);client=fixtures.Client();f=self.fixture
        with self.assertRaises(Paused):self.run_stage(client)
        self.assertEqual(0,client.calls)
        root=f.root/'admission';data=json.loads((root/'input.json').read_text())['data']
        self.assertLess(role.size(data),200000)
        self.assertGreater(json.loads((root/'capacity.json').read_text())['generation_body_bytes'],200000)
        self.assertFalse((root/'generation.receipt.json').exists())

    def test_week_total_records_cannot_exceed_unchanged_cover_schema(self):
        f=self.fixture
        data=week.build(f.unit,f.bundle['reports'][0]);row=data['records'][0]
        data['records']=[dict(row,id='synthetic-'+str(i)) for i in range(1001)]
        with patch.object(week,'build',return_value=data):
            with self.assertRaisesRegex(Paused,'1000条'):self.run_stage(fixtures.Client())
        self.assertFalse((f.root/'admission/generation.receipt.json').exists())

    def test_first_complete_long_assistant_case_keeps_source_order_priority(self):
        rows=[dict(id='large',role='assistant',text='x'*90000)]
        report=self.fixture.bundle['reports'][0]
        first=(dict(key='first',title='第一任务'),rows,[],'available_records')
        second=(dict(key='second',title='第二任务'),[dict(id='short',role='assistant',text='短')],[],'available_records')
        result=role.select_case(iter((first,second)),report)
        self.assertEqual('first',result['task_key']);self.assertEqual(rows,result['records'])

    def test_long_received_unknown_is_not_rewritten_or_resent(self):
        self.longer('x'*125000);client=fixtures.Client();self.run_stage(client)
        root=self.fixture.root/'admission';receipt=root/'generation.receipt.json'
        value=json.loads(receipt.read_text());value['status']='unknown';value.pop('response');atomic(receipt,value)
        snapshot={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
        with self.assertRaises(Paused):self.run_stage(client)
        self.assertEqual(2,client.calls);self.assertEqual(snapshot,{p:p.read_bytes() for p in root.rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()
