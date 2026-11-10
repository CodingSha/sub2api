"""跨语言冻结范围与真实审计包装的工作日筛选；不调用模型。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from source_snapshot import save,QUERY_VERSION
from runtime import atomic
from pipeline import prepare,evaluate_week,evaluate_month,Paused
from month_evidence import freeze_empty,audited_empty
from workday_scope import window,validate,load,registry,DEFAULT
from work_calendar import Calendar


class WorkdayScopeTests(unittest.TestCase):
    def source(self,root,dates,snapshot):
        entries=[dict(content='合成用户输入 '+d,request_id=str(i),created_at=d,truncated=False) for i,d in enumerate(dates)]
        row=dict(id=1,user_id=2,session_id='synthetic',request_id='first',request_count=len(entries),created_at=dates[0],
            request_body=json.dumps(entries),response_body='[]',request_truncated=False,response_truncated=False)
        sha=save(root/'row.json',row)
        snapshot=dict(users=[dict(id=2,email='synthetic@example.test')],snapshot_at=snapshot,rows=[dict(path='row.json',sha256=sha)],query_version=QUERY_VERSION)
        sha=save(root/'snapshot.json',snapshot);atomic(root/'latest.json',dict(path='snapshot.json',sha256=sha))

    def test_default_registry_is_reproducible_from_verified_calendar(self):
        doc=json.loads((Path(__file__).resolve().parents[2]/'deploy/zhiyongli-calendars/cn-2026.json').read_text())
        self.assertEqual(json.loads(DEFAULT.read_text()),registry(Calendar([doc])))

    def test_normal_week_uses_saturday_freeze_without_waiting_for_sunday(self):
        scope=window('2026-11-02','2026-11-09')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root,['2026-11-06T22:00:00+08:00','2026-11-07T01:00:00+08:00'],'2026-11-07T03:00:00+08:00')
            packet=prepare(root,2,'2026-11-02T00:00:00+08:00','2026-11-09T00:00:00+08:00',scope)
            self.assertEqual(1,len(packet['events']));self.assertIn('11-06',packet['events'][0]['text'])
            self.assertEqual(scope,packet['workday_scope'])
            self.assertNotIn('11-07T01:00',str(packet['events']))

    def test_saturday_is_context_sunday_makeup_is_scored_and_later_messages_absent(self):
        scope=window('2026-09-14','2026-09-21')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root,['2026-09-18T10:00:00+08:00','2026-09-19T10:00:00+08:00',
                '2026-09-20T10:00:00+08:00','2026-09-21T01:00:00+08:00'],'2026-09-21T03:00:00+08:00')
            packet=prepare(root,2,'2026-09-14T00:00:00+08:00','2026-09-21T00:00:00+08:00',scope)
            self.assertEqual(['candidate_user','context','candidate_user'],[e['actor_class'] for e in packet['events']])
            self.assertEqual('non_workday_context_only',packet['events'][1]['workday_exclusion'])
            self.assertEqual('2026-09-21T02:00:00+08:00',scope['freeze_at'])

    def test_unclosed_source_and_tampered_working_dates_are_rejected(self):
        scope=window('2026-11-02','2026-11-09')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root,['2026-11-06T10:00:00+08:00'],'2026-11-07T01:59:59+08:00')
            with self.assertRaises(Paused):prepare(root,2,'2026-11-02T00:00:00+08:00','2026-11-09T00:00:00+08:00',scope)
        changed=copy.deepcopy(scope);changed['working_dates'].append('2026-11-07')
        with self.assertRaises(ValueError):validate(changed)
        with self.assertRaises(ValueError):window('2026-12-28','2027-01-04')

    def test_full_holiday_week_retains_context_without_a_person_score(self):
        scope=window('2026-02-16','2026-02-23')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root,['2026-02-18T10:00:00+08:00'],'2026-03-01T00:00:00+08:00')
            packet=prepare(root,2,'2026-02-16T00:00:00+08:00','2026-02-23T00:00:00+08:00',scope)
            self.assertEqual([],scope['working_dates']);self.assertEqual('context',packet['events'][0]['actor_class'])
            with self.assertRaises(Paused) as ex:evaluate_week(packet,root/'review',None)
            self.assertEqual('no_activity',ex.exception.state)
            record=freeze_empty(packet,root/'empty');unit,_,row=audited_empty(record)
            self.assertEqual([],unit['person_events']);self.assertEqual([],row['source_keys'])

    def test_cache_timestamp_does_not_mask_missing_collection_dates(self):
        scope=window('2026-09-07','2026-09-14')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root,['2026-09-08T10:00:00+08:00'],'2026-10-28T00:00:00+08:00')
            snapshot=json.loads((root/'snapshot.json').read_text())
            for fields in (dict(since='2026-09-08T00:00:00+08:00'),dict(until='2026-09-11T00:00:00+08:00')):
                sha=save(root/'snapshot.json',dict(snapshot,**fields));atomic(root/'latest.json',dict(path='snapshot.json',sha256=sha))
                with self.assertRaises(Paused) as ex:
                    prepare(root,2,'2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00',scope)
                self.assertIn('采集',str(ex.exception));self.assertEqual('needs_review',ex.exception.state)

    def test_scoped_month_preserves_three_week_qualification_and_scope_contract(self):
        from test_execution_paths import packet as fixture_packet,FixtureClient
        def source(_,__,a,b,workday_scope=None):
            p=fixture_packet(a,b)
            if workday_scope is not None:p['workday_scope']=workday_scope
            return p
        scope=window('2026-09-01','2026-10-01')
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.prepare',side_effect=source):
            bundle=evaluate_month('synthetic','test','2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',
                Path(tmp),FixtureClient(),workday_scope=scope)
            report=bundle['reports'][0]
            self.assertEqual('80.0',report['score']);self.assertEqual(3,report['eligibility']['valid_weeks'])
            self.assertEqual(scope,report['workday_scope']);self.assertEqual(scope['version'],report['policy_versions']['scope'])
            self.assertTrue(all(w['workday_scope']['registry_sha256']==scope['registry_sha256'] for w in report['weekly_reports']))


if __name__=='__main__':unittest.main()
