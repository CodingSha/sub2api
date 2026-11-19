"""归并前核完整请求及剩余下界；本机合成发送，不调用外部服务。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from goal_reconciliation import reconcile, plan, comparison_schema
from model_flow import ModelFlow
from pipeline import Paused, received
from runtime import atomic, digest
import test_goal_reconciliation as fixtures
from worker import JobClient


class SyntheticClient(JobClient):
    def __init__(self, root, limit=100, turn=100, flow=None):
        config = SimpleNamespace(model='synthetic', responses_url='http://127.0.0.1:1/responses',
            timeout=1, read_key=lambda: 'synthetic-key')
        super().__init__(config, root, limit, turn, flow)
        self.calls = 0; self.invalid_next = False; self.payloads = []

    def _transmit(self, path, request_hash, body, key):
        self.calls += 1; payload = json.loads(body); self.payloads.append(payload)
        data = json.loads(payload['input'])
        goals = [dict(goal_id=g['local_goal_id'], title='合成目标', purpose='合成目的',
            boundary='confirmed', boundary_reason='合成边界', local_goal_ids=[g['local_goal_id']],
            context_event_ids=[]) for g in data['local_goals']]
        if self.invalid_next:
            goals = []; self.invalid_next = False
        record = dict(status='received', request_sha256=request_hash, response=dict(status='completed',
            output=[dict(content=[dict(type='output_text', text=json.dumps(dict(goals=goals)))])]))
        atomic(path, record)
        return record


class GoalCapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fixture = fixtures.GoalReconciliationTests()
        self.data = fixture.data(3); self.cap = fixture.limit(self.data)

    def run_stage(self, client, data=None, cap=None):
        return reconcile(data or self.data, self.root/'merge', client, received,
            max_bytes=cap or self.cap)

    def proof(self):
        return json.loads((self.root/'merge/capacity-observation.json').read_text())['result']

    def test_known_45_comparisons_with_only_five_remaining_stop_without_spending(self):
        fixture = fixtures.GoalReconciliationTests(); data = fixture.data(9)
        client = SyntheticClient(self.root/'job', limit=5)
        before = client.budget.path.read_bytes()
        with self.assertRaises(Paused) as error:
            self.run_stage(client, data, fixture.limit(data))
        self.assertEqual('needs_review', error.exception.state)
        self.assertEqual((0, before), (client.calls, client.budget.path.read_bytes()))
        self.assertEqual((45, 5), (self.proof()['known_new_request_lower_bound'], self.proof()['available_requests']))
        self.assertFalse(list((self.root/'merge').glob('*.receipt.json')))

    def test_global_balance_blocks_without_personal_spending(self):
        flow = ModelFlow(self.root/'flow', dict(version=1, budget_key='synthetic', approved_by='合成',
            reason='阶段下界', max_concurrency=1, requests_per_minute=100,
            reserved_tokens_per_minute=10000000, max_requests=5,
            transient_failure_limit=2, cooldown_seconds=60))
        client = SyntheticClient(self.root/'job', flow=flow)
        with self.assertRaises(Paused): self.run_stage(client)
        self.assertEqual((0, 0, 0), (client.calls, client.budget.used(), flow.remaining_requests()['used']))
        self.assertEqual(5, self.proof()['available_requests'])

    def test_turn_limit_is_not_whole_stage_limit_and_partial_hot_receipts_are_exact(self):
        first = SyntheticClient(self.root/'job', limit=6, turn=3)
        with self.assertRaises(Paused) as error: self.run_stage(first)
        self.assertEqual(('queued', 3), (error.exception.state, first.calls))
        second = SyntheticClient(self.root/'job', limit=6)
        expected = self.run_stage(second)
        self.assertEqual((3, 3, 3), (second.calls, self.proof()['known_new_request_lower_bound'],
            self.proof()['validated_comparisons']))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.root/'merge').glob('*.receipt.json')}
        hot = SyntheticClient(self.root/'job', limit=6)
        self.assertEqual(expected, self.run_stage(hot))
        self.assertEqual((0, 0, 6), (hot.calls, self.proof()['known_new_request_lower_bound'],
            self.proof()['validated_comparisons']))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})

    def first_request(self, client):
        base, groups = plan(self.data, self.cap)
        data = dict(base, local_goals=groups[0])
        root = self.root/'merge'; root.mkdir()
        receipt = root/(digest(data)+'.receipt.json')
        return data, comparison_schema(data), receipt

    def test_bad_received_counts_next_exact_fix_without_writing_feedback(self):
        client = SyntheticClient(self.root/'job', limit=6)
        data, schema, path = self.first_request(client)
        client.invalid_next = True; client.merge_goals(data, schema, path)
        before = path.read_bytes()
        with self.assertRaises(Paused): self.run_stage(client)
        self.assertEqual((1, before), (client.calls, path.read_bytes()))
        self.assertEqual(6, self.proof()['known_new_request_lower_bound'])
        self.assertFalse(list(path.parent.glob('*.contract-error.json')))
        self.assertFalse(list(path.parent.glob('*.fix1.receipt.json')))

    def test_exact_feedback_replays_repaired_receipts_without_new_requests(self):
        client = SyntheticClient(self.root/'job', limit=7); client.invalid_next = True
        expected = self.run_stage(client)
        self.assertEqual(7, client.calls)
        self.assertTrue(any('contract_feedback' in json.loads(p['input']) for p in client.payloads))
        hot = SyntheticClient(self.root/'job', limit=7)
        self.assertEqual(expected, self.run_stage(hot)); self.assertEqual(0, hot.calls)

    def test_later_unknown_is_found_before_first_new_comparison(self):
        client = SyntheticClient(self.root/'job')
        base, groups = plan(self.data, self.cap)
        later = dict(base, local_goals=groups[-1]); root = self.root/'merge'; root.mkdir()
        schema = comparison_schema(later); payload = client.merge_payload(later, schema)
        atomic(root/(digest(later)+'.receipt.json'), dict(status='unknown',
            request_sha256=digest(dict(endpoint=client.config.responses_url, payload=payload))))
        with self.assertRaises(Paused) as error: self.run_stage(client)
        self.assertEqual(('unknown', 0, 0), (error.exception.state, client.calls, client.budget.used()))

    def test_missing_reserved_receipt_is_unknown_and_orphan_attempt_is_not_a_miss(self):
        client = SyntheticClient(self.root/'job'); data, schema, path = self.first_request(client)
        payload = client.merge_payload(data, schema)
        client.budget.reserve(path, digest(dict(endpoint=client.config.responses_url, payload=payload)))
        with self.assertRaises(Paused) as error: self.run_stage(client)
        self.assertEqual(('unknown', 0), (error.exception.state, client.calls))
        # 独立合成账簿中仅有孤立attempt，不能从原始路径重新开始。
        other = SyntheticClient(self.root/'other')
        atomic(path.with_name(path.name.replace('.receipt.json', '.attempt-0.receipt.json')), dict(status='unknown'))
        with self.assertRaisesRegex(ValueError, '原回执缺失'): self.run_stage(other)
        self.assertEqual(0, other.calls)

    def test_raw_material_fits_but_complete_request_does_not_send(self):
        data = fixtures.GoalReconciliationTests().data(1)
        data['local_goals'][0]['goal']['context_event_ids'] = ['\\'*34000]
        client = SyntheticClient(self.root/'job')
        with self.assertRaisesRegex(ValueError, '完整请求超过'): self.run_stage(client, data, 85000)
        self.assertEqual((0, 0), (client.calls, client.budget.used()))

    def test_observation_damage_is_preserved_without_new_sends(self):
        client = SyntheticClient(self.root/'job', limit=1)
        with self.assertRaises(Paused): self.run_stage(client)
        path = self.root/'merge/capacity-observation.json'
        value = json.loads(path.read_text()); value['result']['whole_pipeline_feasibility'] = True
        atomic(path, value); before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '观测来源或签名变化'): self.run_stage(client)
        self.assertEqual((0, before), (client.calls, path.read_bytes()))

    def test_controlled_transport_is_deferred_and_original_state_machine_resumes(self):
        from request_retry import VERSION, attempt_path
        for chained in (False, True):
            with self.subTest(chained=chained), tempfile.TemporaryDirectory() as temp:
                self.root = Path(temp)
                flow = ModelFlow(self.root/'flow', dict(version=1, budget_key='synthetic', approved_by='合成',
                    reason='续发下界', max_concurrency=1, requests_per_minute=100,
                    reserved_tokens_per_minute=10000000, max_requests=20,
                    transient_failure_limit=2, cooldown_seconds=60, max_interrupted_retries=1))
                client = SyntheticClient(self.root/'job', limit=7, flow=flow)
                data, schema, path = self.first_request(client)
                client.merge_goals(data, schema, path)
                failed = json.loads(path.read_text()); failed.update(status='unknown', error_code='stream_ended_without_terminal')
                failed.pop('response'); atomic(path, failed)
                if chained:
                    atomic(attempt_path(path, 0), failed)
                    atomic(path, dict(transport_version=VERSION, request_sha256=failed['request_sha256'],
                        status='retry_wait', attempts=[dict(sha256=digest(failed))], exhausted=False))
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in path.parent.glob('*.receipt.json')}
                base, groups = plan(self.data, self.cap)
                proof = client.preflight_goal_merge(base, groups, path.parent, received)
                self.assertEqual((5, 1, 0), (proof['known_new_request_lower_bound'],
                    proof['deferred_transport_comparisons'], proof['validated_comparisons']))
                self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})
                self.assertEqual(1, client.calls)
                result = self.run_stage(client)
                self.assertEqual((7, 3), (client.calls, len(result['goals'])))
                self.assertTrue(attempt_path(path, 1).exists())

    def test_exhausted_transport_unknown_is_not_hidden_by_enabled_retry_config(self):
        from request_retry import VERSION, attempt_path
        client = SyntheticClient(self.root/'job')
        base, groups = plan(self.data, self.cap)
        data = dict(base, local_goals=groups[-1]); root = self.root/'merge'; root.mkdir()
        payload = client.merge_payload(data, comparison_schema(data))
        path = root/(digest(data)+'.receipt.json')
        failed = dict(status='unknown', error_code='stream_ended_without_terminal',
            request_sha256=digest(dict(endpoint=client.config.responses_url, payload=payload)))
        atomic(attempt_path(path, 0), failed)
        atomic(path, dict(transport_version=VERSION, request_sha256=failed['request_sha256'],
            status='unknown', attempts=[dict(sha256=digest(failed))], exhausted=True))
        client.flow = SimpleNamespace(config=dict(max_interrupted_retries=2))
        with self.assertRaises(Paused) as error: self.run_stage(client)
        self.assertEqual(('unknown', 0), (error.exception.state, client.calls))


if __name__ == '__main__': unittest.main()
