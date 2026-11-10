"""来源复用与百任务调度检查；仅使用合成材料，不发送模型请求。"""
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from model_config import ModelConfig
from pipeline import Paused
from worker import JobClient


class TurnBudgetTests(unittest.TestCase):
    def test_replay_does_not_use_turn_allowance_and_restart_preserves_total(self):
        class Opener:
            calls=0
            def open(self,*args,**kwargs):
                self.calls+=1
                return io.BytesIO(b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key=root/'key';key.write_text('synthetic-key');key.chmod(0o600)
            config=ModelConfig('https://ai.apsatcom.com',key);opener=Opener()
            with patch.object(ModelConfig,'opener',return_value=opener):
                client=JobClient(config,root,2,1)
                client._call({'input':'one'},root/'1.receipt.json')
                client._call({'input':'one'},root/'1.receipt.json')
                with self.assertRaises(Paused) as ex:client._call({'input':'two'},root/'2.receipt.json')
                self.assertEqual('queued',ex.exception.state)
                client=JobClient(config,root,2,1)
                client._call({'input':'one'},root/'1.receipt.json')
                client._call({'input':'two'},root/'2.receipt.json')
                client=JobClient(config,root,2,1)
                with self.assertRaises(Paused) as ex:client._call({'input':'three'},root/'3.receipt.json')
                self.assertEqual('needs_review',ex.exception.state)
                self.assertEqual(2,opener.calls)
                self.assertFalse((root/'3.receipt.json').exists())

    def test_repeated_yield_keeps_shared_index_bounded(self):
        from shared_weeks import slot
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);packet={'events':[],'source_snapshot':'synthetic'}
            for _ in range(150):
                with self.assertRaises(Paused):
                    with slot(root/'shared',packet,root/'review'):
                        raise Paused('queued','合成轮转')
            entries=list((root/'shared').rglob('*.json'));self.assertEqual(1,len(entries))
            self.assertNotIn('previous',json.loads(entries[0].read_text()))
            self.assertLess(entries[0].stat().st_size,1000)


@unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'),'需要显式本地专用测试库')
class ScaleDatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        dsn=os.environ['ZHIYONGLI_TEST_DSN'];url=urlsplit(dsn)
        self.assertEqual(('127.0.0.1','/zhiyongli_test'),(url.hostname,url.path))
        self.db=psycopg.connect(dsn,autocommit=True);self.schema='zy_scale_'+uuid.uuid4().hex
        self.db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        self.db.execute('CREATE TABLE users(id bigint PRIMARY KEY,email text NOT NULL,deleted_at timestamptz)')
        self.db.execute("INSERT INTO users VALUES(1,'synthetic@example.test',NULL)")
        self.db.execute('CREATE TABLE audit_whitelist_users(user_id bigint)')
        self.db.execute('''CREATE TABLE llm_audit_logs(id bigint PRIMARY KEY,updated_at timestamptz,user_id bigint,api_key_id bigint,session_id text,
            request_id text,request_count bigint,platform text,endpoint text,method text,model text,status_code int,
            request_body text,response_body text,request_truncated boolean,response_truncated boolean,created_at timestamptz)''')

    def tearDown(self):
        from psycopg import sql
        self.db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)));self.db.close()

    def source(self):
        import psycopg
        return psycopg.connect(os.environ['ZHIYONGLI_TEST_DSN'],options='-c search_path='+self.schema)

    def test_source_reuses_body_but_detects_change_without_updated_timestamp(self):
        from source_snapshot import collect
        self.db.execute("""INSERT INTO llm_audit_logs(id,updated_at,created_at,user_id,request_body,response_body)
            VALUES(1,'2026-09-22','2026-09-22',1,'合成输入一','合成回复一'),
                  (2,'2026-09-23','2026-09-23',1,'合成输入二','合成回复二')""")
        with tempfile.TemporaryDirectory() as tmp,self.source() as source:
            root=Path(tmp);cache=root/'shared'
            def scan(name):return collect(source,root/name,[999],'2026-09-21T00:00:00+08:00',[1],cache)
            first=scan('first');second=scan('second')
            self.assertEqual((2,0),(first['fetched_body_rows'],first['reused_body_rows']))
            self.assertEqual((0,2),(second['fetched_body_rows'],second['reused_body_rows']))
            for path in (root/'first'/'objects').glob('*.json'):
                self.assertEqual(path.stat().st_ino,(root/'second'/'objects'/path.name).stat().st_ino)
            self.db.execute("UPDATE llm_audit_logs SET response_body='合成修改，不改变更新时间' WHERE id=1")
            third=scan('third');self.assertEqual((1,1),(third['fetched_body_rows'],third['reused_body_rows']))
            path=next((root/'third'/'objects').glob('*.json'));path.write_text('损坏测试')
            with self.assertRaisesRegex(ValueError,'签名损坏'):scan('corrupt')
            self.assertFalse((root/'corrupt'/'latest.json').exists())

    def test_hundred_jobs_rotate_and_unknown_does_not_block_others(self):
        from worker import tick,update
        migration=Path(__file__).resolve().parents[2]/'backend/migrations/245_zhiyongli_jobs.sql'
        self.db.execute(migration.read_text())
        self.db.execute("""INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by)
            SELECT 1,'week',date '2020-01-06'+7*n,date '2020-01-13'+7*n,'1.6',1 FROM generate_series(0,99) n""")
        order=[];counts={}
        def phase(db,job,*args):
            jid=job['id'];order.append(jid);counts[jid]=counts.get(jid,0)+1
            state='unknown' if jid==1 else 'completed' if counts[jid]==3 else 'queued'
            update(db,jid,state,'reading',counts[jid],3,'合成阶段')
        with tempfile.TemporaryDirectory() as tmp,self.source() as source,patch('worker.process',side_effect=phase):
            for _ in range(298):self.assertTrue(tick(self.db,source,tmp,None,None,[999]))
            self.assertFalse(tick(self.db,source,tmp,None,None,[999]))
        self.assertEqual(list(range(1,101)),order[:100])
        self.assertEqual(list(range(2,101)),order[100:199])
        self.assertEqual(1,counts[1]);self.assertTrue(all(counts[n]==3 for n in range(2,101)))
        self.assertEqual([('completed',99),('unknown',1)],self.db.execute('SELECT state,count(*) FROM zhiyongli_jobs GROUP BY state ORDER BY state').fetchall())

    def test_hundred_users_with_four_connections_keep_job_locks_and_isolate_unknown(self):
        import psycopg
        from worker import tick,update
        self.db.execute("INSERT INTO users SELECT n,'synthetic-'||n||'@example.test',NULL FROM generate_series(2,100) n")
        self.db.execute((Path(__file__).resolve().parents[2]/'backend/migrations/245_zhiyongli_jobs.sql').read_text())
        self.db.execute("""INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by)
            SELECT id,'week','2026-09-07','2026-09-14','1.6',1 FROM users""")
        mutex=threading.Lock();active=set();counts={};collisions=[]
        def phase(db,job,*args):
            jid=job['id']
            with mutex:
                if jid in active:collisions.append(jid)
                active.add(jid);counts[jid]=counts.get(jid,0)+1;count=counts[jid]
            try:
                time.sleep(.001)
                update(db,jid,'unknown' if jid==1 else 'completed' if count==3 else 'queued','reading',count,3,'合成阶段')
            finally:
                with mutex:active.remove(jid)
        with tempfile.TemporaryDirectory() as state,patch('worker.process',side_effect=phase):
            def run():
                with psycopg.connect(os.environ['ZHIYONGLI_TEST_DSN'],autocommit=True,options='-c search_path='+self.schema) as db,self.source() as source:
                    while tick(db,source,state,None,None,[999]):pass
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures=[pool.submit(run) for _ in range(4)]
                for future in futures:future.result()
        self.assertEqual([],collisions);self.assertEqual(100,len(counts));self.assertEqual(1,counts[1])
        self.assertTrue(all(counts[n]==3 for n in range(2,101)))
        self.assertEqual([('completed',99),('unknown',1)],self.db.execute('SELECT state,count(*) FROM zhiyongli_jobs GROUP BY state ORDER BY state').fetchall())


if __name__=='__main__':unittest.main()
