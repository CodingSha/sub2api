"""首版观察资格边界与历史资料合同兼容；不是评分效度检验。"""
from datetime import datetime, timedelta
import unittest
from eligibility import decide
from release_scope import assess, signature


def records(tasks, days):
    return [dict(source_ref=str(i), occurred_at=day+'T10:00:00+08:00', task_keys=[str(i % tasks)])
            for i, day in enumerate(days)]


class EligibilityTests(unittest.TestCase):
    def test_month_long_task_and_cross_week_requirement(self):
        days = [(datetime(2026, 9, 1)+timedelta(days=i*2)).date().isoformat() for i in range(10)]
        events = records(1, days)
        refs = {d: [e['source_ref'] for e in events] for d in 'PMJE'}
        result = decide('month', events, refs)
        self.assertTrue(result['eligible'])
        self.assertEqual('long_task', result['route'])
        refs['E'] = ['0', '1']
        self.assertFalse(decide('month', events, refs)['eligible'])

    def test_no_activity_not_zero_or_complete(self):
        refs = {d: [] for d in 'PMJE'}
        self.assertFalse(decide('week', [], refs)['eligible'])
        events = records(5, ['2026-09-07', '2026-09-08', '2026-09-09', '2026-09-09', '2026-09-09'])
        refs = {d: [e['source_ref'] for e in events] for d in 'PMJE'}
        self.assertTrue(decide('week', events, refs)['eligible'])
        self.assertFalse(decide('week', events, refs, complete=False)['eligible'])

    def test_unknown_requires_other_periods_and_month_eligibility(self):
        gap = 'week-2026-09-28/P2：unobservable'
        decision = dict(id=signature(gap), source=gap, status='limitation', reason='局部资料未知不补分', basis=['other-reviewed-period'])
        computed = dict(source_gaps=[gap], exact='80', items={'P2':{'exact':'80'}})
        self.assertTrue(assess(computed, [decision], {'eligible':True})['gradeable'])
        for eligibility in (None, {'eligible':False}):
            with self.assertRaises(ValueError):
                assess(computed, [decision], eligibility)
        computed['items']['P2']['exact'] = None
        with self.assertRaises(ValueError):
            assess(computed, [decision], {'eligible':True})
        computed['items']['P2']['exact'] = '80'
        for state in ('pending','attribution_unclear'):
            gap = 'week-2026-09-28/P2：'+state
            decision.update(id=signature(gap),source=gap)
            computed['source_gaps'] = [gap]
            with self.assertRaises(ValueError):
                assess(computed, [decision], {'eligible':True})


if __name__ == '__main__':
    unittest.main()
