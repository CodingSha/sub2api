"""验证周评只消费五日冻结原文，保留空日、来源签名与真实时间，不平均日分。"""
import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from runtime import atomic, digest
from weekly_daily import evaluate, prepared, sha, checked_call
from workday_scope import window


def receipt(root, stage, value):
    payload=root/(stage+'.input.json')
    signature=digest(dict(endpoint='https://ai.apsatcom.com/v1/responses',payload=json.loads(payload.read_text()))) if payload.exists() else 'a'*64
    atomic(root/(stage+'.receipt.json'),dict(status='received',request_sha256=signature,
        response=dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(value,ensure_ascii=False))])])) )


def fixture(state, active=True):
    job=dict(id=100,user_id=2,user_email='person@example.test',period_start='2026-11-16',period_end='2026-11-23',
        workday_scope=window('2026-11-16','2026-11-23'))
    sources=[]
    for i,day in enumerate(job['workday_scope']['working_dates'],1):
        review=dict(version='daily-task-review-20261120.2',request_digest='a'*64,
            source_labels={},source_sessions={},conversations={},behaviors={})
        source=dict(report_id=i,source_job_id=i,user_id=2,user_email=job['user_email'],period_start=day,
            source_digest='c'*64,request_digest='a'*64,publication_status='published',score_status='no_activity',review=review)
        if active and day=='2026-11-18':
            text='请比较两份报价并说明差异原因。';claim='要求比较报价并解释差异'
            row=dict(id='u18',role='user',text=text,at=day+'T09:36:00+08:00',excerpt=False,names=[])
            key='b'+digest([row['id'],claim])[:20]
            review.update(source_labels={'u18':'报价比较'},source_sessions={'u18':'task-1'},
                behaviors={key:dict(primary_item='P1',source_ref='u18',claim=claim)},
                conversations={'task-1':dict(title='报价比较',goal='比较差异',outcome='',workflow=[],tool_index=[],
                    records=[dict(id='u18',role='user',text=text,excerpt=False,names=[],source_order=0,source_hash=sha(text))])})
            root=state/str(i)/'review';root.mkdir(parents=True)
            (root/'source').mkdir()
            atomic(root/'source/records.json',{'u18':row})
            atomic(root/'selected-tasks.json',[dict(id='task-1',records=[row])])
            atomic(root/'quality.json',dict(quality_status='model_checked'))
            atomic(root/'behavior-catalog.json',review['behaviors'])
            atomic(root/'source-fragments.json',{'f1':dict(record_id='u18',quote=text)})
            receipt(root,'facts',dict(actions=[dict(record_id='u18',action=claim)]))
            atomic(root/'behaviors.input.json',dict(input=json.dumps(dict(actions={'action-1':dict(record_id='u18',action=claim)}))))
            receipt(root,'behaviors',dict(routes={'action-1':dict(fragment_id='f1',primary_item='P1',origin='personal')}))
            source['score_status']='scored'
        sources.append(source)
    return sources,job


class Client:
    config=SimpleNamespace(model='deepseek-v4.1-flash-local',responses_url='https://example.test/responses')
    def __init__(self): self.sent=[]
    def _call(self,payload,path):
        data=json.loads(payload['input']);self.sent.append(data)
        if path.name.startswith('quality'):
            value=dict(issues=[])
        else:
            value=dict(needs_records=[],search_queries=[],items={code:dict(band=80,negative=None,
                refs=item['eligible_refs'],context_refs=[],user_action='要求比较报价。',context_support='任务原文支持比较对象。',
                basis='你明确了比较对象及解释差异的目标。',next_band='本次材料尚未体现更突出的表达精度。') for code,item in data['items'].items()})
        row=dict(status='received',request_sha256=digest(dict(endpoint=self.config.responses_url,payload=payload)),
            response=dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(value,ensure_ascii=False))])]))
        atomic(path,row);return row


class WeeklyDailyTests(unittest.TestCase):
    def test_large_quality_review_splits_whole_tasks_without_cutting_text(self):
        tasks=[dict(id='t'+str(i),records=[dict(id='u'+str(i),role='user',text='x'*100000)]) for i in range(2)]
        client=SimpleNamespace(config=SimpleNamespace(model='test'),output_token_limit=4500)
        data=dict(tasks=tasks,read_records=[])
        with TemporaryDirectory() as tmp, patch('weekly_daily.call',return_value=dict(issues=[])) as send:
            checked_call(client,Path(tmp),'quality-0',data,dict(type='object'),'核对事实')
            self.assertEqual(len(send.call_args_list),2)
            self.assertEqual([t for args in send.call_args_list for t in args.args[3]['tasks']],tasks)
            self.assertTrue((Path(tmp)/'quality-0-batch-coverage.json').exists())
        with TemporaryDirectory() as tmp, patch('weekly_daily.call') as send:
            with self.assertRaisesRegex(ValueError,'超过既有全局预算'):
                checked_call(client,Path(tmp),'judgment',data,dict(type='object'),'判断')
            send.assert_not_called()

    def test_repair_input_and_later_received_repair_do_not_replace_published_allocation(self):
        with TemporaryDirectory() as tmp:
            state=Path(tmp);sources,job=fixture(state);root=state/'3/review'
            atomic(root/'behaviors-repair.input.json',json.loads((root/'behaviors.input.json').read_text()))
            first=prepared(sources,job,state)
            receipt(root,'behaviors-repair',dict(routes={'action-1':dict(fragment_id='f1',primary_item='M1',origin='personal')}))
            self.assertEqual(first,prepared(sources,job,state))
            receipt(root,'behaviors-repair',dict(routes={'action-1':dict(fragment_id='f1',primary_item='P1',origin='supplied')}))
            with self.assertRaisesRegex(ValueError,'不能唯一恢复'):prepared(sources,job,state)

    def test_rejudge_originals_preserves_real_time_and_does_not_consume_daily_scores(self):
        with TemporaryDirectory() as tmp:
            state=Path(tmp);sources,job=fixture(state);client=Client()
            first=evaluate(sources,job,state,state/'week',client)['reports'][0]
            self.assertEqual(first['score'],'16.0')
            self.assertEqual(first['exact'],'16')
            self.assertEqual(first['score_status'],'scored')
            self.assertEqual(first['dimensions'][0]['score'],'80.0')
            self.assertEqual(first['items'][0]['evidence'][0]['occurred_at'],'2026-11-18T09:36:00+08:00')
            self.assertEqual(first['items'][0]['evidence'][0]['source_hash'],sha('请比较两份报价并说明差异原因。'))
            self.assertEqual(len(first['weekly_review']['sources']),5)
            self.assertIsNone(first['dimensions'][1]['score'])
            self.assertEqual(len(client.sent),2)
            self.assertNotIn('source_digest',json.dumps(client.sent))
            self.assertNotIn('score_status',json.dumps(client.sent))
            self.assertNotIn('daily_review',first)
            client2=Client();sources2=copy.deepcopy(sources)
            for row in sources2: row.update(score='1.0',items=[dict(exact='1')])
            second=evaluate(sources2,job,state,state/'week-other',client2)['reports'][0]
            self.assertEqual(first['score'],second['score'])
            self.assertEqual(client.sent,client2.sent)

    def test_empty_week_requires_all_five_sources_and_never_calls_model(self):
        with TemporaryDirectory() as tmp:
            state=Path(tmp);sources,job=fixture(state,active=False);client=Client()
            result=evaluate(sources,job,state,state/'week',client)['reports'][0]
            self.assertEqual(result['score_status'],'no_activity');self.assertEqual(result['score'],'0.0');self.assertFalse(client.sent)
            with self.assertRaisesRegex(ValueError,'缺少工作日'):prepared(sources[:4],job,state)

    def test_wrong_identity_changed_body_and_unchecked_day_are_rejected(self):
        for mode in ('identity','body','quality','signature','allocation_input'):
            with self.subTest(mode=mode),TemporaryDirectory() as tmp:
                state=Path(tmp);sources,job=fixture(state);active=sources[2]
                if mode=='identity':active['user_id']=3
                if mode=='body':active['review']['conversations']['task-1']['records'][0]['text']='替换原文'
                if mode=='quality':atomic(state/'3/review/quality.json',dict(quality_status='failed'))
                if mode=='signature':active['request_digest']='b'*64
                if mode=='allocation_input':atomic(state/'3/review/behaviors.input.json',dict(input='{}'))
                with self.assertRaises(ValueError):prepared(sources,job,state)


@unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'),'需要显式本地专用测试库')
class WeeklyWorkerTests(unittest.TestCase):
    def test_dispatch_import_resume_uses_same_frozen_bundle_without_source_or_model_resend(self):
        from test_worker_fullcycle import WorkerFullCycleTests
        from worker import tick
        case=WorkerFullCycleTests();self.addCleanup(case.doCleanups);case.setUp()
        sources,job=fixture(case.state)
        for source in sources:source['user_email']='local-person@example.test'
        manifest=case.state/'daily-week-sources/2026-11-16/2.json';atomic(manifest,sources)
        scope='weekly-from-daily-20261122.1:synthetic'
        case.db.execute("UPDATE zhiyongli_jobs SET period_start='2026-11-16',period_end='2026-11-23',scope_key=%s,workday_scope=%s::jsonb WHERE id=%s",
            (scope,json.dumps(job['workday_scope']),case.job_id))
        os.environ['ZHIYONGLI_ACTIVE_SCOPE_KEY']=scope
        client=Client();sent=[]
        def model(_,payload,path,**kwargs):return client._call(payload,path)
        def importer(job,bundle):
            sent.append(copy.deepcopy(bundle))
            if len(sent)==1:raise RuntimeError('合成导入应答中断')
            return case.send(job,bundle)
        importer.base_url=case.send.base_url;importer.draft_only=False
        with patch('runtime.CompanyClient._call',model),patch('worker.collect') as collect,patch('worker.prepare') as prepare:
            self.assertTrue(tick(case.db,case.source,case.state,case.client,importer,[999],72,1))
            self.assertEqual(('failed',),case.db.execute('SELECT state FROM zhiyongli_jobs WHERE id=%s',(case.job_id,)).fetchone())
            frozen=(case.state/str(case.job_id)/'import-bundle.json').read_bytes()
            count=len(client.sent);self.assertGreater(count,0)
            case.db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=%s",(case.job_id,))
            self.assertTrue(tick(case.db,case.source,case.state,case.client,importer,[999],72,1))
            self.assertEqual(('completed',),case.db.execute('SELECT state FROM zhiyongli_jobs WHERE id=%s',(case.job_id,)).fetchone())
            self.assertEqual(count,len(client.sent));self.assertEqual(sent[0],sent[1])
            self.assertEqual(frozen,(case.state/str(case.job_id)/'import-bundle.json').read_bytes())
            collect.assert_not_called();prepare.assert_not_called()


if __name__=='__main__':unittest.main()
