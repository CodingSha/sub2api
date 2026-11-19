"""复查容量同时计入候选；压缩展示不能省掉引用或完整来源分母。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from bounded_read import raw_size
from pipeline import audit_materials, received
from model_view import create, manifest, verify
from runtime import CompanyClient, digest, atomic
from test_execution_paths import FixtureClient
from test_model_view import fixture, setup


class AuditInputCapacityTests(unittest.TestCase):
    def test_large_candidate_uses_navigation_without_dropping_its_evidence(self):
        packet, unit = fixture()
        extra = next(e for e in unit['events'] if e['event'] == 'context-0')
        extra['text'] = 'x' * 87000
        next(e for e in packet['events'] if e['event'] == extra['event'])['text'] = extra['text']
        own = next(e for e in unit['events'] if e['event'] in unit['person_events'])
        view = create(unit, seeds=[own, extra])
        review = received(FixtureClient().call(view, None))
        review['period_reviews'][0]['cells'][0]['reason'] = 'r' * 33065
        before = digest(dict(unit=unit, view=view, review=review, packet=packet))

        class Client(FixtureClient):
            def audit_review(self, current, candidate, path):
                signature=digest(dict(unit=current,review=candidate))
                if path.exists():
                    if self.signature!=signature:raise AssertionError('恢复改变原复查请求')
                    return json.loads(path.read_text())
                self.signature=signature
                self.context = copy.deepcopy(current)
                self.assertion = raw_size(dict(unit=current, review=candidate, contract_feedback=None))
                if self.assertion > 120000:
                    raise AssertionError('候选与材料组合超限')
                return super().audit_review(current, candidate, path)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); setup(root, packet); client = Client()
            result = audit_materials(packet, unit, view, review, root, client, lambda *a: None, True, None)
            self.assertTrue(result['supported'])
            self.assertEqual(manifest(unit), client.context['model_view'])
            self.assertIn(own, client.context['events'])
            self.assertNotIn(extra, client.context['events'])
            verify(unit, client.context, packet)
            from audit_readback import context
            atomic(root/'model-review.json', dict(value=review))
            self.assertEqual(client.context, context(root, unit))
            self.assertLess(client.context['context_access']['remaining_bytes'], 120000-raw_size(client.context))
            calls=client.calls
            self.assertEqual(result,audit_materials(packet,unit,view,review,root,client,lambda *a:None,True,None))
            self.assertEqual(calls,client.calls)
            from shared_weeks import navigation_files,reference,check_navigation
            paths=navigation_files(root)
            proof=root/'audit-capacity-access/input.json'
            self.assertIn(proof,paths)
            entry=dict(directory=str(root),navigation_files=[reference(p) for p in paths])
            check_navigation(entry,unit)
            atomic(proof,dict(changed=True))
            with self.assertRaisesRegex(ValueError,'签名变化'):check_navigation(entry,unit)
        self.assertEqual(before, digest(dict(unit=unit, view=view, review=review, packet=packet)))

    def test_required_evidence_over_capacity_stops_before_model(self):
        packet, unit = fixture()
        own = next(e for e in unit['events'] if e['event'] in unit['person_events'])
        view = create(unit, seeds=[own])
        review = received(FixtureClient().call(view, None))
        review['period_reviews'][0]['cells'][0]['reason'] = 'r' * 119000
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); setup(root, packet); client = FixtureClient()
            with self.assertRaisesRegex(ValueError, '容量'):
                audit_materials(packet, unit, view, review, root, client, lambda *a: None, True, None)
            self.assertEqual(0, client.calls)
            self.assertFalse(list(root.glob('audit*.receipt.json')))

    def test_actual_audit_payload_over_capacity_stops_before_transport(self):
        client = CompanyClient.__new__(CompanyClient)
        client.config = type('Config', (), dict(model='synthetic'))()
        calls = []
        client._call = lambda *args: calls.append(args)
        with self.assertRaisesRegex(ValueError, '容量'):
            client.audit_review(dict(events=[dict(event='event', text='"' * 95000)]), {}, Path('unused'))
        self.assertEqual([], calls)

    def test_missing_check_event_is_mandatory_and_existing_audit_is_not_repacked(self):
        packet,unit=fixture();view=create(unit)
        review=received(FixtureClient().call(view,None))
        review['period_reviews'][0]['cells'][0]['missing']=dict(checked_events=['context-299'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);setup(root,packet);client=FixtureClient()
            with self.assertRaisesRegex(ValueError,'未提供|已提供'):
                audit_materials(packet,unit,view,review,root,client,lambda *a:None,True,None)
            self.assertEqual(0,client.calls)
        review=received(FixtureClient().call(view,None))
        review['period_reviews'][0]['cells'][0]['reason']='r'*119000
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);setup(root,packet);client=FixtureClient()
            path=root/'audit.receipt.json';atomic(path,dict(status='unknown'));old=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'容量'):
                audit_materials(packet,unit,view,review,root,client,lambda *a:None,True,None)
            self.assertEqual(old,path.read_bytes());self.assertEqual(0,client.calls)
            self.assertFalse((root/'audit-capacity-access/input.json').exists())

    def test_navigation_includes_remaining_field_before_final_capacity_check(self):
        from navigation_access import Access
        packet,unit=fixture();view=create(unit)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);nav=setup(root,packet)
            normal=Access(nav,root/'normal',native=unit).start(view)
            normal['context_access']['remaining_bytes']=0
            maximum=raw_size(normal)-1
            with self.assertRaisesRegex(ValueError,'容量'):
                Access(nav,root/'bounded',native=unit,unit_bytes=maximum).start(view)

    def test_quote_slot_repair_measures_only_its_actual_transport(self):
        from audit_readback import checked
        from runtime import RUBRIC
        from test_execution_paths import record
        unit=dict(events=[dict(event='source',text='真实格式夹具依据'),dict(event='unused',text='x'*80000)])
        review=dict(padding='r'*33000)
        value=dict(items={i:dict(verdict='supported',reason='具体依据'+'r'*1000,
            person_act='合成本人事实'+i,own_state='observed',
            source_quotes=[dict(event='source',quote='真实格式夹具依据')],problem_codes=[]) for i in RUBRIC['items']},global_issues=[])
        value['items']['P1']['source_quotes'][0]['quote']='不在原文的引文'
        class Client(CompanyClient):
            accepts_revision_feedback=True
            supports_quote_candidates=False
            def __init__(self):
                self.config=type('Config',(),dict(model='synthetic'))();self.calls=0
            def audit_review(self,current,candidate,path,feedback=None):
                self.calls+=1
                if feedback is not None:
                    if raw_size(dict(unit=current,review=candidate,contract_feedback=feedback))<=120000:
                        raise AssertionError('夹具未覆盖整份反馈超限')
                    return record(dict(event='source',quote='真实格式夹具依据'),path)
                return record(value,path)
        with tempfile.TemporaryDirectory() as tmp:
            client=Client()
            _,_,result,_=checked(unit,unit,review,Path(tmp)/'audit.receipt.json',client,received)
            self.assertTrue(result['supported']);self.assertEqual(2,client.calls)


if __name__ == '__main__':
    unittest.main()
