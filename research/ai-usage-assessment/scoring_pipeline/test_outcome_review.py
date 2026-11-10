"""成果观察的证据边界、评分隔离与持久执行合同回归。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from . import outcome_review as r, stable_batch as b
from .batch_client import atomic_json


def fixture():
    period=dict(period_id='month',start='2026-09-01T00:00:00+08:00',end='2026-10-01T00:00:00+08:00')
    request='计算2+2，给出一行答案。';answer='2+2=4'
    events=[dict(event='s:request',event_id='request',at='2026-09-10T12:00:00+08:00',actor_class='human',text=request,human_spans=[[0,len(request)]]),
            dict(event='s:answer',event_id='answer',at='2026-09-10T12:01:00+08:00',actor_class='assistant',text=answer,human_spans=[])]
    u=dict(key='s:t',user_id='test',title='算术回答',periods=[period],person_events=['s:request'],events=events)
    v=dict(version=r.VERSION,key='s:t',evidence=[dict(id='q_req',event='s:request',quote=request),dict(id='q_out',event='s:answer',quote=answer)],needs_events=[],period_reviews=[dict(
        period_ids=['month'],stage=dict(description='一行算术回答',refs=['q_req']),
        requirements=[dict(id='r',text='计算2+2，一行答复',basis='explicit',refs=['q_req'])],
        artifacts=[dict(id='a',description='实际一行答复',version_basis='截点前唯一回答',refs=['q_out'])],
        delivery=dict(status='observed',reason='有答复正文',refs=['q_out']),
        cells=[dict(item=k,verdict='supported',reason='所见算式和格式可直接核对',checked_scope='仅一行可见答复',requirement_ids=['r'],artifact_ids=['a'],refs=['q_out'],verification_level='content_review',critical_gap=False,impact='') for k in r.ITEMS])])
    return u,v


class OutcomeTests(unittest.TestCase):
    def setUp(self):self.u,self.v=fixture();self.part=self.v['period_reviews'][0]
    def test_result_has_no_ability_score(self):
        row=r.check(self.u,self.v)[0]
        self.assertEqual(row['score_effect'],'none');self.assertNotIn('score',row)
    def test_numeric_score_rejected(self):
        self.part['cells'][0]['score']=100
        with self.assertRaisesRegex(ValueError,'禁止能力评分'):r.check(self.u,self.v)
    def test_missing_artifact_is_unknown_not_failure(self):
        self.part['artifacts']=[];self.part['delivery'].update(status='reported',refs=['q_req'])
        for c in self.part['cells']:
            c.update(verdict='unknown',artifact_ids=[],verification_level='unverified',missing=dict(needed='实际成果正文',why_changes_judgment='决定正确性和交付内容',checked_events=['s:request','s:answer']))
        self.assertTrue(r.check(self.u,self.v))
    def test_claim_cannot_prove_supported(self):
        self.part['cells'][1]['verification_level']='self_report'
        with self.assertRaisesRegex(ValueError,'仅自述/未验证'):r.check(self.u,self.v)
    def test_unknown_requirement_cannot_support_quality(self):
        self.part['requirements'][0]['basis']='unknown'
        with self.assertRaisesRegex(ValueError,'未知要求'):r.check(self.u,self.v)
    def test_implicit_requirement_needs_necessity(self):
        self.part['requirements'][0]['basis']='necessary'
        with self.assertRaisesRegex(ValueError,'必要性解释'):r.check(self.u,self.v)
    def test_critical_gap_cannot_be_marked_supported(self):
        self.part['cells'][1]['critical_gap']=True
        with self.assertRaisesRegex(ValueError,'关键缺陷'):r.check(self.u,self.v)
    def test_gap_needs_impact(self):
        self.part['cells'][1]['verdict']='gap'
        with self.assertRaisesRegex(ValueError,'实际影响'):r.check(self.u,self.v)
    def test_future_artifact_is_rejected(self):
        self.u['events'][1]['at']='2026-10-02T00:00:00+08:00'
        with self.assertRaisesRegex(ValueError,'引用未来'):r.check(self.u,self.v)
    def test_false_quote_rejected(self):
        self.v['evidence'][1]['quote']='2+2=5'
        with self.assertRaisesRegex(ValueError,'不逐字存在'):r.check(self.u,self.v)
    def test_missing_quality_aspect_rejected(self):
        self.part['cells'].pop()
        with self.assertRaisesRegex(ValueError,'四项须恰一次'):r.check(self.u,self.v)
    def test_assembly_preserves_outcome_version(self):
        v=b.assemble('s:t',[dict(period_id='month',value=self.v)],contract=r)
        self.assertEqual(v['version'],r.VERSION);self.assertEqual(v['period_reviews'][0]['artifacts'][0]['refs'],['month|q_out'])
        self.assertTrue(r.check(self.u,v))
    def test_model_call_uses_explicit_outcome_contract(self):
        pack=dict(raw_material=dict(person_events=[dict(event='s:request',text=self.u['events'][0]['text'])],prior_person_context=[],linked_context=[dict(event='s:answer',preview=self.u['events'][1]['text'])]))
        client=Mock();client.call.return_value=dict(text=json.dumps(self.v),request_sha256='test')
        with tempfile.TemporaryDirectory() as tmp,patch.object(b,'packet',return_value=(self.u,pack,{e['event']:e for e in self.u['events']})):
            value,rows,_=b.evaluate_one({},'s:t',Path(tmp),dict(run_id='test',replicate='test'),client,self.u['periods'][0],contract=r)
        self.assertEqual(value,self.v);self.assertEqual(rows[0]['score_effect'],'none')
        args=client.call.call_args.args;self.assertEqual(args[0],r.INSTRUCTIONS);self.assertEqual(args[1]['task']['policy_version'],r.VERSION)
    def test_wrong_contract_cannot_resume_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dataset=root/'dataset.json';atomic_json(dataset,{})
            data=dict(tasks={'s:t':dict(local_only=False)})
            with patch.object(b,'verified_dataset',return_value=data),patch.object(b,'configured_model',return_value='test'):
                run=root/'run';b.initialize(dataset,run,contract=r)
                self.assertEqual(b.open_run(run,contract=r)[1]['version'],r.VERSION)
                with self.assertRaisesRegex(ValueError,'执行/语义版本变化'):b.open_run(run)


if __name__=='__main__':unittest.main()
