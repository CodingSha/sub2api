"""验证日评归一权重、精确计算和零分与缺失的区别。"""
import unittest
from fractions import Fraction
from daily_scores import observed_total


class DailyScoresTests(unittest.TestCase):
    def test_partial_dimensions_keep_original_relative_weights(self):
        dimensions=[dict(code=c,exact=n) for c,n in zip('PMJE',('90','80','80',None))]
        self.assertEqual(observed_total(dimensions),Fraction(1400,17))
        self.assertIsNone(dimensions[3]['exact'])
        dimensions[3]['exact']='0'
        self.assertEqual(observed_total(dimensions),Fraction(70))

    def test_single_observed_zero_is_scored_while_no_observations_are_not(self):
        self.assertEqual(observed_total([dict(code='E',exact='0')]),Fraction(0))
        self.assertIsNone(observed_total([dict(code=c,exact=None) for c in 'PMJE']))

    def test_full_dimensions_use_exact_values_before_rounding(self):
        dimensions=[dict(code=c,exact=n) for c,n in zip('PMJE',('250/3','80','80','80'))]
        self.assertEqual(observed_total(dimensions),Fraction(242,3))
