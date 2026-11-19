"""真实PostgreSQL队列消费检查；仅在显式本地专用测试库中执行。"""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid


class ScheduleGuardTests(unittest.TestCase):
    def test_disabled_or_unconfirmed_schedule_does_not_query_or_claim_any_jobs(self):
        from worker import tick
        for fields in ({},{'preparation_first_week':None,'schedule_enabled':False},
                       {'preparation_first_week':'2026-11-16','schedule_enabled':False}):
            with self.subTest(fields=fields):
                client=SimpleNamespace(preparations=SimpleNamespace(),**fields)
                self.assertFalse(tick(None,None,None,client,None,[]))


@unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'), '需要显式本地专用测试库')
class WorkerIntegrationTests(unittest.TestCase):
    def test_new_daily_plan_never_claims_legacy_week_jobs(self):
        self.test_queued_identity_and_empty_source_finish_without_model_or_import(managed=True)

    def test_selected_scope_reuses_frozen_source_and_leaves_old_queue_untouched(self):
        self.test_queued_identity_and_empty_source_finish_without_model_or_import(lazy=True,partial=True)

    def test_lazy_source_session_is_closed_after_collection(self):
        self.test_queued_identity_and_empty_source_finish_without_model_or_import(lazy=True)

    def test_queued_identity_and_empty_source_finish_without_model_or_import(self, lazy=False,partial=False,managed=False):
        import psycopg
        from psycopg import sql
        from model_config import ModelConfig
        from worker import tick
        dsn = os.environ['ZHIYONGLI_TEST_DSN']
        url = urlsplit(dsn)
        self.assertEqual('127.0.0.1', url.hostname)
        self.assertEqual('/zhiyongli_test', url.path)
        schema = 'zy_worker_' + uuid.uuid4().hex
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            try:
                db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
                db.execute('CREATE TABLE users(id bigint PRIMARY KEY,email text NOT NULL,deleted_at timestamptz)')
                db.execute("INSERT INTO users VALUES(1,'local-admin@example.test',NULL),(2,'local-person@example.test',NULL)")
                db.execute('CREATE TABLE audit_whitelist_users(user_id bigint)')
                db.execute('''CREATE TABLE llm_audit_logs(id bigint,updated_at timestamptz,user_id bigint,api_key_id bigint,session_id text,
                    request_id text,request_count bigint,platform text,endpoint text,method text,model text,status_code int,
                    request_body text,response_body text,request_truncated boolean,response_truncated boolean,created_at timestamptz)''')
                root = Path(__file__).resolve().parents[2]
                db.execute((root/'backend/migrations/245_zhiyongli_jobs.sql').read_text())
                db.execute((root/'backend/migrations/246_zhiyongli_workday_scope.sql').read_text())
                db.execute("""INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by)
                    VALUES(2,'week','2026-09-07','2026-09-14','1.6',1)""")
                from workday_scope import window
                import json
                db.execute('UPDATE zhiyongli_jobs SET workday_scope=%s::jsonb',(json.dumps(window('2026-09-07','2026-09-14')),))
                from contextlib import nullcontext
                from worker import source_session
                import threading
                source_dsn = dsn + ('&' if '?' in dsn else '?') + 'options=-csearch_path%3D' + schema + '&application_name=zy_lazy_' + schema
                factory = lambda: source_session(source_dsn, threading.BoundedSemaphore(1))
                session = nullcontext(factory) if lazy else psycopg.connect(dsn, options='-c search_path='+schema)
                with session as source, tempfile.TemporaryDirectory() as state:
                    client = SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',Path(state)/'unused.key'))
                    if managed:
                        client.preparations=SimpleNamespace();client.preparation_first_week='2026-09-14'
                        def no_legacy_call(*args):self.fail('新自动计划不能读取旧评审来源或发送模型请求')
                        with patch('source_snapshot.collect',side_effect=no_legacy_call):
                            self.assertFalse(tick(db,source,state,client,no_legacy_call,[1],1))
                        self.assertEqual('queued',db.execute('SELECT state FROM zhiyongli_jobs').fetchone()[0])
                        return
                    def no_import(*args): self.fail('无记录不能接入虚构报告')
                    with patch('runtime.CompanyClient._call',side_effect=AssertionError('无记录不能调用模型')):
                        self.assertTrue(tick(db,source,state,client,no_import,[1],1))
                        self.assertFalse(tick(db,source,state,client,no_import,[1],1))
                    self.assertEqual(('no_activity','done'),db.execute('SELECT state,phase FROM zhiyongli_jobs').fetchone())
                    self.assertFalse(list(Path(state).rglob('*.receipt.json')))
                    self.assertFalse(list(Path(state).rglob('failure.json')))
                    if lazy:
                        self.assertEqual(0, db.execute('SELECT count(*) FROM pg_stat_activity WHERE application_name=%s', ('zy_lazy_'+schema,)).fetchone()[0])
                    if partial:
                        from workday_scope import DEFAULT
                        from runtime import atomic,digest
                        from pipeline import selected_packet
                        registry=json.loads(DEFAULT.read_text());registry['weeks']['2026-09-07']['evaluation_dates']=['2026-09-07','2026-09-08','2026-09-09']
                        path=Path(state)/'registry.json';atomic(path,registry)
                        with patch.dict(os.environ,{'ZHIYONGLI_WORKDAY_REGISTRY':str(path)}):
                            scope=window('2026-09-07','2026-09-14');key=scope['version']+':'+scope['registry_sha256']
                            selected_packet(Path(state)/'prepared-scopes'/digest(scope)/'2',Path(state)/'1/sources',2,
                                '2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00',scope,create=True)
                            db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=1")
                            db.execute("""INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,workday_scope)
                                VALUES(2,'week','2026-09-07','2026-09-14','1.6',1,%s,%s::jsonb)""",(key,json.dumps(scope)))
                            with patch.dict(os.environ,{'ZHIYONGLI_ACTIVE_SCOPE_KEY':key}),\
                                 patch('worker.collect',side_effect=AssertionError('局部交付不得重新采集来源')),\
                                 patch('runtime.CompanyClient._call',side_effect=AssertionError('无活动不得发模型请求')):
                                self.assertTrue(tick(db,source,state,client,no_import,[1],1))
                                self.assertFalse(tick(db,source,state,client,no_import,[1],1))
                            self.assertEqual([(1,'queued'),(2,'no_activity')],db.execute('SELECT id,state FROM zhiyongli_jobs ORDER BY id').fetchall())
                            self.assertEqual(0,json.loads((Path(state)/'2/inherited-request-budget.json').read_text())[0]['used'])
            finally:
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__': unittest.main()
