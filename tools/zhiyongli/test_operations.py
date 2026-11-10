"""只读可观测性区分未完成、过期和历史完成，不发送告警或模型请求。"""
import copy
from datetime import datetime,timezone,timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit
import uuid

from operations import alerts,deadlines,heartbeat,queue_status
from runtime import atomic


class OperationsTests(unittest.TestCase):
    def test_completed_queue_age_is_not_reported_as_waiting(self):
        now=datetime(2026,9,14,tzinfo=timezone.utc)
        class DB:
            def execute(self,_):return self
            def fetchall(self):return [('completed',2,now-timedelta(days=4),now),('queued',1,now-timedelta(seconds=9),now)]
        result=queue_status(DB(),now)
        self.assertIsNone(result[0]['oldest_wait_seconds'])
        self.assertEqual(4*86400,result[0]['oldest_age_seconds'])
        self.assertEqual(9,result[1]['oldest_wait_seconds'])

    def test_deadlines_count_unknown_dates_and_blocked_states_without_exposing_people(self):
        now=datetime(2026,9,14,tzinfo=timezone.utc)
        class DB:
            def execute(self,query,args):
                self.args=args;return [('queued',dict(candidate_due_at=(now-timedelta(hours=1)).isoformat())),
                    ('unknown',None),('needs_review',dict(candidate_due_at='2026-09-14T00:00:00')),
                    ('running',dict(candidate_due_at=(now+timedelta(hours=1)).isoformat()))]
        db=DB();value=deadlines(db,now)
        self.assertEqual(('1.6',),db.args)
        self.assertEqual((4,1,2,3600),(value['pending'],value['overdue'],value['without_deadline'],value['maximum_overdue_seconds']))
        self.assertNotIn('user',json.dumps(value))
        self.assertEqual(1,value['states']['unknown'])

    def test_heartbeat_missing_stale_invalid_and_fresh_remain_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.assertEqual('missing',heartbeat(root,100)['state'])
            for at,expected in [(10,'stale'),(99,'fresh'),(200,'future_timestamp'),(True,'invalid'),(float('nan'),'invalid')]:
                atomic(root/'heartbeat.json',dict(at=at))
                self.assertEqual(expected,heartbeat(root,100)['state'])

    def test_no_budget_alarm_after_completion_and_actions_are_only_returned_codes(self):
        value=dict(deadlines=dict(pending=0,overdue=0,without_deadline=0,states={}),heartbeat=dict(state='fresh'),
            model=dict(states=[],request_budget=dict(remaining=0),circuit=dict(until_at=0)),preparations=[])
        self.assertEqual([],alerts(value,100))
        value['deadlines'].update(pending=2,overdue=1,states=dict(unknown=1))
        value['model']['states']=[dict(status='unknown',count=1)];value['model']['circuit']['until_at']=120
        value['schedule']=dict(state='needs_review');original=copy.deepcopy(value)
        codes={r['code'] for r in alerts(value,100)}
        self.assertEqual({'candidate_overdue','assessment_unknown','model_unknown_requires_reconciliation',
            'global_request_budget_exhausted','service_cooldown','schedule_needs_review'},codes)
        self.assertEqual(original,value)


@unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'),'需要显式本地专用测试库')
class OperationsDatabaseTests(unittest.TestCase):
    def test_hundred_synthetic_jobs_read_only_deadline_and_terminal_counts(self):
        import psycopg
        from psycopg import sql
        dsn=os.environ['ZHIYONGLI_TEST_DSN'];target=urlsplit(dsn)
        self.assertEqual(('127.0.0.1','/zhiyongli_test'),(target.hostname,target.path))
        schema='zy_operations_'+uuid.uuid4().hex;now=datetime(2026,9,14,tzinfo=timezone.utc)
        with psycopg.connect(dsn,autocommit=True) as setup:
            setup.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
            try:
                setup.execute(sql.SQL('CREATE TABLE {}.zhiyongli_jobs(state text,workday_scope jsonb,rules_version text,created_at timestamptz,updated_at timestamptz)').format(sql.Identifier(schema)))
                for i in range(100):
                    state='queued' if i<50 else 'completed' if i<70 else 'running' if i<90 else 'needs_review'
                    due=now-timedelta(hours=1) if i<70 else now+timedelta(hours=1)
                    scope=json.dumps(dict(candidate_due_at=due.isoformat())) if i<90 else None
                    setup.execute(sql.SQL('INSERT INTO {}.zhiyongli_jobs VALUES(%s,%s::jsonb,%s,%s,%s)').format(sql.Identifier(schema)),
                        (state,scope,'1.6',now-timedelta(days=1),now))
                with psycopg.connect(dsn,options='-c search_path='+schema) as reader:
                    reader.execute('SET TRANSACTION READ ONLY')
                    value=deadlines(reader,now);queue=queue_status(reader,now)
                    self.assertEqual((80,50,10),(value['pending'],value['overdue'],value['without_deadline']))
                    self.assertEqual(20,next(r for r in queue if r['state']=='completed')['count'])
                    with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):reader.execute('DELETE FROM zhiyongli_jobs')
                    reader.rollback()
            finally:setup.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__=='__main__':unittest.main()
