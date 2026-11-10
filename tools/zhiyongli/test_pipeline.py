"""真实采集格式、本人归属、截断和恢复边界；不把合成结果当评分校准。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime
from source_frames import frames
from source_snapshot import save
from protocol_events import extract
from pipeline import assemble, received, month_fragment, Paused

class PipelineTests(unittest.TestCase):
    def test_rollup_truncation_stays_on_corresponding_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            entries=[dict(content='本次用户输入',request_id='one',created_at='2026-09-07T10:00:00+08:00',truncated=False),
                     dict(content='后续用户输入',request_id='two',created_at='2026-09-07T11:00:00+08:00',truncated=False)]
            replies=[dict(e,content='模型回复',truncated=e['request_id']=='one') for e in entries]
            row=dict(id=1,user_id=2,session_id='s',request_id='one',request_count=2,
                created_at=entries[0]['created_at'],request_body=json.dumps(entries),response_body=json.dumps(replies),
                request_truncated=False,response_truncated=True)
            sha=save(root/'row.json',row)
            packet=frames(root,dict(snapshot_at='2026-10-01T00:00:00+08:00',rows=[dict(path='row.json',sha256=sha)]))
            events=extract(packet)['events']
            self.assertEqual([False,False,True,False],[e['truncated'] for e in events])
            self.assertEqual(['candidate_user','candidate_user','context','context'],[e['actor_class'] for e in events])

    def test_captured_text_and_tool_events_are_not_missing_json(self):
        frame=dict(frame='audit:1:request:0',text='帮我分析这份方案',field='request',at='2026-09-07T09:00:00+08:00',
            timestamp_unknown=False,truncated=False,session_id='s',request_id='one',user_id=2,text_sha256='a'*64)
        a=extract(dict(frames=[frame],issues=[],snapshot_at='2026-10-01T00:00:00+08:00'))
        self.assertEqual([],a['issues']);self.assertEqual('candidate_user',a['events'][0]['actor_class'])
        frame['text']=json.dumps(dict(audit_events=[dict(kind='tool_result',text='执行成功'),dict(kind='user',text='继续检查')]))
        b=extract(dict(frames=[frame],issues=[],snapshot_at='2026-10-01T00:00:00+08:00'))
        self.assertEqual(['context','candidate_user'],[e['actor_class'] for e in b['events']])

    def test_unknown_receipt_is_never_treated_as_low_score(self):
        for state in ('requesting','unknown'):
            with self.assertRaises(Paused) as ex:received(dict(status=state))
            self.assertEqual('unknown',ex.exception.state)

    def test_one_goal_weekend_input_and_local_unknown_are_retained(self):
        # 周末也是自然周材料；父目标未决不删除可确认本人输入。
        event=dict(event='e',text='请解释这个报错',actor_class='candidate_user',timestamp_unknown=False,
            at='2026-09-13T10:00:00.123456789+08:00',session_id='s',request_id='r',truncated=False)
        packet=dict(user_id='2',since='2026-09-07T00:00:00+08:00',until='2026-09-14T00:00:00+08:00',events=[event],issues=[])
        identified=dict(materials=[dict(event_id='e',classification='human',human_quotes=[event['text']],reason='本人请求',duplicate_of=None)],
            goals=[dict(goal_id='g',title='解释报错',purpose='获得解释',boundary='unresolved',boundary_reason='父目标未知',event_ids=['e'])],unassigned_events=[])
        unit=assemble(packet,identified)
        self.assertEqual(['e-human-0'],unit['person_events'])
        self.assertEqual(1,len(unit['source_keys']))
        self.assertEqual('unresolved',unit['source_units'][0]['boundary'])
        self.assertEqual(event['text'],unit['events'][-1]['text'])

    def test_fragment_keeps_identity_without_future_or_week_summary(self):
        unit=dict(key='person:week',events=[
            dict(event='before',actor_class='human',at='2026-09-28T10:00:00+08:00',human_spans=[[0,2]]),
            dict(event='inside',actor_class='human',at='2026-09-30T10:00:00+08:00',human_spans=[[0,2]]),
            dict(event='future',actor_class='human',at='2026-10-01T10:00:00+08:00',human_spans=[[0,2]])],
            source_units=[dict(key='stable-task',title='月末之后新增要求',purpose='未来内容',person_events=['before','inside','future'])],
            source_keys=['stable-task'],reading_notes=[dict(event_id='future',summary='不能回填')])
        fragment=month_fragment(unit,'2026-09-30T00:00:00+08:00','2026-10-01T00:00:00+08:00')
        self.assertEqual(['inside'],fragment['person_events'])
        self.assertEqual(['stable-task'],fragment['source_keys'])
        self.assertEqual('context',fragment['events'][0]['actor_class'])
        self.assertNotIn('future',[e['event'] for e in fragment['events']])
        self.assertEqual([],fragment['reading_notes'])
        self.assertNotIn('月末之后新增要求',str(fragment))
        self.assertEqual('human',unit['events'][0]['actor_class'])

    def test_local_input_remains_observation_without_inventing_task(self):
        from eligibility import build
        event=dict(event='local-human',text='继续看看',at='2026-09-07T10:00:00+08:00',human_spans=[[0,4]])
        unit=dict(events=[event],person_events=['local-human'],source_keys=['local'],
            source_units=[dict(key='local',source_kind='local_behavior',person_events=['local-human'])])
        report=dict(period_type='week',period_start='2026-09-07',period_end='2026-09-14')
        result=build(report,[(unit,dict(evidence=[]),dict(cells=[]))])
        self.assertEqual(0,result['task_count'])
        self.assertEqual(1,len(result['observations']))
        self.assertEqual([],result['observations'][0]['task_keys'])

    def test_bounded_read_covers_every_character_and_reuses_receipts(self):
        from bounded_read import plan,read_all,anchors
        class Reader:
            def __init__(self):self.sent=0
            def read_page(self,page,receipt):
                if receipt.exists():return json.loads(receipt.read_text())
                self.sent+=1
                notes={f['fragment_id']:dict(summary='逐页上下文',facts=[dict(anchor_id=anchors(f)[0]['anchor_id'],fact='文字依据')],
                    limitations=[],open_questions=[]) for f in page['fragments']}
                record=dict(status='received',request_sha256='a'*64,response=dict(status='completed',
                    output=[dict(content=[dict(type='output_text',text=json.dumps(dict(notes=notes)))])]))
                runtime.atomic(receipt,record);return record
        packet=dict(events=[dict(event='e',text='测试\\n"文字😀'*1500,actor_class='context')],issues=[])
        reading=plan(packet,8192)
        self.assertEqual(packet['events'][0]['text'],''.join(f['text'] for p in reading['pages'] for f in p['fragments']))
        with tempfile.TemporaryDirectory() as tmp:
            reader=Reader();first=read_all(packet,tmp,client=reader,max_bytes=8192,limit=999)
            self.assertEqual(first['pages'],len(first['received_pages']))
            sent=reader.sent
            self.assertEqual(first['received_pages'],read_all(packet,tmp,client=reader,max_bytes=8192,limit=999)['received_pages'])
            self.assertEqual(sent,reader.sent)
            receipt=next(Path(tmp).glob('*/*.receipt.json'))
            runtime.atomic(receipt,dict(status='unknown'))
            self.assertEqual('reading_blocked',read_all(packet,tmp,client=reader,max_bytes=8192,limit=999)['state'])
            self.assertEqual(sent,reader.sent)

    def test_model_error_detail_is_private_and_existing_rejection_not_resent(self):
        import io,urllib.error
        from model_config import ModelConfig
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key=root/'key';key.write_text('test-key-only');key.chmod(0o600)
            client=runtime.CompanyClient(ModelConfig('https://ai.apsatcom.com',key))
            class Opener:
                def open(self,*args,**kwargs):
                    raise urllib.error.HTTPError('https://ai.apsatcom.com/v1/responses',400,'bad',{},io.BytesIO(b'{"message":"context too long"}'))
            with patch.object(ModelConfig,'opener',return_value=Opener()) as opened:
                receipt=root/'call.receipt.json';record=client._call(dict(input='a'),receipt)
                self.assertEqual('rejected',record['status'])
                self.assertIn('context too long',record['error_detail'])
                self.assertEqual(0o600,receipt.stat().st_mode&0o777)
                client._call(dict(input='a'),receipt)
                self.assertEqual(1,opened.call_count)
                with self.assertRaisesRegex(ValueError,'上下文预算'):
                    client._call(dict(input='x'*200_001),root/'large.receipt.json')
                self.assertFalse((root/'large.receipt.json').exists())

    def test_worker_budget_survives_restart_without_deleting_receipts(self):
        from worker import JobClient
        from model_config import ModelConfig
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime.atomic(root/'prior.receipt.json',dict(status='received'))
            client=JobClient(ModelConfig('https://ai.apsatcom.com',root/'unused.key'),root,1)
            with self.assertRaises(Paused) as ex:client._call(dict(input='next'),root/'next.receipt.json')
            self.assertEqual('needs_review',ex.exception.state)
            self.assertTrue((root/'prior.receipt.json').exists())

    def test_known_oversized_candidates_block_before_paid_reading(self):
        from pipeline import reading_view
        packet=dict(events=[dict(event='long-input',actor_class='candidate_user',text='较长的本人候选材料'*20000)])
        with tempfile.TemporaryDirectory() as tmp, patch('bounded_read.read_all') as read:
            with self.assertRaises(Paused) as ex:
                reading_view(packet,Path(tmp),None,lambda *args:None)
            self.assertEqual('needs_review',ex.exception.state)
            self.assertIn('未发送',str(ex.exception))
            read.assert_not_called()

    def test_worker_different_database_identity_blocks_before_model(self):
        from worker import process
        from model_config import ModelConfig
        from types import SimpleNamespace
        class Database:
            def __init__(self):self.updates=[]
            def execute(self,sql,params):self.updates.append(params)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'1'/'sources';root.mkdir(parents=True)
            signature=save(root/'snapshot.json',dict(users=[dict(id=2,email='other@example.test')],rows=[]))
            save(root/'latest.json',dict(path='snapshot.json',sha256=signature))
            job=dict(id=1,user_id=2,user_email='selected@example.test',period_type='week',period_start='2026-09-07',period_end='2026-09-14')
            from workday_scope import window
            job['workday_scope']=window('2026-09-07','2026-09-14')
            db=Database();send=lambda *args:self.fail('身份不一致不能接入结果')
            with patch.object(runtime.CompanyClient,'identify',side_effect=AssertionError('不得发送模型请求')):
                process(db,job,tmp,None,SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',root/'unused.key')),send,[123])
            self.assertEqual('failed',db.updates[-1][0])
            self.assertIn('人员映射不一致',db.updates[-1][-2])

    def test_single_oversized_candidate_blocks_even_with_merge_executor_before_reading(self):
        from pipeline import reading_view
        from unittest.mock import Mock
        p=dict(events=[dict(event='test',at='2026-09-07T10:00:00+08:00',actor_class='candidate_user',text='原文'*50000)])
        with tempfile.TemporaryDirectory() as tmp,patch('bounded_read.read_all') as read:
            with self.assertRaises(Paused) as ex:reading_view(p,Path(tmp),Mock(),lambda *args:None)
            self.assertIn('单条候选输入',str(ex.exception));read.assert_not_called()

    def test_known_oversized_navigation_blocks_before_any_paid_page(self):
        from pipeline import reading_view
        from unittest.mock import Mock
        p=dict(events=[dict(event='context-'+str(i),at='2026-09-07T10:00:00+08:00',actor_class='context',text='保留全部反馈')
            for i in range(2000)]+[dict(event='user',at='2026-09-07T11:00:00+08:00',actor_class='candidate_user',text='核对执行结果')])
        with tempfile.TemporaryDirectory() as tmp,patch('bounded_read.read_all') as read:
            with self.assertRaises(Paused) as ex:reading_view(p,Path(tmp),Mock(),lambda *args:None)
            self.assertIn('固定来源索引',str(ex.exception));read.assert_not_called()

if __name__=='__main__':unittest.main()
