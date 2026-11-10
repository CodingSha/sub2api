"""候选只增加证据说明，不改变既有数值或放松时间边界。"""
import copy
import unittest

from scoring_pipeline import candidate_review_ab as candidate
from scoring_pipeline import stable_review as base, stable_scores, stable_batch
from scoring_pipeline.test_stable_review import fixture


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.unit, self.value = fixture()
        self.old = copy.deepcopy(self.value)
        self.value['version'] = candidate.VERSION
        self.value['period_reviews'][0]['fact_coverage'] = [dict(fact='概念解释目标', refs=['q'], primary_item='P1', reason='只主归目标，不重复评材料准备')]

    def test_existing_scores_and_original_output_preserved(self):
        before = copy.deepcopy(self.value)
        self.assertEqual(stable_scores.summarize(base.check(self.unit, self.old)), stable_scores.summarize(candidate.check(self.unit, self.value)))
        self.assertEqual(self.value, before)
        self.assertEqual(base.VERSION, self.old['version'])

    def test_coverage_is_not_a_score_bonus(self):
        first = stable_scores.summarize(candidate.check(self.unit, self.value))
        self.value['period_reviews'][0]['fact_coverage'].append(dict(fact='没有独立方法动作', refs=['q'], primary_item=None, reason='不生成方法评分'))
        self.assertEqual(first, stable_scores.summarize(candidate.check(self.unit, self.value)))

    def test_index_rejects_missing_reference(self):
        self.value['period_reviews'][0]['fact_coverage'][0]['refs'] = ['missing']
        with self.assertRaisesRegex(ValueError, '事实索引缺有效引用'):
            candidate.check(self.unit, self.value)

    def test_coverage_cannot_introduce_later_week_context(self):
        later = dict(self.unit['events'][0], event='s:later', at='2026-09-24T00:00:00+08:00', text='月末才出现的事实')
        self.unit['events'].append(later)
        later_period = dict(self.unit['periods'][0], period_id='month-later', start='2026-09-20T00:00:00+08:00')
        self.unit['periods'][0]['end'] = '2026-09-14T00:00:00+08:00'
        self.unit['periods'].append(later_period)
        self.value['evidence'].append(dict(id='later', event='s:later', quote=later['text'], origin='context', origin_reason='月末背景'))
        another = copy.deepcopy(self.value['period_reviews'][0])
        another['period_ids'] = ['month-later']
        # 晚期只保留背景引用和无机会项，避免用旧动作跨期认领。
        for c in another['cells']:
            if c['state'] == 'observed':
                for field in ('action', 'quality', 'performance'):
                    c.pop(field)
                c['state'] = 'no_opportunity'
        self.value['period_reviews'].append(another)
        self.value['period_reviews'][0]['fact_coverage'][0]['refs'] = ['later']
        with self.assertRaisesRegex(ValueError, '未来'):
            candidate.check(self.unit, self.value)

    def test_assembly_preserves_index_and_renames_refs(self):
        value = stable_batch.assemble(self.unit['key'], [dict(period_id='month', value=self.value)], contract=candidate)
        self.assertEqual(value['period_reviews'][0]['fact_coverage'][0]['refs'], ['month|q'])
        self.assertTrue(candidate.check(self.unit, value))

    def test_candidate_does_not_accept_old_version_as_new_result(self):
        with self.assertRaisesRegex(ValueError, '候选版本错误'):
            candidate.check(self.unit, self.old)
