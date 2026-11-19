"""跨语言冻结范围与真实审计包装的工作日筛选；不调用模型。"""
import copy
import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from source_snapshot import save,QUERY_VERSION
from runtime import atomic
from pipeline import prepare,evaluate_week,evaluate_month,Paused
from month_evidence import freeze_empty,audited_empty
from workday_scope import window,validate,load,registry,DEFAULT,daily_window
from work_calendar import Calendar


class WorkdayScopeTests(unittest.TestCase):
    def test_daily_window_seals_at_23_and_delivers_next_morning_without_week_calendar(self):
        scope=daily_window('2026-11-18')
        self.assertEqual(validate(scope),scope)
        self.assertEqual(scope['period_end'],'2026-11-19')
        self.assertEqual(scope['working_dates'],['2026-11-18'])
        self.assertEqual(scope['observed_until'],'2026-11-18T21:00:00+08:00')
        self.assertEqual(scope['freeze_at'],'2026-11-18T23:00:00+08:00')
        self.assertEqual(scope['candidate_due_at'],'2026-11-19T08:00:00+08:00')
        for field,value in [('period_end','2026-11-20'),('working_dates',['2026-11-17']),
                ('freeze_at','2026-11-18T22:00:00+08:00'),('registry_sha256','0'*64)]:
            with self.assertRaisesRegex(ValueError,'固定北京时间'):
                validate(dict(scope,**{field:value}))

    def test_session_usage_window_keeps_only_0800_to_before_2100_as_person_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            times=['2026-11-04T07:59:59+08:00','2026-11-04T08:00:00+08:00',
                '2026-11-04T20:59:59+08:00','2026-11-04T21:00:00+08:00']
            self.source(root,times,'2026-11-07T03:00:00+08:00')
            packet=prepare(root,2,'2026-11-02T00:00:00+08:00','2026-11-09T00:00:00+08:00',
                workday_scope=window('2026-11-02','2026-11-09'),session_review=True)
            candidates=[e for e in packet['events'] if e['actor_class']=='candidate_user']
            self.assertEqual(times[1:3],[e['at'] for e in candidates])
            self.assertEqual(4,len(packet['events']))
            excluded=[e for e in packet['events'] if e.get('usage_window_exclusion')]
            self.assertEqual([times[0],times[-1]],[e['at'] for e in excluded])
            self.assertTrue(packet['usage_window']['end_exclusive'])
            self.assertTrue(all(e['timestamp_basis']=='audit_capture' for e in packet['events']))

    def test_session_preparation_keeps_tool_types_and_response_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root,['2026-11-04T10:00:00+08:00'],'2026-11-07T03:00:00+08:00')
            row=json.loads((root/'row.json').read_text())
            row['request_body']=json.dumps(dict(audit_events=[dict(kind='user',text='请先检查失败'),
                dict(kind='tool_call',text='Tool: exec\nCall ID: local-call\n\n执行测试'),
                dict(kind='tool_result',text='Call ID: local-call\nStatus: error\n\n测试失败')]))
            row['response_body']=json.dumps(dict(audit_events=[dict(kind='assistant',text='测试失败，尚未完成。')]))
            sha=save(root/'row.json',row)
            snapshot=json.loads((root/'snapshot.json').read_text());snapshot['rows'][0]['sha256']=sha
            sha=save(root/'snapshot.json',snapshot);atomic(root/'latest.json',dict(path='snapshot.json',sha256=sha))
            result=prepare(root,2,'2026-11-02T00:00:00+08:00','2026-11-09T00:00:00+08:00',
                workday_scope=window('2026-11-02','2026-11-09'),session_review=True)
            self.assertEqual(['user','tool_call','tool_result','assistant'],[e['audit_kind'] for e in result['events']])
            self.assertEqual(['candidate_user','context','context','context'],[e['actor_class'] for e in result['events']])
            self.assertEqual('测试失败，尚未完成。',result['events'][-1]['text'])
            self.assertTrue(all(e['source']['capture_source']['path']=='row.json' for e in result['events']))

    def test_selected_packet_requires_offline_freeze_and_detects_source_or_scope_changes(self):
        from pipeline import selected_packet
        from runtime import digest
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'sources';source.mkdir()
            registered=json.loads(DEFAULT.read_text())
            registered['weeks']['2026-11-02']['evaluation_dates']=['2026-11-04','2026-11-05','2026-11-06']
            registry_path=root/'registry.json';atomic(registry_path,registered)
            self.source(source,['2026-11-02T10:00:00+08:00','2026-11-04T10:00:00+08:00'],'2026-11-07T03:00:00+08:00')
            with patch.dict(os.environ,{'ZHIYONGLI_WORKDAY_REGISTRY':str(registry_path)}):
                scope=window('2026-11-02','2026-11-09');cache=root/'prepared'
                args=(cache,source,2,'2026-11-02T00:00:00+08:00','2026-11-09T00:00:00+08:00',scope)
                with self.assertRaises(Paused):selected_packet(*args)
                first=selected_packet(*args,create=True)
                before=(cache/'packet.json').read_bytes()
                with patch('pipeline.prepare',side_effect=AssertionError('在线续读不得重新展开完整来源')):
                    self.assertEqual(first,selected_packet(*args))
                self.assertEqual(before,(cache/'packet.json').read_bytes())
                self.assertEqual(1,len(first['events']))
                self.assertEqual('registered_person_workdays',first['source_scope'])
                self.assertEqual(digest(first),digest(prepare(source,2,args[3],args[4],scope)))
                (source/'row.json').write_text('{}')
                with self.assertRaisesRegex(ValueError,'原来源'):selected_packet(*args)
                self.assertEqual(before,(cache/'packet.json').read_bytes())

    def test_selected_packet_never_overwrites_an_unsealed_or_damaged_packet(self):
        from pipeline import selected_packet
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'sources';source.mkdir();cache=root/'prepared';cache.mkdir()
            registered=json.loads(DEFAULT.read_text())
            registered['weeks']['2026-11-02']['evaluation_dates']=['2026-11-04']
            path=root/'registry.json';atomic(path,registered)
            self.source(source,['2026-11-04T10:00:00+08:00'],'2026-11-07T03:00:00+08:00')
            with patch.dict(os.environ,{'ZHIYONGLI_WORKDAY_REGISTRY':str(path)}):
                args=(cache,source,2,'2026-11-02T00:00:00+08:00','2026-11-09T00:00:00+08:00',window('2026-11-02','2026-11-09'))
                (cache/'packet.json').write_text('unsealed')
                with self.assertRaisesRegex(ValueError,'未封口'):selected_packet(*args,create=True)
                (cache/'packet.json').unlink()
                selected_packet(*args,create=True)
                (cache/'packet.json').write_text('{}')
                with self.assertRaisesRegex(ValueError,'签名变化'):selected_packet(*args)

    def test_registered_three_days_keep_week_identity_and_reuse_unchanged_reading_pages(self):
        from bounded_read import plan
        full=window('2026-11-02','2026-11-09')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);registered=json.loads(DEFAULT.read_text())
            registered['weeks']['2026-11-02']['evaluation_dates']=['2026-11-04','2026-11-05','2026-11-06']
            path=root/'registry.json';atomic(path,registered)
            self.source(root,[d+'T10:00:00+08:00' for d in full['working_dates']],'2026-11-07T03:00:00+08:00')
            original=prepare(root,2,'2026-11-02T00:00:00+08:00','2026-11-09T00:00:00+08:00',full)
            with patch.dict(os.environ,{'ZHIYONGLI_WORKDAY_REGISTRY':str(path)}):
                scope=window('2026-11-02','2026-11-09');validate(scope);validate(full)
                selected=prepare(root,2,original['since'],original['until'],scope)
                self.assertEqual(full['working_dates'],scope['working_dates'])
                self.assertNotEqual(full['registry_sha256'],scope['registry_sha256'])
                self.assertEqual((original['since'],original['until']),(selected['since'],selected['until']))
                self.assertEqual(3,len(selected['events']))
                self.assertEqual(plan(original)['pages'][2:],plan(selected)['pages'])
                self.assertEqual(5,len(original['events']))
                changed=copy.deepcopy(scope);changed['evaluation_dates']=['2026-11-02']
                with self.assertRaises(ValueError):validate(changed)
                with self.assertRaises(ValueError):window('2026-11-01','2026-12-01')

    def test_untrusted_or_invalid_subset_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'registry.json'
            for dates in ([],['2026-11-07'],['2026-11-05','2026-11-04'],['2026-11-04','2026-11-04']):
                registered=json.loads(DEFAULT.read_text());registered['weeks']['2026-11-02']['evaluation_dates']=dates;atomic(path,registered)
                with patch.dict(os.environ,{'ZHIYONGLI_WORKDAY_REGISTRY':str(path)}):
                    with self.assertRaises(ValueError):window('2026-11-02','2026-11-09')

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
        def source(_,__,a,b,workday_scope=None,metadata_cache=None):
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
