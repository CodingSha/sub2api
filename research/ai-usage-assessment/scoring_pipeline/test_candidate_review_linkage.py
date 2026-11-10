"""确定矛盾要拦截，语义与多动作政策不能由结构检查代替。"""
import copy
import unittest

from scoring_pipeline import candidate_review_linkage as candidate
from scoring_pipeline import candidate_review_ab as previous, stable_batch, stable_scores
from scoring_pipeline.test_stable_review import fixture


class LinkageTests(unittest.TestCase):
    def setUp(self):
        self.unit, self.value = fixture()
        self.value['version'] = candidate.VERSION
        self.value['period_reviews'][0]['fact_coverage'] = [dict(
            fact='解释概念的目标', refs=['q'], primary_item='P1', reason='只评目标')]

    def test_previous_scores_and_original_are_preserved(self):
        old = dict(self.value, version=previous.VERSION)
        before = copy.deepcopy(self.value)
        self.assertEqual(stable_scores.summarize(previous.check(self.unit, old)),
                         stable_scores.summarize(candidate.check(self.unit, self.value)))
        self.assertEqual(before, self.value)
        self.assertEqual(previous.VERSION, 'pmje-observation-20261103.14.8-ab1')

    def test_primary_cannot_point_to_no_opportunity(self):
        self.value['period_reviews'][0]['fact_coverage'][0]['primary_item'] = 'P2'
        with self.assertRaisesRegex(ValueError, '事实主归与该项状态矛盾'):
            candidate.check(self.unit, self.value)

    def test_primary_cannot_point_to_associated_item(self):
        cell = self.value['period_reviews'][0]['cells'][1]
        cell.update(state='related', primary_item='P1')
        self.value['period_reviews'][0]['fact_coverage'][0]['primary_item'] = 'P2'
        with self.assertRaisesRegex(ValueError, '事实主归与该项状态矛盾'):
            candidate.check(self.unit, self.value)

    def test_explicit_primary_needs_a_source_link(self):
        event = dict(self.unit['events'][0], event='s:other', text='另一条当前观察')
        self.unit['events'].append(event)
        self.value['evidence'].append(dict(id='other', event=event['event'], quote=event['text'],
            origin='context', origin_reason='只作背景'))
        self.value['period_reviews'][0]['fact_coverage'][0]['refs'] = ['other']
        with self.assertRaisesRegex(ValueError, '未连到该项来源'):
            candidate.check(self.unit, self.value)

    def test_null_exclusion_is_not_forced_into_a_score(self):
        self.value['period_reviews'][0]['fact_coverage'][0].update(primary_item=None,
            reason='语义排除是否合理另验；检查器不猜主归')
        self.assertTrue(candidate.check(self.unit, self.value))

    def test_same_source_different_quote_is_not_semantic_equivalence(self):
        # 结构层允许同源不同句；语义相关性留给评审与验收。
        self.unit['events'][0]['text'] += '另一个事实。'
        self.value['evidence'].append(dict(id='other', event='s:e', quote='另一个事实。',
            origin='context', origin_reason='同源另一句'))
        self.value['period_reviews'][0]['fact_coverage'][0]['refs'] = ['other']
        self.assertTrue(candidate.check(self.unit, self.value))

    def test_assembly_preserves_closure(self):
        value = stable_batch.assemble(self.unit['key'], [dict(period_id='month', value=self.value)], contract=candidate)
        self.assertEqual(value['period_reviews'][0]['fact_coverage'][0]['refs'], ['month|q'])
        self.assertTrue(candidate.check(self.unit, value))

    def test_challenge_flag_and_fact_count_do_not_add_points(self):
        before = stable_scores.summarize(candidate.check(self.unit, self.value))
        self.value['period_reviews'][0]['fact_coverage'].append(dict(
            fact='只作排除说明', refs=['q'], primary_item=None, reason='不产生新评分'))
        self.assertEqual(before, stable_scores.summarize(candidate.check(self.unit, self.value)))


if __name__ == '__main__':
    unittest.main()
