"""任务解读阶段的有界夹具；无网络、无生产材料或产品写入。"""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import task_interpretation as stage
from test_task_evidence import fixture
from runtime import atomic, digest
from request_retry import read_receipt


class Client:
    def __init__(self, mutate=None):
        self.config = SimpleNamespace(model='deepseek-v4.1-flash-local', responses_url='http://sub2api:8080/v1/responses')
        self.calls = 0
        self.mutate = mutate or (lambda kind, value: value)

    def _call(self, payload, receipt):
        if receipt.exists():
            return read_receipt(receipt)
        self.calls += 1
        kind = payload['text']['format']['name']
        data = json.loads(payload['input'])
        if kind == 'zhiyongli_task_interpretation':
            value = dict(task_key=data['task_key'], outcome='记录中只有核对请求和反馈，尚无独立验收。',
                claims=[dict(text=t, item_codes=['J2'], record_ids=refs, limitations=[],
                    assessment=dict(kind='unresolved', reason='只记录可核事实，未确认外部完成。')) for t, refs in
                    [('目标是核对字段。', ['h1']), ('本人再次提出核对要求。', ['h2']), ('保存了AI反馈。', ['a2'])]],
                limitations=['部分回复为空，不推定外部完成。'])
        else:
            row = dict(verdict='supported', reason='与夹具中实际记录一致。', record_ids=['h1'])
            value = dict(seen_record_ids=[r['id'] for r in data['source']['records']],
                claims={key: dict(copy.deepcopy(row), record_ids=c['record_ids'])
                        for key,c in data['interpretation']['claims'].items()},
                checks={k: copy.deepcopy(row) for k in stage.CHECKS}, issues=[])
        value = self.mutate(kind, value)
        record = dict(status='received', request_sha256=digest(dict(endpoint=self.config.responses_url, payload=payload)),
            request_kind=kind, response=dict(status='completed', output=[dict(content=[dict(type='output_text', text=json.dumps(value))])]))
        atomic(receipt, record)
        return record


class Cases(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.unit = fixture()
        self.bundle = dict(schema_version=1, reports=[dict(user_email='one@test', period_type='week',
            source_digest='a' * 64, score=80, exact='80', score_status='scored',
            items=[dict(item_code=code, state='observed', reason='原周理由', score=80) for code in stage.ITEMS])])
        self.parent = self.root / 'accepted.json'
        atomic(self.parent, self.bundle)

    def tearDown(self):
        self.temp.cleanup()

    def run_stage(self, client):
        return stage.enrich(self.bundle, self.unit, self.parent, self.root / 'task', client)

    def test_two_receipts_full_records_and_score_unchanged(self):
        client = Client()
        value = self.run_stage(client)
        report = value['reports'][0]
        task = report.pop('task_review')
        self.assertEqual(value, self.bundle)
        self.assertEqual(client.calls, 2)
        self.assertEqual(task['status'], 'case_checked')
        self.assertEqual(task['week_judgments'], [])
        self.assertEqual({r['id'] for r in task['cases'][0]['records']}, {'h1', 'h2', 'a1', 'a2'})
        self.assertEqual(task['cases'][0]['context_status'], 'source_incomplete')
        authored = json.loads((self.root / 'task/authored.json').read_text())
        self.assertNotEqual(authored['generation_receipt_digest'], authored['review_receipt_digest'])
        self.assertEqual(authored['reviewer'], 'model_generated_separately_reviewed')

    def test_readonly_replay_verifies_payloads_without_writes(self):
        client = Client()
        result = self.run_stage(client)
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(stage.verify_supplement(self.bundle, self.unit, self.parent, self.root / 'task', client.config), result)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(client.calls, 2)

    def test_foreign_record_is_rejected_before_audit(self):
        def mutate(kind, value):
            if kind == 'zhiyongli_task_interpretation':
                value['claims'][0]['record_ids'] = ['foreign']
            return value
        client = Client(mutate)
        with self.assertRaises(ValueError):
            self.run_stage(client)
        self.assertEqual(client.calls, 1)
        self.assertFalse((self.root / 'task/manifest.json').exists())

    def test_missing_record_review_dispute_and_duplicate_claim_fail_closed(self):
        for mode in ('missing', 'dispute', 'duplicate'):
            with self.subTest(mode=mode):
                def mutate(kind, value):
                    if kind.endswith('_audit'):
                        if mode == 'missing': value['seen_record_ids'].pop()
                        elif mode == 'dispute': value['claims']['claim_0']['verdict'] = 'revise'
                        else: value['claims']['claim_1_extra'] = value['claims'].pop('claim_1')
                    return value
                folder = self.root / mode
                with self.assertRaises((ValueError, stage.Paused)):
                    stage.enrich(self.bundle, self.unit, self.parent, folder, Client(mutate))
                self.assertFalse((folder / 'manifest.json').exists())
                self.assertEqual(json.loads(self.parent.read_text()), self.bundle)

    def test_source_change_and_receipt_signature_drift_are_rejected(self):
        client = Client()
        self.run_stage(client)
        changed = copy.deepcopy(self.unit)
        changed['events'][0]['text'] += '变化'
        with self.assertRaises(ValueError):
            stage.verify_supplement(self.bundle, changed, self.parent, self.root / 'task', client.config)
        receipt = self.root / 'task/generation.receipt.json'
        record = json.loads(receipt.read_text());record['request_sha256'] = 'b' * 64;atomic(receipt, record)
        with self.assertRaises(ValueError):
            stage.verify_supplement(self.bundle, self.unit, self.parent, self.root / 'task', client.config)

    def test_four_claims_use_identical_input_and_required_output_keys(self):
        def mutate(kind, value):
            if kind == 'zhiyongli_task_interpretation':
                value['claims'].append(copy.deepcopy(value['claims'][2]))
            return value
        client = Client(mutate)
        self.run_stage(client)
        root = self.root / 'task'
        spec = json.loads((root / 'spec.json').read_text())
        audit = json.loads((root / 'audit.json').read_text())
        original = copy.deepcopy(spec)
        expected = {'claim_0', 'claim_1', 'claim_2', 'claim_3'}
        self.assertEqual(set(stage.audit_interpretation(spec)['claims']), expected)
        self.assertEqual(set(audit['claims']), expected)
        schema = stage.audit_schema({'records': [{'id': 'h1'}]}, spec)['properties']['claims']
        self.assertEqual(set(schema['required']), expected)
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(spec, original)

    def test_legacy_duplicate_indices_are_preserved_and_rejected(self):
        def mutate(kind, value):
            if kind == 'zhiyongli_task_interpretation':
                value['claims'].append(copy.deepcopy(value['claims'][2]))
            if kind.endswith('_audit'):
                rows = list(value['claims'].values())
                value['claims'] = [dict(row, index=index) for row, index in zip(rows, (1, 2, 3, 3))]
            return value
        client = Client(mutate)
        with self.assertRaises(ValueError):
            self.run_stage(client)
        raw = json.loads((self.root / 'task/audit.json').read_text())
        self.assertEqual([row['index'] for row in raw['claims']], [1, 2, 3, 3])
        self.assertFalse((self.root / 'task/manifest.json').exists())
        self.assertEqual(json.loads(self.parent.read_text()), self.bundle)

    def test_issues_and_incomplete_claim_evidence_still_block(self):
        for mode in ('issues', 'claim_evidence'):
            def mutate(kind, value):
                if kind.endswith('_audit'):
                    if mode == 'issues': value['issues'] = ['未确认外部验收。']
                    else: value['claims']['claim_1']['record_ids'] = ['h1']
                return value
            folder = self.root / mode
            with self.assertRaises((ValueError, stage.Paused)):
                stage.enrich(self.bundle, self.unit, self.parent, folder, Client(mutate))
            self.assertFalse((folder / 'manifest.json').exists())

    def test_unknown_does_not_send_new_request(self):
        client = Client()
        self.run_stage(client)
        receipt = self.root / 'task/generation.receipt.json'
        record = json.loads(receipt.read_text());record['status'] = 'unknown';record.pop('response');atomic(receipt, record)
        with self.assertRaises(stage.Paused):
            self.run_stage(client)
        self.assertEqual(client.calls, 2)

    def test_large_complete_task_is_not_truncated(self):
        self.unit['events'][0]['text'] = '大' * 70000
        self.unit['events'][0]['human_spans'] = [[0, 70000]]
        client = Client()
        with self.assertRaises(stage.Paused):
            self.run_stage(client)
        self.assertEqual(client.calls, 0)

    def test_chinese_credentials_are_redacted_and_record_hash_matches(self):
        event = self.unit['events'][0]
        event['text'] = '服务器ssh账密是demo-user/demo-pass，你先排查。'
        event['human_spans'] = [[0, len(event['text'])]]
        data = stage.transport(self.unit, self.bundle['reports'][0])
        self.assertNotIn('demo-user/demo-pass', json.dumps(data, ensure_ascii=False))
        for record in data['records']:
            self.assertEqual(stage.hashlib.sha256(record['text'].encode()).hexdigest(), record['text_hash'])

    def test_extra_score_field_and_missing_readonly_files_are_rejected(self):
        def mutate(kind, value):
            if kind == 'zhiyongli_task_interpretation': value['score'] = 100
            return value
        with self.assertRaises(ValueError): self.run_stage(Client(mutate))
        with self.assertRaises(ValueError):
            stage.verify_supplement(self.bundle, self.unit, self.parent, self.root / 'missing', Client().config)
        self.assertFalse((self.root / 'missing').exists())


if __name__ == '__main__':
    unittest.main()
