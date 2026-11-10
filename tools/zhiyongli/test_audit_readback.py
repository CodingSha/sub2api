"""复查可自行发现缺失原文；合成结论仅验证执行，不代表评审质量。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from audit_readback import context,verify_extension
from pipeline import evaluate_week,received,Paused
from runtime import atomic,digest,RUBRIC
from review_control import parent_proof
from shared_weeks import reference
from test_execution_paths import FixtureClient,packet,record


class Auditor(FixtureClient):
    revise=False
    unknown=False

    def identify(self,p,path):return json.loads(path.read_text()) if path.exists() else super().identify(p,path)
    def call(self,u,path):return json.loads(path.read_text()) if path.exists() else super().call(u,path)

    def audit_review(self,u,r,path):
        if path.exists():return json.loads(path.read_text())
        extra=next((e for e in u['events'] if e['event']=='missing-context'),None)
        if extra is None:
            self.calls+=1
            return record(dict(items={},global_issues=[],needs_events=['missing-context']),path)
        if self.unknown:
            self.calls+=1;value=dict(status='unknown');atomic(path,value);return value
        value=received(super().audit_review(u,r,path))
        value['items']['P2']['source_quotes'].append(dict(event=extra['event'],quote=extra['text']))
        if self.revise:value['items']['P2'].update(verdict='revise',problem_codes=['synthetic-counterevidence'])
        return record(value,path)


def source():
    p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
    p['events'].append(dict(event='missing-context',text='合成工具失败与尚未修复的反证，不可由正面概括覆盖。',
        at='2026-09-07T11:00:00+08:00',actor_class='context',timestamp_unknown=False,session_id='test'))
    return p


def view(p,*args):
    value=copy.deepcopy(p);value['events']=value['events'][:1]
    value['reading_notes']=[dict(event_id='missing-context',summary='合成失败导航，评分阶段未补读')]
    return value


class AuditReadbackTests(unittest.TestCase):
    def test_review_stays_frozen_auditor_adds_raw_and_replay_does_not_resend(self):
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);p=source();client=Auditor();result=evaluate_week(p,root,client)
            unit=json.loads((root/'unit.json').read_text());extra=context(root,unit)
            self.assertNotIn('missing-context',{e['event'] for e in unit['events']})
            self.assertEqual(p['events'][-1]['text'],extra['events'][-1]['text'])
            self.assertEqual('context',extra['events'][-1]['actor_class'])
            self.assertEqual(unit['person_events'],extra['person_events'])
            audit=json.loads((root/'audit.json').read_text())
            self.assertEqual((digest(unit),digest(extra)),(audit['unit_sha256'],audit['context_sha256']))
            self.assertIn('audit-readback/1/audit.receipt.json',parent_proof(root,unit)['model_audit_receipt']['path'])
            calls=client.calls;self.assertEqual(result,evaluate_week(p,root,client));self.assertEqual(calls,client.calls)

    def test_counterevidence_disagreement_blocks_and_explicit_resolution_binds_final_audit(self):
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);p=source();client=Auditor();client.revise=True
            with self.assertRaises(Paused) as caught:evaluate_week(p,root,client)
            self.assertEqual('needs_review',caught.exception.state);self.assertFalse((root/'bundle.json').exists())
            unit=json.loads((root/'unit.json').read_text());original=(root/'audit-readback/1/audit.receipt.json').read_bytes()
            audit=json.loads((root/'audit.json').read_text());self.assertFalse(audit['supported'])
            audit['value']['items']['P2'].update(verdict='supported',reason='仅合成逐项处置合同，不代表反证可忽略',problem_codes=[])
            review=json.loads((root/'review.json').read_text())['value']
            proof=dict(parent=parent_proof(root,unit),reviewer='合成核对者',reviewer_kind='codex',reason='仅程序合同测试',review=review,assessment=audit['value'])
            atomic(root/'resolution-proof.json',proof);atomic(root/'review-resolution.json',reference(root/'resolution-proof.json'))
            calls=client.calls;result=evaluate_week(p,root,client)
            self.assertEqual('80.0',result['reports'][0]['score']);self.assertEqual(calls,client.calls)
            self.assertEqual(original,(root/'audit-readback/1/audit.receipt.json').read_bytes())

    def test_unknown_supplement_stays_unknown_across_resume(self):
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);p=source();client=Auditor();client.unknown=True
            with self.assertRaises(Paused) as caught:evaluate_week(p,root,client)
            self.assertEqual('unknown',caught.exception.state);calls=client.calls
            with self.assertRaises(Paused) as caught:evaluate_week(p,root,client)
            self.assertEqual('unknown',caught.exception.state);self.assertEqual(calls,client.calls)
            self.assertFalse((root/'current-model-audit.json').exists())

    def test_one_new_request_per_turn_preserves_and_resumes_audit_chain(self):
        class Fair(Auditor):
            allowance=1
            def check(self,path):
                if not path.exists():
                    if not self.allowance:raise Paused('queued','合成每轮一个新请求')
                    self.allowance-=1
            def identify(self,p,path):self.check(path);return super().identify(p,path)
            def call(self,u,path):self.check(path);return super().call(u,path)
            def audit_review(self,u,r,path):self.check(path);return super().audit_review(u,r,path)
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);client=Fair();p=source();result=None;rounds=0
            while result is None:
                rounds+=1;self.assertLessEqual(rounds,4);client.allowance=1
                try:result=evaluate_week(p,root,client)
                except Paused as ex:self.assertEqual('queued',ex.state)
            self.assertEqual((4,4),(rounds,client.calls))
            self.assertEqual('80.0',result['reports'][0]['score'])

    def test_explicit_resolution_parent_binds_every_audit_attempt_and_query(self):
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);p=source();client=Auditor();evaluate_week(p,root,client)
            unit=json.loads((root/'unit.json').read_text());parent=parent_proof(root,unit)
            paths={Path(ref['path']).relative_to(root.resolve()).as_posix() for ref in parent['model_audit_chain']}
            self.assertTrue({'audit.receipt.json','audit-readback/1/audit.receipt.json','audit-readback/1/request.json'}<=paths)
            atomic(root/'audit-readback/1/request.json',dict(changed=True))
            self.assertNotEqual(parent,parent_proof(root,unit))

    def test_invalid_mixed_future_and_exhausted_requests_never_send_followup(self):
        class Broken(Auditor):
            def audit_review(self,u,r,path):
                if path.exists():return json.loads(path.read_text())
                self.calls+=1;return record(self.value,path)
        for request,error in [(dict(items={},global_issues=[],needs_events=['other-user']),'冻结来源'),
                              (dict(items={'P1':{}},global_issues=[],needs_events=['missing-context']),'不能同时'),
                              (dict(items={},global_issues=[],needs_events=['missing-context']),'截止之后')]:
            with self.subTest(error=error),tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
                p=source()
                if error=='截止之后':p['events'][-1]['at']=p['until']
                client=Broken();client.value=request
                with self.assertRaisesRegex(ValueError,error):evaluate_week(p,Path(tmp),client)
                self.assertEqual(3,client.calls)
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view),patch('audit_readback.MAX_ROUNDS',0):
            client=Auditor()
            with self.assertRaisesRegex(ValueError,'执行上限'):evaluate_week(source(),Path(tmp),client)
            self.assertEqual(3,client.calls)

    def test_capacity_guard_is_before_audit_send_and_does_not_change_review(self):
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view),patch('audit_readback.INPUT_BYTES',100):
            root=Path(tmp);client=Auditor()
            with self.assertRaisesRegex(ValueError,'本轮不发送'):evaluate_week(source(),root,client)
            self.assertEqual(2,client.calls);self.assertTrue((root/'review.json').exists())
            self.assertFalse((root/'audit.receipt.json').exists())

    def test_shared_reuse_rejects_missing_changed_or_added_audit_artifacts(self):
        for mode in ('changed','deleted','added'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
                root=Path(tmp);p=source();client=Auditor();first=root/'first'
                result=evaluate_week(p,first,client,shared_root=root/'shared');calls=client.calls
                self.assertEqual(result,evaluate_week(p,root/'reuse',client,shared_root=root/'shared'))
                target=first/'audit-readback/context.json'
                if mode=='changed':atomic(target,dict(changed=True))
                elif mode=='deleted':target.unlink()
                else:atomic(first/'audit-readback/unexpected.json',dict(added=True))
                with self.assertRaises((ValueError,FileNotFoundError)):
                    evaluate_week(p,root/'tampered',client,shared_root=root/'shared')
                self.assertEqual(calls,client.calls)

    def test_extension_cannot_change_original_ownership_or_forge_added_text(self):
        p=source();unit=dict(events=[dict(event='own',text='合成本人',actor_class='human')],person_events=['own'])
        from context_readback import expand
        value=expand(unit,p,['missing-context'])
        verify_extension(unit,value,p)
        for target,key,text in [(0,'actor_class','context'),(1,'text','伪造反证'),(1,'actor_class','human')]:
            changed=copy.deepcopy(value);changed['events'][target][key]=text
            with self.assertRaises(ValueError):verify_extension(unit,changed,p)

    def test_schema_offers_only_known_event_ids_and_final_judgment_rejects_read_requests(self):
        from semantic_audit import schema,validate
        unit=dict(events=[dict(event='present',text='合成材料')],reading_notes=[dict(event_id='missing-context')])
        contract=schema(unit,RUBRIC['items'])
        self.assertEqual(['missing-context','present'],contract['properties']['needs_events']['items']['enum'])
        with self.assertRaises(ValueError):validate(unit,{},dict(items={},global_issues=[],needs_events=['missing-context']),RUBRIC['items'])
        with self.assertRaises(ValueError):validate(unit,{},dict(items={},global_issues=[],needs_events=None),RUBRIC['items'])


class NodeAuditor(Auditor):
    def audit_review(self,u,r,path):
        if path.exists():return json.loads(path.read_text())
        access=u['context_access']
        if access['raw_read_fragments']:
            return super(Auditor,self).audit_review(u,r,path)
        observed={o['node_id']:o['node'] for o in access['observations'] if o['mode']=='inspect'}
        key=access['root']['node_id']
        while key in observed and observed[key]['kind']=='branch':key=observed[key]['children'][0]
        mode='raw' if key in observed and observed[key]['kind']=='leaf' else 'inspect'
        self.calls+=1
        return record(dict(items={},global_issues=[],needs_events=[],needs_nodes=[dict(node_id=key,mode=mode)]),path)


class NodeAuditTests(unittest.TestCase):
    def test_auditor_can_traverse_different_nodes_without_rewriting_score_input(self):
        from context_navigation import Navigation
        import test_context_navigation as navtests
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=source();helper=navtests.NavigationTests();p['events'].extend(helper.packet(80)['events'])
            nav=Navigation(root/'context-navigation',p);nav.build(helper.notes(p),navtests.Reader(),received)
            client=NodeAuditor()
            with patch('pipeline.reading_view',side_effect=view):result=evaluate_week(p,root,client)
            unit=json.loads((root/'unit.json').read_text());audit_unit=context(root,unit)
            self.assertEqual('80.0',result['reports'][0]['score'])
            self.assertEqual(0,unit['context_access']['raw_read_fragments'])
            self.assertEqual(1,audit_unit['context_access']['raw_read_fragments'])
            calls=client.calls
            with patch('pipeline.reading_view',side_effect=view):self.assertEqual(result,evaluate_week(p,root,client))
            self.assertEqual(calls,client.calls)


if __name__=='__main__':unittest.main()
