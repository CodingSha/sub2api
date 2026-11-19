"""验证完整覆盖、异议保留、旧分不成立和原请求验签；只用合成来源。"""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import week_basis as stage
from pipeline import Paused
from request_retry import read_receipt
from runtime import atomic,digest
from test_task_evidence import fixture


class Client:
    def __init__(self,mutate=lambda kind,value:value):
        self.config=SimpleNamespace(model='test-model',responses_url='http://test/v1/responses')
        self.calls=0
        self.mutate=mutate

    def _call(self,payload,path):
        if path.exists():return read_receipt(path)
        self.calls+=1
        kind=payload['text']['format']['name']
        data=json.loads(payload['input'])
        if kind=='zhiyongli_week_basis':
            refs=['h1','h2','a1','a2']
            value=dict(covered_record_ids=refs,items={code:dict(verdict='consistent',basis='本人明确安排核对字段，支持合理检查设计。',
                higher_band='未保存针对采用条件逐项设计检查的正证，不把缺记录判为能力不足。',boundary_kind='support_missing',
                source_keys=['task'],record_ids=['h1'],limitations=['AI反馈不代表独立验收。']) for code in stage.ITEMS},
                case=dict(task_key='task',outcome='保存了核对请求及AI反馈，外部结果未确认。',
                    claims=[dict(text=t,item_codes=['J2'],record_ids=r,limitations=[],
                        assessment=dict(kind='support',reason='本人明确安排了检查对象。'))
                        for t,r in [('目标是字段核对。',['h1']),('随后再次提出核对要求。',['h2']),('保存了AI反馈。',['a2'])]],
                    limitations=['没有独立验收回执。']))
        else:
            spec=data['interpretation']
            row=dict(verdict='supported',reason='对应原文、归属及判据。',record_ids=['h1'])
            value=dict(seen_record_ids=[r['id'] for r in data['source']['records']],
                week={code:dict(row,own_verdict=spec['items'][code]['verdict'],record_ids=spec['items'][code]['record_ids']) for code in stage.ITEMS},
                case_claims={'claim_'+str(i):dict(row,record_ids=c['record_ids']) for i,c in enumerate(spec['case']['claims'])},
                case_checks={key:copy.deepcopy(row) for key in stage.role.CHECKS},issues=[])
        value=self.mutate(kind,value)
        receipt=dict(status='received',request_sha256=digest(dict(endpoint=self.config.responses_url,payload=payload)),
            request_kind=kind,response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
        atomic(path,receipt)
        return receipt


class Cases(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.unit=fixture();self.unit['source_units'][0]['source_kind']='task'
        self.bundle=dict(reports=[dict(user_email='test@example.invalid',period_type='week',workday_scope=dict(working_dates=['2026-09-08']),
            eligibility=dict(observations=[dict(task_keys=['task'])]),
            items=[dict(item_code=code,state='observed',score=70,reason='既有理由',criterion='固定标准') for code in stage.ITEMS])])
        self.parent=self.root/'bundle.json';atomic(self.parent,self.bundle)
        atomic(self.root/'review.json',dict(value=dict(period_reviews=[],evidence=[])))

    def tearDown(self):self.temp.cleanup()
    def run_stage(self,client):return stage.enrich(self.bundle,self.unit,self.parent,self.root/'new',client)

    def test_all_sources_two_receipts_and_no_score_change(self):
        before=copy.deepcopy(self.bundle);client=Client();result=self.run_stage(client)
        self.assertEqual(result['status'],'basis_checked');self.assertEqual(result['records'],4)
        self.assertEqual(client.calls,2);self.assertEqual(before,self.bundle)
        self.assertEqual(set(result['item_verdicts']),set(stage.ITEMS))

    def test_readonly_replay_never_sends_or_writes(self):
        client=Client();result=self.run_stage(client)
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(result,stage.verify(self.bundle,self.unit,self.parent,self.root/'new',client.config))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()});self.assertEqual(client.calls,2)

    def test_original_grade_can_be_rejected_without_changing_it(self):
        def change(kind,value):
            if kind=='zhiyongli_week_basis':value['items']['P1']['verdict']='needs_reassessment'
            return value
        result=self.run_stage(Client(change));self.assertEqual('requires_reassessment',result['status'])
        self.assertEqual(70,self.bundle['reports'][0]['items'][0]['score'])

    def test_missing_source_record_stops_before_audit(self):
        def change(kind,value):
            if kind=='zhiyongli_week_basis':value['covered_record_ids'].pop()
            return value
        client=Client(change)
        with self.assertRaises(ValueError):self.run_stage(client)
        self.assertEqual(client.calls,1)

    def test_foreign_reference_stops_before_audit(self):
        def change(kind,value):
            if kind=='zhiyongli_week_basis':value['items']['P1']['record_ids']=['foreign']
            return value
        client=Client(change)
        with self.assertRaises(ValueError):self.run_stage(client)
        self.assertEqual(client.calls,1)

    def test_reviewer_disagreement_blocks(self):
        def change(kind,value):
            if kind.endswith('_audit'):value['week']['P1']['own_verdict']='needs_reassessment'
            return value
        with self.assertRaises(Paused):self.run_stage(Client(change))
        self.assertFalse((self.root/'new/manifest.json').exists())

    def test_reviewer_must_read_every_record(self):
        def change(kind,value):
            if kind.endswith('_audit'):value['seen_record_ids'].pop()
            return value
        with self.assertRaises(ValueError):self.run_stage(Client(change))

    def test_disclosed_source_limit_retained_without_fake_resolution(self):
        def change(kind,value):
            if kind.endswith('_audit'):value['issues']=[dict(kind='source_limit',disposition='already_disclosed',reason='解释已明确没有外部验收。',record_ids=['a2'])]
            return value
        result=self.run_stage(Client(change));self.assertEqual(result['source_limits_retained'],1)

    def test_actual_issues_cannot_be_reclassified_by_disposition_alone(self):
        def change(kind,value):
            if kind.endswith('_audit'):value['issues']=[dict(kind='unsupported_grade',disposition='already_disclosed',reason='原档没有依据。',record_ids=['h1'])]
            return value
        with self.assertRaises(Paused):self.run_stage(Client(change))

    def test_local_expression_cannot_be_made_a_task(self):
        self.unit['source_units'][0]['source_kind']='local_behavior'
        self.bundle['reports'][0]['eligibility']['observations'][0]['task_keys']=[]
        atomic(self.parent,self.bundle)
        client=Client()
        with self.assertRaises(Paused):self.run_stage(client)
        self.assertEqual(client.calls,0)

    def test_oversize_never_truncates_or_sends(self):
        atomic(self.root/'review.json',dict(value=dict(large='字'*70000)))
        client=Client()
        with self.assertRaises(Paused):self.run_stage(client)
        self.assertEqual(client.calls,0)

    def test_receipt_payload_tampering_rejected(self):
        client=Client();self.run_stage(client)
        path=self.root/'new/generation.receipt.json';data=json.loads(path.read_text());data['request_sha256']='0'*64;atomic(path,data)
        with self.assertRaises(ValueError):stage.verify(self.bundle,self.unit,self.parent,self.root/'new',client.config)

    def test_source_binding_only_adds_frozen_membership(self):
        data=dict(source_units=[dict(key='one',record_ids=['a']),dict(key='two',record_ids=['b'])],
                  records=[dict(id='a'),dict(id='b')])
        original=dict(items={'P1':dict(source_keys=['one'],record_ids=['b'],verdict='needs_reassessment',basis='原判断待重评')})
        before=copy.deepcopy(original)
        result, changes=stage.bind_sources(data,original)
        self.assertEqual(original,before)
        self.assertEqual(result['items']['P1']['source_keys'],['one','two'])
        row=copy.deepcopy(result);row['items']['P1']['source_keys']=['one']
        self.assertEqual(row,original)
        self.assertEqual(set(changes),{'P1'})

    def test_source_binding_cannot_hide_foreign_records(self):
        data=dict(source_units=[dict(key='one',record_ids=['a'])],records=[dict(id='a')])
        with self.assertRaises(ValueError):
            stage.bind_sources(data,dict(items={'P1':dict(source_keys=['one'],record_ids=['foreign'])}))


if __name__=='__main__':unittest.main()
