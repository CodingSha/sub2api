"""互斥终判、局部纠正及无员工审核出口；合成回执不证明模型质量。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

import jsonschema

from pipeline import evaluate_week, received, Paused
from runtime import CompanyClient, RUBRIC
from review_protocol import VERSION, schema, decode
from test_execution_paths import packet, record
from test_single_pass import Client as SingleClient


def envelope(value):
    return dict(review_output_protocol=VERSION, review=value)


class Client(SingleClient):
    evidence_first=True
    supports_review_protocol=True
    from test_candidate_segments import SegmentClient
    merge_goals=SegmentClient.merge_goals
    def navigate_context(self,*args,**kwargs):
        raise AssertionError('程序索引不调用模型导航')
    def __init__(self, errors=()):
        super().__init__(); self.errors=iter(errors);self.feedbacks=[];self.raws=[]
    def call(self, unit, receipt, feedback=None):
        if receipt.exists():return json.loads(receipt.read_text())
        value=received(super().call(unit,receipt))
        if unit.get('context_access'):value['needs_nodes']=[]
        self.feedbacks.append(feedback)
        error=next(self.errors,None)
        if error=='missing_presentation':value['presentation']=None
        elif error=='bad_quote':value['evidence'][0]['quote']='这不是原文'
        elif error=='mixed':value['needs_events']=['missing']
        elif error=='unknown':
            return record_unknown(receipt)
        result=envelope(value)
        self.raws.append(copy.deepcopy(result))
        return record(result,receipt)


def record_unknown(path):
    from runtime import atomic
    value=dict(status='unknown');atomic(path,value);return value


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.packet=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')

    def test_actual_sender_schema_rejects_null_and_mixed_final(self):
        client=Client();evaluate_week(self.packet,self.root/'week',client)
        unit=json.loads((self.root/'week/unit.json').read_text())
        sender=CompanyClient(SimpleNamespace(model='synthetic',timeout=1))
        sender.evidence_first=True;sender._single_pass=True;sender.presentation_case=client.presentation_case
        sent=[];sender._call=lambda payload,path:sent.append(payload)
        sender.call(unit,self.root/'unused')
        contract=sent[0]['text']['format']['schema']
        self.assertEqual('object',contract['type']);self.assertNotIn('anyOf',contract)
        jsonschema.Draft202012Validator.check_schema(contract)
        jsonschema.validate(client.raws[0],contract)
        for field,value in [('presentation',None),('needs_events',['missing'])]:
            bad=copy.deepcopy(client.raws[0]);bad['review'][field]=value
            with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(bad,contract)
        self.assertEqual(received(record(client.raws[0])),decode(client.raws[0]))

    def test_scoring_only_repairs_preserve_identification_and_resume_without_sends(self):
        client=Client(['mixed','missing_presentation'])
        result=evaluate_week(self.packet,self.root/'week',client)
        self.assertEqual('80.0',result['reports'][0]['score'])
        self.assertEqual(4,client.calls)  # 一次归属、一次评分、两次局部纠正。
        self.assertIsNone(client.feedbacks[0]);self.assertTrue(client.feedbacks[1]['contract_feedback'])
        original=(self.root/'week/review.receipt.json').read_bytes()
        self.assertEqual(result,evaluate_week(self.packet,self.root/'week',client))
        self.assertEqual(4,client.calls);self.assertEqual(original,(self.root/'week/review.receipt.json').read_bytes())
        pointer=json.loads((self.root/'week/current-model-review.json').read_text())
        self.assertIn('review-contract-repair/2',pointer['receipt']['path'])
        self.assertFalse((self.root/'week/active-revision.json').exists())

    def test_invalid_citation_is_corrected_without_relaxing_validator(self):
        client=Client(['bad_quote']);result=evaluate_week(self.packet,self.root/'week',client)
        self.assertEqual(3,client.calls);self.assertEqual('80.0',result['reports'][0]['score'])
        error=client.feedbacks[-1]['contract_feedback']['error']
        self.assertTrue('引文' in error or '原文' in error,error)

    def test_readback_capacity_returns_specific_feedback_and_preserves_previous_receipt(self):
        from review_protocol import call_checked
        from navigation_access import ReadbackCapacity
        client=Client();evaluate_week(self.packet,self.root/'fixture',client)
        unit=json.loads((self.root/'fixture/unit.json').read_text())
        final=client.raws[0]
        bad=copy.deepcopy(final)
        bad['review'].update(evidence=[],period_reviews=[],presentation=None,needs_events=['extra'])
        class Requests:
            def __init__(self):self.calls=0;self.feedback=[]
            def call(self,u,path,feedback=None):
                if path.exists():return json.loads(path.read_text())
                self.calls+=1;self.feedback.append(feedback)
                return record(bad if feedback is None else final,path)
        requests=Requests();path=self.root/'review.receipt.json'
        def prepare(value):raise ReadbackCapacity('完整补读连同证明超过容量；请减少节点')
        result,target,prepared=call_checked(unit,path,requests,received,prepare_readback=prepare)
        self.assertEqual(2,requests.calls);self.assertIsNone(prepared)
        self.assertEqual(final,json.loads(result['response']['output'][0]['content'][0]['text']))
        self.assertIn('连同证明',requests.feedback[-1]['contract_feedback']['error'])
        self.assertEqual(bad,json.loads(json.loads(path.read_text())['response']['output'][0]['content'][0]['text']))
        call_checked(unit,path,requests,received,prepare_readback=prepare)
        self.assertEqual(2,requests.calls)

    def test_repeated_invalid_results_end_as_technical_failure_and_do_not_loop(self):
        client=Client(['missing_presentation']*4)
        for _ in range(2):
            with self.assertRaises(Paused) as ex:evaluate_week(self.packet,self.root/'week',client)
            self.assertEqual(('failed','model_contract_invalid'),(ex.exception.state,ex.exception.reason_code))
        self.assertEqual(4,client.calls);self.assertFalse((self.root/'week/bundle.json').exists())

    def test_unknown_never_enters_correction(self):
        client=Client(['unknown'])
        for _ in range(2):
            with self.assertRaises(Paused) as ex:evaluate_week(self.packet,self.root/'week',client)
            self.assertEqual('unknown',ex.exception.state)
        self.assertEqual(2,client.calls);self.assertEqual([None],client.feedbacks)

    def test_unattributable_material_finishes_with_explicit_unscored_items(self):
        class Unattributable(Client):
            def identify(self, p, receipt):
                if receipt.exists():return json.loads(receipt.read_text())
                self.calls+=1
                return record(dict(materials={e['event']:dict(classification='unresolved',human_quotes=[],
                    reason='来源混有自动注入，不能确认本人',duplicate_of=None,goal_id=None,step='归属不明',assignment_reason='没有确认本人目标，不认领任务')
                    for e in p['events'] if e['actor_class']=='candidate_user'},goals=[]),receipt)
            def call(self,u,receipt,feedback=None):
                if receipt.exists():return json.loads(receipt.read_text())
                self.calls+=1
                from runtime import VERSION as REVIEW_VERSION, REVIEW_POLICY
                event=u['events'][0];ref='context-input'
                cells=[dict(item=i,state='attribution_unclear',reason='输入归属无法确认，本项不计分。',
                    opportunity=dict(description='记录包含候选输入，不能认领为本人。',refs=[ref]),
                    missing=dict(needed='可定位的本人输入',why_changes_judgment='未能确认本人，不能计分。',checked_events=[event['event']]),
                    **(dict(challenge_handling=dict(status='not_demonstrated',reason='没有已证本人处理',refs=[])) if i.startswith('M') else {})) for i in RUBRIC['items']]
                value=dict(version=REVIEW_VERSION,review_policy=REVIEW_POLICY,key=u['key'],needs_events=[],
                    evidence=[dict(id=ref,event=event['event'],quote=event['text'],origin='context',origin_reason='归属未知仅作背景')],
                    period_reviews=[dict(period_ids=[u['periods'][0]['period_id']],cells=cells,context=dict(scope='scored_week_scope',
                        effective_request='记录包含候选请求',necessary_scope='本期材料范围',observed_execution='无法确认本人行为',
                        boundary='uncertain',boundary_reason='没有已证本人输入',refs=[ref]))],
                    presentation=dict(items=[dict(item_code=i,basis='记录无法确认本人行为，本项不计分。',
                        higher_band='归属未确认，本项没有数值，不作邻档比较。') for i in RUBRIC['items']],case=None))
                if u.get('context_access'):value['needs_nodes']=[]
                return record(envelope(value),receipt)
        client=Unattributable();result=evaluate_week(self.packet,self.root/'week',client)
        report=result['reports'][0]
        self.assertEqual('observation_only',report['score_status']);self.assertIsNone(report['score'])
        self.assertEqual(12,len(report['items']));self.assertEqual(2,client.calls)
        self.assertTrue(all(i['exact'] is None and i['state']=='attribution_unclear' for i in report['items']))
        self.assertEqual(0,len(report['eligibility']['observations']))
        self.assertTrue((self.root/'week/bundle.json').exists())


if __name__=='__main__':unittest.main()
