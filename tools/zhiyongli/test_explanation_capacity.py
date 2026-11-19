"""独立合成请求容量回归；不连接模型、生产或员工数据。"""
import copy
import hashlib
import json
from unittest import mock
import unittest

import week_basis as stage
from pipeline import Paused
from runtime import atomic, digest
import test_week_basis as fixture_module
Client = fixture_module.Client


class ExplanationCapacity(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.Cases()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def synthetic(self, count):
        f = self.fixture
        data = stage.prepare(f.unit, f.bundle['reports'][0], f.parent)
        template = data['records'][0]
        records = []
        for i in range(count):
            record = copy.deepcopy(template)
            record.update(id='event-' + hashlib.sha256(('synthetic-' + str(i)).encode()).hexdigest(),
                          text='合成核对“字段”\\n，不声明完成。')
            record['text_hash'] = hashlib.sha256(record['text'].encode()).hexdigest()
            records.append(record)
        ids = [r['id'] for r in records]
        data['records'] = records
        data['case_record_ids'] = ids
        data['source_units'][0]['record_ids'] = ids
        data['case']['task_key'] = data['source_units'][0]['key']
        spec = dict(covered_record_ids=ids, items={code: dict(verdict='consistent', basis='合成已有依据',
            higher_band='合成未知限制', boundary_kind='support_missing', source_keys=[data['source_units'][0]['key']],
            record_ids=[ids[0]], limitations=['合成来源。']) for code in stage.ITEMS},
            case=dict(task_key=data['case']['task_key'], outcome='合成结果未核',
                claims=[dict(text='合成判断' + str(i), item_codes=['J2'], record_ids=[ids[i]], limitations=[],
                             assessment=dict(kind='support', reason='合成依据。')) for i in range(3)], limitations=['合成未知。']))
        stage.validate_spec(data, spec)
        return data, spec

    def execute(self, data, client):
        f = self.fixture
        with mock.patch.object(stage, 'prepare', return_value=data):
            return stage.enrich(f.bundle, f.unit, f.parent, f.root / 'new', client)

    def test_compaction_preserves_constraints_and_completes_former_oversize(self):
        def expand(schema):
            definitions = schema.get('$defs', {})
            def visit(value):
                if isinstance(value, list):
                    return [visit(v) for v in value]
                if not isinstance(value, dict):
                    return value
                if '$ref' in value:
                    self.assertEqual(set(value), {'$ref'})
                    return visit(definitions[value['$ref'].removeprefix('#/$defs/')])
                return {k: visit(v) for k, v in value.items() if k != '$defs'}
            return visit(schema)
        for count in (80, 120):
            with self.subTest(records=count):
                data, spec = self.synthetic(count)
                first_id = data['records'][0]['id']
                def mutate(kind, value):
                    if kind == 'zhiyongli_week_basis':
                        return copy.deepcopy(spec)
                    for row in value['case_checks'].values():
                        row['record_ids'] = [first_id]
                    return value
                client = Client(mutate)
                for purpose, source, original, instructions in (
                    ('zhiyongli_week_basis', data, stage.schema(data), stage.GENERATE),
                    ('zhiyongli_week_basis_audit', dict(source=data, interpretation=spec), stage.audit_schema(data, spec), stage.AUDIT),
                ):
                    payload = stage.request(client, purpose, instructions, source, original)
                    self.assertEqual(expand(payload['text']['format']['schema']), original)
                    # 独立还原原内联schema，其他字段及wire编码逐字保持相同。
                    inline = copy.deepcopy(payload)
                    inline['text']['format']['schema'] = original
                    self.assertLess(stage.role.request_size(payload), stage.role.request_size(inline))
                    self.assertLess(stage.role.request_size(payload), 200000)
                    if count == 120 or purpose.endswith('_audit'):
                        self.assertGreater(stage.role.request_size(inline), 200000)
                f = self.fixture
                with mock.patch.object(stage, 'prepare', return_value=data):
                    result = stage.enrich(f.bundle, f.unit, f.parent, f.root / str(count), client)
                self.assertEqual(result['status'], 'basis_checked')
                self.assertEqual(client.calls, 2)

    def test_audit_lower_bound_stops_before_affordable_generation(self):
        data, _ = self.synthetic(80)
        client = Client()
        # compact后自然fixture的审核下界略小于生成体，单独模拟较长审核指令以覆盖此分支。
        with mock.patch.object(stage, 'AUDIT', stage.AUDIT + 'x' * 2000):
            cost = stage.capacity(client, data)
        limit = (cost['generation_body_bytes'] + cost['audit_body_lower_bound_bytes']) // 2
        self.assertLess(cost['generation_body_bytes'], limit)
        self.assertGreater(cost['audit_body_lower_bound_bytes'], limit)
        # 仅隔离测试较低受测容量，不改正式200000额度。
        with mock.patch.object(stage.role, 'MAX_REQUEST_BYTES', limit), mock.patch.object(stage, 'AUDIT', stage.AUDIT + 'x' * 2000):
            with self.assertRaises(Paused):
                self.execute(data, client)
        self.assertEqual(client.calls, 0)
        frozen = json.loads((self.fixture.root / 'new/capacity.json').read_text())
        self.assertEqual(frozen['max_request_bytes'], limit)
        self.assertFalse((self.fixture.root / 'new/generation.receipt.json').exists())
        self.assertFalse((self.fixture.root / 'new/review.receipt.json').exists())

    def test_generation_oversize_stops_without_call(self):
        data, _ = self.synthetic(120)
        client = Client()
        limit = stage.capacity(client, data)['generation_body_bytes'] - 1
        with mock.patch.object(stage.role, 'MAX_REQUEST_BYTES', limit):
            with self.assertRaises(Paused):
                self.execute(data, client)
        self.assertEqual(client.calls, 0)

    def test_audit_lower_bound_does_not_promise_final_fit(self):
        data, spec = self.synthetic(40)
        client = Client(lambda kind, value: copy.deepcopy(spec) if kind == 'zhiyongli_week_basis' else value)
        cost = stage.capacity(client, data)
        actual = stage.request(client, 'zhiyongli_week_basis_audit', stage.AUDIT,
                               dict(source=data, interpretation=spec), stage.audit_schema(data, spec))
        self.assertLess(cost['audit_body_lower_bound_bytes'], stage.role.request_size(actual))
        self.assertLess(stage.role.request_size(actual), 200000)
        # 合法长度正文加反斜杠，放大完整body而仍在data原120000容量内。
        for item in spec['items'].values():
            item['basis'] = '\\' * 1460
            item['higher_band'] = '\\' * 1460
        stage.validate_spec(data, spec)
        audit_data = dict(source=data, interpretation=spec)
        self.assertLess(stage.role.size(audit_data), 120000)
        actual = stage.request(client, 'zhiyongli_week_basis_audit', stage.AUDIT, audit_data, stage.audit_schema(data, spec))
        self.assertGreater(stage.role.request_size(actual), 200000)
        with self.assertRaises(Paused):
            self.execute(data, client)
        self.assertEqual(client.calls, 1)
        self.assertTrue((self.fixture.root / 'new/generation.receipt.json').exists())
        self.assertTrue((self.fixture.root / 'new/spec.json').exists())
        self.assertFalse((self.fixture.root / 'new/review.receipt.json').exists())

    def test_actual_wire_boundary_and_multibyte_escaping(self):
        role = stage.role
        base = dict(text='中文"\\\n', padding='')
        self.assertEqual(role.request_size(base), len(json.dumps(base, ensure_ascii=False).encode()))
        for target in (199999, 200000, 200001):
            value = dict(base, padding='x' * (target - role.request_size(base)))
            self.assertEqual(role.request_size(value), target)
            client = Client()
            path = self.fixture.root / (str(target) + '.json')
            sentinel = RuntimeError('已到发送接口，测试不实际发送')
            with mock.patch.object(client, '_call', side_effect=sentinel) as call:
                with self.assertRaises(Paused if target > 200000 else RuntimeError):
                    role.call(client, value, path)
                self.assertEqual(call.call_count, int(target <= 200000))

    def test_unknown_existing_receipt_preserved_without_send(self):
        value = dict(text='字' * 70000)
        client = Client()
        path = self.fixture.root / 'unknown.json'
        receipt = dict(status='unknown', request_sha256=digest(dict(endpoint=client.config.responses_url, payload=value)),
                       error='未知终态')
        atomic(path, receipt)
        before = path.read_bytes()
        with self.assertRaises(Paused) as raised:
            stage.role.call(client, value, path)
        self.assertEqual(raised.exception.state, 'unknown')
        self.assertEqual(client.calls, 0)
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
