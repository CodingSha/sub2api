"""来源可独立推进，分析未决和发送账簿不能被采集成功清除。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from preparation import Preparations
import test_preparation as preparation_fixtures
import test_workday_scope as fixtures
from runtime import atomic
from pipeline import Paused
from datetime import datetime

class PreparationCollectionTests(unittest.TestCase):
    def test_session_refresh_uses_registered_next_workday_and_keeps_unknown_closed(self):
        from preparation import nightly_refresh_due
        from workday_scope import window
        scope=window('2026-09-07','2026-09-14')
        for at,want in [('2026-09-11T22:59:59+08:00',False),('2026-09-11T23:00:00+08:00',True),
                ('2026-09-14T07:59:59+08:00',True),('2026-09-14T08:00:00+08:00',False)]:
            self.assertEqual(want,nightly_refresh_due(scope,'2026-09-11',datetime.fromisoformat(at)))
        with tempfile.TemporaryDirectory() as tmp:
            queue=Preparations(tmp);daily=preparation_fixtures.PreparationTests().daily('2026-09-11');daily['mode']='session_facts'
            now=datetime.fromisoformat('2026-09-14T06:00:00+08:00').timestamp()
            with patch('preparation.clock.time',return_value=now):
                queue.enqueue(daily)
                with queue.connect() as db:key=db.execute('SELECT key FROM preparations').fetchone()[0]
                queue.update(key,'prepared','首次完成','2026-09-11');queue.enqueue(daily)
                with queue.connect() as db:row=dict(db.execute('SELECT * FROM preparations').fetchone())
                self.assertEqual(('queued',1),(row['state'],row['refresh_requested']))
                queue.update(key,'unknown','原发送不明');queue.enqueue(daily)
                with queue.connect() as db:row=dict(db.execute('SELECT * FROM preparations').fetchone())
                self.assertEqual('unknown',row['state'])

    def setup_queue(self,root,state='unknown',user=2):
        queue=Preparations(root);daily=preparation_fixtures.PreparationTests().daily('2026-09-08')
        daily['users'][0]['user_id']=user;queue.enqueue(daily)
        with queue.connect() as db:key=db.execute('SELECT key FROM preparations WHERE user_id=?',(user,)).fetchone()[0]
        queue.update(key,'prepared','旧日已准备','2026-09-08');queue.update(key,state,'原分析待核')
        daily['through_date']='2026-09-10';queue.enqueue(daily)
        return queue,key,daily
    def collector(self,_,directory,*args,**kwargs):
        directory.mkdir(parents=True,exist_ok=True)
        fixtures.WorkdayScopeTests().source(directory,['2026-09-10T10:00:00+08:00'],'2026-09-11T02:00:00+08:00')
    def test_terminal_states_collect_new_day_without_model_budget_or_proof_changes(self):
        for state in ('unknown','needs_review','failed'):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);queue,key,daily=self.setup_queue(root,state);job=root/'preparations'/key
                for name in ('request-budget.json','preparation-packet.json','failure.json','old.receipt.json'):
                    atomic(job/name,dict(test='保留旧分析证据'))
                before={name:(job/name).read_bytes() for name in ('request-budget.json','preparation-packet.json','failure.json','old.receipt.json')}
                with patch('source_snapshot.collect',side_effect=self.collector) as collect,patch('worker.JobClient',side_effect=AssertionError('不可创建模型Client')):
                    self.assertTrue(queue.tick(None,SimpleNamespace(),[999]));self.assertFalse(queue.tick(None,SimpleNamespace(),[999]))
                    self.assertEqual(1,collect.call_count)
                with queue.connect() as db:row=db.execute('SELECT * FROM preparations').fetchone()
                self.assertEqual((state,'2026-09-08','2026-09-10','原分析待核'),(row['state'],row['prepared_through'],row['source_collected_through'],row['message']))
                self.assertEqual(before,{n:(job/n).read_bytes() for n in before})
                with self.assertRaises(Paused) as error:queue.ready_budget(dict(user_id=2,user_email='synthetic@example.test',workday_scope=daily['workday_scope']))
                self.assertEqual(state,error.exception.state)
    def test_collection_failure_keeps_watermark_and_allows_other_rows_and_backoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);queue,key,_=self.setup_queue(root)
            with patch('source_snapshot.collect',side_effect=OSError('合成采集失败')) as collect:
                self.assertTrue(queue.tick(None,SimpleNamespace(),[999]));self.assertFalse(queue.tick(None,SimpleNamespace(),[999]))
                self.assertEqual(1,collect.call_count)
            with queue.connect() as db:row=db.execute('SELECT * FROM preparations').fetchone()
            self.assertIsNone(row['source_collected_through']);self.assertEqual('unknown',row['state'])
            self.assertTrue((root/'preparations'/key/'source-collection/2026-09-10.failure.json').exists())
    def test_existing_valid_snapshot_reused_without_connecting_and_bad_objects_identity_reject(self):
        for broken in (None,'object','person'):
            with self.subTest(broken=broken),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);queue,key,_=self.setup_queue(root);source=root/'preparations'/key/'sources/2026-09-10'
                self.collector(None,source)
                if broken=='object':(source/'row.json').write_text('{}')
                if broken=='person':
                    from source_snapshot import save
                    snapshot=json.loads((source/'snapshot.json').read_text());snapshot['users'][0]['email']='wrong@example.test'
                    sha=save(source/'snapshot.json',snapshot);atomic(source/'latest.json',dict(path='snapshot.json',sha256=sha))
                with patch('source_snapshot.collect',side_effect=AssertionError('已有快照不得重采')):
                    self.assertTrue(queue.tick(None,SimpleNamespace(),[999]))
                with queue.connect() as db:row=db.execute('SELECT * FROM preparations').fetchone()
                self.assertEqual('2026-09-10' if broken is None else None,row['source_collected_through'])
                self.assertEqual('unknown',row['state'])

    def test_failed_terminal_collection_does_not_starve_next_person(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);queue,first,daily=self.setup_queue(root)
            second=dict(daily,users=[dict(user_id=3,user_email='other@example.test')]);queue.enqueue(second)
            with queue.connect() as db:
                db.execute("UPDATE preparations SET state='unknown' WHERE user_id=3")
            attempted=[]
            def fail(_,directory,*args,**kwargs):
                attempted.append(args[2][0]);raise OSError('合成故障')
            with patch('source_snapshot.collect',side_effect=fail):
                self.assertTrue(queue.tick(None,SimpleNamespace(),[999]));self.assertTrue(queue.tick(None,SimpleNamespace(),[999]))
                self.assertFalse(queue.tick(None,SimpleNamespace(),[999]))
            self.assertEqual({2,3},set(attempted));self.assertEqual(2,len(attempted))

    def test_early_snapshot_is_recollected_and_query_mismatch_does_not_advance(self):
        for broken_query in (False,True):
            with self.subTest(broken_query=broken_query),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);queue,key,_=self.setup_queue(root);source=root/'preparations'/key/'sources/2026-09-10'
                self.collector(None,source)
                from source_snapshot import save
                value=json.loads((source/'snapshot.json').read_text())
                if broken_query:value['query_version']='old-query'
                else:value['snapshot_at']='2026-09-10T12:00:00+08:00'
                sha=save(source/'snapshot.json',value);atomic(source/'latest.json',dict(path='snapshot.json',sha256=sha))
                with patch('source_snapshot.collect',side_effect=self.collector) as collect:
                    queue.tick(None,SimpleNamespace(),[999])
                    self.assertEqual(0 if broken_query else 1,collect.call_count)
                with queue.connect() as db:row=db.execute('SELECT * FROM preparations').fetchone()
                self.assertEqual(None if broken_query else '2026-09-10',row['source_collected_through'])

    def test_legacy_schema_migrates_without_marking_analysis_or_sources_complete(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with sqlite3.connect(root/'preparations.sqlite') as db:
                db.execute('CREATE TABLE preparations(key TEXT PRIMARY KEY,user_id INTEGER,user_email TEXT,scope TEXT,through_date TEXT,prepared_through TEXT,state TEXT,updated REAL,message TEXT)')
                db.execute("INSERT INTO preparations VALUES('legacy',2,'synthetic@example.test','{}','2026-09-10','2026-09-08','unknown',1,'原未决')")
            queue=Preparations(root)
            with queue.connect() as db:row=db.execute('SELECT * FROM preparations').fetchone()
            self.assertEqual(('unknown','2026-09-08',None,0),(row['state'],row['prepared_through'],row['source_collected_through'],row['source_updated']))
            self.assertFalse(list(root.rglob('request-budget.json')))

    def test_declared_source_range_cannot_be_replaced_by_late_snapshot_time(self):
        from source_snapshot import save
        for field,stamp in (('since','2026-09-09T00:00:00+08:00'),('until','2026-09-10T00:00:00+08:00')):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);queue,key,_=self.setup_queue(root);source=root/'preparations'/key/'sources/2026-09-10'
                self.collector(None,source);snapshot=json.loads((source/'snapshot.json').read_text());snapshot[field]=stamp
                sha=save(source/'snapshot.json',snapshot);atomic(source/'latest.json',dict(path='snapshot.json',sha256=sha))
                with patch('source_snapshot.collect') as collect:
                    queue.tick(None,SimpleNamespace(),[999]);collect.assert_not_called()
                with queue.connect() as db:row=db.execute('SELECT * FROM preparations').fetchone()
                self.assertIsNone(row['source_collected_through']);self.assertEqual('unknown',row['state'])
                error=json.loads((root/'preparations'/key/'source-collection/2026-09-10.failure.json').read_text())
                self.assertIn('采集',error['traceback'])

if __name__=='__main__':unittest.main()
