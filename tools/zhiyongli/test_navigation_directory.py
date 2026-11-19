"""子节点定位目录的兼容、容量、恢复与权限；合成夹具不证明语义质量。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bounded_read import raw_size
from context_navigation import Navigation
from navigation_access import Access, DIRECTORY_MODE, INPUT_BYTES, ReadbackCapacity, set_remaining
from pipeline import received
from runtime import atomic, digest
import runtime
from model_config import ModelConfig
from navigation_contract import DIRECTORY_INSTRUCTIONS
import test_context_navigation as navigation_tests


class NavigationDirectoryTests(unittest.TestCase):
    def build(self, root, n=100, long_raw=False):
        helper = navigation_tests.NavigationTests()
        packet = helper.packet(n)
        if long_raw:
            packet['events'][0]['text'] = '原文及反证不得被目录挤掉。' * 700
        notes = helper.notes(packet)
        nav = Navigation(root / 'tree', packet)
        reader = navigation_tests.Reader()
        nav.build(notes, reader, received)
        unit = dict(version=runtime.VERSION, key='synthetic:week', evaluation_scope='person_week',
                    user_id=packet['user_id'], since=packet['since'], until=packet['until'],
                    periods=[dict(period_id='week', start=packet['since'], end=packet['until'])],
                    person_events=[], events=[])
        return nav, reader, unit

    def inspect(self, access, unit, stage='root'):
        key = unit['context_access']['root']['node_id']
        return access.expand(unit, [dict(node_id=key, mode='inspect')], stage)

    def legacy(self, nav, directory, unit_bytes=None):
        created = Access(nav, directory, unit_bytes=unit_bytes)
        identity = dict(created.identity)
        identity.pop('child_directory_mode')
        atomic(created.root / 'input.json', identity)
        return Access(nav, directory, unit_bytes=unit_bytes)

    def test_existing_contract_keeps_mode_absent_and_replays_exact_queries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            legacy = self.legacy(nav, root / 'access')
            first = legacy.start(unit)
            expanded = self.inspect(legacy, first)
            proof_before = (root / 'access/queries/root.json').read_bytes()
            calls = reader.calls
            current = Access(nav, root / 'access')
            self.assertIsNone(current.directory_mode)
            self.assertNotIn('child_directory_mode', current.identity)
            self.assertNotIn('child_directory_mode', first['context_access'])
            self.assertNotIn('child_directory', expanded['context_access'])
            self.assertEqual(first, current.start(unit))
            self.assertEqual(expanded, self.inspect(current, first))
            self.assertEqual(proof_before, (root / 'access/queries/root.json').read_bytes())
            self.assertEqual(calls, reader.calls)
            child = nav.load(first['context_access']['root']['node_id'])['children'][0]
            query = [dict(node_id=child, mode='inspect')]
            self.assertEqual(legacy.expand(expanded, query, 'child'), current.expand(expanded, query, 'child'))

    def test_new_contract_complete_direct_children_without_grandchild_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            access = Access(nav, root / 'access')
            initial = access.start(unit)
            result = self.inspect(access, initial)
            self.assertEqual(DIRECTORY_MODE, access.identity['child_directory_mode'])
            directory = result['context_access']['child_directory']
            self.assertEqual('complete', directory['status'])
            parent = initial['context_access']['root']['node_id']
            children = nav.load(parent)['children']
            self.assertEqual([parent], directory['parent_ids'])
            self.assertEqual([nav.descriptor(c) for c in children], directory['branches'][0]['children'])
            self.assertEqual(set([parent] + children), set(result['context_access']['available_node_ids']))
            grandchild = nav.load(children[0])['children'][0]
            self.assertNotIn(grandchild, result['context_access']['available_node_ids'])
            with self.assertRaisesRegex(ValueError, '已经展示'):
                access.check(result, [dict(node_id=grandchild, mode='raw')])
            self.assertEqual([], result['events'])
            self.assertEqual([], result['person_events'])
            access.verify(result)

    def test_complete_directory_replays_identically_without_calls_or_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            before = digest(nav.packet)
            access = Access(nav, root / 'access')
            initial = access.start(unit)
            first = self.inspect(access, initial)
            calls = reader.calls
            proofs = {str(p.relative_to(access.root)): p.read_bytes() for p in access.root.rglob('*.json')}
            resumed = Access(nav, root / 'access')
            self.assertEqual(first, self.inspect(resumed, resumed.start(unit)))
            self.assertEqual(proofs, {str(p.relative_to(access.root)): p.read_bytes() for p in access.root.rglob('*.json')})
            self.assertEqual(before, digest(nav.packet))
            self.assertEqual(calls, reader.calls)

    def test_directory_aggregate_64kb_fallback_preserves_original_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            access = Access(nav, root / 'access')
            initial = access.start(unit)
            original = nav.descriptor
            # 每个描述单独合法，整批目录不应裁前 N 个或阻断原查询。
            def padded(key, db=None):
                return dict(original(key, db), synthetic_padding='x' * 20_000)
            with patch.object(nav, 'descriptor', side_effect=padded):
                result = self.inspect(access, initial)
            directory = result['context_access']['child_directory']
            self.assertEqual('not_provided_capacity', directory['status'])
            self.assertNotIn('branches', directory)
            self.assertEqual(1, len(result['context_access']['observations']))
            self.assertLess(raw_size(result), access.unit_bytes)
            access.verify(result)

    def test_multi_parent_directory_falls_back_as_one_batch_without_partial_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            access = Access(nav, root / 'access')
            inspected = self.inspect(access, access.start(unit))
            parent = inspected['context_access']['root']['node_id']
            parents = nav.load(parent)['children'][:2]
            original = nav.descriptor
            def padded(key, db=None):
                return dict(original(key, db), synthetic_padding='x' * 1200) if nav.load(key, db)['kind'] == 'leaf' else original(key, db)
            with patch.object(nav, 'descriptor', side_effect=padded):
                result = access.expand(inspected, [dict(node_id=k, mode='inspect') for k in parents], 'two-parents')
            directory = result['context_access']['child_directory']
            self.assertEqual(parents, directory['parent_ids'])
            self.assertEqual('not_provided_capacity', directory['status'])
            self.assertNotIn('branches', directory)
            expected = set(inspected['context_access']['available_node_ids'])
            for key in parents:
                expected.update(nav.load(key)['children'])
            self.assertEqual(expected, set(result['context_access']['available_node_ids']))
            self.assertEqual(3, len(result['context_access']['observations']))
            access.verify(result)

    def test_directory_and_values_joint_64kb_limit_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            access = Access(nav, root / 'access')
            initial = access.start(unit)
            parent = initial['context_access']['root']['node_id']
            children = nav.load(parent)['children']
            original = nav.descriptor
            # 目录本身小于64KB，但加上原始inspect值即超过；仍须整批回退。
            base = raw_size(access.child_directory([parent]))
            padding = (INPUT_BYTES - base - 100) // len(children)
            def padded(key, db=None):
                return dict(original(key, db), synthetic_padding='x' * padding)
            with patch.object(nav, 'descriptor', side_effect=padded):
                self.assertLessEqual(raw_size(access.child_directory([parent])), INPUT_BYTES)
                result = self.inspect(access, initial)
            self.assertEqual('not_provided_capacity', result['context_access']['child_directory']['status'])
            access.verify(result)

    def test_instance_unit_capacity_fallback_does_not_block_old_legal_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            legacy = self.legacy(nav, root / 'legacy')
            legacy_output = self.inspect(legacy, legacy.start(unit))
            limit = raw_size(legacy_output) + 500
            access = Access(nav, root / 'access', unit_bytes=limit)
            initial = access.start(unit)
            result = self.inspect(access, initial)
            self.assertLessEqual(raw_size(result), limit)
            self.assertEqual('not_provided_capacity', result['context_access']['child_directory']['status'])
            self.assertEqual(legacy_output['context_access']['observations'], result['context_access']['observations'])
            self.assertEqual(legacy_output['context_access']['available_node_ids'], result['context_access']['available_node_ids'])
            access.verify(result)

    def test_raw_reclaims_directory_and_remaining_budget_without_changing_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root, n=32, long_raw=True)
            probe = Access(nav, root / 'probe')
            probe_initial = probe.start(unit)
            probe_inspected = self.inspect(probe, probe_initial)
            leaf = nav.load(probe_initial['context_access']['root']['node_id'])['children'][0]
            raw_query = [dict(node_id=leaf, mode='raw')]
            probe_raw = probe.expand(probe_inspected, raw_query, 'raw')
            limit = max(raw_size(probe_inspected), raw_size(probe_raw)) + 500
            self.assertLess(limit, raw_size(probe_raw) + raw_size(probe_inspected['context_access']['child_directory']))
            access = Access(nav, root / 'access', unit_bytes=limit)
            inspected = self.inspect(access, access.start(unit))
            self.assertEqual('complete', inspected['context_access']['child_directory']['status'])
            expected = copy.deepcopy(inspected)
            expected['context_access'].pop('child_directory')
            set_remaining(expected, limit)
            self.assertEqual(expected['context_access']['remaining_bytes'], inspected['context_access']['remaining_bytes'])
            final = access.expand(inspected, raw_query, 'raw')
            self.assertNotIn('child_directory', final['context_access'])
            self.assertEqual(nav.packet['events'][0]['text'], final['events'][0]['text'])
            self.assertEqual('context', final['events'][0]['actor_class'])
            self.assertEqual([], final['events'][0]['human_spans'])
            self.assertEqual([], final['person_events'])
            self.assertEqual(1, final['context_access']['raw_read_fragments'])
            self.assertEqual(final, Access(nav, root / 'access', unit_bytes=limit).expand(inspected, raw_query, 'raw'))
            self.assertIn('child_directory', inspected['context_access'])

    def test_forged_directory_description_child_order_and_parent_rejected_even_if_resealed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            access = Access(nav, root / 'access')
            inspected = self.inspect(access, access.start(unit))
            for mutation in ('description', 'order', 'parent'):
                forged = copy.deepcopy(inspected)
                directory = forged['context_access']['child_directory']
                if mutation == 'description':
                    directory['branches'][0]['children'][0]['summary'] = '伪造摘要'
                elif mutation == 'order':
                    directory['branches'][0]['children'].reverse()
                else:
                    directory['parent_ids'] = ['f' * 64]
                # 模拟即使磁盘状态封口被伪造，目录仍须重算树内容验证。
                access.remember(forged)
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    access.verify(forged)

    def test_tree_child_corruption_and_changed_frozen_tree_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            access = Access(nav, root / 'access')
            inspected = self.inspect(access, access.start(unit))
            child = inspected['context_access']['child_directory']['branches'][0]['children'][0]['node_id']
            with nav.connect() as db:
                body = json.loads(db.execute('SELECT body FROM nodes WHERE id=?', (child,)).fetchone()[0])
                body['value']['summary'] = '内容与节点签名不符'
                db.execute('UPDATE nodes SET body=? WHERE id=?', (json.dumps(body), child))
            with self.assertRaises(ValueError):
                access.verify(inspected)
            tree = json.loads((nav.root / 'tree.json').read_text())
            tree['limitation'] = '冻结树被改变'
            atomic(nav.root / 'tree.json', tree)
            with self.assertRaises(ValueError):
                Access(nav, root / 'access')

    def test_legacy_company_payloads_exclude_mode_and_only_new_contract_adds_instructions(self):
        from task_identification import INSTRUCTIONS as identify_instructions
        from semantic_audit import INSTRUCTIONS as audit_instructions
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            legacy = self.legacy(nav, root / 'legacy')
            legacy_unit = self.inspect(legacy, legacy.start(unit))
            new_unit = copy.deepcopy(legacy_unit)
            new_unit['context_access']['child_directory_mode'] = DIRECTORY_MODE
            config = ModelConfig('https://ai.apsatcom.com', root / 'never-read-key')
            client = runtime.CompanyClient(config)
            review = dict(evidence=[], period_reviews=[])
            for method in ('call', 'identify', 'audit_payload'):
                with self.subTest(method=method):
                    if method == 'audit_payload':
                        left = client.audit_payload(legacy_unit, review)
                        right = client.audit_payload(new_unit, review)
                    else:
                        with patch.object(client, '_call', side_effect=lambda payload, receipt: payload):
                            left = getattr(client, method)(legacy_unit, root / 'unused.receipt.json')
                            right = getattr(client, method)(new_unit, root / 'unused.receipt.json')
                    self.assertNotIn(DIRECTORY_INSTRUCTIONS, left['instructions'])
                    if method == 'identify':
                        self.assertEqual(identify_instructions, left['instructions'])
                        self.assertIn('materials为空对象、goals为空数组', right['instructions'])
                        self.assertNotIn('请求补读的轮次仍输出完整materials和goals作为暂定判断', right['instructions'])
                        self.assertTrue(right['instructions'].endswith(DIRECTORY_INSTRUCTIONS))
                    else:
                        self.assertEqual(left['instructions'] + DIRECTORY_INSTRUCTIONS, right['instructions'])
                    if method == 'audit_payload':
                        self.assertEqual(audit_instructions, left['instructions'])
                    left_input = json.loads(left['input'])
                    right_input = json.loads(right['input'])
                    new_access = right_input['unit']['context_access'] if method == 'audit_payload' else right_input['context_access']
                    self.assertEqual(DIRECTORY_MODE, new_access.pop('child_directory_mode'))
                    self.assertEqual(left_input, right_input)
                    # 新归属合同另含纯补读空图结构；它有独立合同测试，这里核旧提示及其余传输参数。
                    excluded = {'instructions', 'input', 'text'} if method == 'identify' else {'instructions', 'input'}
                    self.assertEqual({k: v for k, v in left.items() if k not in excluded},
                                     {k: v for k, v in right.items() if k not in excluded})
            self.assertFalse((root / 'unused.receipt.json').exists())

    def test_new_contract_all_three_company_payloads_include_directory_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nav, reader, unit = self.build(root)
            access = Access(nav, root / 'access')
            current_unit = access.start(unit)
            self.assertEqual(DIRECTORY_MODE, current_unit['context_access']['child_directory_mode'])
            client = runtime.CompanyClient(ModelConfig('https://ai.apsatcom.com', root / 'never-read-key'))
            with patch.object(client, '_call', side_effect=lambda payload, receipt: payload):
                payloads = [client.call(current_unit, root / 'unused.receipt.json'),
                            client.identify(current_unit, root / 'unused.receipt.json'),
                            client.audit_payload(current_unit, dict(evidence=[], period_reviews=[]))]
            for payload in payloads:
                self.assertIn(DIRECTORY_INSTRUCTIONS, payload['instructions'])
            self.assertFalse((root / 'unused.receipt.json').exists())

    def event_context(self, root, new=True, unit_bytes=None):
        nav, reader, unit = self.build(root, n=32, long_raw=True)
        known = copy.deepcopy(nav.packet['events'][-1])
        known.update(actor_class='human', human_spans=[[0, 1]])
        unit['events'] = [known]
        unit['person_events'] = [known['event']]
        access = Access(nav, root / 'access', unit_bytes=unit_bytes) if new else self.legacy(nav, root / 'access', unit_bytes=unit_bytes)
        inspected = self.inspect(access, access.start(unit))
        return nav, access, unit, inspected

    def test_complete_event_expansion_reclaims_only_new_directory_and_keeps_evidence(self):
        from context_readback import expand
        with tempfile.TemporaryDirectory() as tmp:
            nav, access, unit, inspected = self.event_context(Path(tmp))
            before = copy.deepcopy(inspected)
            result = expand(inspected, nav.packet, ['0'])
            limit = raw_size(result) + 50
            self.assertLess(limit, raw_size(result) + raw_size(inspected['context_access']['child_directory']))
            with patch('context_readback.UNIT_BYTES', limit):
                result = expand(inspected, nav.packet, ['0'])
            self.assertNotIn('child_directory', result['context_access'])
            for key in ('observations', 'available_node_ids', 'raw_node_ids', 'inspected_node_ids'):
                self.assertEqual(inspected['context_access'][key], result['context_access'][key])
            self.assertEqual(inspected['events'], result['events'][:-1])
            self.assertEqual(inspected['person_events'], result['person_events'])
            self.assertEqual(nav.packet['events'][0]['text'], result['events'][-1]['text'])
            self.assertEqual('context', result['events'][-1]['actor_class'])
            self.assertEqual([], result['events'][-1]['human_spans'])
            self.assertEqual(before, inspected)
            # 无新mode的既有扩展字段和剩余量不能被新规则清理或改写。
            legacy = copy.deepcopy(inspected)
            legacy['context_access'].pop('child_directory_mode')
            legacy['context_access']['remaining_bytes'] = 321
            legacy['context_access']['child_directory'] = dict(legacy_extension='必须原样保留')
            legacy_result = expand(legacy, nav.packet, ['0'])
            self.assertEqual(legacy['context_access'], legacy_result['context_access'])

    def event_client(self, audit=False):
        from test_execution_paths import record
        class Client:
            def __init__(self):
                self.calls = 0
            def send(self, current, path):
                if path.exists():
                    return json.loads(path.read_text())
                self.calls += 1
                missing = '0' not in {e['event'] for e in current['events']}
                if audit:
                    value = dict(items={}, global_issues=[], needs_events=['0'] if missing else [], needs_nodes=[])
                else:
                    value = dict(version=runtime.VERSION, review_policy=runtime.REVIEW_POLICY, key=current['key'],
                                 needs_events=['0'] if missing else [], needs_nodes=[], evidence=[], period_reviews=[])
                return record(value, path)
            def call(self, current, path):
                return self.send(current, path)
            def audit_review(self, current, review, path):
                return self.send(current, path)
        return Client()

    def test_review_and_audit_event_paths_refresh_only_new_mode_and_preserve_proofs(self):
        import context_readback
        import audit_readback
        for audit in (False, True):
            for new in (False, True):
                with self.subTest(audit=audit, new=new), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    nav, access, unit, inspected = self.event_context(root, new=new)
                    if not new:
                        inspected['context_access']['remaining_bytes'] = 321
                        access.remember(inspected)
                    prior_states = {p.name: p.read_bytes() for p in (access.root / 'states').glob('*.json')}
                    client = self.event_client(audit)
                    if audit:
                        # 本例只验证补读执行，最终语义校验独立测试，不冒称评分质量通过。
                        with patch('audit_readback.validate', return_value='合成终态'):
                            audit_readback.complete(inspected, {}, nav.packet, root / 'run', client, received, access=access)
                        final_path = root / 'run/audit-readback/context.json'
                    else:
                        with patch.object(access, 'start', return_value=inspected):
                            context_readback.complete(unit, nav.packet, root / 'run', client, received, navigation_access=access)
                        final_path = root / 'run/context-readback/1/unit.json'
                    final = json.loads(final_path.read_text())
                    self.assertEqual(2, client.calls)
                    self.assertEqual(inspected['events'], final['events'][:-1])
                    self.assertEqual(inspected['person_events'], final['person_events'])
                    self.assertEqual(inspected['context_access']['observations'], final['context_access']['observations'])
                    if new:
                        self.assertNotIn('child_directory', final['context_access'])
                        expected = copy.deepcopy(final)
                        set_remaining(expected, access.unit_bytes)
                        self.assertEqual(expected['context_access']['remaining_bytes'], final['context_access']['remaining_bytes'])
                        self.assertLess(final['context_access']['remaining_bytes'], inspected['context_access']['remaining_bytes'])
                    else:
                        self.assertEqual(321, final['context_access']['remaining_bytes'])
                    for name, contents in prior_states.items():
                        self.assertEqual(contents, (access.root / 'states' / name).read_bytes())
                    access.verify(final)

    def test_new_event_paths_reject_instance_overflow_before_remember_or_second_call(self):
        import context_readback
        import audit_readback
        for audit in (False, True):
            with self.subTest(audit=audit), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                nav, access, unit, inspected = self.event_context(root, unit_bytes=15_000)
                prior_states = {p.name: p.read_bytes() for p in (access.root / 'states').glob('*.json')}
                before = copy.deepcopy(inspected)
                client = self.event_client(audit)
                from semantic_audit import ContractError
                with self.assertRaises(ContractError if audit else ReadbackCapacity):
                    if audit:
                        audit_readback.complete(inspected, {}, nav.packet, root / 'run', client, received, access=access)
                    else:
                        with patch.object(access, 'start', return_value=inspected):
                            context_readback.complete(unit, nav.packet, root / 'run', client, received, navigation_access=access)
                self.assertEqual(1, client.calls)
                self.assertEqual(prior_states, {p.name: p.read_bytes() for p in (access.root / 'states').glob('*.json')})
                self.assertEqual(before, inspected)
                self.assertFalse((root / 'run/context-readback/1/unit.json').exists())
                self.assertFalse((root / 'run/audit-readback/1/unit.json').exists())
                receipt = root / 'run' / ('audit.receipt.json' if audit else 'review.receipt.json')
                self.assertEqual('received', json.loads(receipt.read_text())['status'])

    def test_month_multi_period_accesses_disable_directory_through_real_traversal(self):
        import month_readback
        import test_month_readback as month_tests
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            periods, directories = month_tests.setup(root / 'periods')
            calls = []
            def capture(*args, **kwargs):
                calls.append(kwargs.copy())
                return Access(*args, **kwargs)
            with patch('month_readback.Access', side_effect=capture):
                views, accesses = month_readback.prepare(periods, directories, 'J2', root / 'month')
            self.assertEqual(2, len(views))
            self.assertEqual([False, False], [c['child_directory'] for c in calls])
            for view in views:
                access = accesses[view['period_id']]
                self.assertIsNone(access.directory_mode)
                self.assertNotIn('child_directory_mode', view['context_access'])
                expanded = self.inspect(access, view)
                self.assertNotIn('child_directory', expanded['context_access'])
                self.assertGreater(len(expanded['context_access']['available_node_ids']), 1)
                self.assertEqual(view['supplied_quotes'], expanded['supplied_quotes'])
                access.verify(expanded)


class InitialDirectoryTests(unittest.TestCase):
    build = NavigationDirectoryTests.build
    inspect = NavigationDirectoryTests.inspect

    def test_new_initial_directory_equals_an_explicit_root_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);nav,reader,unit=self.build(root)
            manual=Access(nav,root/'manual')
            base=manual.start(unit)
            expected=self.inspect(manual,base,'manual-root')
            access=Access(nav,root/'automatic',initial_directory=True)
            expected['context_access']['initial_directory_mode']=access.initial_directory_mode
            set_remaining(expected)
            calls=reader.calls;packet_sha=digest(nav.packet)
            actual=access.start(unit)
            self.assertEqual(expected,actual)
            self.assertEqual(calls,reader.calls)
            self.assertEqual(packet_sha,digest(nav.packet))
            self.assertEqual(([],[],0),(actual['events'],actual['person_events'],actual['context_access']['raw_read_fragments']))
            proof=json.loads((access.root/'queries/initial-root.json').read_text())
            base['context_access']['initial_directory_mode']=access.initial_directory_mode
            set_remaining(base)
            self.assertEqual(digest(base),proof['previous_unit_sha256'])
            self.assertEqual(digest(actual),proof['next_unit_sha256'])
            access.verify(actual)

    def test_resumed_root_keeps_its_initial_mode_without_reconfiguration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);nav,reader,unit=self.build(root)
            access=Access(nav,root/'access',initial_directory=True)
            first=access.start(unit)
            before={str(p.relative_to(access.root)):p.read_bytes() for p in access.root.rglob('*.json')}
            resumed=Access(nav,root/'access')
            self.assertEqual(first,resumed.start(unit))
            self.assertEqual(before,{str(p.relative_to(access.root)):p.read_bytes() for p in access.root.rglob('*.json')})
            child=nav.load(access.tree['root'])['children'][0]
            actual=access.expand(first,[dict(node_id=child,mode='inspect')],'child')
            self.assertEqual(actual,resumed.expand(first,[dict(node_id=child,mode='inspect')],'child'))

    def test_existing_roots_never_acquire_an_initial_mode(self):
        for directory in (True,False):
            with self.subTest(directory=directory),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);nav,reader,unit=self.build(root)
                old=Access(nav,root/'access',child_directory=directory)
                first=old.start(unit);identity=old.identity.copy()
                new=Access(nav,root/'access',initial_directory=True)
                self.assertEqual(identity,new.identity)
                self.assertIsNone(new.initial_directory_mode)
                self.assertEqual(first,new.start(unit))
                self.assertFalse((new.root/'queries/initial-root.json').exists())

    def test_full_directory_over_64kb_leaves_only_the_original_start_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);nav,reader,unit=self.build(root)
            access=Access(nav,root/'access',initial_directory=True)
            original=nav.descriptor
            def padded(key,db=None):
                return dict(original(key,db),synthetic_padding='x'*20_000)
            with patch.object(nav,'descriptor',side_effect=padded):
                result=access.start(unit)
            state=result['context_access']
            self.assertEqual([access.tree['root']],state['available_node_ids'])
            self.assertEqual([],state['inspected_node_ids'])
            self.assertNotIn('child_directory',state)
            self.assertFalse(list((access.root/'queries').glob('*.json')))
            self.assertEqual(1,len(list((access.root/'states').glob('*.json'))))
            access.verify(result)

    def test_small_instance_capacity_falls_back_without_opening_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);nav,reader,unit=self.build(root)
            probe=Access(nav,root/'probe',initial_directory=True)
            expanded=probe.start(unit)
            # 起点状态仍留在磁盘，只用它的真实对象大小设较小实例上限。
            base=Access(nav,root/'base').start(unit)
            base['context_access']['initial_directory_mode']=probe.initial_directory_mode
            limit=raw_size(base)+200
            self.assertGreater(raw_size(expanded),limit)
            access=Access(nav,root/'limited',initial_directory=True,unit_bytes=limit)
            result=access.start(unit)
            self.assertLessEqual(raw_size(result),limit)
            self.assertEqual([],result['context_access']['observations'])
            self.assertFalse((access.root/'queries/initial-root.json').exists())
            self.assertEqual(1,len(list((access.root/'states').glob('*.json'))))
            access.verify(result)

    def test_leaf_root_never_preloads_raw_or_writes_an_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);nav,reader,unit=self.build(root,n=1)
            access=Access(nav,root/'access',initial_directory=True)
            result=access.start(unit)
            self.assertEqual([],result['events'])
            self.assertEqual([],result['context_access']['raw_node_ids'])
            self.assertFalse((access.root/'queries/initial-root.json').exists())
            access.verify(result)

    def test_actual_identification_payload_overflow_falls_back_before_proofs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);nav,reader,unit=self.build(root)
            client=runtime.CompanyClient(ModelConfig('https://ai.apsatcom.com',root/'never-read-key'))
            probe=Access(nav,root/'probe',initial_directory=True)
            def base_payload_size(count):
                source=dict(unit,synthetic_padding='\\'*count)
                base=Access(nav,root/('base-'+str(count))).start(source)
                base['context_access']['initial_directory_mode']=probe.initial_directory_mode
                set_remaining(base)
                return len(json.dumps(client.identification_payload(base),ensure_ascii=False).encode())
            left,right=0,60_000
            while left<right:
                middle=(left+right+1)//2
                # 留500字节，小目录增加的input转义和schema枚举会突破真实传输上限。
                if base_payload_size(middle)<=runtime.MAX_REQUEST_BYTES-500:left=middle
                else:right=middle-1
            unit['synthetic_padding']='\\'*left
            enlarged=probe.start(unit)
            self.assertLessEqual(raw_size(enlarged),probe.unit_bytes)
            self.assertGreater(len(json.dumps(client.identification_payload(enlarged),ensure_ascii=False).encode()),runtime.MAX_REQUEST_BYTES)
            access=Access(nav,root/'guarded',initial_directory=True)
            actual=access.start(unit,initial_payload=client.identification_payload)
            self.assertLessEqual(len(json.dumps(client.identification_payload(actual),ensure_ascii=False).encode()),runtime.MAX_REQUEST_BYTES)
            self.assertEqual([],actual['context_access']['observations'])
            self.assertFalse((access.root/'queries/initial-root.json').exists())
            self.assertEqual(1,len(list((access.root/'states').glob('*.json'))))
            access.verify(actual)
            self.assertFalse((root/'never-read-key').exists())

    def test_corrupt_navigation_is_never_a_capacity_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);nav,reader,unit=self.build(root)
            access=Access(nav,root/'access',initial_directory=True)
            child=nav.load(access.tree['root'])['children'][0]
            with nav.connect() as db:
                db.execute('UPDATE nodes SET body=? WHERE id=?',('{}',child))
            with self.assertRaisesRegex(ValueError,'签名'):
                access.start(unit)
            self.assertFalse((access.root/'queries/initial-root.json').exists())

    def test_corrupt_initial_mode_or_missing_directory_contract_is_rejected(self):
        for mode,directory in (('unknown',True),('initial-root-if-complete-20261116.1',False)):
            with self.subTest(mode=mode,directory=directory),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);nav,reader,unit=self.build(root)
                access=Access(nav,root/'access',child_directory=directory)
                atomic(access.root/'input.json',dict(access.identity,initial_directory_mode=mode))
                with self.assertRaisesRegex(ValueError,'初始根目录'):
                    Access(nav,root/'access',initial_directory=True)


if __name__ == '__main__':
    unittest.main()
