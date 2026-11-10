"""法定调休、公司例外和周末处理窗口；仅校验计划，不触发评审。"""
from datetime import date
import json
from pathlib import Path
import unittest

from work_calendar import Calendar


class WorkCalendarTests(unittest.TestCase):
    def setUp(self):
        self.document=json.loads((Path(__file__).resolve().parents[2]/'deploy/zhiyongli-calendars/cn-2026.json').read_text())
        self.calendar=Calendar([self.document])

    def test_normal_week_has_54_hours(self):
        p=self.calendar.week_plan('2026-11-02')
        self.assertEqual(5,len(p['working_dates']))
        self.assertEqual('2026-11-07T00:00:00+08:00',p['cutoff'])
        self.assertEqual('2026-11-07T02:00:00+08:00',p['freeze_at'])
        self.assertEqual('2026-11-09T08:00:00+08:00',p['candidate_due_at'])
        self.assertEqual(54,p['processing_hours'])

    def test_sunday_makeup_is_work_and_leaves_six_hours(self):
        p=self.calendar.week_plan('2026-09-14')
        self.assertIn('2026-09-20',p['working_dates'])
        self.assertEqual('2026-09-21T02:00:00+08:00',p['freeze_at'])
        self.assertEqual(6,p['processing_hours'])

    def test_holiday_and_next_workday_are_not_weekday_guesses(self):
        self.assertFalse(self.calendar.working(date(2026,9,25)))
        self.assertTrue(self.calendar.working(date(2026,10,10)))
        p=self.calendar.week_plan('2026-04-27')
        self.assertEqual('2026-05-01T00:00:00+08:00',p['cutoff'])
        self.assertEqual('2026-05-06T08:00:00+08:00',p['candidate_due_at'])

    def test_full_holiday_week_is_not_employee_inactivity(self):
        p=self.calendar.week_plan('2026-02-16')
        self.assertEqual('non_evaluation_week',p['state'])
        self.assertIsNone(p['cutoff'])

    def test_company_override_is_explicit_and_changes_signature(self):
        company=dict(version='company-1',approved_by='合成确认人',reason='合成公司例外',dates=[dict(date='2026-11-07',working=True,reason='合成补班')])
        c=Calendar([self.document],company);p=c.week_plan('2026-11-02')
        self.assertNotEqual(c.signature,self.calendar.signature)
        self.assertEqual('2026-11-08T02:00:00+08:00',p['freeze_at'])
        self.assertEqual(30,p['processing_hours'])
        company['dates'][0]['working']='true'
        with self.assertRaises(ValueError):Calendar([self.document],company)

    def test_unconfigured_year_blocks_even_next_due_date(self):
        with self.assertRaisesRegex(ValueError,'年份'):self.calendar.working(date(2027,1,1))
        with self.assertRaisesRegex(ValueError,'年份'):self.calendar.week_plan('2026-12-28')


if __name__=='__main__':unittest.main()
