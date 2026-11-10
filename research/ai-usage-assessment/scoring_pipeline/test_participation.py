"""周期与参与候选的边界回归；不声称统计校准或真人评分有效。"""

import json
from pathlib import Path
import tempfile
import unittest

from .common import ContractError
from .policy import VERSION
from .runner import periods_for
from .participation import apply_participation
from .reader_report import safe_quote


class ParticipationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'usage.json'
        self.periods = periods_for('2026-09-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00', 'both')
        self.manifest = {'users': ['u'], 'periods': self.periods}
        self.summary = [{'user_id': 'u', 'period': self.periods[-1], 'rating': None, 'overall_exact': '90',
                         'overall_score': '90.0', 'evaluation_complete': True}]
        self.packets, self.results, self.windows = {}, [], []
        for index, period in enumerate(self.periods[:-1]):
            pid, eid = str(index), 'event-' + str(index)
            self.packets[pid] = {'user_id': 'u', 'boundary': 'confirmed', 'period_id': period['period_id'], 'active_human_event_ids': [eid],
                                 'events': [{'event_id': eid, 'text': '真实工作'}],
                                 'materials': [{'event_id': eid, 'classification': 'human', 'human_spans': [[0, 4]]}]}
            self.results.append({'packet_id': pid, 'semantic_complete': True, 'boundary': 'confirmed'})
            self.windows.append({'user_id': 'u', 'period_id': period['period_id'], 'coverage': 'confirmed',
                'coverage_basis': '合成约定完整采集且纳入评价，不是真实核查', 'activity': 'substantive', 'reason': '合成真实工作约定',
                'reviewer_id': 'initial', 'review_context': {'team_id': 't', 'agent_id': 'a', 'context_id': 'initial'},
                'packet_ids': [pid], 'evidence': [{'event_id': eid, 'start': 0, 'end': 4, 'quote': '真实工作'}]})

    def apply(self):
        self.path.write_text(json.dumps({'policy_version': VERSION, 'windows': self.windows}))
        return apply_participation(self.summary, self.manifest, self.packets, self.results, self.path)

    def no_use(self, index):
        self.packets.pop(str(index))
        self.windows[index].update(activity='none', packet_ids=[], evidence=[])

    def test_monday_publication_and_partial_weeks_are_explicit(self):
        weeks = self.periods[:-1]
        self.assertEqual([p['publish_on'] for p in weeks], ['2026-09-07', '2026-09-14', '2026-09-21', '2026-09-28', '2026-10-05'])
        self.assertEqual([p['complete_week'] for p in weeks], [False, True, True, True, False])
        self.assertEqual(weeks[0]['calendar_start'][:10], '2026-08-31')
        self.assertEqual(weeks[-1]['calendar_end'][:10], '2026-10-05')

    def test_one_substantive_task_in_each_week_is_enough(self):
        self.assertTrue(self.apply()['complete'])
        row = self.summary[0]
        self.assertEqual(row['participation_exact'], '100')
        self.assertEqual(row['monthly_candidate_exact'], '91')
        self.assertEqual(row['overall_score'], '90.0')
        self.assertEqual(row['rating']['id'], 'L4')

    def test_no_use_week_reduces_month_without_inventing_zero_performance(self):
        for i in range(1, 5): self.no_use(i)
        self.apply(); row = self.summary[0]
        self.assertEqual(row['participation_exact'], '20')  # 月首片段占6/30天。
        self.assertEqual(row['monthly_candidate_exact'], '83')
        self.assertEqual(row['rating']['id'], 'L3')
        self.assertEqual(row['overall_exact'], '90')

    def test_month_end_three_days_does_not_count_as_full_week(self):
        self.no_use(4); self.apply()
        self.assertEqual(self.summary[0]['participation_exact'], '90')

    def test_collection_gap_is_unknown_not_zero(self):
        self.windows[2]['coverage'] = 'incomplete'
        self.assertFalse(self.apply()['complete'])
        self.assertIsNone(self.summary[0]['participation_score'])
        self.assertIsNone(self.summary[0]['monthly_candidate_score'])
        self.assertIsNone(self.summary[0]['rating'])

    def test_missing_usage_declaration_does_not_infer_inactivity(self):
        apply_participation(self.summary, self.manifest, self.packets, self.results)
        self.assertIsNone(self.summary[0]['rating'])

    def test_no_complete_performance_is_not_imputed(self):
        self.summary[0]['overall_exact'] = None
        self.apply()
        self.assertEqual(self.summary[0]['participation_exact'], '100')
        self.assertIsNone(self.summary[0]['monthly_candidate_score'])

    def test_usage_cannot_claim_none_when_human_activity_exists(self):
        self.windows[0].update(activity='none', packet_ids=[], evidence=[])
        with self.assertRaises(ContractError): self.apply()

    def test_usage_does_not_require_scoring_to_be_finished(self):
        self.results[0]['semantic_complete'] = False
        self.assertTrue(self.apply()['complete'])

    def test_usage_still_requires_confirmed_parent_boundary(self):
        self.packets['0']['boundary'] = 'unresolved'
        with self.assertRaises(ContractError): self.apply()

    def test_usage_cannot_borrow_quote_from_another_week(self):
        self.windows[0]['evidence'][0]['event_id'] = 'event-1'
        with self.assertRaises(ContractError): self.apply()

    def test_unclosed_audit_cannot_publish_candidate_rating(self):
        self.summary[0]['evaluation_complete'] = False
        self.apply()
        self.assertEqual(self.summary[0]['monthly_candidate_exact'], '91')
        self.assertIsNone(self.summary[0]['rating'])

    def test_completed_limited_review_can_show_separate_trial_rating(self):
        self.summary[0].update(evaluation_complete=False, semantic_review_complete=True, semantic_audit_closed=True)
        self.apply()
        self.assertEqual(self.summary[0]['trial_rating']['id'], 'L4')
        self.assertIsNone(self.summary[0]['rating'])
        self.assertEqual(self.summary[0]['rating_status'], '观察试评月级，材料缺证单列')

    def test_trial_rating_display_cannot_round_across_level_boundary(self):
        self.summary[0].update(overall_exact='3748/45', evaluation_complete=False,
                               semantic_review_complete=True, semantic_audit_closed=True)
        self.apply()
        row = self.summary[0]
        self.assertEqual(row['monthly_candidate_exact'], '2124/25')
        self.assertEqual(row['trial_rating']['id'], 'L3')
        self.assertEqual(row['monthly_candidate_score'], '84.96')

    def test_unread_or_unaudited_review_cannot_show_trial_rating(self):
        for read, audited in [(False, True), (True, False)]:
            self.summary[0].update(evaluation_complete=False, semantic_review_complete=read, semantic_audit_closed=audited)
            self.apply()
            self.assertIsNone(self.summary[0]['trial_rating'])

    def test_public_report_hides_obvious_credentials(self):
        self.assertNotIn('abc123', safe_quote('password=abc123'))
        self.assertNotIn('abcdef', safe_quote('Bearer abcdef'))

    def test_usage_quote_cannot_use_system_part_of_mixed_prompt(self):
        self.packets['0']['materials'][0]['human_spans'] = [[2, 4]]
        with self.assertRaises(ContractError): self.apply()

    def test_month_only_input_cannot_claim_participation_complete(self):
        self.manifest['periods'] = self.periods[-1:]
        self.windows = []
        self.assertFalse(self.apply()['complete'])

    def test_complete_demo_binds_independent_audit_to_usage_version(self):
        from .examples import run_demo
        from .reporting import check_or_score
        directory = self.path.parent / 'demo'
        result = run_demo(directory)
        self.assertTrue(result['report_complete'])
        run = directory / 'run'
        data = json.loads((run / 'scored/report-data.json').read_text())
        month = next(r for r in data['summary'] if r['period']['kind'] == 'monthly')
        self.assertEqual(month['monthly_candidate_exact'], '74')
        page = (run / 'scored/report.html').read_text()
        self.assertIn('合成接口迁移演示', page)
        self.assertIn('合成演示：', page)
        self.assertIn('目标是兼容旧导出接口', (run / 'scored/analysis/report.html').read_text())
        self.assertIn('周内片段', page)
        usage = json.loads((run / 'usage.json').read_text())
        usage['windows'][0]['reason'] = '改动参与判断，旧复核不得自动通过'
        (run / 'usage.json').write_text(json.dumps(usage))
        with self.assertRaises(ContractError):
            check_or_score(run, run / 'reviews.jsonl', run / 'stale', audit_path=run / 'audits.jsonl', usage_path=run / 'usage.json', report=True)


if __name__ == '__main__':
    unittest.main()
