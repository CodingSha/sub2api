"""v12验证：隔离旧版、背景证据时点、工具定位与执行失败不能变评分。"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import contextual_review as candidate
from . import test_dsh_v11 as fixtures


class ContextTests(unittest.TestCase):
    def setUp(self):
        attributes = {
            candidate.batch: ['VERSION', 'RUBRIC', 'EXECUTION_VERSION', 'INSTRUCTIONS', 'check_review', 'packet'],
            candidate.serial: ['VERSION', 'check_review'],
            candidate.base: ['VERSION'],
            candidate.scoring: ['VERSION', 'RUBRIC'],
        }
        saved = [(module, name, getattr(module, name)) for module, names in attributes.items() for name in names]
        self.addCleanup(lambda: [setattr(module, name, value) for module, name, value in saved])
        candidate.activate()
        f = fixtures.HandoffTests()
        f.setUp()
        self.unit, self.value = f.u, f.a
        self.unit['source_groups'] = []
        self.value['context_assessments'] = [dict(
            period_ids=[p['period_id'] for p in self.unit['periods']],
            effective_request='解释给定对象', necessary_scope='解释概念即可',
            execution_scope='没有必须借助工具的环境依赖', scope_changes='没有新增范围',
            evidence=[copy.deepcopy(f.a['usage'][0]['evidence'][0])],
            boundary=dict(status='consistent', reason='本人指代对象可核对', evidence=f.a['usage'][0]['evidence']),
            task_complexity=dict(status='ordinary', reason='无已证条件交互', evidence=f.a['usage'][0]['evidence']))]

    def test_old_version_rejected(self):
        self.value['policy_version'] = candidate.OLD_VERSION
        with self.assertRaisesRegex(ValueError, 'v12评分版本'):
            candidate.check_review(self.unit, self.value)

    def test_background_missing_is_not_filled(self):
        self.value.pop('context_assessments')
        with self.assertRaisesRegex(ValueError, '缺模型任务背景'):
            candidate.check_review(self.unit, self.value)

    def test_context_cannot_use_future_evidence(self):
        self.unit['events'][0]['at'] = '2026-10-01T00:00:00+08:00'
        with self.assertRaisesRegex(ValueError, '未来'):
            candidate.check_review(self.unit, self.value)

    def test_context_must_cover_each_period_once(self):
        self.value['context_assessments'] *= 2
        with self.assertRaisesRegex(ValueError, '每期间恰一次'):
            candidate.check_review(self.unit, self.value)

    def test_context_evidence_must_exist(self):
        self.value['context_assessments'][0]['evidence'] = [dict(event='e1', quote='编造的新引文')]
        with self.assertRaisesRegex(ValueError, '引文不逐字'):
            candidate.check_review(self.unit, self.value)

    def test_valid_context_preserves_behavior_score(self):
        result = candidate.check_review(self.unit, self.value)
        self.assertEqual(result[0]['cells'][0]['band'], 100)
        self.assertEqual(result[0]['policy_version'], candidate.VERSION)

    def test_tool_metadata_does_not_supply_judgment(self):
        self.unit['events'][0]['session_id'] = 'session'
        tool = copy.deepcopy(self.unit['events'][0])
        tool.update(event='tool', text='Tool: read\nCall ID: c1\nType: function_call\n\n{"path":"a.py"}',
                    human_spans=[], actor_class='tool')
        self.unit['events'].append(tool)
        data = dict(policy_version=candidate.VERSION, tasks={'t1':self.unit}, source_groups={})
        with patch.object(candidate.base, 'task', return_value=self.unit):
            _, pack, _ = candidate.packet(data, 't1')
        raw = pack['raw_material']
        rows = [dict(zip(raw['context_columns'], row)) for row in raw['context_index']]
        self.assertEqual(rows[0]['tool_name'], 'read')
        self.assertEqual(rows[0]['call_id'], 'c1')
        self.assertNotIn('context_assessments', pack)
        self.assertNotIn('task_complexity', pack)

    def test_restricted_packet_does_not_read(self):
        self.unit['local_only'] = True
        with patch.object(candidate.base, 'task', side_effect=AssertionError('不能读受限原文')):
            with self.assertRaisesRegex(ValueError, '受限'):
                candidate.packet(dict(tasks={'t1':self.unit}), 't1')


if __name__ == '__main__':
    unittest.main()
