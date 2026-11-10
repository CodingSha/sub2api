"""验证实际发送装配及原合同边界，不宣称模型语义已修复。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import candidate_review_judgment as candidate, repair_scope, stable_scores, stable_batch
from .test_stable_review import fixture


class JudgmentTests(unittest.TestCase):
    def setUp(self):
        self.unit, self.value = fixture()
        self.value['version'] = candidate.VERSION
        self.value['period_reviews'][0]['fact_coverage'] = [dict(
            fact='当前目标明确', refs=['q'], primary_item='P1', reason='当前本人输入')]

    def test_previous_outputs_keep_original_fields_and_scores(self):
        old = dict(self.value, version=candidate.previous.VERSION)
        before = copy.deepcopy(self.value)
        old_rows = candidate.previous.check(self.unit, old)
        rows = candidate.check(self.unit, self.value)
        self.assertEqual(old_rows, rows)
        self.assertEqual(stable_scores.summarize(old_rows), stable_scores.summarize(rows))
        self.assertEqual(before, self.value)

    def test_old_version_and_false_proof_still_rejected(self):
        with self.assertRaisesRegex(ValueError, '候选版本错误'):
            candidate.check(self.unit, dict(self.value, version=candidate.previous.VERSION))
        self.value['evidence'][0]['quote'] = '原文中不存在'
        with self.assertRaisesRegex(ValueError, '引文'):
            candidate.check(self.unit, self.value)

    def test_only_three_anchor_texts_change(self):
        before, after = copy.deepcopy(candidate.previous.RUBRIC), copy.deepcopy(candidate.RUBRIC)
        before['version'] = after['version']
        changed = []
        for item in candidate.ITEMS:
            for stage in ('effective', 'adapted', 'verified'):
                if before['performance_anchors'][item][stage] != after['performance_anchors'][item][stage]:
                    changed.append((item, stage))
                    before['performance_anchors'][item][stage] = after['performance_anchors'][item][stage]
        self.assertEqual(changed, [('P1', 'verified'), ('E2', 'adapted'), ('E3', 'adapted')])
        self.assertEqual(before, after)
        self.assertEqual(candidate.PERFORMANCE, candidate.previous.PERFORMANCE)

    def test_replaces_existing_explanations_instead_of_appending(self):
        self.assertFalse(candidate.INSTRUCTIONS.startswith(candidate.previous.INSTRUCTIONS))
        self.assertEqual(candidate.INSTRUCTIONS.count('评价单元是本项的任务×期间整体轨迹'), 1)
        self.assertNotIn('E3某次实际复用已有适配核对时应保留该事实', candidate.INSTRUCTIONS)
        self.assertNotIn('e0014', candidate.INSTRUCTIONS)

    def test_request_removes_derived_notes_without_losing_material(self):
        payload = dict(task=dict(key='s:t', source_gaps=[dict(item='J1', needed='历史判断')],
                                 source_limits=dict(truncated=True), periods=self.unit['periods'],
                                 raw_material=dict(person_events=self.unit['events'])),
                       full_context=self.unit['events'], correction=None, rubric=candidate.RUBRIC)
        before = copy.deepcopy(payload)
        actual = candidate.prepare_payload(payload)
        expected = copy.deepcopy(payload)
        del expected['task']['source_gaps']
        self.assertEqual(actual, expected)
        self.assertEqual(payload, before)
        self.assertEqual(candidate.prepare_payload(actual), actual)

    def test_actual_client_sanitizes_initial_read_and_repair(self):
        record = dict(request_sha256='offline-test', text=json.dumps(self.value, ensure_ascii=False))
        with tempfile.TemporaryDirectory() as temp:
            client = candidate.JudgmentClient('unused', audit_dir=Path(temp))
            for correction in (None, '所需事件全文已补齐，请重新返回完整判断。',
                               dict(contract_error='q引用错误', previous_output=record['text'], instruction='旧返修说明')):
                payload = dict(task=dict(source_gaps=[dict(item='J1', needed='历史评语')], raw_material={'text':'原始证据'}),
                               full_context=[], correction=correction, rubric=candidate.RUBRIC)
                original = copy.deepcopy(payload)
                with patch.object(repair_scope.JSONClient, 'call', return_value=record) as remote:
                    client.call(candidate.INSTRUCTIONS, payload, Path(temp), 'offline')
                sent = remote.call_args.args[1]
                self.assertNotIn('source_gaps', sent['task'])
                self.assertEqual(sent['task']['raw_material'], payload['task']['raw_material'])
                self.assertEqual(sent['rubric'], candidate.RUBRIC)
                self.assertEqual(remote.call_args.args[0], candidate.INSTRUCTIONS)
                if isinstance(correction, dict):
                    self.assertEqual(sent['correction']['instruction'], repair_scope.INSTRUCTION)
                    self.assertEqual(sent['correction']['previous_output'], record['text'])
                self.assertEqual(original, payload)

    def test_batch_entry_uses_candidate_contract_and_clean_request(self):
        event = self.unit['events'][0]
        packet = dict(source_gaps=[dict(item='J1', needed='旧索引评语')], raw_material=dict(
            person_events=[dict(event=event['event'], text=event['text'])],
            prior_person_context=[], linked_context=[]))
        record = dict(request_sha256='offline-integration', text=json.dumps(self.value, ensure_ascii=False))
        with tempfile.TemporaryDirectory() as temp:
            client = candidate.JudgmentClient('unused', audit_dir=Path(temp) / 'audit')
            with patch.object(stable_batch, 'packet', return_value=(self.unit, packet, {event['event']:event})), \
                 patch.object(repair_scope.JSONClient, 'call', return_value=record) as remote:
                value, rows, _ = stable_batch.evaluate_one({}, self.unit['key'], Path(temp),
                    dict(run_id='offline', replicate='test'), client, self.unit['periods'][0], contract=candidate)
            actual = remote.call_args.args[1]
            self.assertEqual(actual['task']['policy_version'], candidate.VERSION)
            self.assertNotIn('source_gaps', actual['task'])
            self.assertEqual(actual['rubric'], candidate.RUBRIC)
            self.assertEqual(value, self.value)
            self.assertEqual(rows, candidate.check(self.unit, value))


if __name__ == '__main__':
    unittest.main()
