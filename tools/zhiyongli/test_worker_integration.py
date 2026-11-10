"""真实PostgreSQL队列消费检查；仅在显式本地专用测试库中执行。"""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid


@unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'), '需要显式本地专用测试库')
class WorkerIntegrationTests(unittest.TestCase):
    def test_queued_identity_and_empty_source_finish_without_model_or_import(self):
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
                with psycopg.connect(dsn, options='-c search_path='+schema) as source, tempfile.TemporaryDirectory() as state:
                    client = SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',Path(state)/'unused.key'))
                    def no_import(*args): self.fail('无记录不能接入虚构报告')
                    with patch('runtime.CompanyClient._call',side_effect=AssertionError('无记录不能调用模型')):
                        self.assertTrue(tick(db,source,state,client,no_import,[1],1))
                        self.assertFalse(tick(db,source,state,client,no_import,[1],1))
                    self.assertEqual(('no_activity','done'),db.execute('SELECT state,phase FROM zhiyongli_jobs').fetchone())
                    self.assertFalse(list(Path(state).rglob('*.receipt.json')))
                    self.assertFalse(list(Path(state).rglob('failure.json')))
            finally:
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__': unittest.main()
