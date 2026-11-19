"""实际合成accepted链进入独立说明；不替换权威验签、不调用真实模型。"""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from explanation_stage import enrich,verify
from pipeline import evaluate_week
from runtime import atomic,digest
from shared_weeks import reference
from test_execution_paths import FixtureClient,packet
import task_interpretation
import week_basis
from workday_scope import window


class BasisClient:
    def __init__(self,mutate=None):
        self.config=SimpleNamespace(model='synthetic-model',responses_url='http://synthetic.invalid/v1/responses')
        self.calls=0;self.mutate=mutate;self.inputs=[]

    def _call(self,payload,path):
        from request_retry import read_receipt
        if path.exists():return read_receipt(path)
        self.calls+=1;data=json.loads(payload['input']);kind=payload['text']['format']['name']
        self.inputs.append(copy.deepcopy(data))
        if kind=='zhiyongli_week_basis':
            ids=data['case_record_ids'];key=data['case']['task_key']
            boundaries=week_basis.scoring_boundaries(data['week_items'])
            value=dict(covered_record_ids=[r['id'] for r in data['records']],
                items={code:dict(verdict='consistent',basis='合成本人行为支持当前档。',higher_band='更高档所需正证未核实。',
                    boundary_kind=boundaries[code][0],source_keys=[s['key'] for s in data['source_units']],record_ids=ids,
                    limitations=['仅验证结构。']) for code in week_basis.ITEMS},
                case=dict(task_key=key,outcome='合成记录，外部结果未确认。',limitations=['没有独立真人验收。'],
                    claims=[dict(text=text,item_codes=['J2'],record_ids=ids,limitations=[],
                        assessment=dict(kind='support',reason='合成本人明确表达。')) for text in ('合成目标。','合成过程。','合成结果边界。')]))
        else:
            spec=data['interpretation'];source=data['source'];ids=source['case_record_ids']
            row=dict(verdict='supported',reason='合成记录支持。',record_ids=ids)
            value=dict(seen_record_ids=[r['id'] for r in source['records']],
                week={code:dict(row,own_verdict=spec['items'][code]['verdict'],record_ids=spec['items'][code]['record_ids']) for code in week_basis.ITEMS},
                case_claims={'claim_'+str(i):copy.deepcopy(row) for i in range(len(spec['case']['claims']))},
                case_checks={key:copy.deepcopy(row) for key in task_interpretation.CHECKS},issues=[])
        record=dict(status='received',request_sha256=digest(dict(endpoint=self.config.responses_url,payload=payload)),
            response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
        atomic(path,record)
        if self.mutate:self.mutate(kind)
        return record


class ExplanationStageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.parent=self.root/'accepted';self.output=self.root/'explanation';shared=self.root/'shared'
        source=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        source['workday_scope']=window('2026-09-07','2026-09-14')
        self.bundle=evaluate_week(source,self.parent,FixtureClient(),shared_root=shared)
        entries=[p for p in shared.rglob('*.json') if json.loads(p.read_text()).get('state')=='accepted']
        self.assertEqual(1,len(entries));self.entry_path=entries[0];self.entry=reference(self.entry_path)

    def snapshot(self,root):
        return {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}

    def test_actual_accepted_chain_two_calls_preserves_scores_and_readonly_replay(self):
        before=self.snapshot(self.parent);client=BasisClient();result=enrich(self.entry,self.output,client)
        self.assertEqual('basis_checked',result['status']);self.assertEqual(2,client.calls)
        self.assertEqual(before,self.snapshot(self.parent));self.assertTrue((self.output/'result.json').exists())
        self.assertEqual(self.bundle,json.loads((self.parent/'bundle.json').read_text()))
        output=self.snapshot(self.output)
        self.assertEqual(result,verify(self.entry,self.output,client.config));self.assertEqual(2,client.calls)
        self.assertEqual(output,self.snapshot(self.output));self.assertEqual(before,self.snapshot(self.parent))

    def test_wrong_review_or_entry_or_missing_file_refuses_without_explanation_send(self):
        scenarios=('review','entry','missing')
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                target=(self.entry_path if scenario=='entry' else self.parent/('unit.json' if scenario=='missing' else 'review.json'))
                original=target.read_bytes()
                if scenario=='missing':target.unlink()
                else:target.write_text('{"tampered":true}')
                client=BasisClient()
                try:
                    with self.assertRaises((ValueError,FileNotFoundError,KeyError)):enrich(self.entry,self.root/scenario,client)
                    self.assertEqual(0,client.calls);self.assertFalse((self.root/scenario/'result.json').exists())
                finally:target.write_bytes(original)

    def test_original_review_changes_during_generation_preserves_receipts_without_top_result(self):
        def mutate(kind):
            if kind=='zhiyongli_week_basis':(self.parent/'review.json').write_text('{"changed":true}')
        client=BasisClient(mutate)
        with self.assertRaisesRegex(ValueError,'已核评分链或说明代码变化'):enrich(self.entry,self.output,client)
        self.assertEqual(2,client.calls)
        self.assertTrue((self.output/'basis/generation.receipt.json').exists())
        self.assertTrue((self.output/'basis/review.receipt.json').exists())
        self.assertFalse((self.output/'result.json').exists())

    def test_readonly_missing_explanation_file_does_not_rebuild(self):
        client=BasisClient();enrich(self.entry,self.output,client)
        path=self.output/'basis/spec.json';path.unlink();before=self.snapshot(self.output)
        with self.assertRaises(ValueError):verify(self.entry,self.output,client.config)
        self.assertEqual(before,self.snapshot(self.output));self.assertFalse(path.exists());self.assertEqual(2,client.calls)

    def test_reregistered_score_mismatch_cannot_replace_authoritative_review(self):
        path=self.parent/'bundle.json';bundle=json.loads(path.read_text())
        self.assertEqual('80.0',bundle['reports'][0]['score'])
        bundle['reports'][0]['score']='99.0';atomic(path,bundle)
        entry=json.loads(self.entry_path.read_text())
        entry['files']['bundle.json']=reference(path);atomic(self.entry_path,entry)
        client=BasisClient()
        with self.assertRaises(ValueError):enrich(reference(self.entry_path),self.output,client)
        self.assertEqual(0,client.calls)
        self.assertFalse((self.output/'result.json').exists())

    def test_reregistered_reason_identity_and_native_mismatches_refuse_without_send(self):
        for mode in ('item_reason','source_digest','user_email','native_report'):
            with self.subTest(mode=mode):
                name='native-report.json' if mode=='native_report' else 'bundle.json'
                path=self.parent/name;original=path.read_bytes();entry_bytes=self.entry_path.read_bytes()
                value=json.loads(original)
                if mode=='item_reason':value['reports'][0]['items'][0]['reason']='重新登记的错误理由'
                elif mode=='source_digest':value['reports'][0]['source_digest']='0'*64
                elif mode=='user_email':value['reports'][0]['user_email']='other@example.invalid'
                else:value['score']='99.0'
                atomic(path,value);entry=json.loads(entry_bytes)
                entry['files'][name]=reference(path);atomic(self.entry_path,entry)
                client=BasisClient();output=self.root/mode
                try:
                    with self.assertRaises(ValueError):enrich(reference(self.entry_path),output,client)
                    self.assertEqual(0,client.calls);self.assertFalse((output/'result.json').exists())
                finally:path.write_bytes(original);self.entry_path.write_bytes(entry_bytes)

    def test_review_aba_during_prepare_uses_the_verified_original_snapshot(self):
        path=self.parent/'review.json';original=path.read_bytes();review=json.loads(original)['value']
        prepare=week_basis.prepare
        def swapped(*args,**kwargs):
            path.write_text('{"value":{"untrusted_aba":true}}')
            try:return prepare(*args,**kwargs)
            finally:path.write_bytes(original)
        client=BasisClient()
        with patch('week_basis.prepare',side_effect=swapped):
            result=enrich(self.entry,self.output,client)
        self.assertEqual('basis_checked',result['status']);self.assertEqual(2,client.calls)
        self.assertEqual(task_interpretation.clean(review),client.inputs[0]['original_review'])
        self.assertEqual(task_interpretation.clean(review),client.inputs[1]['source']['original_review'])
        self.assertEqual(original,path.read_bytes());self.assertTrue((self.output/'result.json').exists())


if __name__=='__main__':unittest.main()
