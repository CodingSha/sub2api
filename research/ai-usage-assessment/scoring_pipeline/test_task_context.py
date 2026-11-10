"""任务过程阅读的边界回归，不验证评分效度。"""
import unittest
from .weekly_performance.task_context import task_context
from .weekly_performance.scope022 import scope_units


def event(key, role, text, *, at='2026-09-08T12:00:00+08:00', request='r', session='s', **extra):
    return dict(event=key, actor_class=role, text=text, at=at,
                request_id=request, session_id=session, **extra)


def unit(events, persons=('proposal',)):
    return dict(key='任务', periods=[dict(period_id='week-2026-09-07',
        start='2026-09-07T00:00:00+08:00', end='2026-09-14T00:00:00+08:00')],
        events=events, person_events=list(persons), source_gaps=['附件正文缺失'])


class TaskContextTests(unittest.TestCase):
    def test_proposal_keeps_contradicting_feedback_without_old_quotes(self):
        value = task_context(unit([event('proposal', 'human', '只改一个仓库？'),
            event('reply', 'assistant', '不能保持原检索能力'),
            event('tool', 'tool', '实际查询返回不同')]))
        self.assertEqual([e['event'] for e in value['events']], ['proposal', 'reply', 'tool'])
        self.assertEqual(value['source_gaps'], ['附件正文缺失'])
        self.assertEqual(value['person_events'], ['proposal'])

    def test_request_identifier_cannot_cross_session(self):
        value = task_context(unit([event('proposal', 'human', '问题'),
            event('other', 'assistant', '另一会话的成功', session='other')]))
        self.assertEqual(value['other_context_events'], ['other'])
        self.assertEqual(len(value['events']), 1)

    def test_future_and_unknown_reply_do_not_fill_past_evaluation(self):
        value = task_context(unit([event('proposal', 'human', '方案'),
            event('future', 'assistant', '成功', at='2026-09-14T00:00:00+08:00'),
            event('unknown', 'assistant', '成功', response_timestamp_unknown=True)]))
        self.assertEqual([e['event'] for e in value['events']], ['proposal'])
        self.assertEqual(value['withheld_time_unknown'], ['unknown'])

    def test_prior_task_members_are_background_not_current_actions(self):
        value = task_context(unit([event('proposal', 'human', '继续方案'),
            event('prior', 'human', '原需求', at='2026-09-06T00:00:00+08:00', request='old'),
            event('prior-reply', 'assistant', '原约束', at='2026-09-06T00:01:00+08:00', request='old')]),
            task_person_events=['proposal', 'prior'])
        self.assertEqual(value['background_person_events'], ['prior'])
        self.assertEqual(value['person_events'], ['proposal'])
        self.assertEqual(len(value['events']), 3)

    def test_week_scope_preserves_gaps_from_every_task(self):
        first = dict(unit([event('proposal', 'human', '任务一')]),
                     key='任务一', user_id='test-user', title='任务一')
        second = dict(unit([event('second', 'human', '任务二')], persons=('second',)),
                      key='任务二', user_id='test-user', title='任务二',
                      source_gaps=[dict(item='M1', needed='方案反馈缺失')])
        week = scope_units([first, second], 'week-2026-09-07', evaluation_scope='person_week')
        self.assertEqual(week['source_gaps'], ['附件正文缺失', dict(item='M1', needed='方案反馈缺失')])
        self.assertEqual(set(week['person_events']), {'proposal', 'second'})
        self.assertEqual(week['source_keys'], ['任务一', '任务二'])


if __name__ == '__main__':
    unittest.main()
