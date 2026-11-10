"""只检验既有约束汇总、原校验保留及无原判修改，不证明语义正确。"""
import copy
import unittest

from . import candidate_review_linkage as base, contract_diagnostics as candidate
from .test_stable_review import fixture


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.unit, self.value = fixture()
        self.value['version'] = base.VERSION
        self.value['period_reviews'][0]['fact_coverage'] = [dict(
            fact='目标事实', refs=['q'], primary_item='P1', reason='只评目标')]

    def test_valid_rows_identical_and_input_unchanged(self):
        before = copy.deepcopy(self.value)
        self.assertEqual(base.check(self.unit, self.value), candidate.check_with_diagnostics(self.unit, self.value))
        self.assertEqual(candidate.known_issues(self.unit, self.value), [])
        self.assertEqual(before, self.value)

    def test_reports_coexisting_errors_and_preserves_invalid_output(self):
        part = self.value['period_reviews'][0]
        part['cells'][1] = dict(copy.deepcopy(part['cells'][0]), item='P2')
        part['cells'][1]['action']['fact'] = '同句中的另一材料事实'
        part['fact_coverage'].append(dict(fact='候选处置', refs=[], primary_item=None, reason='待核'))
        self.value['evidence'][0]['quote'] = '不存在的引文'
        before = copy.deepcopy(self.value)
        issues = candidate.known_issues(self.unit, self.value)
        self.assertEqual({i['code'] for i in issues}, {'引文来源', '共引独立说明', '事实引用'})
        with self.assertRaises(ValueError) as err:
            candidate.check_with_diagnostics(self.unit, self.value)
        self.assertIn('引文不逐字存在', str(err.exception))
        self.assertIn('fact_coverage[1]', str(err.exception))
        self.assertLessEqual(len(str(err.exception)), 600)
        self.assertEqual(before, self.value)

    def test_no_known_issue_does_not_bypass_full_validator(self):
        self.value['period_reviews'][0]['cells'][0]['performance']['verdict'] = 'complete'
        self.assertEqual(candidate.known_issues(self.unit, self.value), [])
        with self.assertRaisesRegex(ValueError, '表现档与AI递进判断不自洽'):
            candidate.check_with_diagnostics(self.unit, self.value)

    def test_invalid_ledger_identity_does_not_create_cascades(self):
        self.value['evidence'].append(copy.deepcopy(self.value['evidence'][0]))
        self.assertEqual(candidate.known_issues(self.unit, self.value), [])
        with self.assertRaisesRegex(ValueError, '引文ID缺失/重复'):
            candidate.check_with_diagnostics(self.unit, self.value)

    def test_unknown_action_reference_does_not_crash_diagnostics(self):
        self.value['period_reviews'][0]['cells'][0]['action']['refs'] = ['missing']
        self.assertEqual(candidate.known_issues(self.unit, self.value), [])
        with self.assertRaisesRegex(ValueError, '不存在的引文ID'):
            candidate.check_with_diagnostics(self.unit, self.value)

    def test_valid_semantic_ambiguity_is_not_auto_corrected(self):
        self.value['period_reviews'][0]['fact_coverage'][0].update(primary_item=None,
            reason='保留语义排除解释，诊断不猜应归哪项')
        self.assertTrue(candidate.check_with_diagnostics(self.unit, self.value))
        self.assertIsNone(self.value['period_reviews'][0]['fact_coverage'][0]['primary_item'])


if __name__ == '__main__':
    unittest.main()
