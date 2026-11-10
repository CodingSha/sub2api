"""024月度汇总的时间、缺失、恢复及线性计算边界，不模拟真人校准。"""
import copy
import unittest
from fractions import Fraction

from .common import ContractError
from .weekly_performance.month_rollup import SCALE_VERSION, ITEMS, bound_period, month_windows, summarize_month


def periods(verdicts=('good',)*5):
    rows = []
    for w, verdict in zip(month_windows('2026-09-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00'), verdicts):
        rows.append(dict(user_id='example', period_id=w['calendar_id'],
                         scale_version=SCALE_VERSION, evidence_checked=True, evidence_ids=['proof'],
                         window={k: w[k].isoformat() for k in ('start', 'end')},
                         source_gaps=[], source_keys=['source'],
                         cells=[dict(item=i, state='observed', quality=dict(label='sufficient'),
                                     performance=dict(verdict=verdict)) for i in ITEMS]))
    return rows


def run(rows, **kwargs):
    return summarize_month(rows, start='2026-09-01T00:00:00+08:00', end='2026-10-01T00:00:00+08:00', **kwargs)


class MonthlyRollupTests(unittest.TestCase):
    def test_empty_period_requires_frozen_source_check(self):
        rows = periods()
        row = rows[-1]
        row.update(source_keys=[], cells=[dict(item=i, state='no_opportunity') for i in ITEMS])
        with self.assertRaises(ContractError): run(rows)
        row['empty_source_check'] = dict(expected_person_events=0, exact_match=True,
            audit_path='synthetic-test-audit.json', audit_sha256='synthetic')
        self.assertTrue(run(rows)['publishable'])

    def test_unknown_state_is_not_a_silent_exclusion(self):
        rows = periods()
        rows[0]['cells'][0]['state'] = 'typo'
        with self.assertRaises(ContractError): run(rows)

    def test_native_binding_and_fragment_time_attribution(self):
        scope = 'scored_fragment_scope'
        period = dict(period_id='week-2026-09-01', start='2026-09-01T00:00:00+08:00', end='2026-09-07T00:00:00+08:00')
        unit = dict(version=SCALE_VERSION, key='example', user_id='example', title='合同合成用例',
            evaluation_scope='person_week_fragment', periods=[period], source_keys=['source'], source_gaps=[],
            person_events=['event'], events=[dict(event='event', event_id='synthetic', text='明确目标',
                at='2026-09-02T00:00:00+08:00', actor_class='human', human_spans=[[0,4]])])
        cells = [dict(item=i, state='no_opportunity', reason='合成用例无独立机会',
            opportunity=dict(description='无独立机会', refs=[])) for i in ITEMS]
        for c in cells:
            if c['item'].startswith('M'): c['challenge_handling'] = dict(status='not_demonstrated', reason='无')
        cells[0] = dict(item='P1', state='observed', reason='合成合同检查', opportunity=dict(description='目标',refs=['proof']),
            action=dict(fact='输入目标', ai_role='执行', attribution_explanation='本人', refs=['proof']),
            quality=dict(label='sufficient', reason='合成正证', refs=['proof']),
            performance=dict(verdict='good', scope=scope, reason='合成比较'))
        review = dict(version=SCALE_VERSION, key='example', needs_events=[],
            evidence=[dict(id='proof', event='event', quote='明确目标', origin='direct_expression', origin_reason='本人表达')],
            period_reviews=[dict(period_ids=[period['period_id']], cells=cells,
                context=dict(scope=scope, effective_request='目标', necessary_scope='当前对象', observed_execution='待执行',
                    boundary_reason='合成用例', boundary='consistent', refs=['proof']))])
        row = bound_period(unit, review)
        self.assertEqual(row['window'], period)
        for mutation in ('before', 'future', 'nonhuman', 'wrongquote', 'emptyrefs', 'wrongscope'):
            u, v = copy.deepcopy(unit), copy.deepcopy(review)
            if mutation == 'before': u['events'][0]['at'] = '2026-08-31T12:00:00+08:00'
            if mutation == 'future': u['events'][0]['at'] = '2026-09-07T00:00:00+08:00'
            if mutation == 'nonhuman': u['events'][0]['actor_class'] = 'assistant'
            if mutation == 'wrongquote': v['evidence'][0]['quote'] = '不存在'
            if mutation == 'emptyrefs': v['period_reviews'][0]['cells'][0]['action']['refs'] = []
            if mutation == 'wrongscope': v['period_reviews'][0]['cells'][0]['performance']['scope'] = 'scored_week_scope'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): bound_period(u, v)

    def test_month_intersections_and_fraction(self):
        result = run(periods(('good', 'reasonable', 'good', 'outstanding', 'reasonable')))
        self.assertEqual([x['days'] for x in result['coverage']], [6, 7, 7, 7, 3])
        self.assertEqual(Fraction(result['exact']), Fraction(790, 10))
        self.assertEqual(result['rating']['level'], 'L3')

    def test_no_round_trip_to_a_higher_band(self):
        result = run(periods(('good', 'good', 'reasonable', 'reasonable', 'reasonable')))
        self.assertEqual(result['items']['E2']['exact'], '223/3')
        self.assertLessEqual(Fraction(result['items']['E2']['exact']), 80)

    def test_rounded_display_does_not_raise_rating(self):
        rows = periods(('outstanding',)*5)
        next(c for c in rows[-1]['cells'] if c['item'] == 'E1')['performance']['verdict'] = 'good'
        result = run(rows)
        self.assertEqual(result['exact'], '1799/20')
        self.assertEqual(result['score'], '90.0')
        self.assertEqual(result['rating']['level'], 'L4')
        self.assertTrue(result['rating']['rounding_crosses_boundary'])
        self.assertIn('仍按L4', result['rating']['score_display'])

    def test_unknown_not_zero_or_full_credit(self):
        rows = periods()
        rows[0]['cells'][0] = dict(item='P1', state='unobservable')
        result = run(rows)
        self.assertEqual(result['items']['P1']['assessed_days'], 24)
        self.assertEqual(result['items']['P1']['exact'], '80')
        self.assertFalse(result['publishable'])
        self.assertIsNone(result['rating']['level'])

    def test_no_opportunity_preserved(self):
        rows = periods()
        for row in rows:
            row['cells'][0] = dict(item='P1', state='no_opportunity')
        result = run(rows)
        self.assertIsNone(result['items']['P1']['exact'])
        self.assertEqual(result['dimensions']['P']['item_count'], 2)
        self.assertEqual(result['exact'], '80')

    def test_entire_dimension_unknown_no_reweight(self):
        rows = periods()
        for row in rows:
            row['cells'] = [dict(item=c['item'], state='no_opportunity') if c['item'].startswith('E') else c for c in row['cells']]
        self.assertIsNone(run(rows)['exact'])

    def test_missing_or_unchecked_period_cannot_publish(self):
        rows = periods()
        self.assertFalse(run(rows[:-1])['publishable'])
        rows[0]['evidence_checked'] = False
        self.assertFalse(run(rows)['publishable'])

    def test_structured_task_gaps_survive_month_rollup(self):
        rows = periods()
        gap = dict(task_id='task', item='M1', needed='方案后续反馈')
        rows[0]['source_gaps'] = ['缺少附件', gap]
        rows[1]['source_gaps'] = [copy.deepcopy(gap), '缺少附件']
        result = run(rows)
        self.assertEqual(result['source_gaps'], ['缺少附件', gap])
        self.assertEqual(result['exact'], '80')
        self.assertFalse(result['publishable'])
        self.assertIsNone(result['rating']['level'])

    def test_overlap_mixed_user_scale_rejected(self):
        for mutation in ('duplicate', 'user', 'scale', 'window'):
            rows = periods()
            if mutation == 'duplicate': rows.append(copy.deepcopy(rows[0]))
            if mutation == 'user': rows[1]['user_id'] = 'another'
            if mutation == 'scale': rows[1]['scale_version'] = 'old'
            if mutation == 'window': rows[1]['window']['start'] = rows[0]['window']['start']
            with self.subTest(mutation=mutation), self.assertRaises(ContractError): run(rows)

    def test_open_gap_not_averaged_away(self):
        rows = periods()
        rows[0]['cells'][0]['quality']['label'] = 'necessary_gap'
        rows[0]['cells'][0]['performance']['verdict'] = 'necessary_gap'
        self.assertFalse(run(rows)['publishable'])
        decision = dict(period_id=rows[0]['period_id'], item='P1', status='open', reason='未恢复的必要保护')
        result = run(rows, gap_dispositions=[decision])
        self.assertEqual(result['dimensions']['P']['exact'], '40')
        self.assertEqual(result['exact'], '72')

    def test_recovery_keeps_history_and_requires_later_proof(self):
        rows = periods()
        rows[0]['cells'][0]['quality']['label'] = 'necessary_gap'
        rows[0]['cells'][0]['performance']['verdict'] = 'necessary_gap'
        decision = dict(period_id=rows[0]['period_id'], item='P1', status='recovered', reason='后续本人处理已恢复',
                        recovery_period=rows[1]['period_id'], recovery_refs=['proof'])
        result = run(rows, gap_dispositions=[decision])
        self.assertEqual(result['items']['P1']['exact'], '72')
        self.assertEqual(result['dimensions']['P']['exact'], '232/3')
        decision['recovery_period'] = rows[0]['period_id']
        with self.assertRaises(ContractError): run(rows, gap_dispositions=[decision])


if __name__ == '__main__':
    unittest.main()
