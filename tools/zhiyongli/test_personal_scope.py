"""个人范围保护专项；只用合成材料与本地专用库。"""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from personal_scope import excluded_user_ids
import test_scale


class PersonalScopeTests(unittest.TestCase):
    def test_configuration_is_strict(self):
        for value in ('0', '-1', '22,22', '22,', ' ', '+22', '２２', str(2**63)):
            with patch.dict(os.environ, ZHIYONGLI_EXCLUDED_USER_IDS=value):
                with self.assertRaises(ValueError):
                    excluded_user_ids()
        with patch.dict(os.environ, ZHIYONGLI_EXCLUDED_USER_IDS='22, 31'):
            self.assertEqual([22, 31], excluded_user_ids())

    def test_existing_job_and_preparation_do_not_read_cached_sources_or_send(self):
        from preparation import Preparations
        from test_preparation import PreparationTests
        from worker import process
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = Preparations(root)
            queue.enqueue(PreparationTests().daily())
            sentinel = root/'original.receipt.json'
            sentinel.write_text('保留原回执')
            client = SimpleNamespace(config=None)
            with patch.dict(os.environ, ZHIYONGLI_EXCLUDED_USER_IDS='2'), \
                    patch('worker.update') as update, \
                    patch('source_snapshot.collect', side_effect=AssertionError('禁止读取排除来源')), \
                    patch('pipeline.prepare', side_effect=AssertionError('禁止复用缓存评分')):
                queue.enqueue(PreparationTests().daily('2026-09-09'))
                self.assertTrue(queue.tick(None, client, [999]))
                self.assertEqual('needs_review', queue.snapshot()[0]['state'])
                process(None, dict(id=123, user_id=2), root, None, client, None, [999])
                self.assertEqual('needs_review', update.call_args.args[2])
            self.assertEqual('保留原回执', sentinel.read_text())


@unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'), '需要显式本地专用测试库')
class PersonalSourceTests(unittest.TestCase):
    setUp = test_scale.ScaleDatabaseTests.setUp
    tearDown = test_scale.ScaleDatabaseTests.tearDown
    source = test_scale.ScaleDatabaseTests.source

    def test_excluded_user_never_enters_snapshot_but_database_and_cache_remain(self):
        from source_snapshot import collect
        self.db.execute("INSERT INTO users VALUES(22,'shared@example.test',NULL)")
        self.db.execute("""INSERT INTO llm_audit_logs(id,user_id,created_at,updated_at,request_body)
            VALUES(1,1,'2026-11-03','2026-11-03','个人合成输入'),
                  (2,22,'2026-11-03','2026-11-03','公共合成输入')""")
        with tempfile.TemporaryDirectory() as tmp, self.source() as source:
            root = Path(tmp)
            collect(source, root/'before', [999], '2026-11-02T00:00:00+08:00', shared_root=root/'cache')
            with patch.dict(os.environ, ZHIYONGLI_EXCLUDED_USER_IDS='22'):
                result = collect(source, root/'after', [999], '2026-11-02T00:00:00+08:00', shared_root=root/'cache')
                self.assertEqual((1, 1, 0, 1), (result['rows'], result['users'], result['fetched_body_rows'], result['reused_body_rows']))
                pointer = json.loads((root/'after'/'latest.json').read_text())
                snapshot = json.loads((root/'after'/pointer['path']).read_text())
                self.assertEqual([22], snapshot['excluded_user_ids'])
                self.assertEqual([1], [u['id'] for u in snapshot['users']])
            self.assertEqual(2, len(list((root/'cache'/'objects').glob('*.json'))))
        self.assertEqual(2, self.db.execute('SELECT count(*) FROM llm_audit_logs').fetchone()[0])
        self.assertEqual(0, self.db.execute('SELECT count(*) FROM audit_whitelist_users').fetchone()[0])
