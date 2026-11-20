"""本地PG非空来源到Worker导入的合成全链；不证明真实模型质量或Go接入。"""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import test_execution_paths as fixtures


@unittest.skipUnless(os.environ.get('ZHIYONGLI_TEST_DSN'), '需要显式本地专用测试库')
class WorkerFullCycleTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.dsn=os.environ['ZHIYONGLI_TEST_DSN'];url=urlsplit(self.dsn)
        self.assertEqual(('127.0.0.1',55414,'/zhiyongli_test'),(url.hostname,url.port,url.path))
        self.db=psycopg.connect(self.dsn,autocommit=True)
        self.schema='zy_fullcycle_'+uuid.uuid4().hex
        self.db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.clean_database)
        self.db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        self.db.execute('CREATE TABLE users(id bigint PRIMARY KEY,email text NOT NULL,deleted_at timestamptz)')
        self.db.execute("INSERT INTO users VALUES(1,'local-admin@example.test',NULL),(2,'local-person@example.test',NULL)")
        self.db.execute('CREATE TABLE audit_whitelist_users(user_id bigint)')
        self.db.execute('''CREATE TABLE llm_audit_logs(id bigint,updated_at timestamptz,user_id bigint,api_key_id bigint,session_id text,
            request_id text,request_count bigint,platform text,endpoint text,method text,model text,status_code int,
            request_body text,response_body text,request_truncated boolean,response_truncated boolean,created_at timestamptz)''')
        root=Path(__file__).resolve().parents[2]
        for migration in ('241_zhiyongli_reports.sql','245_zhiyongli_jobs.sql','246_zhiyongli_workday_scope.sql',
                          '248_zhiyongli_daily_reports.sql','249_zhiyongli_day_rechecks.sql'):
            self.db.execute((root/'backend/migrations'/migration).read_text())
        from workday_scope import window
        scope=window('2026-09-07','2026-09-14')
        self.job_id=self.db.execute('''INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,workday_scope)
            VALUES(2,'week','2026-09-07','2026-09-14','1.6',1,%s::jsonb) RETURNING id''',(json.dumps(scope),)).fetchone()[0]
        from runtime import RUBRIC
        self.text='\n'.join('合成合同夹具'+item for item in RUBRIC['items'])
        self.db.execute('''INSERT INTO llm_audit_logs VALUES(101,'2026-09-08T10:00:00+08:00',2,777,'synthetic-session',
            'synthetic-request',1,'synthetic','/v1/responses','POST','synthetic-model',200,%s,'',false,false,'2026-09-08T10:00:00+08:00')''',(self.text,))
        self.source=psycopg.connect(self.dsn,options='-c search_path='+self.schema)
        self.addCleanup(self.source.close)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.state=Path(tmp.name)
        self.model_key=self.state/'model.key';self.model_key.write_text('synthetic-model-key');self.model_key.chmod(0o600)
        self.worker_key='synthetic-worker-key-for-local-test-only'
        self.worker_key_file=self.state/'worker.key';self.worker_key_file.write_text(self.worker_key);self.worker_key_file.chmod(0o600)
        from model_config import ModelConfig
        self.client=SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',self.model_key),single_pass=False)
        self.requests=[];self.import_requests=[];self.http_errors=[];self.model_errors=[];self.disputed=False
        self.import_suffix='/import'
        self.explanation_disputed=False;self.explanation_unknown=False;self.local_behavior=False;self.import_reply_changes={}
        test=self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                test.import_requests.append((self.path,dict(self.headers),body))
                if self.headers.get('X-Zhiyongli-Worker-Key')!=test.worker_key:
                    test.http_errors.append('wrong_worker_key');self.send_error(403);return
                if self.path!='/api/v1/internal/zhiyongli/jobs/'+str(test.job_id)+test.import_suffix:
                    test.http_errors.append('wrong_import_route');self.send_error(404);return
                # 返回Run字段与Go成功响应契约一致；此服务器不冒充真正Go存储。
                run=dict(id=9001,digest='a'*64,source_name=body['source_name'],
                    report_count=len(body['reports']),created_at='2026-11-15T00:00:00Z')
                run.update(test.import_reply_changes)
                payload=json.dumps(dict(code=0,data=run)).encode()
                self.send_response(200);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            def log_message(self,*args):
                pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs=dict(poll_interval=0.02),daemon=True)
        self.thread.start();self.addCleanup(self.stop_http)
        from worker import importer
        self.send=importer('http://127.0.0.1:'+str(self.server.server_port),str(self.worker_key_file))
        self.fixture=fixtures.FixtureClient()
        self.environment=patch.dict(os.environ,{});self.environment.start();self.addCleanup(self.environment.stop)
        os.environ.pop('ZHIYONGLI_ACTIVE_SCOPE_KEY',None)

    def clean_database(self):
        from psycopg import sql
        try:
            self.db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        finally:
            self.db.close()

    def stop_http(self):
        self.server.shutdown();self.thread.join(timeout=2);self.server.server_close()

    def model_response(self,request,**kwargs):
        payload=json.loads(request.data);name=payload['text']['format']['name']
        self.requests.append(payload);data=json.loads(payload['input'])
        from material_packing import VERSION as PACKED,unpack
        if payload.get('metadata',{}).get('material_encoding')==PACKED:data=unpack(data)
        if name=='zhiyongli_session_action':
            from runtime import RUBRIC
            piece=data['initial_reading']['items'][0]
            location=dict(read_id=data['initial_read_id'],event_key=piece['event_key'],start=piece['start'],end=piece['end'])
            sources=[]
            for code in RUBRIC['items']:
                quote='合成合同夹具'+code;start=piece['text'].index(quote)+piece['start']
                sources.append(dict(id='person-'+code,location=dict(location,start=start,end=start+len(quote)),
                    origin='direct_expression',origin_reason='合成合同原句'))
            update=dict(materials=[dict(event_key=piece['event_key'],classification='human',spans=[location],
                task_id='synthetic',reason='合成原文')],tasks=[dict(task_id='synthetic',title='合成合同验证',
                purpose='仅验证程序，不评价真实员工',boundary='confirmed',boundary_reason='合成目标')],
                sources=sources,summary='合成完整合同')
            response=fixtures.record(dict(action=dict(operation='finish',arguments=dict(update=update,
                final=dict(source_ids=[s['id'] for s in sources],coverage_note='全部合成原文已提供')))))['response']
        elif name=='zhiyongli_session_claims':
            from test_session_review import ownership
            response=fixtures.record(ownership(data))['response']
        elif name=='zhiyongli_session_score':
            from runtime import RUBRIC
            unit=dict(key=data['key'],periods=data['periods'],events=[dict(event=data['person_events'][0],actor_class='human')])
            review=json.loads(self.fixture.call(unit,None)['response']['output'][0]['content'][0]['text'])
            del review['evidence']
            value=dict(review=review,presentation=dict(case=None,items=[dict(item_code=code,
                basis='合成原文支持本项正常表现。',higher_band='没有证明相邻更高档的完整优势。') for code in RUBRIC['items']]))
            for cell in review['period_reviews'][0]['cells']:
                cell['behavior_ids']=['act-'+cell['item']]
                cell['action'].pop('refs');cell['action'].pop('fact')
                from test_session_review import anchor_response
                anchor_response(cell)
            codes=data.get('item_codes',list(RUBRIC['items']))
            review['period_reviews'][0]['cells']=[c for c in review['period_reviews'][0]['cells'] if c['item'] in codes]
            value['presentation']['items']=[i for i in value['presentation']['items'] if i['item_code'] in codes]
            case=data.get('presentation_case')
            if case:
                value['presentation']['case']=dict(task_key=case['task_key'],outcome='合成用例未提供外部验收回执。',
                    claims=[dict(text='合成'+part,item_codes=['P1'],record_ids=case['record_ids'][:1],
                        assessment=dict(kind='support',reason='仅核合成原文。')) for part in ('目标','过程','结果边界')])
            response=fixtures.record(value)['response']
        elif name=='zhiyongli_task_identification':
            candidates=[e for e in data['events'] if e['actor_class']=='candidate_user']
            self.assertEqual(1,len(candidates));event=candidates[0]
            # 复用原合成合同，原识别真实请求使用唯一event_id字段。
            packet=dict(data,events=[dict(event,event=event['event_id'])])
            response=self.fixture.identify(packet,None)['response']
            if self.local_behavior:
                value=json.loads(response['output'][0]['content'][0]['text'])
                value['goals'][0].update(boundary='unresolved',boundary_reason='局部表达有据，但完整目标未确认，不造正式任务')
                response['output'][0]['content'][0]['text']=json.dumps(value)
        elif name=='zhiyongli_review':
            if 'presentation_case' in data:
                from test_single_pass import Client
                fixture=Client();fixture.presentation_case=data['presentation_case']
                self.fixture_presentation=fixture
                temp=self.state/'synthetic-review'/str(len(self.requests))
                response=fixture.call(data,temp)['response']
            else:response=self.fixture.call(data,None)['response']
            if 'review_output_protocol' in payload['text']['format']['schema']['properties']:
                from review_protocol import VERSION
                value=json.loads(response['output'][0]['content'][0]['text'])
                if data.get('context_access'):value['needs_nodes']=[]
                if getattr(self,'contract_errors',0)>0:
                    self.contract_errors-=1;value['presentation']=None
                response['output'][0]['content'][0]['text']=json.dumps(dict(review_output_protocol=VERSION,
                    review=value))
        elif name=='zhiyongli_semantic_audit':
            response=self.fixture.audit_review(data['unit'],data['review'],None)['response']
            value=json.loads(response['output'][0]['content'][0]['text'])
            evidence={e['id']:e for e in data['review']['evidence']}
            for item,judgment in value['items'].items():
                ref=evidence['person-'+item]
                judgment.update(person_act='合成本项独立行为：'+ref['quote'],own_state='observed',
                    source_quotes=[dict(event=ref['event'],quote=ref['quote'])])
            if self.disputed:
                value['items']['P2'].update(verdict='unresolved',reason='合成待核：缺必要语义证据，不允许自动导入',problem_codes=['synthetic-unresolved'])
            value.update(remarks=[],needs_events=[])
            response['output'][0]['content'][0]['text']=json.dumps(value)
        elif name in {'zhiyongli_week_basis','zhiyongli_week_basis_audit'}:
            from test_explanation_stage import BasisClient
            if self.explanation_unknown and name=='zhiyongli_week_basis':
                return io.BytesIO(b'data: [DONE]\n\n')
            basis=BasisClient()
            response=basis._call(payload,self.state/'synthetic-responses'/str(len(self.requests)))['response']
            if self.explanation_disputed and name=='zhiyongli_week_basis':
                value=json.loads(response['output'][0]['content'][0]['text'])
                value['items']['P2']['verdict']='needs_reassessment'
                response['output'][0]['content'][0]['text']=json.dumps(value)
        else:
            self.fail('出现未登记的模型请求：'+name)
        return io.BytesIO(('data: '+json.dumps(dict(type='response.completed',response=response))+'\n\n').encode())

    def tick(self,new_request_limit=20):
        from model_config import ModelConfig
        from worker import tick
        with patch.object(ModelConfig,'opener') as opener:
            def response(*args,**kwargs):
                try:return self.model_response(*args,**kwargs)
                except Exception as ex:
                    self.model_errors.append((type(ex).__name__,str(ex)));raise
            opener.return_value.open.side_effect=response
            return tick(self.db,self.source,self.state,self.client,self.send,[999],100,new_request_limit)

    def test_single_pass_two_rotations_imports_with_no_routine_audit(self):
        self.client.single_pass=True
        for index in range(2):
            self.assertTrue(self.tick(new_request_limit=1))
            state=self.db.execute('SELECT state,message FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()
            self.assertEqual('queued' if index==0 else 'completed',state[0],state)
        self.assertEqual(['zhiyongli_task_identification','zhiyongli_review'],
            [p['text']['format']['name'] for p in self.requests])
        self.assertEqual(1,len(self.import_requests))
        report=self.import_requests[0][2]['reports'][0]
        self.assertEqual('generated',report['task_review']['status'])
        self.assertEqual([],report['task_review']['checked_task_keys'])
        self.assertEqual('80.0',report['score'])
        root=self.state/str(self.job_id)
        self.assertEqual(2,len(json.loads((root/'request-budget.json').read_text())['entries']))
        self.assertFalse((root/'review/audit.receipt.json').exists())
        before={str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')}
        self.db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=%s",(self.job_id,))
        self.assertTrue(self.tick())
        self.assertEqual(2,len(self.requests))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')})

    def test_evidence_first_worker_preserves_mode_and_completes_without_shared_legacy_state(self):
        from source_index import MODE
        self.client.single_pass=True;self.client.evidence_first=True
        for _ in range(2):
            self.assertTrue(self.tick(new_request_limit=1))
        self.assertEqual(('completed','done'),self.db.execute(
            'SELECT state,phase FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone())
        self.assertEqual(1,len(self.import_requests))
        self.assertEqual(2,len(self.requests))
        stamp=json.loads((self.state/str(self.job_id)/'review/input.json').read_text())
        self.assertEqual(MODE,stamp['source_access_mode'])
        self.assertFalse((self.state/'shared-weeks').exists())
        self.assertFalse(list((self.state/str(self.job_id)/'review').glob('reading/*.receipt.json')))

    def test_session_worker_rotates_from_source_to_candidate_without_legacy_reading(self):
        self.client.single_pass=True;self.client.session_review=True
        for index in range(14):
            self.assertTrue(self.tick(new_request_limit=1))
            self.assertEqual('queued' if index<13 else 'completed',self.db.execute(
                'SELECT state FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()[0],self.model_errors)
        self.assertEqual(['zhiyongli_session_action','zhiyongli_session_claims']+['zhiyongli_session_score']*12,
            [p['text']['format']['name'] for p in self.requests])
        self.assertEqual('80.0',self.import_requests[0][2]['reports'][0]['score'])
        self.assertEqual(('completed','done'),self.db.execute(
            'SELECT state,phase FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone())
        root=self.state/str(self.job_id)/'review'
        self.assertTrue((root/'session-completed.json').exists())
        self.assertFalse((root/'input.json').exists());self.assertFalse((self.state/'shared-weeks').exists())
        before={p:p.read_bytes() for p in root.rglob('*.receipt.json')}
        self.db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=%s",(self.job_id,))
        self.assertTrue(self.tick())
        self.assertEqual(14,len(self.requests))
        self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*.receipt.json')})

    def test_session_source_connection_failure_is_automatically_reclaimed_after_backoff(self):
        self.client.single_pass=True;self.client.session_review=True
        with patch('worker.collect',side_effect=ConnectionRefusedError('合成本地来源连接失败')):
            self.assertTrue(self.tick())
        self.assertEqual('failed',self.db.execute('SELECT state FROM zhiyongli_jobs').fetchone()[0])
        self.assertEqual([],self.requests)
        self.assertFalse(self.tick())
        import execution_recovery
        failure=json.loads((self.state/str(self.job_id)/'failure.json').read_text())
        with patch.object(execution_recovery.time,'time',return_value=failure['at']+61):
            self.assertTrue(self.tick())
        self.assertEqual(('completed','done'),self.db.execute('SELECT state,phase FROM zhiyongli_jobs').fetchone())
        ledger=json.loads((self.state/str(self.job_id)/'connection-recovery.json').read_text())
        self.assertEqual(1,len(ledger['attempts']))
        self.assertEqual(1,len(self.import_requests))

    def test_session_import_connection_recovery_keeps_completed_model_receipts(self):
        from urllib.error import URLError
        self.client.single_pass=True;self.client.session_review=True
        send=self.send
        def interrupted(*args):raise URLError('合成本地导入连接失败')
        interrupted.base_url=send.base_url
        self.send=interrupted
        self.assertTrue(self.tick())
        self.assertEqual(('failed','paused'),self.db.execute('SELECT state,phase FROM zhiyongli_jobs').fetchone())
        sent=len(self.requests)
        root=self.state/str(self.job_id)
        before={p:p.read_bytes() for p in root.rglob('*.receipt.json')}
        self.assertFalse(self.tick())
        import execution_recovery
        failure=json.loads((root/'failure.json').read_text())
        self.send=send
        with patch.object(execution_recovery.time,'time',return_value=failure['at']+61):
            self.assertTrue(self.tick())
        self.assertEqual(sent,len(self.requests))
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertEqual(('completed','done'),self.db.execute('SELECT state,phase FROM zhiyongli_jobs').fetchone())
        self.assertEqual(1,len(self.import_requests))

    def test_evidence_first_output_correction_survives_rotations_and_imports_automatically(self):
        self.client.single_pass=True;self.client.evidence_first=True;self.contract_errors=2
        for i in range(4):
            self.assertTrue(self.tick(new_request_limit=1))
            self.assertEqual('completed' if i==3 else 'queued',self.db.execute(
                'SELECT state FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()[0],self.model_errors)
        self.assertEqual(1,len(self.import_requests));self.assertEqual(4,len(self.requests))
        self.assertEqual(1,sum(p['text']['format']['name']=='zhiyongli_task_identification' for p in self.requests))
        self.assertEqual('80.0',self.import_requests[0][2]['reports'][0]['score'])
        self.assertTrue(all(p['text']['format']['name']!='zhiyongli_semantic_audit' for p in self.requests))

    def test_evidence_first_technical_failure_is_not_employee_review(self):
        self.client.single_pass=True;self.client.evidence_first=True
        with patch('worker.evaluate_week',side_effect=ValueError('合成执行合同失败')):
            self.assertTrue(self.tick())
        self.assertEqual(('failed','execution_contract_failed'),self.db.execute(
            'SELECT state,phase FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone())
        failure=json.loads((self.state/str(self.job_id)/'failure.json').read_text())
        self.assertFalse(failure['employee_review_required'])
        self.assertEqual([],self.import_requests)
        self.assertEqual([],self.requests)

    def test_evidence_first_budget_block_is_capacity_failure_without_resend_or_import(self):
        from pipeline import Paused
        self.client.single_pass=True;self.client.evidence_first=True
        with patch('worker.evaluate_week',side_effect=Paused('needs_review','合成预算不足',reason_code='goal_budget_exhausted')):
            self.assertTrue(self.tick())
        self.assertEqual(('failed','capacity_blocked'),self.db.execute(
            'SELECT state,phase FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone())
        outcome=json.loads((self.state/str(self.job_id)/'outcome.json').read_text())
        self.assertEqual('capacity',outcome['category'])
        self.assertFalse(outcome['employee_review_required'])
        self.assertFalse(outcome['automatic_resend_allowed'])
        self.assertEqual([],self.import_requests)

    def test_evidence_first_program_contract_dispute_is_not_semantic_employee_review(self):
        from pipeline import Disputed
        self.client.single_pass=True;self.client.evidence_first=True
        with patch('worker.evaluate_week',side_effect=Disputed('合成输出格式失败',reason_code='model_contract_invalid')):
            self.assertTrue(self.tick())
        self.assertEqual(('failed','model_contract_failed'),self.db.execute(
            'SELECT state,phase FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone())
        outcome=json.loads((self.state/str(self.job_id)/'outcome.json').read_text())
        self.assertEqual('execution_contract',outcome['category'])
        self.assertFalse(outcome['employee_review_required'])
        self.assertFalse(self.import_requests)

    def test_evidence_first_legacy_capacity_pause_does_not_become_employee_review(self):
        from pipeline import Paused
        self.client.single_pass=True;self.client.evidence_first=True
        with patch('worker.evaluate_week',side_effect=Paused('needs_review','分层归属的确定性装配超容量，本次未发送阅读请求')):
            self.assertTrue(self.tick())
        self.assertEqual(('failed','execution_blocked'),self.db.execute(
            'SELECT state,phase FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone())
        outcome=json.loads((self.state/str(self.job_id)/'outcome.json').read_text())
        self.assertFalse(outcome['employee_review_required'])
        self.assertFalse(self.requests);self.assertFalse(self.import_requests)

    def test_evidence_first_readback_capacity_is_technical_and_preserves_failure(self):
        from navigation_access import ReadbackCapacity
        self.client.single_pass=True;self.client.evidence_first=True
        with patch('worker.evaluate_week',side_effect=ReadbackCapacity('合成补读容量不足')):
            self.assertTrue(self.tick())
        self.assertEqual(('failed','capacity_blocked'),self.db.execute(
            'SELECT state,phase FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone())
        failure=json.loads((self.state/str(self.job_id)/'failure.json').read_text())
        self.assertEqual('capacity',failure['category']);self.assertFalse(failure['employee_review_required'])
        self.assertFalse(self.requests);self.assertFalse(self.import_requests)

    def test_evidence_first_unhandled_attribution_is_contract_failure_without_employee_review(self):
        from pipeline import Paused
        self.client.single_pass=True;self.client.evidence_first=True
        with patch('worker.evaluate_week',side_effect=Paused('needs_review','本人归属确实无法确定',reason_code='source_attribution_unresolved')):
            self.assertTrue(self.tick())
        self.assertEqual('failed',self.db.execute(
            'SELECT state FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()[0])
        outcome=json.loads((self.state/str(self.job_id)/'outcome.json').read_text())
        self.assertFalse(outcome['employee_review_required']);self.assertEqual('execution_contract',outcome['category'])
        self.assertEqual('failed',outcome['state']);self.assertEqual('needs_review',outcome['original_state'])
        self.assertFalse(self.import_requests)

    def test_one_request_rotation_completes_all_five_stages_without_unknown(self):
        # 每轮只允许一次新发送，覆盖评分之后说明生成/复查分属两轮的持久恢复。
        for count in range(1,6):
            self.assertTrue(self.tick(new_request_limit=1))
            state=self.db.execute('SELECT state,message FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()
            self.assertEqual(count,len(self.requests),state)
            self.assertEqual('completed' if count==5 else 'queued',state[0],state)
            self.assertEqual(int(count==5),len(self.import_requests))
        self.assertEqual(['zhiyongli_task_identification','zhiyongli_review','zhiyongli_semantic_audit',
            'zhiyongli_week_basis','zhiyongli_week_basis_audit'],[p['text']['format']['name'] for p in self.requests])
        self.assertEqual('week_checked',self.import_requests[0][2]['reports'][0]['task_review']['status'])
        root=self.state/str(self.job_id)
        self.assertEqual(5,len(json.loads((root/'request-budget.json').read_text())['entries']))
        self.db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=%s",(self.job_id,))
        self.assertTrue(self.tick(new_request_limit=1))
        self.assertEqual((5,2),(len(self.requests),len(self.import_requests)))

    def test_nonempty_source_full_cycle_completes_and_repeat_tick_is_idle(self):
        from runtime import digest
        self.assertTrue(self.tick())
        state=self.db.execute('SELECT state,phase,message FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()
        root=self.state/str(self.job_id)
        details=[json.loads(p.read_text()) for p in root.rglob('failure.json')]
        self.assertEqual(('completed','done'),state[:2],details or self.model_errors or state)
        self.assertEqual([],self.http_errors)
        self.assertEqual(['zhiyongli_task_identification','zhiyongli_review','zhiyongli_semantic_audit',
            'zhiyongli_week_basis','zhiyongli_week_basis_audit'],
            [p['text']['format']['name'] for p in self.requests])
        self.assertEqual(1,len(self.import_requests))
        sent=self.import_requests[0][2]
        packet=json.loads((root/'review/packet.json').read_text())
        self.assertTrue(any(e['text']==self.text and e['actor_class']=='candidate_user' for e in packet['events']))
        self.assertTrue((root/'sources/latest.json').is_file())
        self.assertEqual('80.0',sent['reports'][0]['score'])
        self.assertEqual('local-person@example.test',sent['reports'][0]['user_email'])
        original=json.loads((root/'review/bundle.json').read_text())
        without_explanation=json.loads(json.dumps(sent))
        self.assertIn('task_review',without_explanation['reports'][0])
        del without_explanation['reports'][0]['task_review']
        self.assertEqual(digest(original),digest(without_explanation))
        imported=json.loads((root/'import.json').read_text())
        self.assertEqual((9001,1),(imported['id'],imported['report_count']))
        self.assertEqual(5,len(json.loads((root/'request-budget.json').read_text())['entries']))
        before={str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')}
        self.assertEqual(5,len(before))
        self.assertFalse(self.tick())
        self.assertEqual((5,1),(len(self.requests),len(self.import_requests)))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')})
        # 显式重入同任务复用模型及说明；导入仍须向原端点同包核验，不能只信本地证明。
        self.db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=%s",(self.job_id,))
        self.assertTrue(self.tick())
        self.assertEqual((5,2),(len(self.requests),len(self.import_requests)))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')})

    def test_checked_import_server_mismatch_stops_actual_worker_as_needs_review(self):
        self.assertTrue(self.tick())
        root=self.state/str(self.job_id)
        before={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in root.glob('import*.json')}
        receipts={str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')}
        # HTTP回执模拟产品库重建后的新Run；真实Worker必须保留原证明并停止完成标记。
        self.import_reply_changes=dict(created_at='2026-11-16T00:00:00Z')
        self.db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=%s",(self.job_id,))
        self.assertTrue(self.tick())
        state=self.db.execute('SELECT state,phase FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()
        self.assertEqual(('needs_review','paused'),state)
        self.assertEqual((5,2),(len(self.requests),len(self.import_requests)))
        self.assertEqual(before,{p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in root.glob('import*.json')})
        self.assertEqual(receipts,{str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')})
        self.assertEqual(5,len(json.loads((root/'request-budget.json').read_text())['entries']))

    def test_local_behavior_observation_imports_without_inventing_formal_review(self):
        from runtime import digest
        self.local_behavior=True
        self.assertTrue(self.tick())
        state=self.db.execute('SELECT state,phase,message FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()
        root=self.state/str(self.job_id)
        self.assertEqual(('completed','done'),state[:2],state)
        self.assertEqual(['zhiyongli_task_identification','zhiyongli_review','zhiyongli_semantic_audit'],
            [p['text']['format']['name'] for p in self.requests])
        self.assertEqual(1,len(self.import_requests))
        bundle=self.import_requests[0][2];report=bundle['reports'][0]
        self.assertEqual('scored',report['score_status'])
        self.assertEqual('80.0',report['score'])
        self.assertNotIn('task_review',report)
        self.assertTrue(report['eligibility']['observations'])
        self.assertTrue(all(row['task_keys']==[] for row in report['eligibility']['observations']))
        self.assertTrue(report['items']);self.assertTrue(report['limitations'])
        unit=json.loads((root/'review/unit.json').read_text())
        self.assertEqual({'local_behavior'},{source['source_kind'] for source in unit['source_units']})
        original=json.loads((root/'review/bundle.json').read_text())
        self.assertEqual(digest(original),digest(bundle))
        self.assertEqual(3,len(json.loads((root/'request-budget.json').read_text())['entries']))
        self.assertFalse(list((self.state/'shared-weeks').rglob('presentation-bundle.json')))
        before={str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')}
        self.db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=%s",(self.job_id,))
        self.assertTrue(self.tick())
        self.assertEqual((3,2),(len(self.requests),len(self.import_requests)))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')})

    def test_unresolved_semantic_audit_remains_needs_review_and_never_imports(self):
        self.disputed=True
        self.assertTrue(self.tick())
        state=self.db.execute('SELECT state,message FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()
        root=self.state/str(self.job_id)
        self.assertEqual('needs_review',state[0],[state,[json.loads(p.read_text()) for p in root.rglob('failure.json')]])
        self.assertIn('语义复查',state[1])
        self.assertEqual([],self.import_requests)
        self.assertFalse((root/'import.json').exists())
        audits=list(root.rglob('audit.json'))
        self.assertEqual(1,len(audits))
        self.assertTrue(all(not json.loads(p.read_text())['supported'] for p in audits))
        self.assertIn('未决缺口',state[1])
        self.assertEqual(3,len(self.requests))
        self.assertEqual(3,len(json.loads((root/'request-budget.json').read_text())['entries']))
        self.assertFalse(self.tick())
        self.assertEqual((3,0),(len(self.requests),len(self.import_requests)))

    def test_unbound_import_file_cannot_mark_job_completed(self):
        from runtime import atomic
        path=self.state/str(self.job_id)/'import.json';atomic(path,{})
        before=path.read_bytes()
        self.assertTrue(self.tick())
        state=self.db.execute('SELECT state FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()[0]
        self.assertEqual('needs_review',state)
        self.assertEqual((5,0),(len(self.requests),len(self.import_requests)))
        self.assertEqual(before,path.read_bytes())
        self.assertFalse(path.with_name('import-input.json').exists())

    def test_explanation_requires_reassessment_preserves_score_and_never_imports(self):
        self.explanation_disputed=True
        self.assertTrue(self.tick())
        state=self.db.execute('SELECT state,message FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()
        root=self.state/str(self.job_id)
        self.assertEqual('needs_review',state[0],state)
        self.assertEqual((5,0),(len(self.requests),len(self.import_requests)))
        self.assertFalse((root/'import-input.json').exists())
        self.assertEqual('80.0',json.loads((root/'review/bundle.json').read_text())['reports'][0]['score'])

    def test_explanation_unknown_same_job_reentry_does_not_resend_or_import(self):
        self.explanation_unknown=True
        self.assertTrue(self.tick())
        state=self.db.execute('SELECT state FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()[0]
        self.assertEqual('unknown',state)
        self.assertEqual((4,0),(len(self.requests),len(self.import_requests)))
        before={str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')}
        self.db.execute("UPDATE zhiyongli_jobs SET state='queued' WHERE id=%s",(self.job_id,))
        self.assertTrue(self.tick())
        state=self.db.execute('SELECT state FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()[0]
        self.assertEqual('unknown',state)
        self.assertEqual((4,0),(len(self.requests),len(self.import_requests)))
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.state.rglob('*.receipt.json')})

    def daily_preparations(self,state):
        from preparation import Preparations
        from runtime import atomic
        from workday_scope import window
        scope=window('2026-09-07','2026-09-14')
        queue=Preparations(self.state)
        daily=dict(through_date=scope['working_dates'][-1],workday_scope=scope,mode='reading_only',
            users=[dict(user_id=2,user_email='local-person@example.test')])
        queue.enqueue(daily)
        with queue.connect() as db:key=db.execute('SELECT key FROM preparations').fetchone()[0]
        if state=='prepared':
            queue.update(key,state,'合成准备完成',daily['through_date'])
            atomic(self.state/'preparations'/key/'request-budget.json',dict(version=1,entries={}))
        else:queue.update(key,state,'合成日常未决')
        self.client.preparations=queue;self.client.schedule_enabled=True
        self.client.preparation_first_week='2026-09-07'
        return queue

    def test_ready_week_completes_despite_other_daily_backlog(self):
        from model_config import ModelConfig
        from worker import run_turn
        queue=self.daily_preparations('prepared')
        # 模拟另一人的日常队列不断有工作；本人的准备已真实落入SQLite。
        with patch.object(queue,'tick',return_value=True) as daily,patch.object(ModelConfig,'opener') as opener:
            opener.return_value.open.side_effect=self.model_response
            worked,priority=run_turn(self.db,self.source,self.state,self.client,self.send,[999],100,20)
            self.assertTrue(worked);self.assertEqual([],self.requests)
            worked,priority=run_turn(self.db,self.source,self.state,self.client,self.send,[999],100,20,
                preparation_first=priority)
            self.assertTrue(worked);self.assertEqual(1,daily.call_count)
        state=self.db.execute('SELECT state FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()[0]
        self.assertEqual('completed',state,[json.loads(p.read_text()) for p in self.state.rglob('failure.json')])
        self.assertEqual((5,1),(len(self.requests),len(self.import_requests)))

    def test_rotation_never_scores_person_with_unknown_daily_preparation(self):
        from worker import run_turn
        queue=self.daily_preparations('unknown')
        with patch.object(queue,'tick',return_value=True),patch('runtime.CompanyClient._call',side_effect=AssertionError('未准备不能发模型')):
            worked,_=run_turn(self.db,self.source,self.state,self.client,self.send,[999],100,20,preparation_first=False)
        self.assertTrue(worked)
        state=self.db.execute('SELECT state FROM zhiyongli_jobs WHERE id=%s',(self.job_id,)).fetchone()[0]
        self.assertEqual('unknown',state)
        self.assertEqual((0,0),(len(self.requests),len(self.import_requests)))


class WeekPresentationApplicabilityTests(unittest.TestCase):
    def test_valid_local_sources_do_not_require_a_formal_task_case(self):
        from worker import formal_week_review_required
        for status in ('scored','observation_only','data_pending'):
            report=dict(score_status=status,eligibility=dict(observations=[dict(task_keys=[])]),
                items=[dict(state='no_opportunity')])
            self.assertFalse(formal_week_review_required(report))

    def test_formal_task_still_requires_full_review_regardless_of_score_status(self):
        from worker import formal_week_review_required
        for status in ('scored','observation_only','data_pending'):
            self.assertTrue(formal_week_review_required(dict(score_status=status,
                eligibility=dict(observations=[dict(task_keys=[]),dict(task_keys=['formal-task'])]))))

    def test_missing_or_malformed_eligibility_never_silently_skips(self):
        from worker import formal_week_review_required
        for report in ({},dict(eligibility=None),dict(eligibility={}),
                dict(eligibility=dict(observations=None)),dict(eligibility=dict(observations=[{}])),
                dict(eligibility=dict(observations=[dict(task_keys=None)])),
                dict(eligibility=dict(observations=[dict(task_keys=[''])])),
                dict(eligibility=dict(observations=[dict(task_keys=[None])]))):
            with self.subTest(report=report),self.assertRaises((ValueError,KeyError,TypeError)):
                formal_week_review_required(report)


if __name__=='__main__':
    unittest.main()
