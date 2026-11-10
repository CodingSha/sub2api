"""验证旧聚合政策保持及未知不补分，不验证语义有效性。"""
import unittest
from .stable_scores import summarize


def row(key,values):
    return dict(key=key,user_id='test',period_id='month',cells=[
        dict(item=item,state='observed',performance=dict(verdict=verdict)) for item,verdict in values.items()])


class ScoreTests(unittest.TestCase):
    def test_same_quality_can_show_different_performance(self):
        result=summarize([row('a',dict(P1='sufficient',P2='tailored',P3='complete'))])
        self.assertEqual(result['dimensions']['P']['exact'],'90')
        self.assertIsNone(result['score'])
    def test_task_weights_unchanged(self):
        result=summarize([row('a',dict(P1='sufficient',P2='complete')),row('b',dict(P1='complete'))])
        self.assertEqual(result['dimensions']['P']['exact'],'95')
    def test_necessary_gap_not_averaged_away(self):
        result=summarize([row('a',dict(M1='necessary_gap',M2='complete'))])
        self.assertEqual(result['dimensions']['M']['exact'],'60')
    def test_unknown_is_not_zero_or_full_marks(self):
        result=summarize([row('a',dict(P1='unresolved'))])
        self.assertIsNone(result['dimensions']['P']['exact'])
        self.assertEqual(result['dimensions']['P']['unresolved'],1)
    def test_four_dimensions_remain_equal(self):
        result=summarize([row('a',dict(P1='sufficient',M1='tailored',J1='complete',E1='sufficient'))])
        self.assertEqual(result['exact'],'175/2')
    def test_duplicate_task_rejected(self):
        with self.assertRaises(ValueError):summarize([row('a',{}),row('a',{})])
