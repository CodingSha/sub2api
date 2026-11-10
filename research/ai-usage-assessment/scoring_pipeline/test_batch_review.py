"""持久执行回归：模拟接口，不用员工评分分布作为通过条件。"""
import copy
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from . import batch_review as batch
from .batch_client import ResponsesClient, ModelError, terminal_response, response_text, digest
from . import test_dsh_v11 as fixtures


class FakeClient:
    def __init__(self, values):
        self.values, self.calls = iter(values), []

    def call(self, instructions, data, cache, run_id):
        self.calls.append(copy.deepcopy(data))
        value = next(self.values)
        if isinstance(value, Exception): raise value
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return dict(text=text, request_sha256=digest(data), response_sha256=digest(text), response_id='fake-response', model='test-model')


class BatchTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.HandoffTests(); fixture.setUp(); self.u=fixture.u; self.u['source_groups']=[]; self.a=fixture.a
        self.u['periods'][0]['kind']='weekly'
        self.a['self_check']=dict(decision='completed', reason='逐项自检全部12项与参与事实')
        for part in self.a['assessments']:
            for c in part['cells']:
                if c['status']=='scored':
                    c['progression'].setdefault('adapted', {'met':False}); c['progression'].setdefault('verified', {'met':False})
        self.data=dict(policy_version=batch.VERSION,tasks={'t1':self.u},source_groups={},frozen_files=[])
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name); self.run=self.root/'run'
        self.dataset=self.root/'dataset.json'; self.save_dataset()
        taskpatch=patch.object(batch.base,'task',side_effect=lambda d,k:dict(self.u,key=k,goal_id=k))
        taskpatch.start(); self.addCleanup(taskpatch.stop)
    def save_dataset(self): self.dataset.write_text(json.dumps(self.data))
    def init(self): return batch.initialize(self.dataset,self.run,model='test-model')
    def good(self): return dict(unit_reviews=[copy.deepcopy(self.a)],needs_events=[])
    def test_two_keys_restart_and_skip(self):
        self.data['tasks']['t2']=dict(self.u,key='t2',goal_id='t2'); self.save_dataset(); self.init()
        second=self.good(); second['unit_reviews'][0]['key']='t2'; client=FakeClient([self.good(),second])
        self.assertEqual(batch.work(self.run,client,limit=1)['contract_valid'],1)
        self.assertEqual(batch.work(self.run,client)['contract_valid'],2)
        self.assertEqual(batch.work(self.run,client)['contract_valid'],2); self.assertEqual(len(client.calls),2)
    def test_transport_failure_not_employee_unknown(self):
        self.init(); s=batch.work(self.run,FakeClient([ModelError('transport_TimeoutError',True)]))
        self.assertEqual(s['contract_valid'],0); self.assertEqual(s['queue_counts']['failed'],1)
        self.assertFalse(s['ordinary_queue_complete']); batch.retry_failed(self.run)
        self.assertEqual(batch.work(self.run,FakeClient([self.good()]))['contract_valid'],1)
    def test_local_block_never_reads_or_calls(self):
        self.u['local_only']=True; self.save_dataset(); self.init(); client=FakeClient([])
        with patch.object(batch,'packet',side_effect=AssertionError('禁止读受限原文')): s=batch.work(self.run,client)
        self.assertEqual(s['queue_counts']['blocked_local'],1); self.assertFalse(client.calls)
    def test_policy_refusal_not_requeued(self):
        self.init(); batch.work(self.run,FakeClient([ModelError('policy_blocked')]))
        self.assertEqual(batch.retry_failed(self.run)['requeued'],[])
        self.assertEqual(batch.base.read(self.run/'queue.json')['t1']['status'],'blocked_policy')
    def test_missing_complexity_not_defaulted(self):
        a=copy.deepcopy(self.a)
        for p in a['assessments']:
            for c in p['cells']:
                if c['item']=='M1': c.pop('opportunity_complexity',None)
        with self.assertRaisesRegex(ValueError,'显式判断复杂资格'): batch.check_explicit(self.u,a)
    def test_missing_requirement_not_defaulted(self):
        a=copy.deepcopy(self.a); c=next(c for p in a['assessments'] for c in p['cells'] if c['status']=='scored'); c['requirements'][0].pop('status')
        with self.assertRaisesRegex(ValueError,'显式判断要求状态'): batch.check_explicit(self.u,a)
    def test_repair_calls_model(self):
        self.init(); bad=self.good(); bad['unit_reviews'][0]['key']='wrong'; client=FakeClient([bad,self.good()])
        self.assertEqual(batch.work(self.run,client)['contract_valid'],1); self.assertIn('contract_error',client.calls[1]['correction'])
    def test_contract_failure_bounded(self):
        self.init(); client=FakeClient(['not json']*3); self.assertEqual(batch.work(self.run,client)['contract_valid'],0)
        self.assertEqual(len(client.calls),3); self.assertEqual(batch.base.read(self.run/'queue.json')['t1']['last_error'],'contract_failed')
    def test_double_writer_lock(self):
        self.init()
        with batch.RunLock(self.run):
            with self.assertRaises(ValueError): batch.work(self.run,FakeClient([]))
    def test_stop_preserves_pending(self):
        self.init(); (self.run/'STOP').touch(); client=FakeClient([])
        self.assertEqual(batch.work(self.run,client)['queue_counts']['pending'],1); self.assertFalse(client.calls)
    def test_crash_after_result_before_queue_save(self):
        self.init(); _,m=batch.open_run(self.run); batch.freeze(self.data,self.run,m,self.a,dict(calls=[]))
        state=batch.base.read(self.run/'queue.json'); state['t1']['status']='running'; batch.atomic_json(self.run/'queue.json',state)
        client=FakeClient([]); self.assertEqual(batch.work(self.run,client)['contract_valid'],1); self.assertFalse(client.calls)
    def test_no_overwrite(self):
        self.init(); batch.work(self.run,FakeClient([self.good()])); _,m=batch.open_run(self.run)
        with self.assertRaises(ValueError): batch.freeze(self.data,self.run,m,self.a,dict(calls=[]))
    def test_index_change_blocks_resume(self):
        self.init(); self.dataset.write_text('{}')
        with self.assertRaises(ValueError): batch.open_run(self.run)
    def test_statistics_split_periods(self):
        self.u['periods'].append(dict(self.u['periods'][0],kind='monthly',period_id='month'))
        self.a['assessments'][0]['period_ids'].append('month'); self.a['usage'].append(dict(self.a['usage'][0],period_id='month'))
        self.save_dataset(); self.init(); s=batch.work(self.run,FakeClient([self.good()]))
        self.assertEqual(sum(s['physical_merged_cells'].values()),12); self.assertEqual(sum(sum(v.values()) for v in s['period_cells'].values()),24)
    def test_session_link_not_time_link(self):
        self.u['events'][0]['session_id']='s1'; e=copy.deepcopy(self.u['events'][0]); e.update(event='e2',session_id='other',text='另一会话不能按时间邻接'); self.u['events'].append(e)
        _,pack,_=batch.packet(self.data,'t1'); row=next(x for x in batch.context_rows(pack) if x['event']=='e2')
        self.assertIsNone(row['preview']); self.assertEqual(row['linkage'],'source_group_only')
    def test_context_fetch_before_decision(self):
        self.init(); client=FakeClient([dict(needs_events=['e1']),self.good()]); self.assertEqual(batch.work(self.run,client)['contract_valid'],1)
        self.assertEqual(client.calls[1]['full_context'][0]['event'],'e1')
    def test_out_of_source_fetch_rejected(self):
        self.init(); client=FakeClient([dict(needs_events=['secret'])]*3); self.assertEqual(batch.work(self.run,client)['contract_valid'],0)
        self.assertTrue(all(not x['full_context'] for x in client.calls))
    def test_budget_not_employee_unknown(self):
        self.init(); client=FakeClient([])
        with patch.object(batch,'MAX_INPUT_CHARS',1): s=batch.work(self.run,client)
        self.assertEqual(s['contract_valid'],0); self.assertFalse(client.calls)

    def test_missing_source_forces_full_linked_context(self):
        self.u['events'][0]['session_id']='s1'
        e=copy.deepcopy(self.u['events'][0]); e.update(event='e2',session_id='s1',actor_class='assistant',human_spans=[],text='完整背景'*250)
        self.u['events'].append(e); self.save_dataset(); self.init()
        value=self.good()
        c=next(c for p in value['unit_reviews'][0]['assessments'] for c in p['cells'] if c['item']=='P2')
        c.clear(); c.update(item='P2',status='unknown',reason='缺决定性原始材料',evidence=[],missing=dict(type='missing_source',needed='截图',why_changes_judgment='足以改变输入是否能推进',checked_events=['e2'],source_absence='源未采集截图'))
        client=FakeClient([value,self.good()]); self.assertEqual(batch.work(self.run,client)['contract_valid'],1)
        self.assertEqual(client.calls[1]['full_context'][0]['text'],e['text'])
    def test_same_group_sensitive_task_blocks_send(self):
        self.u['source_groups']=['g']; other=dict(self.u,key='t2',local_only=True)
        self.data['tasks']['t2']=other
        with self.assertRaisesRegex(ValueError,'来源组包含受限任务'): batch.packet(self.data,'t1')
    def test_people_are_interleaved_without_changing_task_weights(self):
        self.data['tasks']['t2']=dict(self.u,key='t2',user_id='other')
        self.data['tasks']['t3']=dict(self.u,key='t3')
        self.save_dataset(); self.init()
        self.assertEqual(list(batch.base.read(self.run/'queue.json')),['t1','t2','t3'])
    def test_reused_standalone_preserves_baseline_and_reopens(self):
        self.init(); batch.work(self.run,FakeClient([self.good()]))
        a=copy.deepcopy(self.a)
        legacy=self.root/'legacy'; batch.serial.initialize(self.data,legacy,None)
        batch.serial.freeze(self.data,legacy,a,'standalone')
        second=self.root/'second'
        s=batch.initialize(self.dataset,second,reuse=legacy,model='test-model')
        self.assertEqual(s['contract_valid'],0); self.assertEqual(s['queue_counts']['pending'],1)
        self.assertEqual(len(batch.base.read(second/'manifest.json')['reopened']),1)
        self.assertEqual(len(list((self.run/'reviews').glob('*.json'))),1)

    def test_program_result_reuse_keeps_completed(self):
        self.init(); batch.work(self.run,FakeClient([self.good()]))
        second=self.root/'second'
        s=batch.initialize(self.dataset,second,reuse=self.run,model='test-model')
        self.assertEqual(s['contract_valid'],1)
        self.assertEqual(batch.base.read(second/'manifest.json')['reopened'],[])


class HttpTests(unittest.TestCase):
    def stream(self):
        r=dict(id='r1',status='completed',output=[dict(content=[dict(type='output_text',text='{}')])])
        return io.BytesIO(('data: '+json.dumps(dict(type='response.completed',response=r))+'\n\n').encode())
    def test_truncated_never_success(self):
        with self.assertRaises(ModelError): terminal_response(io.BytesIO(b'data: {"type":"response.output_text.delta"}\n\n'))
    def test_incomplete_and_refusal_never_success(self):
        for r in [dict(status='incomplete',incomplete_details={'reason':'max_output_tokens'}),dict(status='completed',output=[dict(content=[dict(type='refusal')])])]:
            with self.assertRaises(ModelError): response_text(r)
    def call_setup(self,directory,opener):
        auth=Path(directory)/'.codex/auth.json'; auth.parent.mkdir(); auth.write_text('{"OPENAI_API_KEY":"fake-test-key"}')
        return ResponsesClient('test-model',opener=opener,sleep=lambda n:None)
    def test_http_retry_bounded_cache_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            calls=[]
            def opener(request,timeout):
                calls.append(request)
                if len(calls)==1: raise urllib.error.HTTPError('url',429,'retry',{},io.BytesIO(b''))
                return self.stream()
            client=self.call_setup(directory,opener); cache=Path(directory)/'cache'
            with patch('pathlib.Path.home',return_value=Path(directory)),patch('scoring_pipeline.batch_client.configured_model',return_value='test-model'):
                a=client.call('rules',{},cache,'run'); b=client.call('rules',{},cache,'run')
            self.assertEqual(len(calls),2); self.assertEqual(a,b); self.assertEqual(len(a['attempts']),2)
            self.assertTrue(all('fake-test-key' not in p.read_text() for p in cache.glob('*.json'))); self.assertFalse(list(cache.glob('*.sse')))
    def test_policy_http_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            def opener(*args,**kwargs): raise urllib.error.HTTPError('url',400,'blocked',{},io.BytesIO(b'{"code":"cyber_policy"}'))
            client=self.call_setup(directory,opener); client.sleep=lambda n:self.fail('不得重试')
            with patch('pathlib.Path.home',return_value=Path(directory)),patch('scoring_pipeline.batch_client.configured_model',return_value='test-model'):
                with self.assertRaisesRegex(ModelError,'policy_blocked'): client.call('rules',{},Path(directory)/'cache','run')


if __name__=='__main__': unittest.main()
