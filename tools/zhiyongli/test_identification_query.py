"""纯归属补读省去暂定图输出，最终覆盖和访问权限不能放宽。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from context_navigation import Navigation
from identification_readback import complete
from navigation_access import Access
from navigation_contract import DIRECTORY_MODE, identification_instructions
from pipeline import received
from task_identification import INSTRUCTIONS, canonical, schema
from test_execution_paths import FixtureClient, packet, record
import test_context_navigation as navigation


class QueryClient(FixtureClient):
    def __init__(self, invalid=None):
        super().__init__()
        self.invalid=invalid
        self.inputs=[]

    def identify(self, page, path):
        if path.exists():return json.loads(path.read_text())
        self.inputs.append(copy.deepcopy(page))
        if not page['context_access']['inspected_node_ids']:
            self.calls+=1
            key='f'*64 if self.invalid=='hidden' else page['context_access']['root']['node_id']
            if self.invalid=='unknown':return record_unknown(path)
            return record(dict(materials={},goals=[],needs_nodes=[dict(node_id=key,mode='inspect')]),path)
        if self.invalid=='empty_final':
            self.calls+=1
            return record(dict(materials={},goals=[],needs_nodes=[]),path)
        value=received(super().identify(page,path))
        return record(dict(value,needs_nodes=[]),path)


def record_unknown(path):
    from runtime import atomic
    value=dict(status='unknown')
    atomic(path,value)
    return value


class IdentificationQueryTests(unittest.TestCase):
    def build(self, root):
        source=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        source['events'].append(dict(event='background',text='工具失败，结局未知。',actor_class='context',
            timestamp_unknown=False,at='2026-09-07T10:01:00+08:00',session_id='test'))
        nav=Navigation(root/'tree',source)
        nav.build(navigation.NavigationTests().notes(source),navigation.Reader(),received)
        return source,Access(nav,root/'access')

    def test_initial_directory_removes_only_root_query_and_preserves_final_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,old_access=self.build(root)
            old_client=QueryClient()
            old_context,old_graph=complete(source,root/'old-readback',old_access,old_client,received)
            new_access=Access(old_access.navigation,root/'new-access',initial_directory=True)
            new_client=QueryClient()
            # 复用正式发送器的纯请求生成器，不读key、不发真实模型请求。
            import runtime
            from model_config import ModelConfig
            company=runtime.CompanyClient(ModelConfig('https://ai.apsatcom.com',root/'never-read-key'))
            new_client.identification_payload=company.identification_payload
            new_context,new_graph=complete(source,root/'new-readback',new_access,new_client,received)
            self.assertEqual(canonical(old_context,old_graph),canonical(new_context,new_graph))
            self.assertEqual((2,1),(old_client.calls,new_client.calls))
            self.assertEqual([],new_context['context_access']['raw_node_ids'])
            self.assertFalse((root/'new-readback/0/request.json').exists())
            self.assertEqual((new_context,new_graph),complete(source,root/'new-readback',
                Access(old_access.navigation,root/'new-access'),new_client,received))
            self.assertEqual(1,new_client.calls)

    def test_enabling_initial_directory_cannot_bypass_an_existing_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,access=self.build(root);client=QueryClient('unknown')
            with self.assertRaisesRegex(ValueError,'结果不明'):
                complete(source,root/'readback',access,client,received)
            receipt=root/'readback/0/identify.receipt.json';before=receipt.read_bytes()
            resumed=Access(access.navigation,root/'access',initial_directory=True)
            with self.assertRaisesRegex(ValueError,'结果不明'):
                complete(source,root/'readback',resumed,client,received)
            self.assertEqual(before,receipt.read_bytes())
            self.assertIsNone(resumed.initial_directory_mode)
            self.assertEqual(1,client.calls)

    def test_pure_query_then_full_identification_preserves_final_coverage_and_replays(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,access=self.build(root);client=QueryClient()
            current,value=complete(source,root/'readback',access,client,received)
            expected=received(FixtureClient().identify(current,root/'expected.receipt.json'))
            self.assertEqual(canonical(current,expected),canonical(current,value))
            self.assertEqual(set(e['event'] for e in source['events'] if e['actor_class']=='candidate_user'),set(value['materials']))
            self.assertEqual(2,client.calls)
            proof=json.loads((root/'readback/0/request.json').read_text())
            self.assertFalse(proof['provisional_judgment_not_adopted'])
            saved={str(p):p.read_bytes() for p in (root/'readback').rglob('*.json')}
            self.assertEqual((current,value),complete(source,root/'readback',access,client,received))
            self.assertEqual(2,client.calls)
            self.assertEqual(saved,{str(p):p.read_bytes() for p in (root/'readback').rglob('*.json')})

    def test_empty_final_is_not_no_activity_and_never_forms_identified_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,access=self.build(root);client=QueryClient('empty_final')
            with self.assertRaisesRegex(ValueError,'全部候选输入'):
                complete(source,root/'readback',access,client,received)
            self.assertEqual(2,client.calls)
            self.assertFalse((root/'readback/context.json').exists())

    def test_empty_query_does_not_allow_hidden_node_or_skip_required_materials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,access=self.build(root);client=QueryClient('hidden')
            with self.assertRaisesRegex(ValueError,'已经展示'):
                complete(source,root/'readback',access,client,received)
            self.assertEqual(1,client.calls)
            self.assertFalse((root/'readback/context.json').exists())

    def test_unknown_query_receipt_cannot_be_reissued_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,access=self.build(root);client=QueryClient('unknown')
            for _ in range(2):
                with self.assertRaisesRegex(ValueError,'结果不明'):
                    complete(source,root/'readback',access,client,received)
            self.assertEqual(1,client.calls)
            self.assertEqual('unknown',json.loads((root/'readback/0/identify.receipt.json').read_text())['status'])

    def test_schema_and_instructions_allow_empty_query_only_in_new_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,access=self.build(root);new=access.start(source)
            legacy=copy.deepcopy(new);legacy['context_access'].pop('child_directory_mode')
            def materials(value):
                result=schema(value);node=result['properties']['materials']
                if '$ref' in node:node=result['$defs'][node['$ref'].split('/')[-1]]
                return node
            self.assertNotIn('anyOf',materials(legacy))
            self.assertIn(dict(type='object',properties={},required=[],additionalProperties=False),materials(new)['anyOf'])
            self.assertEqual(INSTRUCTIONS,identification_instructions(INSTRUCTIONS,legacy))
            current=identification_instructions(INSTRUCTIONS,new)
            self.assertIn('最终一轮才完整覆盖全部候选槽位',current)
            self.assertNotIn('仍输出完整materials和goals作为暂定判断',current)
            self.assertEqual(DIRECTORY_MODE,new['context_access']['child_directory_mode'])

    def test_zero_candidates_keeps_saved_background_without_manufacturing_human_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source,_=self.build(root)
            for event in source['events']:event['actor_class']='context'
            nav=Navigation(root/'background-tree',source)
            nav.build(navigation.NavigationTests().notes(source),navigation.Reader(),received)
            view=Access(nav,root/'background-access').start(source)
            result=canonical(view,dict(materials={},goals=[],needs_nodes=[]))
            self.assertEqual([],result['materials']);self.assertEqual([],result['goals'])
            self.assertEqual(set(e['event'] for e in source['events']),set(e['event_id'] for e in result['unassigned_events']))
            self.assertIsInstance(schema(view),dict)
