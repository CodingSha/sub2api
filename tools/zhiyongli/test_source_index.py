"""原文可达、反证补读及隔离评分回归；合成模型不证明真实评分质量。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bounded_read import raw_size
from navigation_access import Access
from pipeline import evaluate_week, received, Paused
from runtime import digest
from source_index import SourceIndex, build_view, MODE, open_navigation
from test_execution_paths import packet, record
from test_single_pass import Client as SingleClient
from test_candidate_segments import SegmentClient


def source():
    value = packet('2026-09-07T00:00:00+08:00', '2026-09-14T00:00:00+08:00')
    value['events'][0].update(source_user_id='test', timestamp_basis='audit_capture')
    value['events'].append(dict(event='long', text='工具原文\\n\x00😀' * 12000,
        actor_class='context', at='2026-09-07T10:00:00+08:00', session_id='other',
        request_id='other', source_user_id='test', protocol_role='tool',
        timestamp_unknown=False, timestamp_basis='audit_capture', truncated=False))
    value['events'].append(dict(event='failure', text='真实反例夹具：测试未通过，不能认定已验收。',
        actor_class='context', at='2026-09-07T10:00:00+08:00', session_id='test',
        request_id='test', source_user_id='test', protocol_role='tool',
        timestamp_unknown=False, timestamp_basis='audit_capture', truncated=False))
    return value


class Client(SingleClient):
    evidence_first = True
    merge_goals = SegmentClient.merge_goals

    def read_page(self, *args, **kwargs):
        raise AssertionError('证据优先路径不应先执行模型全读')

    def navigate_context(self, *args, **kwargs):
        raise AssertionError('程序索引不应调用模型建导航')

    def identify(self, value, receipt):
        if receipt.exists():
            return json.loads(receipt.read_text())
        access = value['context_access']
        if not access['raw_read_fragments']:
            self.calls += 1
            target = next(n for n in access['related_sources']['nodes'] if n['event_id'] == 'failure')
            return record(dict(materials={}, goals=[], needs_nodes=[dict(node_id=target['node_id'], mode='raw')]), receipt)
        assert any(e['text'] == '真实反例夹具：测试未通过，不能认定已验收。' for e in value['events'])
        result = received(super().identify(value, receipt))
        result['goals'][0]['context_event_ids'] = [next(e['event'] for e in value['events'] if e.get('source_event') == 'failure')]
        return record(result, receipt)


class SourceIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_every_character_and_empty_event_accessible_without_model_reading(self):
        value = source(); value['events'].append(dict(value['events'][-1], event='empty', text=''))
        before = digest(value); nav = SourceIndex(self.root, value); nav.build()
        leaves = json.loads((self.root/'leaves.json').read_text())['node_ids']
        actual = {e['event']: [] for e in value['events']}
        for key in leaves:
            node = nav.read([key], raw=True)['nodes'][0]
            actual[node['event_id']].append(node['text'])
            self.assertLess(raw_size(node), 14000)
        self.assertEqual({e['event']: e['text'] for e in value['events']}, {k: ''.join(v) for k, v in actual.items()})
        self.assertEqual(before, digest(value))
        self.assertFalse(list(self.root.glob('*.receipt.json')))

    def test_reopen_identical_index_and_refuse_changed_person_text_or_scope(self):
        value = source(); nav = SourceIndex(self.root, value); tree = nav.build()
        stamp = (self.root/'tree.json').stat().st_mtime_ns
        self.assertEqual(tree, open_navigation(self.root, value).build())
        self.assertEqual(stamp, (self.root/'tree.json').stat().st_mtime_ns)
        for key, changed in [('user_id', 'other'), ('until', '2026-09-15T00:00:00+08:00')]:
            wrong = copy.deepcopy(value); wrong[key] = changed
            with self.assertRaises(ValueError):
                SourceIndex(self.root, wrong)
        wrong = copy.deepcopy(value); wrong['events'][-1]['text'] = '被改写'
        with self.assertRaises(ValueError):
            SourceIndex(self.root, wrong)

    def test_modified_live_source_or_node_is_rejected(self):
        value = source(); nav = SourceIndex(self.root, value); nav.build()
        key = json.loads((self.root/'leaves.json').read_text())['node_ids'][-1]
        value['events'][-1]['text'] = '改为成功'
        with self.assertRaisesRegex(ValueError, '已变化'):
            nav.read([key], raw=True)
        with nav.connect() as db:
            db.execute('UPDATE nodes SET body=? WHERE id=?', ('{}', key))
        with self.assertRaisesRegex(ValueError, '签名'):
            nav.read([key])

    def test_related_lookup_requires_person_session_and_request_and_retains_truncation(self):
        value = source(); value['events'][-1]['truncated'] = True
        nav = SourceIndex(self.root, value); nav.build()
        related = nav.related(dict(events=[value['events'][0]]))
        self.assertEqual(1, related['total_fragments'])
        self.assertEqual('failure', related['nodes'][0]['event_id'])
        self.assertTrue(related['nodes'][0]['truncated'])
        for field in ['source_user_id', 'session_id', 'request_id']:
            event = dict(value['events'][0]); event[field] = None
            self.assertEqual(0, nav.related(dict(events=[event]))['total_fragments'])

    def test_links_are_bounded_and_partial_is_explicit(self):
        value = source(); value['events'][-1]['text'] *= 8000
        nav = SourceIndex(self.root, value); nav.build()
        related = nav.related(dict(events=[value['events'][0]]))
        self.assertGreater(related['total_fragments'], 24)
        self.assertEqual(24, len(related['nodes']))
        self.assertFalse(related['complete'])

    def test_program_directory_compaction_keeps_all_raw_evidence_and_access(self):
        value=source();view=build_view(value,self.root)
        nav=open_navigation(self.root/'context-navigation',value)
        access=Access(nav,self.root/'access',initial_directory=True)
        current=access.start(view)
        for observation in current['context_access']['observations']:
            self.assertNotIn('value',observation['node'])
        key=next(n['node_id'] for n in current['context_access']['related_sources']['nodes'] if n['event_id']=='failure')
        expanded=access.expand(current,[dict(node_id=key,mode='raw')],'counterevidence')
        self.assertTrue(all(e in expanded['events'] for e in current['events']))
        self.assertTrue(set(current['context_access']['available_node_ids'])<=set(expanded['context_access']['available_node_ids']))
        self.assertIn('真实反例夹具：测试未通过，不能认定已验收。',[e['text'] for e in expanded['events']])
        access.verify(expanded)

    def test_raw_background_stays_context_and_unexposed_nodes_cannot_be_requested(self):
        value = source(); view = build_view(value, self.root)
        nav = open_navigation(self.root/'context-navigation', value)
        access = Access(nav, self.root/'access', initial_directory=True)
        start = access.start(view)
        key = start['context_access']['related_sources']['nodes'][0]['node_id']
        end = access.expand(start, [dict(node_id=key, mode='raw')], 'failure')
        event = next(e for e in end['events'] if e.get('source_event') == 'failure')
        self.assertEqual('context', event['actor_class']); self.assertEqual([], event['human_spans'])
        wrong = copy.deepcopy(start); wrong['context_access']['available_node_ids'].append('0'*64)
        with self.assertRaisesRegex(ValueError, '未绑定'):
            access.expand(wrong, [dict(node_id='0'*64, mode='raw')], 'forged')

    def test_single_pass_pipeline_reads_counterevidence_and_resumes_without_new_requests(self):
        value = source(); client = Client()
        # 展示案例是可选项；这里专验背景读取、完整分母与同次评分。
        with patch('single_pass.select_case', return_value=None):
            result = evaluate_week(value, self.root/'week', client)
            calls = client.calls
            self.assertEqual(result, evaluate_week(value, self.root/'week', client))
        self.assertEqual(3, calls); self.assertEqual(calls, client.calls)
        self.assertEqual('80.0', result['reports'][0]['score'])
        unit = json.loads((self.root/'week/unit.json').read_text())
        self.assertTrue(any(e['event'] == 'long' for e in unit['events']))
        self.assertEqual(MODE, json.loads((self.root/'week/input.json').read_text())['source_access_mode'])
        self.assertFalse((self.root/'week/reading').exists())

    def test_incomplete_display_case_does_not_block_full_scoring_or_claim_unseen_records(self):
        from single_pass import select_case
        value=source();client=Client()
        value['events'].append(dict(value['events'][1],event='unrelated-long'))
        # 同请求的整条工具正文超过首轮背景容量，但案例自身仍在展示上限内。
        value['events'][1].update(session_id='test',request_id='test',content_kind='text',text='完整工具过程。'*2500)
        result=evaluate_week(value,self.root/'week',client)
        unit=json.loads((self.root/'week/unit.json').read_text())
        prior=select_case(unit)
        selection=json.loads((self.root/'week/single-pass-case-selection.json').read_text())
        self.assertIsNotNone(prior)
        self.assertFalse(set(prior['record_ids'])<=set(selection['visible_event_ids']))
        self.assertIsNone(json.loads((self.root/'week/single-pass-case.json').read_text()))
        self.assertEqual('80.0',result['reports'][0]['score'])
        self.assertIsNone(result['reports'][0].get('task_review'))
        calls=client.calls
        self.assertEqual(result,evaluate_week(value,self.root/'week',client))
        self.assertEqual(calls,client.calls)

    def test_complete_visible_case_remains_in_report(self):
        result=evaluate_week(source(),self.root/'week',Client())
        case=json.loads((self.root/'week/single-pass-case.json').read_text())
        selection=json.loads((self.root/'week/single-pass-case-selection.json').read_text())
        self.assertIsNotNone(case)
        self.assertTrue(set(case['record_ids'])<=set(selection['visible_event_ids']))
        self.assertEqual(case['record_ids'],[r['id'] for r in result['reports'][0]['task_review']['cases'][0]['records']])

    def test_old_mode_and_shared_state_cannot_be_silently_reused(self):
        client = Client(); value = source()
        with self.assertRaisesRegex(Paused, '独立状态'):
            evaluate_week(value, self.root/'week', client, shared_root=self.root/'shared')
        self.assertEqual(0, client.calls)
        with patch('single_pass.select_case', return_value=None):
            evaluate_week(value, self.root/'week', client)
        client.evidence_first = False
        with self.assertRaisesRegex(Paused, '已冻结'):
            evaluate_week(value, self.root/'week', client)

    def test_unknown_identification_stops_without_scoring_or_automatic_resend(self):
        class UnknownClient(Client):
            accepts_revision_feedback = True
            def identify(self, value, receipt, feedback=None):
                self.calls += 1
                return dict(status='unknown')
            def call(self, *args, **kwargs):
                raise AssertionError('未知请求后不得进入评分')
        client = UnknownClient()
        with self.assertRaises((ValueError, RuntimeError)):
            evaluate_week(source(), self.root/'week', client)
        self.assertEqual(1, client.calls)
        self.assertFalse((self.root/'week/bundle.json').exists())


if __name__ == '__main__':
    unittest.main()
