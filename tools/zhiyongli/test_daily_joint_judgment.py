import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import jsonschema

from daily_assessment import evaluate
from daily_export import bundle
from daily_joint_judgment import assess, candidate, numbered_tasks
from model_config import ModelConfig
from pipeline import Paused
from request_budget import RequestBudget
from review_schema import check_local_schema
from runtime import CompanyClient
from test_daily_assessment import row
from workday_scope import daily_window
from worker import JobClient


class JointDailyTests(unittest.TestCase):
    def setUp(self):
        self.person = row('u1', 'user', '先生成插件清单。之后调试。')
        self.reply = row('a1', 'assistant', '已生成清单，调试时复现了异常。')
        self.tasks = [dict(id='task-1', records=[self.person, self.reply], workflow_index=[])]
        self.result = dict(needs_records=[], items={},
            task_summaries={'task-1': dict(title='插件检查', goal='生成并调试', outcome='AI报告异常', workflow=[])})
        self.result['items']['J2'] = dict(band=70, negative=None, evidence=[dict(fragment_id='q2',
            claim='你安排生成清单后调试。', origin='personal')])

    def test_sufficient_material_is_assessed_with_one_request(self):
        seen = []

        def send(client, root, name, data, schema, instructions):
            seen.append(name)
            check_local_schema(schema)
            jsonschema.validate(self.result, schema)
            person, reply = data['tasks'][0]['records']
            self.assertEqual(''.join(text for _, text in person['text_parts']), self.person['text'])
            self.assertEqual(reply, self.reply)
            self.assertTrue(client.daily_natural_json)
            return self.result

        value, read = assess(SimpleNamespace(), None, self.tasks, {}, send)
        self.assertEqual(seen, ['joint-assessment'])
        self.assertEqual(read, [])
        self.assertEqual(value['items']['J2']['band'], 'reasonable')
        self.assertIsNone(value['items']['E1']['band'])
        self.assertNotIn('复现', value['items']['J2']['basis'])

    def test_rejects_ai_quote_or_same_span_scored_twice(self):
        evidence = self.result['items']['J2']['evidence'][0]
        _, fragments = numbered_tasks(self.tasks)
        evidence['fragment_id'] = 'a1'
        with self.assertRaisesRegex(ValueError, '缺少本人原文'):
            candidate(self.result, self.tasks, fragments)
        evidence['fragment_id'] = 'q2'
        self.result['items']['E1'] = dict(band=70, negative=None,
            evidence=[dict(evidence, claim='你指定了交互顺序。')])
        with self.assertRaisesRegex(ValueError, '同段原话跨项重叠'):
            candidate(self.result, self.tasks, fragments)

    def test_tool_readback_is_bounded_to_one_round_and_selected_records(self):
        tool = row('tool1', 'tool', '已执行调试。复现了异常。', task_id='task-1')
        request = dict(needs_records=[dict(record_id=tool['id'], start=6, chars=6)], items=None, task_summaries=None)
        calls = []

        def send(client, root, name, data, schema, instructions):
            calls.append(name)
            check_local_schema(schema)
            if name == 'joint-assessment':
                self.assertFalse(data['tool_directory']['preview_is_evidence'])
                jsonschema.validate(request, schema)
                return request
            self.assertEqual(data['read_records'][0]['text'], tool['text'][6:12])
            self.assertEqual(data['read_records'][0]['role'], 'tool')
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(request, schema)
            jsonschema.validate(self.result, schema)
            return self.result

        value, read = assess(SimpleNamespace(), None, self.tasks, {tool['id']: tool}, send)
        self.assertEqual(calls, ['joint-assessment', 'joint-assessment-final'])
        self.assertEqual(len(read), 1)
        self.assertEqual(value['items']['J2']['band'], 'reasonable')

    def test_new_candidate_directory_cannot_bypass_original_request_budget(self):
        with TemporaryDirectory() as target:
            root = Path(target)
            original = RequestBudget(root / 'original', 20, 20)
            for index in range(20):
                original.reserve(original.root / f'{index}.receipt.json', str(index))
            before = original.path.read_bytes()
            output = root / 'candidate'
            output.mkdir()
            config = ModelConfig('https://ai.apsatcom.com', root / 'unused-key')
            client = JobClient(config, output, 20, new_request_limit=2, budget_root=original.root)
            with patch.object(CompanyClient, '_call') as transport, self.assertRaises(Paused) as error:
                client._call({}, output / 'joint-assessment.receipt.json')
            self.assertEqual(error.exception.reason_code, 'request_budget_exhausted')
            transport.assert_not_called()
            self.assertEqual(original.path.read_bytes(), before)
            self.assertFalse((output / 'request-budget.json').exists())

    def test_selected_packet_reaches_report_but_cannot_be_exported(self):
        day = '2026-11-18'
        packet = dict(user_id=1, user_email='person@example.test', workday_scope=daily_window(day))
        groups = [dict(session='session', records=[self.person, self.reply])]
        plan = dict(candidates=[dict(window_id='w1', title='插件检查', goal='生成并调试')])
        with TemporaryDirectory() as target, patch('daily_assessment.prepare', return_value=(groups, {}, {})), \
                patch('daily_assessment.checked_call', side_effect=[plan, self.result]) as send:
            root = Path(target)
            metrics = evaluate(packet, day, root, SimpleNamespace(reasoning_effort=None), joint_candidate=True)
            result = json.loads((root / 'report.json').read_text())
            self.assertEqual([request.args[2] for request in send.call_args_list], ['task-plan', 'joint-assessment'])
            self.assertEqual(result['score'], '70.0')
            self.assertEqual(metrics['quality_status'], 'joint_candidate_unvalidated')
            with self.assertRaisesRegex(ValueError, '尚未完成'):
                bundle(packet, day, root, 'deepseek-v4.1-flash-local')
