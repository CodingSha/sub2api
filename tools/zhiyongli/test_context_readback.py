"""原文补读的范围、容量、崩溃续跑与最终候选绑定；只使用合成模型。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from context_readback import complete, expand,InvalidReadback
from pipeline import evaluate_week, received, Paused
from review_control import model_receipt, parent_proof
from runtime import atomic, digest, RUBRIC, VERSION, REVIEW_POLICY
from test_execution_paths import FixtureClient, packet, record


class ReadbackClient(FixtureClient):
    def __init__(self, response=None):
        super().__init__();self.response=response;self.inputs=[]

    def identify(self, p, receipt):
        return json.loads(receipt.read_text()) if receipt.exists() else super().identify(p, receipt)

    def call(self, unit, receipt):
        if receipt.exists():return json.loads(receipt.read_text())
        self.inputs.append(copy.deepcopy(unit))
        if 'missing-context' not in {e['event'] for e in unit['events']}:
            self.calls+=1
            return record(self.response or dict(version=VERSION,review_policy=REVIEW_POLICY,
                key=unit['key'],needs_events=['missing-context'],evidence=[],period_reviews=[]),receipt)
        return super().call(unit,receipt)

    def audit_review(self, unit, review, receipt):
        return json.loads(receipt.read_text()) if receipt.exists() else super().audit_review(unit,review,receipt)


class ReadbackTests(unittest.TestCase):
    def source(self):
        p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        p['events'].append(dict(event='missing-context',text='合成工具失败与后续未决，必须保留。\\n😀',
            at='2026-09-07T10:01:00+08:00',actor_class='context',timestamp_unknown=False,session_id='test'))
        return p

    def unit(self,p):
        return dict(key='test:week',user_id='test',version=VERSION,evaluation_scope='person_week',
            events=[],person_events=['confirmed'],reading_notes=[dict(event_id='missing-context',summary='失败待核')])

    def test_exact_raw_added_without_ownership_or_scope_change(self):
        p=self.source();u=self.unit(p);original=copy.deepcopy(u)
        expanded=expand(u,p,['missing-context'])
        self.assertEqual(original,u)
        self.assertEqual(p['events'][-1]['text'],expanded['events'][0]['text'])
        self.assertEqual('context',expanded['events'][0]['actor_class'])
        self.assertEqual([],expanded['events'][0]['human_spans'])
        self.assertEqual(u['person_events'],expanded['person_events'])
        self.assertEqual(digest(p),expanded['context_readback'][0]['packet_sha256'])

    def test_unknown_repeated_duplicate_and_invalid_requests_are_rejected(self):
        p=self.source();u=self.unit(p)
        for request in ([],['other-user'],['missing-context']*2,[None],['x']*25,'missing-context'):
            with self.subTest(request=request),self.assertRaises(ValueError):expand(u,p,request)
        with self.assertRaisesRegex(ValueError,'已经完整提供'):
            expand(expand(u,p,['missing-context']),p,['missing-context'])

    def test_future_and_oversized_raw_are_blocked_without_truncation(self):
        p=self.source();u=self.unit(p)
        p['events'][-1]['at']=p['until']
        with self.assertRaisesRegex(ValueError,'截止之后'):expand(u,p,['missing-context'])
        p=self.source();p['events'][-1]['text']='完整原文'*20000
        with self.assertRaisesRegex(ValueError,'超出当前评分容量'):expand(u,p,['missing-context'])
        self.assertEqual('完整原文'*20000,p['events'][-1]['text'])

    def test_full_synthetic_pipeline_audits_augmented_unit_and_binds_final_receipt(self):
        p=self.source();client=ReadbackClient()
        def view(packet,*args):
            result=copy.deepcopy(packet);result['events']=result['events'][:1]
            result['reading_notes']=[dict(event_id='missing-context',summary='合成失败，须补原文')]
            return result
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);result=evaluate_week(p,root,client)
            self.assertEqual('80.0',result['reports'][0]['score'])
            final=json.loads((root/'unit.json').read_text())
            self.assertIn('missing-context',{e['event'] for e in final['events']})
            self.assertEqual((root/'context-readback/1/review.receipt.json').resolve(),model_receipt(root,final))
            proof=parent_proof(root,final)
            self.assertEqual(str(model_receipt(root,final)),proof['model_receipt']['path'])
            first=(root/'review.receipt.json').read_bytes();calls=client.calls
            self.assertEqual(result,evaluate_week(p,root,client))
            self.assertEqual(calls,client.calls);self.assertEqual(first,(root/'review.receipt.json').read_bytes())
            atomic(root/'context-readback/1/review.receipt.json',dict(status='unknown'))
            with self.assertRaises(Paused) as ex:evaluate_week(p,root,client)
            self.assertEqual('unknown',ex.exception.state);self.assertEqual(calls,client.calls)

    def test_mixed_scores_or_changed_source_never_send_extra_request(self):
        p=self.source();u=self.unit(p)
        client=ReadbackClient(dict(needs_events=['missing-context'],evidence=[{'quote':'部分评分'}]))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError,'不能同时提交评分'):complete(u,p,root,client,received)
            self.assertEqual(1,client.calls)
            p['events'][-1]['text']='已变化'
            with self.assertRaisesRegex(ValueError,'冻结来源'):complete(u,p,root,client,received)
            self.assertEqual(1,client.calls)

    def test_mixed_readback_uses_bounded_revision_and_keeps_prior_receipt(self):
        class Repairer(ReadbackClient):
            accepts_revision_feedback=True
            feedback=None;always=False
            def call(self,unit,path,feedback=None):
                if path.exists():return json.loads(path.read_text())
                if feedback is None or self.always:
                    self.calls+=1
                    return record(dict(version=VERSION,review_policy=REVIEW_POLICY,key=unit['key'],
                        needs_events=['missing-context'],evidence=[dict(quote='违规部分评分')],period_reviews=[]),path)
                self.feedback=feedback
                return super().call(unit,path)
        def view(packet,*args):
            result=copy.deepcopy(packet);result['events']=result['events'][:1]
            result['reading_notes']=[dict(event_id='missing-context',summary='合成背景')]
            return result
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);client=Repairer();result=evaluate_week(self.source(),root,client)
            self.assertEqual(1,len(result['reports']))
            self.assertEqual(1,len(list((root/'revisions').iterdir())))
            self.assertIn('不能同时提交评分',client.feedback['contract_error']['error'])
            original=(root/'review.receipt.json').read_bytes();calls=client.calls
            self.assertEqual(result,evaluate_week(self.source(),root,client))
            self.assertEqual(calls,client.calls);self.assertEqual(original,(root/'review.receipt.json').read_bytes())
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);client=Repairer();client.always=True
            with self.assertRaises(Paused):evaluate_week(self.source(),root,client)
            self.assertEqual(2,len(list((root/'revisions').iterdir())))
            calls=client.calls
            with self.assertRaises(Paused):evaluate_week(self.source(),root,client)
            self.assertEqual(calls,client.calls)

    def test_capacity_is_revisable_but_integrity_error_is_not(self):
        from types import SimpleNamespace
        from navigation_access import ReadbackCapacity
        p=self.source();u=self.unit(p)
        reply=dict(version=VERSION,review_policy=REVIEW_POLICY,key=u['key'],needs_events=[],
            needs_nodes=[dict(node_id='synthetic',mode='raw')],evidence=[],period_reviews=[])
        for failure in (ReadbackCapacity('合计超容量'),ValueError('冻结证明变化')):
            with tempfile.TemporaryDirectory() as tmp:
                client=ReadbackClient(reply)
                def expand(*args):raise failure
                access=SimpleNamespace(start=lambda unit:unit,expand=expand)
                with self.assertRaises(ValueError) as error:complete(u,p,Path(tmp),client,received,navigation_access=access)
                self.assertEqual(isinstance(failure,ReadbackCapacity),isinstance(error.exception,InvalidReadback))
                self.assertEqual(1,client.calls)

    def test_round_limit_preserves_last_request_without_additional_call(self):
        p=self.source();u=self.unit(p);client=ReadbackClient()
        with tempfile.TemporaryDirectory() as tmp,patch('context_readback.MAX_ROUNDS',0):
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError,'执行上限'):complete(u,p,root,client,received)
            self.assertEqual(1,client.calls);self.assertTrue((root/'review.receipt.json').exists())
            self.assertFalse((root/'context-readback/1').exists())

    def test_transport_only_allows_frozen_navigation_ids(self):
        from review_schema import schema
        u=self.unit(self.source())
        u['events']=[dict(event='present')]
        transport=schema(u,RUBRIC,VERSION)['properties']['needs_events']
        self.assertEqual(['missing-context'],transport['items']['enum'])
        self.assertEqual(24,transport['maxItems'])

    def test_shared_reuse_checks_supplemental_receipt_signature(self):
        p=self.source();client=ReadbackClient()
        def view(packet,*args):
            result=copy.deepcopy(packet);result['events']=result['events'][:1]
            result['reading_notes']=[dict(event_id='missing-context',summary='失败待核')]
            return result
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);shared=root/'shared';first=root/'first'
            result=evaluate_week(p,first,client,shared_root=shared);calls=client.calls
            self.assertEqual(result,evaluate_week(p,root/'reuse',client,shared_root=shared))
            self.assertEqual(calls,client.calls)
            path=first/'context-readback/1/review.receipt.json'
            changed=json.loads(path.read_text());changed['note']='已被改动';atomic(path,changed)
            with self.assertRaisesRegex(ValueError,'签名变化'):
                evaluate_week(p,root/'tampered',client,shared_root=shared)
            self.assertEqual(calls,client.calls)


if __name__=='__main__':unittest.main()
