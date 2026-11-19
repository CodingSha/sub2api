"""单次装配的只读索引复用；完整记录与异常相同，不代替模型质量校准。"""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from runtime import digest
from scoring_pipeline.weekly_performance import task_context as context
from task_evidence import task_records
from test_task_evidence import fixture
import week_basis


def outcome(call):
    try:
        return ('value', call())
    except Exception as error:
        return ('error', type(error).__name__, str(error))


class TaskContextIndexTests(unittest.TestCase):
    def test_record_variants_preserve_full_values_and_raw_duplicate_semantics(self):
        for mode in ('plain', 'duplicate', 'unknown_linked', 'unknown_other', 'other_session',
                     'history', 'deadline', 'unpaired', 'other_person', 'alias', 'system',
                     'nul', 'credential', 'truncated', 'ambiguous', 'raw_response'):
            with self.subTest(mode=mode):
                unit = fixture()
                if mode == 'duplicate':
                    unit['events'].append(dict(unit['events'][3], text='重复标识保留末值', at='2026-09-09T10:00:00+08:00'))
                elif mode.startswith('unknown_'):
                    unit['events'].append(dict(unit['events'][3 if mode.endswith('linked') else 4],
                                               event='unknown', response_timestamp_unknown=True))
                elif mode == 'other_session':
                    unit['events'].append(dict(unit['events'][3], event='other-session', session_id='different'))
                elif mode in ('history', 'deadline'):
                    unit['events'].append(dict(unit['events'][3], event=mode,
                        at='2026-09-06T10:00:00+08:00' if mode == 'history' else unit['periods'][0]['end']))
                elif mode == 'unpaired':
                    unit['events'][0].pop('request_id')
                elif mode == 'other_person':
                    unit['events'].append(dict(unit['events'][0], event='other-person'))
                    unit['person_events'].append('other-person')
                elif mode == 'alias':
                    unit['events'][0]['event_id'] = 'canonical'
                    unit['events'].append(dict(unit['events'][0], event='alias', actor_class='source_context'))
                elif mode == 'system':
                    unit['events'].append(dict(unit['events'][1], event='system', actor_class='system'))
                elif mode in ('nul', 'credential'):
                    unit['events'][3]['text'] = '合成\x00反馈' if mode == 'nul' else 'password=synthetic-value'
                elif mode == 'truncated':
                    unit['events'][3]['truncated'] = True
                elif mode == 'ambiguous':
                    unit['events'][3]['response_ambiguous'] = True
                else:
                    if mode == 'raw_response':
                        unit['events'][3].update(actor_class='context', protocol_role='assistant', content_kind='raw_response')
                before = copy.deepcopy(unit)
                index = context.TaskContextIndex(unit)
                expected = task_records(unit, 'task')
                for _ in range(2):
                    self.assertEqual(expected, task_records(unit, 'task', context_index=index))
                self.assertEqual(before, unit)

    def test_lazy_sort_keeps_original_validation_error_priority(self):
        for mode in ('no_person', 'person_outside', 'missing_person', 'bad_period', 'bad_unrelated_time', 'duplicate_source',
                     'missing_events_duplicate_source'):
            with self.subTest(mode=mode):
                unit = fixture()
                unit['events'][-1]['at'] = 'bad-unrelated-time'
                if mode == 'no_person':
                    unit['person_events'] = []
                elif mode == 'person_outside':
                    unit['events'][0]['at'] = '2026-09-15T10:00:00+08:00'
                elif mode == 'missing_person':
                    unit['events'] = unit['events'][1:]
                elif mode == 'bad_period':
                    unit['periods'] = []
                elif mode in ('duplicate_source', 'missing_events_duplicate_source'):
                    unit['source_units'].append(copy.deepcopy(unit['source_units'][0]))
                    if mode == 'missing_events_duplicate_source':
                        unit.pop('events')
                index = context.TaskContextIndex(unit)
                expected = outcome(lambda: task_records(unit, 'task'))
                self.assertEqual('error', expected[0])
                self.assertEqual(expected, outcome(lambda: task_records(unit, 'task', context_index=index)))

    def test_direct_context_keeps_all_fields_and_revalidates_each_person_slice(self):
        unit = fixture(); index = context.TaskContextIndex(unit)
        self.assertEqual(context.task_context(unit), context.task_context(unit, context_index=index))
        isolated = dict(unit, person_events=['h2'])
        self.assertEqual(context.task_context(isolated), context.task_context(isolated, context_index=index))
        invalid = dict(unit, person_events=['missing'])
        self.assertEqual(outcome(lambda: context.task_context(invalid)),
                         outcome(lambda: context.task_context(invalid, context_index=index)))

    def test_foreign_event_list_cannot_reuse_index_even_with_same_contents(self):
        unit = fixture(); index = context.TaskContextIndex(unit)
        other = copy.deepcopy(unit)
        with self.assertRaisesRegex(ValueError, '不属于当前来源'):
            task_records(other, 'task', context_index=index)
        task_records(unit, 'task', context_index=index)
        unit['events'] = copy.deepcopy(unit['events'])
        with self.assertRaisesRegex(ValueError, '不属于当前来源'):
            task_records(unit, 'task', context_index=index)

    def test_record_outputs_are_independent_and_new_operation_rechecks_source(self):
        unit = fixture(); before = copy.deepcopy(unit); index = context.TaskContextIndex(unit)
        first = task_records(unit, 'task', context_index=index)
        first[1][0]['text'] = '调用方修改展示结果'
        first[1][0]['limitations'].append('调用方新增限制')
        self.assertEqual(before, unit)
        self.assertEqual(task_records(unit, 'task'), task_records(unit, 'task', context_index=index))
        # 索引不跨装配保存；下一操作重新解析已改变的来源时间。
        unit['events'][-1]['at'] = 'changed-invalid-time'
        fresh = context.TaskContextIndex(unit)
        self.assertEqual(outcome(lambda: task_records(unit, 'task')),
                         outcome(lambda: task_records(unit, 'task', context_index=fresh)))

    def test_multi_task_build_and_model_payload_are_unchanged(self):
        unit = fixture()
        unit['source_keys'] = ['first', 'second']
        unit['source_units'] = [dict(key=key, title='合成字段核对', purpose='核对约定对象',
            source_kind='task', person_events=[eid]) for key, eid in zip(unit['source_keys'], unit['person_events'])]
        report = dict(eligibility=dict(observations=[dict(task_keys=unit['source_keys'])]),
            workday_scope=dict(working_dates=['2026-09-08']),
            items=[dict(item_code=code, state='observed', score=70, reason='既有合成判断', criterion='固定标准')
                   for code in week_basis.ITEMS])
        before = copy.deepcopy((unit, report)); original = week_basis.role.records_for
        def uncached(unit, key, **kwargs):
            return original(unit, key)
        with patch.object(week_basis.role, 'records_for', side_effect=uncached):
            expected = week_basis.build(unit, report)
        actual = week_basis.build(unit, report)
        self.assertEqual(expected, actual); self.assertEqual(before, (unit, report))
        client = SimpleNamespace(config=SimpleNamespace(model='synthetic-model'))
        payloads = [week_basis.request(client, 'synthetic-basis', week_basis.GENERATE, data, week_basis.schema(data))
                    for data in (expected, actual)]
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(digest(payloads[0]), digest(payloads[1]))


if __name__ == '__main__':
    unittest.main()
