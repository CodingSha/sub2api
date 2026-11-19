"""日常来源只准备不评分，续跑不丢日期更新及unknown；无真实模型调用。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from model_config import ModelConfig
from pipeline import prepare,evaluate_week,Paused
from preparation import Preparations
import test_workday_scope as source_fixtures
import test_reading_cache as reading_fixtures
from workday_scope import window


class PreparationTests(unittest.TestCase):
    def daily(self,day='2026-09-08'):
        return dict(through_date=day,workday_scope=window('2026-09-07','2026-09-14'),users=[dict(user_id=2,user_email='synthetic@example.test')])

    def test_preparation_snapshot_is_marked_and_cannot_be_scored_before_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source_fixtures.WorkdayScopeTests().source(root,['2026-09-08T10:00:00+08:00','2026-09-09T01:00:00+08:00'],'2026-09-09T02:00:00+08:00')
            packet=prepare(root,2,'2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00',self.daily()['workday_scope'],
                preparation=True,preparation_until='2026-09-09T00:00:00+08:00')
            self.assertTrue(packet['preparation_only']);self.assertEqual(1,len(packet['events']))
            with self.assertRaises(Paused) as ex:evaluate_week(packet,root/'evaluation',None)
            self.assertIn('禁止生成周分',str(ex.exception))

    def test_new_date_is_not_lost_during_preparation_and_unknown_does_not_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue=Preparations(tmp);queue.enqueue(self.daily())
            with queue.connect() as db:key=db.execute('SELECT key FROM preparations').fetchone()[0]
            queue.update(key,'running','合成执行中')
            queue.enqueue(self.daily('2026-09-09'))
            queue.update(key,'prepared','合成完成旧日期','2026-09-08')
            with queue.connect() as db:
                r=db.execute('SELECT * FROM preparations').fetchone();self.assertEqual(('queued','2026-09-09','2026-09-08'),(r['state'],r['through_date'],r['prepared_through']))
            queue.update(key,'unknown','合成未决')
            queue.enqueue(self.daily('2026-09-10'))
            self.assertEqual('unknown',queue.snapshot()[0]['state'])

    def test_short_daily_sources_need_no_paid_read_and_do_not_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);queue=Preparations(root);queue.enqueue(self.daily())
            def collect(_,directory,*args,**kwargs):
                directory.mkdir(parents=True,exist_ok=True)
                source_fixtures.WorkdayScopeTests().source(directory,['2026-09-08T10:00:00+08:00'],'2026-09-09T02:00:00+08:00')
            base=SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',root/'unused'))
            with patch('source_snapshot.collect',side_effect=collect),patch('runtime.CompanyClient._call',side_effect=AssertionError('短来源不应提前付费阅读')):
                self.assertTrue(queue.tick(None,base,[999]));self.assertFalse(queue.tick(None,base,[999]))
            self.assertEqual('prepared',queue.snapshot()[0]['state'])
            self.assertFalse(list(root.rglob('bundle.json')));self.assertFalse(list(root.rglob('*.receipt.json')))

    def test_long_daily_reading_is_reused_by_frozen_week_without_resending(self):
        # 同时使用真实快照装配、分页、API合成回执、共享计划和独立任务预算。
        reading_fixtures.ReadingCacheTests.setUp(self)
        from source_snapshot import save
        from runtime import atomic
        from pipeline import reading_view
        from worker import JobClient
        queue=Preparations(self.root/'state');queue.enqueue(self.daily())
        def collect(_,directory,*args,**kwargs):
            directory.mkdir(parents=True,exist_ok=True)
            stamp='2026-09-08T10:00:00+08:00'
            source_fixtures.WorkdayScopeTests().source(directory,[stamp],'2026-09-13T02:00:00+08:00')
            row=json.loads((directory/'row.json').read_text())
            row['response_body']=json.dumps([dict(content='合成工具结果，保留失败反证。'*6000,request_id='0',created_at=stamp)])
            sha=save(directory/'row.json',row)
            snap=json.loads((directory/'snapshot.json').read_text());snap['rows'][0]['sha256']=sha
            sha=save(directory/'snapshot.json',snap);atomic(directory/'latest.json',dict(path='snapshot.json',sha256=sha))
        base=SimpleNamespace(config=self.config)
        from preparation import prepared_packet
        from runtime import digest
        captured=[]
        def capture(*args,**kwargs):
            value=prepared_packet(*args,**kwargs);captured.append(value);return value
        with patch('source_snapshot.collect',side_effect=collect),patch('preparation.prepared_packet',side_effect=capture):
            self.assertTrue(queue.tick(None,base,[999],new_request_limit=100))
        self.assertEqual(captured[0][1],digest(captured[0][0]))
        navigation=json.loads(next((self.root/'state/preparations').rglob('prepared-navigation.json')).read_text())
        self.assertEqual(captured[0][1],navigation['packet_sha256'])
        self.assertEqual('prepared',queue.snapshot()[0]['state']);self.assertGreater(self.calls,1)
        sources=next((self.root/'state/preparations').glob('*/sources/2026-09-08'))
        scope=self.daily()['workday_scope']
        packet=prepare(sources,2,'2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00',scope)
        final=JobClient(self.config,self.root/'final',1,1)
        final.reading_cache_root=self.root/'state/read-pages';final.reading_plan_root=self.root/'state/reading-plans'
        final.navigation_cache_root=self.root/'state/navigation-branches'
        sent=self.calls;view=reading_view(packet,self.root/'final',final,lambda *args:None)
        self.assertIn('context_navigation',view)
        self.assertTrue(list((self.root/'state/preparations').rglob('prepared-navigation.json')))
        self.assertFalse(list((self.root/'state/preparations').rglob('nodes.sqlite')))
        self.assertTrue(list((self.root/'state/navigation-branches').rglob('*.receipt.json')))
        self.assertEqual((sent,0),(self.calls,final.budget.used()))
        self.assertFalse(list((self.root/'state').rglob('bundle.json')))

    def test_daily_oversized_navigation_stops_before_reading_or_sending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);queue=Preparations(root);queue.enqueue(self.daily())
            def collect(_,directory,*args,**kwargs):
                directory.mkdir(parents=True,exist_ok=True)
                source_fixtures.WorkdayScopeTests().source(directory,['2026-09-08T10:00:00+08:00'],'2026-09-09T02:00:00+08:00')
            packet=dict(events=[dict(event='context-'+str(i),at='2026-09-08T10:00:00+08:00',
                actor_class='context',text='完整保留工具结果') for i in range(2000)],issues=[])
            base=SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',root/'unused'))
            with patch('source_snapshot.collect',side_effect=collect),patch('pipeline.prepare',return_value=packet),\
                    patch('bounded_read.read_all') as read,patch('runtime.CompanyClient._call') as send:
                self.assertTrue(queue.tick(None,base,[999]));read.assert_not_called();send.assert_not_called()
            self.assertEqual('needs_review',queue.snapshot()[0]['state'])
            self.assertFalse(list(root.rglob('bundle.json')))

    def test_midnight_reading_only_reuses_actual_unchanged_events_after_row_revision(self):
        reading_fixtures.ReadingCacheTests.setUp(self)
        from source_snapshot import save
        from runtime import atomic,digest
        from pipeline import reading_view
        from worker import JobClient
        queue=Preparations(self.root/'state');daily=self.daily();daily['mode']='reading_only';queue.enqueue(daily)
        def collect(_,directory,*args,**kwargs):
            directory.mkdir(parents=True,exist_ok=True)
            stamp='2026-09-08T10:00:00+08:00'
            source_fixtures.WorkdayScopeTests().source(directory,[stamp],'2026-09-13T02:00:00+08:00')
            row=json.loads((directory/'row.json').read_text())
            row['response_body']=json.dumps([dict(content='合成完整原文与失败反证。'*6000,request_id='0',created_at=stamp)])
            sha=save(directory/'row.json',row)
            snap=json.loads((directory/'snapshot.json').read_text());snap['rows'][0]['sha256']=sha
            sha=save(directory/'snapshot.json',snap);atomic(directory/'latest.json',dict(path='snapshot.json',sha256=sha))
        base=SimpleNamespace(config=self.config)
        from preparation import prepared_packet
        captured=[]
        def capture(*args,**kwargs):
            value=prepared_packet(*args,**kwargs);captured.append(value);return value
        with patch('source_snapshot.collect',side_effect=collect),patch('preparation.prepared_packet',side_effect=capture):
            self.assertTrue(queue.tick(None,base,[999],new_request_limit=100))
        self.assertEqual(captured[0][1],digest(captured[0][0]))
        reading=json.loads(next((self.root/'state/preparations').rglob('prepared-reading.json')).read_text())
        self.assertEqual(captured[0][1],reading['packet_sha256'])
        self.assertEqual('prepared',queue.snapshot()[0]['state']);sent=self.calls
        self.assertGreater(sent,0)
        self.assertFalse(list((self.root/'state/preparations').rglob('nodes.sqlite')))
        sources=next((self.root/'state/preparations').glob('*/sources/2026-09-08'))
        # 同一聚合行在后续日期追加正文：存储哈希改变，已有事件的原文和角色未改变。
        row=json.loads((sources/'row.json').read_text());entries=json.loads(row['response_body'])
        entries.append(dict(content='次日新增完整内容',request_id='later',created_at='2026-09-09T10:00:00+08:00'))
        row['response_body']=json.dumps(entries);sha=save(sources/'row.json',row)
        snap=json.loads((sources/'snapshot.json').read_text());snap['rows'][0]['sha256']=sha
        sha=save(sources/'snapshot.json',snap);atomic(sources/'latest.json',dict(path='snapshot.json',sha256=sha))
        packet=prepare(sources,2,'2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00',daily['workday_scope'])
        final=JobClient(self.config,self.root/'final',100,100);final.reading_content_identity=True;final.reading_efficiency=True
        final.reading_cache_root=self.root/'state/read-pages';final.reading_plan_root=self.root/'state/reading-plans'
        from bounded_read import read_all
        status=read_all(packet,self.root/'final/reading',client=final,limit=None)
        self.assertEqual('reading_complete_pending_reconciliation',status['state'])
        self.assertEqual(1,self.calls-sent)
        self.assertEqual(1,final.budget.used())
        old_calls=self.calls
        self.assertFalse(queue.tick(None,base,[999]));self.assertEqual(old_calls,self.calls)
        self.assertFalse(list((self.root/'state').rglob('bundle.json')))

    def test_week_waits_for_last_day_and_never_treats_missing_budget_as_zero(self):
        from runtime import atomic
        with tempfile.TemporaryDirectory() as tmp:
            queue=Preparations(tmp);daily=self.daily('2026-09-11');daily['mode']='reading_only'
            job=dict(user_id=2,user_email='synthetic@example.test',workday_scope=daily['workday_scope'])
            with self.assertRaises(Paused):queue.ready_budget(job)
            queue.enqueue(daily)
            with queue.connect() as db:key=db.execute('SELECT key FROM preparations').fetchone()[0]
            queue.update(key,'prepared','合成完成','2026-09-10')
            with self.assertRaises(Paused):queue.ready_budget(job)
            queue.update(key,'unknown','原请求不明')
            with self.assertRaises(Paused) as caught:queue.ready_budget(job)
            self.assertEqual('unknown',caught.exception.state)
            queue.update(key,'prepared','合成完成','2026-09-11')
            with self.assertRaises(FileNotFoundError):queue.ready_budget(job)
            atomic(Path(tmp)/'preparations'/key/'request-budget.json',dict(version=1,entries={'old-receipt':{}}))
            proof=queue.ready_budget(job);self.assertEqual(1,proof['used']);self.assertEqual(64,len(proof['sha256']))

    def test_legacy_preparation_database_migration_preserves_unknown_and_dates(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            with sqlite3.connect(Path(tmp)/'preparations.sqlite') as db:
                db.execute("CREATE TABLE preparations(key TEXT PRIMARY KEY,user_id INTEGER,user_email TEXT,scope TEXT,through_date TEXT,prepared_through TEXT,state TEXT,updated REAL,message TEXT)")
                db.execute("INSERT INTO preparations VALUES('legacy',2,'synthetic@example.test','{}','2026-09-08',NULL,'unknown',0,'原请求仍待核')")
            queue=Preparations(tmp)
            with queue.connect() as db:row=dict(db.execute('SELECT * FROM preparations').fetchone())
            self.assertEqual(('unknown','2026-09-08','navigation','原请求仍待核'),(row['state'],row['through_date'],row['mode'],row['message']))


if __name__=='__main__':unittest.main()
