"""有界视图与完整材料双重校验；合成结论不证明真实评分语义。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_view import create, verify, hydrate, check_review, expose
from pipeline import assemble, evaluate_unit, received
from runtime import atomic, digest, response_value
from task_identification import canonical
from test_execution_paths import FixtureClient, packet, record
import test_context_navigation as navtests
from context_navigation import Navigation
from navigation_access import Access
from review_control import model_receipt
from audit_readback import context


def fixture():
    p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
    e=p['events'][0]
    p['events'] += [dict(event='context-'+str(i),text='合成背景及未决核查'+str(i)+'x'*500,
        actor_class='context',at=e['at'],timestamp_unknown=False) for i in range(300)]
    identified=canonical(p,received(FixtureClient().identify(p,None)))
    return p,assemble(p,identified)


def setup(root,p):
    atomic(root/'packet.json',p)
    nav=Navigation(root/'context-navigation',p)
    nav.build(navtests.NavigationTests().notes(p),navtests.Reader(),received)
    return nav


class Cached(FixtureClient):
    def call(self,u,path):return json.loads(path.read_text()) if path.exists() else super().call(u,path)
    def audit_review(self,u,r,path):return json.loads(path.read_text()) if path.exists() else super().audit_review(u,r,path)


class ModelViewTests(unittest.TestCase):
    def test_repeated_expression_does_not_hide_later_distinct_action_or_goal(self):
        from bounded_read import raw_size
        p,unit,_=self.paired();first=next(e for e in unit['events'] if e['event'] in unit['person_events'])
        repeated=[dict(first,event='repeat-'+str(i)) for i in range(8)]
        later=dict(first,event='later-person',text='没有出现弹窗，请改用输入页。')
        unit['events']=[first]+repeated+[later]
        unit['person_events']=[e['event'] for e in unit['events']]
        unit['source_units'][0]['person_events']=unit['person_events'][:-1]
        unit['source_units'].append(dict(key='second-goal',title='合成后续目标',person_events=[later['event']]))
        unit['source_keys'].append('second-goal');before=digest(unit)
        with patch('model_view.SEED_BYTES',2*max(raw_size(first),raw_size(later))):view=create(unit)
        self.assertIn(later,view['events']);self.assertIn(first,view['events'])
        self.assertLess(len(view['person_events']),len(unit['person_events']))
        self.assertEqual(unit['source_keys'],view['source_keys']);verify(unit,view,p)
        self.assertEqual(before,digest(unit))

    def test_unread_goal_remains_visible_without_claiming_personal_raw_was_read(self):
        p,unit=fixture();view=create(unit,seeds=[])
        self.assertEqual(unit['source_keys'],view['source_keys'])
        self.assertEqual([],view['person_events'])
        self.assertEqual([],view['source_units'][0]['person_events'])
        self.assertEqual(len(unit['source_units'][0]['person_events']),view['source_units'][0]['full_membership']['count'])
        verify(unit,view,p)

    def test_oversized_personal_event_does_not_prevent_smaller_later_event(self):
        from bounded_read import raw_size
        p,unit,_=self.paired();first=next(e for e in unit['events'] if e['event'] in unit['person_events'])
        large=dict(first,event='large-person',text='不能裁掉的本人原文'*1000)
        unit['events']=[large,first];unit['person_events']=[large['event'],first['event']]
        unit['source_units'][0]['person_events']=unit['person_events']
        with patch('model_view.SEED_BYTES',raw_size(first)):view=create(unit)
        self.assertIn(first,view['events']);self.assertNotIn(large,view['events']);verify(unit,view,p)

    def paired(self):
        p,unit=fixture();own=next(e for e in unit['events'] if e['event'] in unit['person_events'])
        own.update(source_user_id=unit['user_id'],request_id='paired-request',session_id='paired-session')
        reply=dict(event='paired-reply',text='已收到质疑，核对发现还有五个接口。',
            source_user_id=unit['user_id'],request_id='paired-request',session_id='paired-session',
            actor_class='context',protocol_role='assistant',timestamp_unknown=False,at=own['at'])
        unit['events'].append(reply)
        return p,unit,reply

    def test_same_request_reply_is_provided_as_context_without_personal_credit(self):
        p,unit,reply=self.paired();before=digest(unit);view=create(unit)
        self.assertIn(reply,view['events']);self.assertNotIn(reply['event'],view['person_events'])
        verify(unit,view,p);self.assertEqual(before,digest(unit))
        # 复查沿用评分实际提供的完整原句，重放验签不变。
        self.assertEqual(view,create(unit,seeds=view['events']))
        changed=copy.deepcopy(view);changed['events'][-1]['actor_class']='human'
        with self.assertRaises(ValueError):verify(unit,changed,p)

    def test_request_pairing_rejects_other_user_session_unknown_history_or_missing_identity(self):
        for changes in (dict(source_user_id='other'),dict(session_id='other'),
                        dict(timestamp_unknown=True),dict(protocol_role='user'),dict(actor_class='human'),
                        dict(source_user_id=None),dict(session_id='')):
            with self.subTest(changes=changes):
                _,unit,reply=self.paired();reply.update(changes)
                self.assertNotIn(reply,create(unit)['events'])
        _,unit,reply=self.paired();next(e for e in unit['events'] if e['event'] in unit['person_events']).pop('source_user_id')
        reply.pop('source_user_id')
        self.assertNotIn(reply,create(unit)['events'])

    def test_later_session_feedback_is_context_without_inventing_request_causality(self):
        p,unit,reply=self.paired();later=dict(reply,event='later-delivery',request_id='different-request',text='已交付修订文件。')
        tool=dict(later,event='other-request-tool',protocol_role='tool')
        unit['events'].extend([later,tool]);view=create(unit)
        self.assertIn(later,view['events']);self.assertNotIn(tool,view['events'])
        self.assertNotIn(later['event'],view['person_events']);verify(unit,view,p)
        self.assertIn('不独立证明时序因果',view['model_view']['limitation'])

    def test_session_delivery_context_precedes_bulk_progress_when_capacity_is_limited(self):
        p,unit,reply=self.paired();own=next(e for e in unit['events'] if e['event'] in unit['person_events'])
        own['at']='2026-09-07T12:00:00+08:00'
        bulk=[dict(reply,event='progress-'+str(i),request_id='progress-'+str(i),text='合成过程背景'*600,
            at='2026-09-07T10:00:00+08:00') for i in range(20)]
        before=dict(reply,event='previous-delivery',request_id='previous',at='2026-09-07T11:00:00+08:00')
        final=dict(reply,event='final-delivery',request_id='final',at='2026-09-07T13:00:00+08:00')
        unit['events'].extend(bulk+[before,final]);view=create(unit)
        self.assertIn(before,view['events']);self.assertIn(final,view['events'])
        self.assertLess(len(view['events']),len(unit['events']));verify(unit,view,p)

    def test_context_capacity_keeps_complete_smaller_reply_and_full_denominator(self):
        from bounded_read import raw_size
        p,unit,reply=self.paired();huge=dict(reply,event='large-reply',text='超长原文'*20_000)
        unit['events'].insert(-1,huge);view=create(unit)
        self.assertNotIn(huge,view['events']);self.assertIn(reply,view['events'])
        self.assertLess(raw_size(view),120_000)
        self.assertEqual(len(unit['events']),view['model_view']['collections']['events']['count'])
        verify(unit,view,p)

    def test_empty_initial_view_has_valid_readback_schema_without_empty_enumerations(self):
        from review_schema import schema
        from semantic_audit import schema as audit_schema
        from runtime import RUBRIC,VERSION
        p,unit=fixture();view=create(unit,seeds=[])
        def check(value):
            if isinstance(value,dict):
                for key in ('enum','anyOf'):
                    if key in value:self.assertTrue(value[key])
                for child in value.values():check(child)
            elif isinstance(value,list):
                for child in value:check(child)
        check(schema(view,RUBRIC,VERSION));check(audit_schema(view,RUBRIC['items']))

    def test_large_native_preserves_full_coverage_with_bounded_model_inputs_and_export(self):
        p,unit=fixture()
        class Client(Cached):
            def call(self,u,path):
                self.assert_view(u);return super().call(u,path)
            def audit_review(self,u,r,path):
                self.assert_view(u);return super().audit_review(u,r,path)
            def assert_view(self,u):
                from bounded_read import raw_size
                self_size=raw_size(u)
                if self_size>=120_000:raise AssertionError(self_size)
                if u['model_view']['collections']['events']['count']!=len(unit['events']):raise AssertionError('分母丢失')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);setup(root,p);client=Client();original=digest(unit)
            result=evaluate_unit(p,unit,root,client,lambda *args:None)
            native=json.loads((root/'unit.json').read_text())
            self.assertEqual(original,digest(native));self.assertEqual(unit,native)
            view=json.loads((root/'model-view/score-context.json').read_text())
            self.assertLess(len(view['events']),len(native['events']))
            self.assertEqual('80.0',result['reports'][0]['score'])
            self.assertEqual('1.6',result['reports'][0]['policy_versions']['rules'])
            model_receipt(root,native);verify(native,context(root,native),p)
            calls=client.calls;self.assertEqual(result,evaluate_unit(p,unit,root,client,lambda *args:None))
            self.assertEqual(calls,client.calls)
            changed=copy.deepcopy(view);changed['events'][0]['text']='伪造本人'
            atomic(root/'model-view/score-context.json',changed)
            with self.assertRaises(ValueError):model_receipt(root,native)

    def test_native_source_membership_and_full_denominator_cannot_be_replaced_by_partial_view(self):
        p,unit=fixture();view=create(unit);verify(unit,view,p)
        self.assertEqual(unit,hydrate(unit,view,p))
        for collection in ('source_keys','person_events','source_units','events'):
            changed=copy.deepcopy(unit);changed[collection]=changed[collection][:-1]
            with self.subTest(collection=collection),self.assertRaises(ValueError):verify(changed,view,p)
        for target in ('person_events','source_keys'):
            changed=copy.deepcopy(view);changed[target]=[]
            with self.subTest(target=target),self.assertRaises(ValueError):verify(unit,changed,p)

    def test_only_actual_provided_raw_can_support_review_or_missing_check(self):
        p,unit=fixture();view=create(unit)
        review=received(FixtureClient().call(view,None));check_review(view,review)
        review['evidence'][0].update(event='context-299',quote='合成背景')
        with self.assertRaisesRegex(ValueError,'未实际提供'):check_review(view,review)
        review=received(FixtureClient().call(view,None))
        review['period_reviews'][0]['cells'][0]['missing']=dict(checked_events=['context-299'])
        with self.assertRaisesRegex(ValueError,'未提供'):check_review(view,review)

    def test_node_read_restores_only_previously_approved_personal_spans(self):
        p,unit=fixture();view=create(unit,seeds=[]);event=p['events'][0]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);nav=setup(root,p);access=Access(nav,root/'access',native=unit);view=access.start(view)
            key=json.loads((nav.root/'leaves.json').read_text())['node_ids'][0]
            # 通过已经提供的根逐层展开，不能猜叶标识。
            i=0
            while key not in view['context_access']['available_node_ids']:
                pending=[n for n in view['context_access']['available_node_ids']
                    if n not in view['context_access']['inspected_node_ids'] and nav.load(n)['kind']=='branch']
                view=access.expand(view,[dict(node_id=pending[0],mode='inspect')],str(i));i+=1
            view=access.expand(view,[dict(node_id=key,mode='raw')],'raw')
            self.assertEqual(unit['person_events'],view['person_events']);verify(unit,view,p)
            own=next(e for e in view['events'] if e['actor_class']=='human')
            self.assertEqual(event['text'],own['text'])
            # 原始user角色不是本人；删除核准集合后同一原文不会被自动认领。
            unowned=copy.deepcopy(unit);unowned['person_events']=[]
            other=create(unowned,seeds=[])
            expose(unowned,other,dict(event_id=event['event'],start=0,end=len(event['text'])))
            self.assertEqual([],other['person_events']);self.assertEqual([],other['events'])

    def test_projection_rejects_cross_person_future_or_forged_raw_and_does_not_change_native(self):
        p,unit=fixture();original=digest(unit);view=create(unit)
        with self.assertRaises(ValueError):verify(unit,view,dict(p,user_id='other'))
        source=copy.deepcopy(p['events'][-1]);source.update(event_id=source['event'],actor_class='context',human_spans=[])
        view['events'].append(source);verify(unit,view,p)
        changed=copy.deepcopy(view);changed['events'][-1]['actor_class']='human'
        with self.assertRaises(ValueError):verify(unit,changed,p)
        self.assertEqual(original,digest(unit))

    def test_added_frozen_background_hydrates_without_changing_coverage(self):
        p,unit=fixture();view=create(unit);extra=copy.deepcopy(p['events'][1])
        extra.update(event='context-0-span-0-10',event_id='context-0-span-0-10',
            text=extra['text'][:10],source_event='context-0',source_span=[0,10],actor_class='context',human_spans=[])
        view['events'].append(extra);result=hydrate(unit,view,p)
        self.assertEqual(len(unit['events'])+1,len(result['events']))
        for key in ('person_events','source_keys','source_units'):self.assertEqual(unit[key],result[key])
        changed=copy.deepcopy(view);changed['events'][-1]['text']='伪造'
        with self.assertRaises(ValueError):hydrate(unit,changed,p)

    def test_oversized_input_without_navigation_never_calls_model(self):
        p,unit=fixture();client=FixtureClient()
        with tempfile.TemporaryDirectory() as tmp:
            from pipeline import Paused
            with self.assertRaises(Paused):evaluate_unit(p,unit,Path(tmp),client,lambda *args:None)
            self.assertEqual(0,client.calls)


if __name__=='__main__':unittest.main()
