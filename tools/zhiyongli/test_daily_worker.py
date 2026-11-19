"""本地真实PG日评队列与模拟模型/接入HTTP的续跑检查，不声称真实模型质量通过。"""
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from daily_pilot import RUBRIC
from runtime import atomic, digest
from workday_scope import daily_window
from worker import importer, tick


@unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'), '需要显式本地专用测试库')
class DailyWorkerTests(unittest.TestCase):
    def test_pilot_claims_only_listed_day_jobs_even_when_other_jobs_are_queued(self):
        from test_worker_fullcycle import WorkerFullCycleTests as CycleFixture
        case=CycleFixture()
        self.addCleanup(case.doCleanups)
        case.setUp()
        root=Path(__file__).resolve().parents[2]
        for name in ('241_zhiyongli_reports.sql','248_zhiyongli_daily_reports.sql'):
            case.db.execute((root/'backend/migrations'/name).read_text())
        case.db.execute("INSERT INTO users VALUES(3,'local-pilot@example.test',NULL),(4,'local-bystander@example.test',NULL)")
        scope=daily_window('2026-11-18')
        jobs={user:case.db.execute('''INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,workday_scope)
            VALUES(%s,'day','2026-11-18','2026-11-19','1.6',1,'same-day',%s::jsonb) RETURNING id''',
            (user,json.dumps(scope))).fetchone()[0] for user in (4,2,3)}
        selected=[jobs[2],jobs[3]]
        dispatched=[]
        def complete(db,job,*args):
            dispatched.append(job['id'])
            db.execute("UPDATE zhiyongli_jobs SET state='completed' WHERE id=%s",(job['id'],))
        with patch('worker.process',side_effect=complete):
            case.client.pilot_day_job_ids=[case.job_id]
            self.assertFalse(tick(case.db,case.source,case.state,case.client,None,[999]))
            case.client.pilot_day_job_ids=selected
            self.assertTrue(tick(case.db,case.source,case.state,case.client,None,[999]))
            self.assertTrue(tick(case.db,case.source,case.state,case.client,None,[999]))
            self.assertFalse(tick(case.db,case.source,case.state,case.client,None,[999]))
        self.assertCountEqual(dispatched,selected)
        remaining=case.db.execute("SELECT id FROM zhiyongli_jobs WHERE state='queued'").fetchall()
        self.assertCountEqual(remaining,[(case.job_id,),(jobs[4],)])

    def test_day_dispatch_resumes_without_resending_plan_or_entering_month_flow(self):
        self.check_day_dispatch()

    def test_pilot_uses_draft_route_even_on_an_old_server_without_the_route(self):
        self.check_day_dispatch(pilot=True, draft_available=False)

    def test_pilot_saves_draft_and_cannot_be_restarted_in_publish_mode(self):
        self.check_day_dispatch(pilot=True, draft_available=True)

    def check_day_dispatch(self, *, pilot=False, draft_available=False):
        from test_worker_fullcycle import WorkerFullCycleTests as CycleFixture
        case=CycleFixture()
        self.addCleanup(case.doCleanups)
        case.setUp()
        regular_send=case.send
        if pilot:
            case.client.pilot_day_job_ids=[case.job_id]
            case.send=importer(regular_send.base_url,case.worker_key_file,draft_only=True)
            if draft_available:
                case.import_suffix='/import-draft'
        root=Path(__file__).resolve().parents[2]
        for name in ('241_zhiyongli_reports.sql','248_zhiyongli_daily_reports.sql'):
            case.db.execute((root/'backend/migrations'/name).read_text())
        scope=daily_window('2026-11-18')
        case.db.execute("UPDATE zhiyongli_jobs SET period_type='day',period_start='2026-11-18',period_end='2026-11-19',workday_scope=%s::jsonb",(json.dumps(scope),))
        request=json.dumps(dict(audit_events=[dict(kind='user',text='请比较两份报价，说明差异原因。')]),ensure_ascii=False)
        response=json.dumps(dict(audit_events=[dict(kind='assistant',text='两份报价的差异和原因已整理。')]),ensure_ascii=False)
        case.db.execute("UPDATE llm_audit_logs SET request_body=%s,response_body=%s,created_at='2026-11-18T10:00:00+08:00',updated_at='2026-11-18T10:00:01+08:00'",(request,response))
        calls=[]
        def model(client,payload,path,*,before_send=None):
            signature=digest(dict(endpoint=client.config.responses_url,payload=payload))
            if path.exists():
                saved=json.loads(path.read_text())
                if saved['request_sha256']!=signature:raise ValueError('恢复请求发生漂移')
                return saved
            data=json.loads(payload['input'])
            if 'windows' in data:
                value=dict(candidates=[dict(window_id=data['windows'][0]['id'],title='比较报价',goal='解释两份报价差异')])
            elif path.stem.startswith('facts.'):
                task=data['tasks'][0]
                user=next(row for row in task['records'] if row['role']=='user')
                action=dict(record_id=user['id'],action='要求比较两份报价并解释差异',explicit_reason='',new_fact='',
                            new_fact_record='',claimed_completed_by_user='',selected_asset=[])
                value=dict(task_summaries={task['id']:dict(title=task['title'],goal=task['goal'],outcome='已说明报价差异。',workflow=[])},actions=[action])
            elif path.stem.startswith('behaviors.'):
                value=dict(routes={'action-1':dict(fragment_id=next(iter(data['fragments'])),primary_item='P1',origin='personal')})
            elif path.stem.startswith('judge-P-0.'):
                identity=next(iter(data['behaviors']))
                value=dict(needs_records=[],items=dict(P1=dict(user_action='要求比较报价。',context_support='明确了比较对象与交付内容。',band=70,basis='你明确了比较对象和解释差异的目标。',next_band='本次尚未呈现目标细化的具体优势。',refs=[identity],context_refs=[],negative=None)))
            elif path.stem.startswith('explanation.'):
                value=dict(summary='你明确说明了比较与解释的目标。',items=dict(P1=dict(basis='你明确了比较对象和解释差异的目标。',next_band='本次尚未呈现目标细化的具体优势。')))
            elif path.stem.startswith('quality-0.'):
                value=dict(issues=[])
            else:raise AssertionError('未登记的日评阶段：'+path.name)
            with client.send_slot(payload,path,signature,b'local-test'):
                if before_send:before_send()
                saved=dict(request_sha256=signature,status='received',sent_at=time.time(),client_elapsed_seconds=.01,response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value,ensure_ascii=False))])]))
                atomic(path,saved)
                calls.append(path.name)
                return saved
        with patch('runtime.CompanyClient._call',model),patch('worker.evaluate_month',side_effect=AssertionError('日评不能进入月评')),patch('worker.evaluate_week',side_effect=AssertionError('日评不能进入周评')):
            for _ in range(4):
                self.assertTrue(tick(case.db,case.source,case.state,case.client,case.send,[999],20,1))
                self.assertEqual('queued',case.db.execute('SELECT state FROM zhiyongli_jobs').fetchone()[0])
            self.assertTrue(tick(case.db,case.source,case.state,case.client,case.send,[999],20,1))
            expected=('failed','paused') if pilot and not draft_available else ('completed','done')
            self.assertEqual(expected,case.db.execute('SELECT state,phase FROM zhiyongli_jobs').fetchone())
            self.assertFalse(tick(case.db,case.source,case.state,case.client,case.send,[999],20,1))
            if pilot:
                case.db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=%s",(case.job_id,))
                case.client.pilot_day_job_ids=None
                self.assertTrue(tick(case.db,case.source,case.state,case.client,regular_send,[999],20,1))
                self.assertEqual('failed',case.db.execute('SELECT state FROM zhiyongli_jobs').fetchone()[0])
        self.assertEqual(calls,['task-plan.receipt.json','facts.receipt.json','behaviors.receipt.json','judge-P-0.receipt.json','quality-0.receipt.json'])
        self.assertEqual(len(case.import_requests),1)
        self.assertEqual(case.import_requests[0][0].rsplit('/',1)[1],'import-draft' if pilot else 'import')
        exported=case.import_requests[0][2]['reports'][0]
        self.assertEqual(exported['period_type'],'day')
        self.assertEqual(exported['period_start'],'2026-11-18')
        self.assertEqual(exported['daily_review']['version'],scope['version'])
        self.assertEqual(bool(list(case.state.rglob('failure.json'))),pilot)


if __name__=='__main__':unittest.main()
