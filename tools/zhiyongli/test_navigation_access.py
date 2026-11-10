"""分层节点披露、原文、身份/时窗及续跑；模型夹具只证明执行合同。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from context_navigation import Navigation
from navigation_access import Access, query_schema
from pipeline import received,evaluate_week,month_fragment
from runtime import atomic, VERSION, REVIEW_POLICY, digest
import test_context_navigation as navigation_tests
from test_execution_paths import FixtureClient,packet,record


class AccessTests(unittest.TestCase):
    def build(self,root,n=100):
        helper=navigation_tests.NavigationTests();p=helper.packet(n)
        nav=Navigation(root/'context-navigation',p);nav.build(helper.notes(p),navigation_tests.Reader(),received)
        u=dict(user_id=p['user_id'],key='synthetic:week',version=VERSION,evaluation_scope='person_week',
            periods=[dict(period_id='week',start=p['since'],end=p['until'])],person_events=[],events=[])
        access=Access(nav,root/'navigation-access')
        return p,nav,access,u

    def raw_leaf(self,access,unit):
        current=unit;key=unit['context_access']['root']['node_id'];index=0
        while access.navigation.load(key)['kind']=='branch':
            current=access.expand(current,[dict(node_id=key,mode='inspect')],str(index));index+=1
            key=access.navigation.load(key)['children'][0]
        return current,key

    def test_tree_traversal_exposes_children_and_exact_raw_with_explicit_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,nav,access,u=self.build(Path(tmp));initial=access.start(u)
            unit,key=self.raw_leaf(access,initial)
            final=access.expand(unit,[dict(node_id=key,mode='raw')],'raw')
            self.assertEqual(p['events'][0]['text'],final['events'][0]['text'])
            self.assertEqual('context',final['events'][0]['actor_class']);self.assertEqual([],final['events'][0]['human_spans'])
            self.assertEqual(1,final['context_access']['raw_read_fragments'])
            self.assertEqual(100,final['context_access']['total_indexed_fragments'])
            self.assertEqual([],final['person_events'])
            self.assertEqual(unit['context_access']['raw_read_fragments'],0)
            self.assertEqual(final,access.expand(unit,[dict(node_id=key,mode='raw')],'raw'))

    def test_hidden_nodes_forged_state_duplicate_and_raw_branch_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,nav,access,u=self.build(Path(tmp));unit=access.start(u)
            root=unit['context_access']['root']['node_id'];hidden=nav.load(root)['children'][0]
            with self.assertRaisesRegex(ValueError,'已经展示'):access.expand(unit,[dict(node_id=hidden,mode='inspect')],'hidden')
            forged=copy.deepcopy(unit);forged['context_access']['available_node_ids'].append(hidden)
            with self.assertRaisesRegex(ValueError,'未绑定'):access.expand(forged,[dict(node_id=hidden,mode='inspect')],'forged')
            with self.assertRaisesRegex(ValueError,'先遍历到叶'):access.expand(unit,[dict(node_id=root,mode='raw')],'branch')
            # 发送补读前的校验即可识别分支原文请求，识别流程据此带原因重发，不必等到读取时终止。
            with self.assertRaisesRegex(ValueError,'是分支节点'):access.check(unit,[dict(node_id=root,mode='raw')])
            with self.assertRaisesRegex(ValueError,'重复'):access.expand(unit,[dict(node_id=root,mode='inspect')]*2,'duplicate')
            exposed=access.expand(unit,[dict(node_id=root,mode='inspect')],'inspect')
            with self.assertRaisesRegex(ValueError,'已经提供'):access.expand(exposed,[dict(node_id=root,mode='inspect')],'repeat')

    def test_identity_window_and_frozen_tree_change_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,nav,access,u=self.build(Path(tmp))
            for modified in (dict(u,user_id='other'),dict(u,periods=[dict(start=p['since'],end='2026-09-12T00:00:00+08:00')])):
                with self.assertRaisesRegex(ValueError,'人员或评价时窗'):access.start(modified)
            tree=json.loads((nav.root/'tree.json').read_text());tree['limitation']='已改变';atomic(nav.root/'tree.json',tree)
            with self.assertRaisesRegex(ValueError,'冻结输入'):Access(nav,Path(tmp)/'navigation-access')

    def test_schema_only_offers_exposed_nodes_and_material_capacity_preserves_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,nav,access,u=self.build(Path(tmp));unit=access.start(u)
            self.assertEqual([unit['context_access']['root']['node_id']],query_schema(unit)['items']['properties']['node_id']['enum'])
            current,key=self.raw_leaf(access,unit);before=copy.deepcopy(current)
            with patch('navigation_access.UNIT_BYTES',100):
                with self.assertRaisesRegex(ValueError,'超过评分容量'):access.expand(current,[dict(node_id=key,mode='raw')],'oversized')
            self.assertEqual(before,current);self.assertFalse((access.root/'queries/oversized.json').exists())

    def test_month_fragment_drops_whole_week_navigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,nav,access,u=self.build(Path(tmp))
            # 使用真实装配后的单位，测试截止前片段不能继承整周摘要。
            source=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
            from pipeline import assemble
            from task_identification import canonical
            client=FixtureClient();graph=received(client.identify(source,Path(tmp)/'identity.receipt.json'))
            full=assemble(source,canonical(source,graph));full['context_access']={'whole_week':'future_summary'}
            fragment=month_fragment(full,'2026-09-07T00:00:00+08:00','2026-09-11T00:00:00+08:00')
            self.assertNotIn('context_access',fragment)

    def test_initial_unit_capacity_is_checked_before_model_or_state_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p,nav,access,u=self.build(Path(tmp))
            with patch('navigation_access.UNIT_BYTES',100):
                with self.assertRaisesRegex(ValueError,'本次不发送'):access.start(u)
            self.assertFalse((access.root/'states').exists())


class NavigatingClient(FixtureClient):
    def identify(self,p,path):return json.loads(path.read_text()) if path.exists() else super().identify(p,path)
    def call(self,u,path):
        if path.exists():return json.loads(path.read_text())
        access=u['context_access']
        if access['raw_read_fragments']:
            result=super().call(u,path);value=json.loads(result['response']['output'][0]['content'][0]['text'])
            value['needs_nodes']=[];return record(value,path)
        observed={o['node_id']:o['node'] for o in access['observations'] if o['mode']=='inspect'}
        key=access['root']['node_id']
        while key in observed and observed[key]['kind']=='branch':
            children=observed[key]['children'];key=children[1] if key!=access['root']['node_id'] else children[0]
        mode='raw' if key in observed and observed[key]['kind']=='leaf' else 'inspect'
        self.calls+=1
        return record(dict(version=VERSION,review_policy=REVIEW_POLICY,key=u['key'],evidence=[],period_reviews=[],
            needs_events=[],needs_nodes=[dict(node_id=key,mode=mode)]),path)
    def audit_review(self,u,r,path):return json.loads(path.read_text()) if path.exists() else super().audit_review(u,r,path)


class NavigationPipelineTests(unittest.TestCase):
    def test_review_and_audit_use_navigated_raw_and_replay_never_resends(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
            helper=navigation_tests.NavigationTests();context=helper.packet(80)['events']
            p['events'].extend(context)
            nav=Navigation(root/'context-navigation',p);nav.build(helper.notes(p),navigation_tests.Reader(),received)
            def view(original,*args):
                result=copy.deepcopy(original);result['events']=result['events'][:1];return result
            client=NavigatingClient()
            with patch('pipeline.reading_view',side_effect=view):
                result=evaluate_week(p,root,client)
                self.assertEqual('80.0',result['reports'][0]['score'])
                final=json.loads((root/'unit.json').read_text())
                self.assertEqual(1,final['context_access']['raw_read_fragments'])
                self.assertTrue(any(e.get('source_event')=='0' for e in final['events']))
                calls=client.calls;self.assertEqual(result,evaluate_week(p,root,client));self.assertEqual(calls,client.calls)
                self.assertEqual(digest(final),json.loads((root/'audit.json').read_text())['unit_sha256'])

    def test_shared_reuse_verifies_navigation_tree_store_and_access_chain(self):
        for artifact in ('context-navigation/tree.json','context-navigation/nodes.sqlite','navigation-access/input.json'):
            with self.subTest(artifact=artifact),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);first=root/'first';p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
                helper=navigation_tests.NavigationTests();p['events'].extend(helper.packet(80)['events'])
                nav=Navigation(first/'context-navigation',p)
                nav.build(helper.notes(p),navigation_tests.Reader(),received)
                def view(original,*args):
                    result=copy.deepcopy(original);result['events']=result['events'][:1];return result
                client=NavigatingClient()
                with patch('pipeline.reading_view',side_effect=view):
                    result=evaluate_week(p,first,client,shared_root=root/'shared');calls=client.calls
                    self.assertEqual(result,evaluate_week(p,root/'reused',client,shared_root=root/'shared'))
                    self.assertEqual(calls,client.calls)
                    target=first/artifact
                    if target.suffix=='.sqlite':
                        with target.open('ab') as stream:stream.write(b'changed')
                    else:
                        value=json.loads(target.read_text());value['changed']='变更';atomic(target,value)
                    with self.assertRaisesRegex(ValueError,'分层导航或访问链的签名变化'):
                        evaluate_week(p,root/'tampered',client,shared_root=root/'shared')
                    self.assertEqual(calls,client.calls)

    def test_unknown_node_request_and_mixed_completion_never_send_followup(self):
        class Broken(NavigatingClient):
            def call(self,u,path):
                if path.exists():return json.loads(path.read_text())
                self.calls+=1
                value=dict(version=VERSION,review_policy=REVIEW_POLICY,key=u['key'],
                    evidence=[],period_reviews=[],needs_events=[],needs_nodes=[dict(node_id='hidden',mode='raw')])
                if self.mixed:value['evidence']=[{'quote':'不能混合部分评分'}]
                return record(value,path)
        for mixed in (False,True):
            with self.subTest(mixed=mixed),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);helper=AccessTests();p,nav,access,u=helper.build(root)
                client=Broken();client.mixed=mixed
                from context_readback import complete
                with self.assertRaisesRegex(ValueError,'不能同时提交评分' if mixed else '已经展示'):
                    complete(u,p,root,client,received,navigation_access=access)
                self.assertEqual(1,client.calls)
                self.assertFalse((root/'context-readback/1').exists())


if __name__=='__main__':unittest.main()
