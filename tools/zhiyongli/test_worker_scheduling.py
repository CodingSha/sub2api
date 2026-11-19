"""日常积压与封口周评共存时不饿死；不改变每轮或全局预算。"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from worker import run_turn


class WorkerSchedulingTests(unittest.TestCase):
    def test_unending_daily_backlog_cannot_starve_assessment_and_reverse(self):
        history=[]
        preparations=SimpleNamespace(tick=Mock(side_effect=lambda *a: history.append('daily') or True))
        client=SimpleNamespace(preparations=preparations,schedule_enabled=True)
        priority=True
        with patch('worker.tick',side_effect=lambda *a: history.append('week') or True) as weekly:
            for _ in range(20):
                worked,priority=run_turn(None,None,None,client,None,[999],3000,1,preparation_first=priority)
                self.assertTrue(worked)
        self.assertEqual(['daily','week']*10,history)
        self.assertEqual(10,weekly.call_count)
        self.assertEqual(10,preparations.tick.call_count)
        self.assertTrue(all(c.args[-2:]==(3000,1) for c in preparations.tick.call_args_list))

    def test_empty_preferred_queue_falls_back_without_wasting_turn(self):
        preparations=SimpleNamespace(tick=Mock(return_value=False))
        client=SimpleNamespace(preparations=preparations,schedule_enabled=True)
        with patch('worker.tick',return_value=True) as weekly:
            self.assertEqual((True,True),run_turn(None,None,None,client,None,[],preparation_first=True))
            self.assertEqual(1,weekly.call_count)
        preparations.tick=Mock(return_value=True)
        with patch('worker.tick',return_value=False) as weekly:
            self.assertEqual((True,False),run_turn(None,None,None,client,None,[],preparation_first=False))
            self.assertEqual(1,weekly.call_count)

    def test_disabled_schedule_does_not_run_daily_and_empty_turn_stays_idle(self):
        preparations=SimpleNamespace(tick=Mock(side_effect=AssertionError('关闭后不能领取日常任务')))
        client=SimpleNamespace(preparations=preparations,schedule_enabled=False)
        with patch('worker.tick',return_value=False):
            self.assertEqual((False,True),run_turn(None,None,None,client,None,[]))
        preparations.tick.assert_not_called()
        with patch('worker.tick',return_value=True) as weekly:
            self.assertEqual((True,True),run_turn(None,None,None,SimpleNamespace(),None,[]))
            weekly.assert_called_once()

    def test_daily_error_is_not_swallowed_or_replaced_with_weekly_success(self):
        preparations=SimpleNamespace(tick=Mock(side_effect=ValueError('合成来源损坏')))
        client=SimpleNamespace(preparations=preparations,schedule_enabled=True)
        with patch('worker.tick') as weekly,self.assertRaisesRegex(ValueError,'来源损坏'):
            run_turn(None,None,None,client,None,[])
        weekly.assert_not_called()


if __name__=='__main__':unittest.main()
