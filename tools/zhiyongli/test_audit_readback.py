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

    def test_bounded_auto_revision_feeds_audit_back_and_keeps_prior_rounds(self):
        class Reviser(Auditor):
            accepts_revision_feedback=True
            feedback=None;always=False
            def call(self,u,path,feedback=None):
                if feedback is not None and not path.exists():self.feedback=feedback
                return super().call(u,path)
            def audit_review(self,u,r,path):
                self.revise=self.always or self.feedback is None
                return super().audit_review(u,r,path)
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);p=source();client=Reviser();result=evaluate_week(p,root,client)
            self.assertEqual('80.0',result['reports'][0]['score'])
            self.assertIn('按复查意见自动修订',''.join(result['reports'][0]['limitations']))
            # 首轮候选与复查分歧原样保留，修订轮带入的是首轮逐项意见。
            self.assertFalse(json.loads((root/'audit.json').read_text())['supported']);self.assertFalse((root/'bundle.json').exists())
            self.assertEqual((1,'revise'),(client.feedback['round'],client.feedback['audit']['items']['P2']['verdict']))
            rounds=list((root/'revisions').iterdir());self.assertEqual(1,len(rounds))
            self.assertEqual(1,json.loads((rounds[0]/'revision.json').read_text())['automatic_round'])
            self.assertEqual((root/'identify.receipt.json').stat().st_ino,(rounds[0]/'identify.receipt.json').stat().st_ino)
            calls=client.calls;self.assertEqual(result,evaluate_week(p,root,client));self.assertEqual(calls,client.calls)
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);p=source();client=Reviser();client.always=True
            with self.assertRaises(Paused) as caught:evaluate_week(p,root,client)
            self.assertEqual('needs_review',caught.exception.state);self.assertEqual(2,len(list((root/'revisions').iterdir())))
            calls=client.calls
            with self.assertRaises(Paused):evaluate_week(p,root,client)
            self.assertEqual((calls,2),(client.calls,len(list((root/'revisions').iterdir()))))

    def test_review_failing_program_checks_is_revised_with_the_checker_reason(self):
        class Misquoting(Auditor):
            accepts_revision_feedback=True
            feedback=None;always=False
            def call(self,u,path,feedback=None):
                if path.exists():return json.loads(path.read_text())
                if feedback is not None:self.feedback=feedback
                value=received(super().call(u,path))
                if feedback is None or self.always:value['evidence'][0]['quote']='来源中不存在的合成引文'
                return record(value,path)
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);p=source();client=Misquoting();result=evaluate_week(p,root,client)
            self.assertEqual(1,len(result['reports']))
            self.assertEqual(('review',None),(client.feedback['contract_error']['stage'],client.feedback['audit']))
            self.assertIn('逐字',client.feedback['contract_error']['error'])
            self.assertEqual('来源中不存在的合成引文',client.feedback['previous_review']['evidence'][0]['quote'])
            # 未过校验的首轮候选不进入复查，也不产出报告。
            self.assertFalse((root/'audit.receipt.json').exists());self.assertFalse((root/'bundle.json').exists())
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            root=Path(tmp);client=Misquoting();client.always=True
            with self.assertRaises(Paused) as caught:evaluate_week(source(),root,client)
            self.assertEqual('needs_review',caught.exception.state);self.assertIn('未通过程序校验',str(caught.exception))
            self.assertEqual(2,len(list((root/'revisions').iterdir())))
        # 不支持修订反馈的执行器保持原有校验异常，不改写为争议状态。
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.reading_view',side_effect=view):
            client=Misquoting();client.accepts_revision_feedback=False
            with self.assertRaises(ValueError):evaluate_week(source(),Path(tmp),client)

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

    def test_remarks_do_not_block_but_global_issues_do_and_event_enum_is_defined_once(self):
        from semantic_audit import schema,validate
        from review_schema import schema as review_schema
        from runtime import VERSION
        unit=dict(key='k',events=[dict(event='only-synthetic-event',text='合成材料')],person_events=['only-synthetic-event'])
        items={i:dict(verdict='supported',reason='合成',source_quotes=[dict(event='only-synthetic-event',quote='合成')],problem_codes=[])
            for i in RUBRIC['items']}
        value=dict(items=items,global_issues=[],remarks=['仅说明材料范围'],needs_events=[])
        self.assertTrue(validate(unit,{},value,RUBRIC['items'])['supported'])
        self.assertFalse(validate(unit,{},dict(value,global_issues=['跨项重复归责']),RUBRIC['items'])['supported'])
        with self.assertRaises(ValueError):validate(unit,{},dict(value,remarks='说明'),RUBRIC['items'])
        # 复查先独立判定状态：与候选状态不同时，即使结论写了支持也不算通过，并指明分项供修订。
        review=dict(period_reviews=[dict(cells=[dict(item=i,state='observed') for i in RUBRIC['items']])])
        agreed={i:dict(v,person_act='合成行为',own_state='observed') for i,v in items.items()}
        self.assertTrue(validate(unit,review,dict(value,items=agreed),RUBRIC['items'])['supported'])
        differing=dict(agreed,M3=dict(agreed['M3'],person_act='无',own_state='no_opportunity'))
        result=validate(unit,review,dict(value,items=differing),RUBRIC['items'])
        self.assertEqual((False,['M3']),(result['supported'],result['state_mismatches']))
        with self.assertRaises(ValueError):validate(unit,review,dict(value,items=dict(agreed,M3=dict(agreed['M3'],own_state='其他'))),RUBRIC['items'])
        for contract in (schema(unit,RUBRIC['items']),review_schema(unit,RUBRIC,VERSION)):
            text=json.dumps(contract)
            # 变体内只引用集中定义，事件枚举不再随分项重复。
            self.assertLessEqual(text.count('only-synthetic-event'),5);self.assertIn('#/$defs/',text)

    def test_compacted_schema_expands_back_to_the_same_constraints(self):
        from review_schema import compact,obj,array,enum
        shared=obj(dict(event=enum(*['synthetic-event-'+str(i) for i in range(8)]),quote=dict(type='string')))
        original=obj(dict(a=array(shared),b={'anyOf':[obj(dict(kind=enum('x'),refs=array(shared))),obj(dict(kind=enum('y'),refs=array(shared)))]}))
        compacted=compact(original)
        def expand(node):
            if isinstance(node,dict):
                if '$ref' in node:return expand(compacted['$defs'][node['$ref'].rsplit('/',1)[1]])
                return {k:expand(v) for k,v in node.items() if k!='$defs'}
            return [expand(v) for v in node] if isinstance(node,list) else node
        self.assertEqual(original,expand(compacted))
        self.assertLess(len(json.dumps(compacted)),len(json.dumps(original)))
        self.assertNotIn('$defs',compact(obj(dict(a=dict(type='string')))))

    def test_identification_contract_failure_is_resent_with_reason_and_bounded(self):
        from task_identification import checked,CONTRACT_RETRIES
        class Client:
            accepts_revision_feedback=True
            def __init__(self,good_after):self.good_after=good_after;self.seen=[]
            def identify(self,packet,path,feedback=None):
                self.seen.append((path.name,feedback))
                return dict(ok=len(self.seen)>self.good_after)
        def check(value):
            if not value['ok']:raise ValueError('synthetic contract problem')
            return 'accepted'
        with tempfile.TemporaryDirectory() as d:
            client=Client(1);receipt=Path(d)/'0.receipt.json'
            self.assertEqual('accepted',checked(client,{},receipt,lambda record:record,check)[2])
            # 第二次请求带上校验原因并使用独立回执；首个回执路径不被覆盖。
            self.assertEqual([('0.receipt.json',None),('0.fix1.receipt.json',dict(attempt=1,error='ValueError: synthetic contract problem'))],client.seen)
            self.assertEqual(dict(attempt=1,error='ValueError: synthetic contract problem'),json.loads((Path(d)/'0.contract-error.json').read_text()))
            client=Client(99)
            with self.assertRaisesRegex(ValueError,'synthetic contract problem'):
                checked(client,{},Path(d)/'1.receipt.json',lambda record:record,check)
            self.assertEqual(CONTRACT_RETRIES+1,len(client.seen))
            # 不支持修订的执行器维持原行为：只发一次，校验失败直接上抛。
            client=Client(99);client.accepts_revision_feedback=False
            with self.assertRaises(ValueError):checked(client,{},Path(d)/'2.receipt.json',lambda record:record,check)
            self.assertEqual(1,len(client.seen))
            # 结果不明等暂停状态不进入重发。
            client=Client(0)
            def paused(record):raise Paused('unknown','synthetic unknown')
            with self.assertRaises(Paused):checked(client,{},Path(d)/'3.receipt.json',paused,check)
            self.assertEqual(1,len(client.seen))

    def test_identification_schema_forbids_goal_on_non_human_material(self):
        from task_identification import schema as task_schema
        events=[dict(event='synthetic-a',actor_class='candidate_user',timestamp_unknown=False),
            dict(event='synthetic-b',actor_class='candidate_user',timestamp_unknown=True),
            dict(event='synthetic-c',actor_class='context',timestamp_unknown=False)]
        contract=task_schema(dict(events=events))
        def expand(node):
            if isinstance(node,dict):
                if '$ref' in node:return expand(contract['$defs'][node['$ref'].rsplit('/',1)[1]])
                return {k:expand(v) for k,v in node.items() if k!='$defs'}
            return [expand(v) for v in node] if isinstance(node,list) else node
        slots=expand(contract)['properties']['materials']['properties']
        self.assertEqual({'synthetic-a','synthetic-b'},set(slots))
        for key,others in (('synthetic-a',['system','auxiliary','unresolved']),('synthetic-b',['system','auxiliary','duplicate','unresolved'])):
            human,other=slots[key]['anyOf']
            self.assertEqual(['human'],human['properties']['classification']['enum'])
            self.assertEqual(['string','null'],human['properties']['goal_id']['type'])
            self.assertEqual(others,other['properties']['classification']['enum'])
            # 非本人材料的目标字段只能为空，模型无法再输出会被程序整批拒绝的组合。
            self.assertEqual(dict(type='null'),other['properties']['goal_id'])
            self.assertEqual(0,other['properties']['human_quotes']['maxItems'])
            self.assertEqual(1,human['properties']['human_quotes']['minItems'])


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
