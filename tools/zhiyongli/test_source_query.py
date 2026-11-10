"""来源范围和跨批次一致性；真实本地PG，合成材料，无模型发送。"""
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import source_snapshot
import test_scale


class SharedSourceCacheTests(unittest.TestCase):
    def test_long_cached_body_keeps_original_bytes_and_detects_tail_corruption(self):
        value = {'text': '合成原文\n"引用"\\路径' * 100000, 'role': 'user'}
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
        expected = hashlib.sha256(raw).hexdigest()
        self.assertGreater(len(raw), 1024*1024)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = source_snapshot.ObjectCache(root/'shared')
            try:
                # 按原有序列化合同预存旧对象，验证新程序兼容既有SHA和索引。
                body = root/'shared/objects'/(expected+'.json')
                body.parent.mkdir();body.write_bytes(raw)
                cache.db.execute('INSERT INTO revisions VALUES(?,?)', ('old', expected))
                self.assertEqual(expected, cache.get('old'))
                self.assertEqual(expected, cache.put('new', value))
                cache.attach(expected, root/'job')
                linked = root/'job/objects'/body.name
                self.assertEqual(body.stat().st_ino, linked.stat().st_ino)
                self.assertEqual(raw, linked.read_bytes())
                with body.open('r+b') as stream:
                    stream.seek(-1, 2);stream.write(b'!')
                with self.assertRaisesRegex(ValueError, '签名损坏'):
                    cache.get('old')
                with self.assertRaisesRegex(ValueError, '签名损坏'):
                    cache.attach(expected, root/'job')
            finally:
                cache.db.close()

    def test_parallel_first_open_preserves_all_revisions(self):
        for _ in range(3):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);barrier=threading.Barrier(16)
                def collect(index):
                    barrier.wait(timeout=10)
                    cache=source_snapshot.ObjectCache(root)
                    try:
                        return cache.put(str(index),{'source':index})
                    finally:
                        cache.db.close()
                with ThreadPoolExecutor(max_workers=16) as pool:
                    signatures=list(pool.map(collect,range(16)))
                cache=source_snapshot.ObjectCache(root)
                try:
                    self.assertEqual(16,len(set(signatures)))
                    self.assertEqual(signatures,[cache.get(str(i)) for i in range(16)])
                finally:
                    cache.db.close()


class SourceResumeTests(unittest.TestCase):
    def legacy(self,root):
        from test_workday_scope import WorkdayScopeTests
        from runtime import atomic
        root.mkdir(parents=True,exist_ok=True)
        WorkdayScopeTests().source(root,['2026-09-08T10:00:00+08:00'],'2026-09-13T02:00:00+08:00')
        snapshot=json.loads((root/'snapshot.json').read_text());snapshot.pop('query_version')
        sha=source_snapshot.save(root/'snapshot.json',snapshot)
        atomic(root/'latest.json',dict(path='snapshot.json',sha256=sha))
        return {p.name:p.read_bytes() for p in root.iterdir()}

    def test_legacy_job_preserves_sources_without_scoring_or_import(self):
        from model_config import ModelConfig
        from workday_scope import window
        from worker import process
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sources=root/'123/sources';before=self.legacy(sources)
            client=SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',root/'unused.key'))
            with patch('worker.update') as update,patch('worker.collect') as collect, \
                    patch('worker.prepare') as prepare,patch('runtime.CompanyClient._call') as call, \
                    patch('worker.evaluate_month') as month,patch('worker.evaluate_week') as week:
                for period in ('week','month'):
                    process(None,dict(id=123,user_id=2,user_email='synthetic@example.test',
                        period_type=period,period_start='2026-09-07',period_end='2026-09-14',
                        workday_scope=window('2026-09-07','2026-09-14')),root,None,client,None,[999])
                    self.assertEqual('needs_review',update.call_args.args[2])
                collect.assert_not_called();prepare.assert_not_called();call.assert_not_called()
                month.assert_not_called();week.assert_not_called()
            self.assertEqual(before,{p.name:p.read_bytes() for p in sources.iterdir()})

    def test_legacy_daily_preparation_is_retained_and_stops_before_reading(self):
        from model_config import ModelConfig
        from preparation import Preparations
        from test_preparation import PreparationTests
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);queue=Preparations(root);queue.enqueue(PreparationTests().daily())
            with queue.connect() as db:key=db.execute('SELECT key FROM preparations').fetchone()[0]
            sources=root/'preparations'/key/'sources/2026-09-08';before=self.legacy(sources)
            client=SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',root/'unused.key'))
            with patch('source_snapshot.collect') as collect,patch('pipeline.prepare') as prepare, \
                    patch('runtime.CompanyClient._call') as call:
                self.assertTrue(queue.tick(None,client,[999]))
                collect.assert_not_called();prepare.assert_not_called();call.assert_not_called()
            self.assertEqual('needs_review',queue.snapshot()[0]['state'])
            self.assertEqual(before,{p.name:p.read_bytes() for p in sources.iterdir()})


@unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'), '需要显式本地专用测试库')
class SourceQueryTests(unittest.TestCase):
    setUp = test_scale.ScaleDatabaseTests.setUp
    tearDown = test_scale.ScaleDatabaseTests.tearDown
    source = test_scale.ScaleDatabaseTests.source

    def seed(self):
        self.db.execute("""INSERT INTO users VALUES(2,'second@example.test',NULL),
            (3,'whitelist@example.test',NULL),(4,'deleted@example.test',now()),
            (5,'third@example.test',NULL),(22,'shared@example.test',NULL)""")
        self.db.execute('INSERT INTO audit_whitelist_users VALUES(3)')
        self.db.execute("""INSERT INTO llm_audit_logs(id,user_id,session_id,api_key_id,created_at,updated_at,request_body)
            VALUES(1,1,'active',NULL,'2026-10-01','2026-10-01','活跃前文'),
                  (2,1,'active',NULL,'2026-11-03','2026-11-03','当期'),
                  (3,1,'',NULL,'2026-10-01','2026-10-01','空会话旧记录'),
                  (4,1,'',NULL,'2026-11-03','2026-11-03','空会话当前记录'),
                  (5,1,NULL,NULL,'2026-10-01','2026-10-01','无会话旧记录'),
                  (6,1,NULL,NULL,'2026-11-03','2026-11-03','无会话当前记录'),
                  (7,1,'inactive',NULL,'2026-10-01','2026-10-01','不活跃前文'),
                  (8,1,'excluded-key',NULL,'2026-10-01','2026-10-01','原有真实前文'),
                  (9,1,'excluded-key',999,'2026-11-03','2026-11-03','排除专用key'),
                  (10,2,'foreign',NULL,'2026-10-01','2026-10-01','另一个人的旧会话'),
                  (11,5,'foreign',NULL,'2026-11-03','2026-11-03','不能激活别人的前文'),
                  (12,2,'other',NULL,'2026-11-03','2026-11-03','第二人当期'),
                  (13,3,NULL,NULL,'2026-11-03','2026-11-03','白名单'),
                  (14,4,NULL,NULL,'2026-11-03','2026-11-03','已删除'),
                  (15,22,NULL,NULL,'2026-11-03','2026-11-03','公共账户')""")
        # 多个同一时刻的批次，检验分页无重复、无遗漏及确定性排序。
        self.db.execute("""INSERT INTO llm_audit_logs(id,user_id,session_id,created_at,updated_at,request_body)
            SELECT n,1,'batch-'||n,'2026-11-03','2026-11-03','合成正文'||n FROM generate_series(1000,1601) n""")

    def snapshot(self, root):
        pointer = json.loads((root/'latest.json').read_text())
        return json.loads((root/pointer['path']).read_text())

    def test_active_history_empty_ids_identity_filters_and_many_batches(self):
        self.seed()
        with tempfile.TemporaryDirectory() as tmp, self.source() as source, patch.dict(os.environ,ZHIYONGLI_EXCLUDED_USER_IDS='22'):
            root=Path(tmp)
            first=source_snapshot.collect(source,root/'first',[999],'2026-11-02T00:00:00+08:00',shared_root=root/'cache')
            snapshot=self.snapshot(root/'first')
            expected={1,2,4,6,8,11,12}|set(range(1000,1602))
            self.assertEqual(expected,{row['id'] for row in snapshot['rows']})
            self.assertEqual(len(expected),len(snapshot['rows']))
            self.assertEqual([1,2,5],[u['id'] for u in snapshot['users']])
            rows=snapshot['rows']
            self.assertEqual(rows,sorted(rows,key=lambda r:(datetime.fromisoformat(r['updated_at']),r['id'])))
            second=source_snapshot.collect(source,root/'second',[999],'2026-11-02T00:00:00+08:00',shared_root=root/'cache')
            self.assertEqual((len(expected),0),(first['fetched_body_rows'],first['reused_body_rows']))
            self.assertEqual((0,len(expected)),(second['fetched_body_rows'],second['reused_body_rows']))
            selected=source_snapshot.collect(source,root/'selected',[999],'2026-11-02T00:00:00+08:00',[2],root/'cache')
            self.assertEqual((1,1),(selected['users'],selected['rows']))
            self.assertEqual(12,self.snapshot(root/'selected')['rows'][0]['id'])

    def test_concurrent_revision_between_batches_keeps_frozen_body_and_next_scan_detects_it(self):
        self.seed()
        original=source_snapshot.metadata_batches
        def concurrent(*args):
            for index,batch in enumerate(original(*args)):
                yield batch
                if index==0:
                    self.db.execute("UPDATE llm_audit_logs SET request_body='下一快照的新正文' WHERE id=1601")
        with tempfile.TemporaryDirectory() as tmp, self.source() as source:
            root=Path(tmp)
            with patch('source_snapshot.metadata_batches',side_effect=concurrent):
                source_snapshot.collect(source,root/'frozen',[999],'2026-11-02T00:00:00+08:00',[1],root/'cache')
            snapshot=self.snapshot(root/'frozen')
            row=next(r for r in snapshot['rows'] if r['id']==1601)
            body=json.loads((root/'frozen'/row['path']).read_text())
            self.assertEqual('合成正文1601',body['request_body'])
            result=source_snapshot.collect(source,root/'next',[999],'2026-11-02T00:00:00+08:00',[1],root/'cache')
            self.assertEqual(1,result['fetched_body_rows'])
            row=next(r for r in self.snapshot(root/'next')['rows'] if r['id']==1601)
            self.assertEqual('下一快照的新正文',json.loads((root/'next'/row['path']).read_text())['request_body'])
