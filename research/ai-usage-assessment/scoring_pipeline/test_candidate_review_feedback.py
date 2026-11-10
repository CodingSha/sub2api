"""核对当轮接纳闸门、诊断异常保护及限定文本替换，不代替语义验收。"""
import copy
import unittest
from unittest.mock import patch

from . import candidate_review_feedback as candidate, contract_feedback as feedback
from .test_stable_review import fixture


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.unit, self.value = fixture()
        self.value['version'] = candidate.VERSION
        self.value['period_reviews'][0]['fact_coverage'] = [dict(
            fact='目标事实', refs=['q'], primary_item='P1', reason='只评目标')]

    def test_current_validator_used_and_input_unchanged(self):
        before = copy.deepcopy(self.value)
        expected = candidate.check_semantics(self.unit, self.value)
        with patch.object(candidate, 'check_semantics', wraps=candidate.check_semantics) as check:
            self.assertEqual(candidate.check(self.unit, self.value), expected)
            check.assert_called_once_with(self.unit, self.value)
        self.assertEqual(before, self.value)

    def test_old_version_rejected(self):
        self.value['version'] = candidate.previous.VERSION
        with self.assertRaisesRegex(ValueError, '候选版本错误'):
            candidate.check(self.unit, self.value)

    def test_diagnostics_failure_preserves_original_error(self):
        self.value['period_reviews'][0]['cells'][0]['performance']['verdict'] = 'complete'
        with patch.object(feedback, 'known_issues', side_effect=TypeError('辅助诊断异常')):
            with self.assertRaisesRegex(ValueError, '表现档与AI递进判断不自洽'):
                candidate.check(self.unit, self.value)

    def test_no_diagnostic_issue_cannot_bypass_gate(self):
        self.value['period_reviews'][0]['cells'][0]['performance']['verdict'] = 'complete'
        with self.assertRaisesRegex(ValueError, '表现档与AI递进判断不自洽'):
            candidate.check(self.unit, self.value)

    def test_preserves_mastery_and_aggregation_boundaries(self):
        for phrase in ('不表示处理得好或自动高分', '不能把前者档位借给后者',
                       '不新增同项多动作的最高、最低、平均或其他合成规则'):
            self.assertIn(phrase, candidate.INSTRUCTIONS)
        self.assertEqual(candidate.PERFORMANCE, candidate.previous.PERFORMANCE)
        self.assertNotIn('e0342', candidate.INSTRUCTIONS)
        self.assertLessEqual(len(candidate.INSTRUCTIONS), len(candidate.previous.INSTRUCTIONS) + 100)


if __name__ == '__main__':
    unittest.main()
