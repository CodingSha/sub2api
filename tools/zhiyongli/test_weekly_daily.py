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
from weekly_daily import evaluate, prepared, sha, checked_call, read_large_context, source_scoped_schema
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
    def test_legacy_unknown_context_reference_uses_separate_scoped_repair(self):
        from weekly_daily import checked_judgment as original
        with TemporaryDirectory() as tmp:
            state=Path(tmp);sources,job=fixture(state);client=Client();stages=[]
            root=state/'week';(root/'P1').mkdir(parents=True)
            # 已有原阶段保留旧schema；用替身模拟该旧合同允许了不存在的context_ref。
            atomic(root/'P1/judgment.receipt.json',dict(status='received'))
            def judge(client,part,stage,data,schema,send):
                stages.append((stage,copy.deepcopy(schema),copy.deepcopy(data)))
                value=original(client,part,stage,data,schema,send)
                if stage=='judgment':value['items']['P1']['context_refs']=['不存在的原文编号']
                return value
            with patch('weekly_daily.checked_judgment',side_effect=judge):
                result=evaluate(sources,job,state,root,client)['reports'][0]
            self.assertEqual(result['score'],'16.0')
            self.assertEqual([row[0] for row in stages],['judgment','judgment-source-scope-repair-v1'])
            self.assertNotEqual(stages[0][1],stages[1][1])
            self.assertIn('d20261118-u18',json.dumps(stages[1][1]))
            self.assertEqual(stages[1][2]['previous_invalid_judgment']['items']['P1']['context_refs'],['不存在的原文编号'])

    def test_batched_audit_schema_restricts_refs_and_drops_unavailable_actor_variant(self):
        import jsonschema
        from review_schema import obj, array, compact
        issue=dict(anyOf=[obj(dict(kind=dict(type='string',enum=['wording']),source_ref=dict(type='string',enum=['u1','u2']))),
            obj(dict(kind=dict(type='string',enum=['wrong_actor']),source_ref=dict(type='string',enum=['a2'])))])
        schema=compact(obj(dict(issues=array(issue))))
        narrowed=source_scoped_schema(schema,{'u1'})
        jsonschema.Draft202012Validator.check_schema(narrowed)
        jsonschema.validate(dict(issues=[dict(kind='wording',source_ref='u1')]),narrowed)
        for kind,ref in [('wording','u2'),('wrong_actor','a2')]:
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(dict(issues=[dict(kind=kind,source_ref=ref)]),narrowed)

    def test_published_evidence_selects_actual_quote_among_same_catalog_candidates(self):
        with TemporaryDirectory() as tmp:
            state=Path(tmp);sources,job=fixture(state);root=state/'3/review'
            first=prepared(sources,job,state);key=next(iter(sources[2]['review']['behaviors']))
            text=next(iter(first['quotes'].values()))
            atomic(root/'report.json',dict(items=[dict(evidence=[dict(id=key,quote=text)])]))
            fragments=json.loads((root/'source-fragments.json').read_text())
            fragments['f2']=dict(record_id='u18',quote='比较两份报价')
            atomic(root/'source-fragments.json',fragments)
            atomic(root/'behaviors-repair.input.json',json.loads((root/'behaviors.input.json').read_text()))
            receipt(root,'behaviors-repair',dict(routes={'action-1':dict(fragment_id='f2',primary_item='P1',origin='personal')}))
            self.assertEqual(first,prepared(sources,job,state))

    def test_signed_stage_coordinates_survive_rebuilt_final_fragment_index(self):
        with TemporaryDirectory() as tmp:
            state=Path(tmp);sources,job=fixture(state);root=state/'3/review'
            payload=json.loads((root/'behaviors.input.json').read_text())
            data=json.loads(payload['input']);text=sources[2]['review']['conversations']['task-1']['records'][0]['text']
            data['fragments']={'old-fragment':['u18',0,len(text)]}
            payload['input']=json.dumps(data)
            atomic(root/'behaviors.input.json',payload)
            receipt(root,'behaviors',dict(routes={'action-1':dict(fragment_id='old-fragment',primary_item='P1',origin='personal')}))
            atomic(root/'source-fragments.json',{'new-fragment':dict(record_id='u18',quote='比较')})
            first=prepared(sources,job,state)
            self.assertEqual(list(first['quotes'].values()),[text])
            invalid=dict(payload,input=json.dumps(dict(data,actions={'action-1':dict(record_id='u18',action='旧的未发布主张')})))
            atomic(root/'behaviors-repair.input.json',invalid)
            receipt(root,'behaviors-repair',dict(routes={'action-1':dict(fragment_id='不存在的历史定位',primary_item='P1',origin='personal')}))
            self.assertEqual(first,prepared(sources,job,state))

    def test_large_quality_review_splits_whole_tasks_without_cutting_text(self):
        tasks=[dict(id='t'+str(i),records=[dict(id='u'+str(i),role='user',text='x'*100000)]) for i in range(2)]
        client=SimpleNamespace(config=SimpleNamespace(model='test'),output_token_limit=4500)
        data=dict(tasks=tasks,read_records=[])
        with TemporaryDirectory() as tmp, patch('weekly_daily.call',return_value=dict(issues=[])) as send:
            checked_call(client,Path(tmp),'quality-0',data,dict(type='object'),'核对事实')
            self.assertEqual(len(send.call_args_list),2)
            self.assertEqual([t for args in send.call_args_list for t in args.args[3]['tasks']],tasks)
            self.assertTrue((Path(tmp)/'quality-0-batch-coverage.json').exists())

    def test_received_cross_batch_finding_uses_independent_scoped_repair(self):
        from review_schema import obj, array
        tasks=[dict(id=str(i),records=[dict(id='u'+str(i),role='user',text='x'*100000)]) for i in range(2)]
        client=SimpleNamespace(config=SimpleNamespace(model='test'),output_token_limit=4500)
        schema=obj(dict(issues=array(obj(dict(source_ref=dict(type='string',enum=['u0','u1']))))))
        before=copy.deepcopy(schema)
        def respond(client,root,name,data,used_schema,instructions):
            if name.endswith('-source-scope-repair-v2'):
                self.assertIn('previous_invalid_audit',data)
                return dict(issues=[])
            return dict(issues=[dict(source_ref='u1' if data['tasks'][0]['id']=='0' else 'u0')])
        with TemporaryDirectory() as tmp,patch('weekly_daily.has_receipt',return_value=True),patch('weekly_daily.call',side_effect=respond) as send:
            result=checked_call(client,Path(tmp),'quality-0',dict(tasks=tasks,read_records=[]),schema,'原始复查要求')
            self.assertEqual(result,dict(issues=[]));self.assertEqual(len(send.call_args_list),4)
            self.assertEqual(send.call_args_list[0].args[4],before)
            self.assertEqual(schema,before)

    def test_large_judgment_reads_every_task_and_synthesizes_without_daily_scores(self):
        tasks=[dict(id='t'+str(i),records=[dict(id='u'+str(i),role='user',text='比较报价'),
            dict(id='a'+str(i),role='assistant',text='x'*100000)]) for i in range(2)]
        own={str(i):dict(source_ref='u'+str(i),claim='要求比较报价',primary_item='P1') for i in range(2)}
        client=SimpleNamespace(config=SimpleNamespace(model='test'),output_token_limit=4500)
        def respond(client,root,name,data,schema,instructions):
            if '-context-' in name:
                return dict(behaviors={key:dict(support='已说明比较目标',limitations='未展示执行结果',
                    context_refs=[b['source_ref']]) for key,b in data['behaviors'].items()})
            return dict(final=True)
        with TemporaryDirectory() as tmp,patch('weekly_daily.call',side_effect=respond) as send:
            result=checked_call(client,Path(tmp),'judgment',dict(tasks=tasks,behaviors=own),{},'综合判断')
            self.assertEqual(result,dict(final=True))
            self.assertEqual(len(send.call_args_list),3)
            reads=send.call_args_list[:-1]
            self.assertEqual([t for args in reads for t in args.args[3]['tasks']],tasks)
            final=send.call_args_list[-1].args[3]
            self.assertEqual([r for t in final['tasks'] for r in t['records']],
                [r for t in tasks for r in t['records'] if r['role']=='user'])
            self.assertEqual(len(final['context_readings']),2)
            self.assertNotIn('band',json.dumps(final))
        with TemporaryDirectory() as tmp,patch('weekly_daily.call',return_value=dict(behaviors={'0':dict(
                context_refs=['虚构编号'])})):
            with self.assertRaisesRegex(ValueError,'不属于本批完整原文'):
                read_large_context(client,Path(tmp),'judgment',dict(tasks=tasks,behaviors=own))

    def test_failed_factual_repair_requires_new_original_text_verification(self):
        with TemporaryDirectory() as tmp:
            state=Path(tmp);sources,job=fixture(state);client=Client();calls=[]
            def verify(client,root,tasks,behaviors,judgments,prose,*rest):
                calls.append(root)
                if len(calls)==1:
                    atomic(root/'quality.json',dict(issues=[dict(kind='wording',problem='疑问误写成要求')]))
                    raise ValueError('日评事实及归属检查未通过')
                return judgments,prose
            with patch('weekly_daily.check_quality',side_effect=verify):
                result=evaluate(sources,job,state,state/'week',client)['reports'][0]
            self.assertEqual(result['score'],'16.0')
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[1].name,'factual-verification-v2')
            self.assertTrue((calls[0]/'quality.json').exists())
            self.assertIn('factual_requirement',client.sent[-1])

    def test_context_quote_repair_keeps_original_read_and_checks_replacement(self):
        client=SimpleNamespace(config=SimpleNamespace(model='test'),output_token_limit=4500,daily_natural_json=True)
        data=dict(tasks=[dict(id='t',records=[dict(id='u',role='user',text='请比较两份报价')])],
            behaviors={'b':dict(source_ref='u',claim='比较报价')})
        invalid=dict(behaviors={'b':dict(support='比较目标',limitations='',quotes=[dict(record_id='u',quote='比较…报价')])})
        valid=dict(behaviors={'b':dict(support='比较目标',limitations='',quotes=[dict(record_id='u',quote='比较两份报价')])})
        with TemporaryDirectory() as tmp,patch('weekly_daily.call',side_effect=[invalid,valid]) as send:
            root=Path(tmp)
            atomic(root/'judgment.receipt.json',dict(status='received'))
            atomic(root/'judgment-context-v1-coverage.json',dict(task_ids=['t']))
            result=read_large_context(client,root,'judgment',data)
            self.assertEqual(result['context_readings'],[valid])
            self.assertEqual(send.call_args_list[0].args[2],'judgment-context-v1-0')
            self.assertEqual(send.call_args_list[1].args[2],'judgment-context-v1-0-quote-repair-v2')
            self.assertEqual(send.call_args_list[1].args[3]['previous_invalid_reading'],invalid)
            self.assertTrue(client.daily_natural_json)

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
            sources[0]['score_status']='observation_only'
            observed=evaluate(sources,job,state,state/'observation-week',Client())['reports'][0]
            self.assertEqual(observed['score'],'0.0');self.assertEqual(observed['score_status'],'scored')
            self.assertIn('来源与日评齐全',observed['score_reason'])

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
