"""v14的证据边界和持久执行回归；不以合同测试代替语义校准。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scoring_pipeline import stable_review as r, stable_batch as b
from scoring_pipeline.batch_client import ModelError, atomic_json, digest


def fixture():
    text='解释API的含义。'
    period={'period_id':'month','start':'2026-09-01T00:00:00+08:00','end':'2026-10-01T00:00:00+08:00'}
    event=dict(event='s:e',event_id='event',at='2026-09-10T00:00:00+08:00',text=text,actor_class='human',human_spans=[[0,len(text)]])
    unit=dict(key='s:t',user_id='test',title='解释概念',person_events=['s:e'],periods=[period],events=[event])
    context=dict(effective_request='解释API',necessary_scope='概念解释',observed_execution='解释',scope='parent',boundary='consistent',boundary_reason='单一目标',refs=['q'],
        challenge=dict(level='routine',reason='明确局部',alternative='无系统设计要求',scope_basis=dict(kind='explicit_requirement',fact='本人只要求概念解释',refs=['q']),routine_test=dict(sufficient='yes',reason='按概念释义即可满足要求，没有未决关联设计',refs=['q']),facets=[dict(aspect=f,status='not_demonstrated',fact='无该必要挑战',refs=[]) for f in r.FACETS],missing=[]))
    cells=[dict(item=k,state='no_opportunity',reason='局部提问没有独立处理机会',opportunity=dict(description='仅概念提问',refs=['q'])) for k in r.ITEMS]
    for c in cells:
        if c['item'].startswith('M'):c['challenge_handling']=dict(status='not_demonstrated',issue='',reason='无本人挑战处理',refs=[])
    cells[0].update(state='observed',action=dict(fact='明确概念与解释动作',refs=['q'],ai_role='解释',attribution_explanation='本人直接提问'),quality=dict(label='sufficient',reason='简单目标清楚',refs=['q']),performance=dict(verdict='sufficient',reason='目标有效，未见针对性选择',progression=dict(effective=dict(met=True,fact='明确解释对象',refs=['q']),adapted=dict(met=False,fact='无条件选择证据',refs=[]),verified=dict(met=False,fact='无对应核对证据',refs=[]))))
    value=dict(version=r.VERSION,key=unit['key'],evidence=[dict(id='q',event='s:e',quote=text,origin='direct_expression',origin_reason='本人提问')],
        period_reviews=[dict(period_ids=['month'],context=context,cells=cells)],needs_events=[])
    return unit,value


class ContractTests(unittest.TestCase):
    def setUp(self):self.u,self.v=fixture();self.c=self.v['period_reviews'][0]['cells'][0]
    def test_sufficient_is_not_advanced_behavior(self):
        row=r.check(self.u,self.v)[0];self.assertEqual(row['cells'][0]['performance']['verdict'],'sufficient');self.assertNotIn('level',row['cells'][0]['action'])
    def test_false_quote(self):
        self.v['evidence'][0]['quote']='不存在'
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_future(self):
        self.u['events'][0]['at']='2026-10-02T00:00:00+08:00'
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_earlier_week_cannot_receive_later_action(self):
        self.u['periods'][0]['end']='2026-09-08T00:00:00+08:00'
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_foreign_task_person(self):
        self.u['person_events']=[]
        with self.assertRaisesRegex(ValueError,'引文q s:e不在当前person_events'):r.check(self.u,self.v)
    def test_ai_not_person(self):
        self.u['events'][0]['actor_class']='assistant'
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_ledger_person_error_locates_quote(self):
        self.u['events'][0]['human_spans']=[]
        with self.assertRaisesRegex(ValueError,'账本本人归属/引文q s:e：引文超出已确认本人范围'):r.check(self.u,self.v)
    def test_input_error_locates_period_item_and_quote(self):
        self.u['events'][0]['actor_class']='assistant'
        self.v['evidence'][0]['origin']='context'
        with self.assertRaisesRegex(ValueError,'month/P1/本人动作/引文q s:e：非本人来源不能主归本人'):r.check(self.u,self.v)
    def test_person_span_error_locates_input(self):
        self.v['evidence'][0]['origin']='quoted_or_forwarded'
        self.u['events'][0]['human_spans']=[[0,2]]
        with self.assertRaisesRegex(ValueError,'month/P1/本人动作/引文q s:e：引文超出已确认本人范围'):r.check(self.u,self.v)
    def test_prior_action_is_context_not_current_input(self):
        self.u['periods'][0]['start']='2026-09-11T00:00:00+08:00'
        with self.assertRaisesRegex(ValueError,'month/P1/本人动作/引文q s:e：本人主张不属于本期'):r.check(self.u,self.v)
    def test_cross_task_context_cannot_be_input(self):
        self.u['person_events']=[]
        self.v['evidence'][0]['origin']='context'
        with self.assertRaisesRegex(ValueError,'month/P1/本人动作/引文q s:e：本人动作跨任务'):r.check(self.u,self.v)
    def test_copy_can_be_input_without_authorship_claim(self):
        self.v['evidence'][0]['origin']='quoted_or_forwarded';self.assertTrue(r.check(self.u,self.v))
    def test_copy_cannot_claim_method_analysis(self):
        self.v['evidence'][0]['origin']='quoted_or_forwarded'
        m=self.v['period_reviews'][0]['cells'][3];m.update(action=self.c.pop('action'),performance=self.c.pop('performance'),quality=self.c.pop('quality'),state='observed');self.c['state']='no_opportunity'
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_higher_band_needs_basis(self):
        self.c['performance']['progression']['adapted'].update(met=True,refs=['q'])
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_verified_needs_actual_check(self):
        self.c['performance']['progression']['adapted'].update(met=True,refs=['q'],actual_condition='真实歧义',choice='限定目标',difference_from_basic='纠正实际误解',fact='主动限定范围')
        self.c['performance']['progression']['verified'].update(met=True,refs=['q'])
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_gap_needs_responsibility(self):
        self.c['quality']['label']='necessary_gap'
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_unresolved_is_not_sufficient_or_zero(self):
        self.c['quality'].update(label='unresolved',missing=dict(needed='原始要求',why_changes_judgment='决定归责',checked_events=['s:e']))
        self.c['performance']['verdict']='unresolved'
        self.assertEqual(r.check(self.u,self.v)[0]['cells'][0]['quality']['label'],'unresolved')
    def test_sufficient_does_not_automatically_receive_full_marks(self):
        self.c['performance']['verdict']='complete'
        with self.assertRaisesRegex(ValueError,'递进判断不自洽'):r.check(self.u,self.v)
    def test_verified_cannot_skip_adapted(self):
        self.c['performance']['progression']['verified'].update(met=True,refs=['q'])
        with self.assertRaisesRegex(ValueError,'不能跳过针对性'):r.check(self.u,self.v)
    def test_copy_cannot_claim_verified_action(self):
        self.v['evidence'][0]['origin']='quoted_or_forwarded'
        self.c['performance']['progression']['adapted'].update(met=True,refs=['q'])
        with self.assertRaisesRegex(ValueError,'缺本人动作 adapted'):r.check(self.u,self.v)
    def test_missing_item(self):
        self.v['period_reviews'][0]['cells'].pop()
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_unknown_cannot_be_scored(self):
        self.c['state']='unobservable'
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_unobservable_needs_positive_opportunity_source(self):
        c=self.v['period_reviews'][0]['cells'][1];c.update(state='unobservable',missing=dict(needed='材料',why_changes_judgment='改变判断',checked_events=['s:e']))
        c['opportunity']['refs']=[]
        with self.assertRaisesRegex(ValueError,'具体机会'):r.check(self.u,self.v)
    def test_absent_method_is_not_unknown_challenge_handling(self):
        self.v['period_reviews'][0]['cells'][3]['challenge_handling']['status']='unresolved'
        with self.assertRaisesRegex(ValueError,'未证明独立挑战处理'):r.check(self.u,self.v)
    def test_related_cannot_point_to_no_opportunity(self):
        self.v['period_reviews'][0]['cells'][1].update(state='related',primary_item='P3')
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_duplicate_period(self):
        self.v['period_reviews'][0]['period_ids'].append('month')
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_shared_quote_requires_difference(self):
        self.v['period_reviews'][0]['cells'][1].update(state='observed',action=dict(self.c['action'],fact='材料足够'),quality=copy.deepcopy(self.c['quality']),performance=copy.deepcopy(self.c['performance']))
        with self.assertRaises(ValueError):r.check(self.u,self.v)
    def test_challenge_needs_grounded_scope(self):
        self.v['period_reviews'][0]['context']['challenge']['scope_basis']['refs']=[]
        with self.assertRaisesRegex(ValueError,'必要范围缺来源'):r.check(self.u,self.v)
    def test_challenge_needs_routine_path_reason(self):
        self.v['period_reviews'][0]['context']['challenge']['routine_test'].pop('reason')
        with self.assertRaisesRegex(ValueError,'缺常规路径检验'):r.check(self.u,self.v)
    def test_challenge_cannot_contradict_own_routine_test(self):
        self.v['period_reviews'][0]['context']['challenge']['level']='complex'
        with self.assertRaisesRegex(ValueError,'不自洽'):r.check(self.u,self.v)


class ExecutionTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.run=Path(self.tmp.name)/'run';self.run.mkdir();self.u,self.v=fixture()
    def tearDown(self):self.tmp.cleanup()
    def test_explicit_empty_selection_never_expands_to_all(self):
        with patch.object(b,'verified_dataset',return_value=dict(tasks={'s:t':dict(local_only=False)})):
            with self.assertRaisesRegex(ValueError,'显式空列表'):
                b.initialize(self.run/'unused.json',self.run/'new',keys=[])
        self.assertFalse((self.run/'new').exists())
    def test_restricted_packet_rejected_before_send(self):
        with self.assertRaises(ValueError):b.packet({'tasks':{'s:t':{'local_only':True}}},'s:t',self.u['periods'][0])
    def test_running_module_is_frozen(self):
        self.assertIn(str(Path(b.__file__).resolve()),b.module_hashes())
    def test_period_assembly_keeps_explicit_judgments(self):
        parts=[dict(period_id='month',value=self.v)]
        result=b.assemble('s:t',parts);self.assertEqual(result['evidence'][0]['id'],'month|q')
        self.assertEqual(result['period_reviews'][0]['cells'][0]['action']['refs'],['month|q'])
        self.assertTrue(r.check(self.u,result))
    def test_request_physically_excludes_future_and_index_summary(self):
        prior=copy.deepcopy(self.u['events'][0]);prior.update(event='s:prior',at='2026-09-02T00:00:00+08:00',text='早期需求')
        future=copy.deepcopy(self.u['events'][0]);future.update(event='s:future',at='2026-09-20T00:00:00+08:00',text='未来才知道的根因')
        unit=copy.deepcopy(self.u);unit['events'] += [prior,future];unit['person_events'] += ['s:prior','s:future']
        period=dict(self.u['periods'][0],start='2026-09-07T00:00:00+08:00',end='2026-09-14T00:00:00+08:00')
        allowed={e['event']:e for e in unit['events']}
        pack=dict(title='未来修好的目标',purpose='未来才知道的根因',boundary_reason='月末推断',periods=[period],raw_material=dict(person_events=[dict(event=e['event'],text=e['text']) for e in unit['events']],context_columns=[],context_index=[],other_context_columns=[],other_context_index=[]))
        data={'tasks':{'s:t':dict(local_only=False)}}
        with patch.object(b.source,'task',return_value=unit),patch.object(b,'source_packet',return_value=(unit,pack,allowed)):
            u,p,es=b.packet(data,'s:t',period)
        self.assertNotIn('s:future',es);self.assertNotIn('未来',json.dumps(p,ensure_ascii=False));self.assertEqual(u['person_events'],['s:e'])
        self.assertEqual(p['raw_material']['prior_person_context'][0]['event'],'s:prior')
    def test_unknown_response_time_is_not_request_time(self):
        unknown=dict(self.u['events'][0],event='s:reply',actor_class='assistant',human_spans=[],response_timestamp_unknown=True,text='回复可能实际发生在周末后')
        unit=copy.deepcopy(self.u);unit['events'].append(unknown);allowed={e['event']:e for e in unit['events']}
        period=self.u['periods'][0]
        pack=dict(periods=[period],raw_material=dict(person_events=[dict(event='s:e',text=self.u['events'][0]['text'])],context_columns=['event','linkage','preview'],context_index=[['s:reply','same_session_or_request',unknown['text']]],other_context_columns=['event'],other_context_index=[['s:reply']]))
        with patch.object(b.source,'task',return_value=unit),patch.object(b,'source_packet',return_value=(unit,pack,allowed)):
            u,p,es=b.packet({'tasks':{'s:t':dict(local_only=False)}},'s:t',period)
        self.assertNotIn('s:reply',es);self.assertNotIn(unknown['text'],json.dumps(p,ensure_ascii=False));self.assertEqual(p['raw_material']['other_context_index'],[])
        self.assertEqual(p['withheld_context']['response_time_unknown_count'],1)
    def test_other_task_context_keeps_role(self):
        other=dict(self.u['events'][0],event='s:other')
        unit=copy.deepcopy(self.u);unit['events'].append(other)
        pack=dict(raw_material=dict(person_events=[dict(event='s:e',text=self.u['events'][0]['text'])],context_columns=['event','kind','linkage','preview'],context_index=[['s:other','other_task_person_context','same_session_or_request',other['text']]],other_context_columns=[],other_context_index=[]))
        with patch.object(b.source,'task',return_value=unit),patch.object(b,'source_packet',return_value=(unit,pack,{e['event']:e for e in unit['events']})):
            _,p,_=b.packet({'tasks':{'s:t':dict(local_only=False)}},'s:t',self.u['periods'][0])
        linked=p['raw_material']['linked_context'][0]
        self.assertEqual(linked['kind'],'other_task_person_context')
        self.assertEqual(linked['assessment_role'],'other_task_context')
    def test_complete_visible_missing_check_does_not_repeat_call(self):
        self.v['period_reviews'][0]['cells'][1].update(state='unobservable',missing=dict(needed='更多背景',why_changes_judgment='决定是否可评',checked_events=['s:e']))
        pack=dict(raw_material=dict(person_events=[dict(event='s:e',text=self.u['events'][0]['text'])],prior_person_context=[]))
        from unittest.mock import Mock
        client=Mock();client.call.return_value=dict(text=json.dumps(self.v),request_sha256='request')
        with patch.object(b,'packet',return_value=(self.u,pack,{'s:e':self.u['events'][0]})):
            value,_,_=b.evaluate_one({},'s:t',self.run,dict(run_id='test',replicate='test'),client,self.u['periods'][0])
        self.assertEqual(value,self.v);client.call.assert_called_once()
        self.assertEqual(client.call.call_args.args[1]['full_context'],[])
    def test_context_preview_is_compact_and_full_event_remains_readable(self):
        other=dict(self.u['events'][0],event='s:context',text='背景'*700,actor_class='tool')
        unit=copy.deepcopy(self.u);unit['events'].append(other)
        pack=dict(raw_material=dict(person_events=[dict(event='s:e',text=self.u['events'][0]['text'])],context_columns=['event','kind','linkage','preview'],context_index=[['s:context','tool','same_session_or_request',other['text']]],other_context_columns=[],other_context_index=[]))
        with patch.object(b.source,'task',return_value=unit),patch.object(b,'source_packet',return_value=(unit,pack,{e['event']:e for e in unit['events']})):
            _,packet,allowed=b.packet({'tasks':{'s:t':dict(local_only=False)}},'s:t',self.u['periods'][0])
        self.assertEqual(len(packet['raw_material']['linked_context'][0]['preview']),200)
        self.assertTrue(packet['raw_material']['linked_context'][0]['preview_only'])
        self.assertEqual(allowed['s:context']['text'],other['text'])
    def test_extra_behavior_rating_is_rejected(self):
        self.v['period_reviews'][0]['cells'][0]['behavior']={'level':'B4'}
        with self.assertRaisesRegex(ValueError,'额外B评级'):r.check(self.u,self.v)
    def test_requested_context_is_added_without_repeating_visible_input(self):
        from unittest.mock import Mock
        context=dict(self.u['events'][0],event='s:context',actor_class='tool',text='必要上下文'*200)
        self.u['events'].append(context)
        pack=dict(raw_material=dict(person_events=[dict(event='s:e',text=self.u['events'][0]['text'])],prior_person_context=[],linked_context=[dict(event='s:context',preview=context['text'][:200])]))
        client=Mock();client.call.side_effect=[dict(text=json.dumps({'needs_events':['s:context']})),dict(text=json.dumps(self.v))]
        with patch.object(b,'packet',return_value=(self.u,pack,{e['event']:e for e in self.u['events']})):
            _,_,trace=b.evaluate_one({},'s:t',self.run,dict(run_id='test',replicate='test'),client,self.u['periods'][0])
        self.assertEqual(client.call.call_args_list[0].args[1]['full_context'],[])
        self.assertEqual(client.call.call_args_list[1].args[1]['full_context'],[context])
        self.assertEqual(set(trace['full_context_events']),{'s:e','s:context'})
    def test_worker_lock_excludes_second_writer(self):
        with b.RunLock(self.run):
            with self.assertRaises(ValueError):
                with b.RunLock(self.run):pass
    def test_failure_not_employee_unknown_and_success_not_recalled(self):
        data={'tasks':{'s:t':dict(local_only=False)}};m={'model':'test'}
        atomic_json(self.run/'queue.json',{'s:t':dict(status='pending',calls=0,history=[])})
        with patch.object(b,'open_run',return_value=(data,m)),patch.object(b,'inventory',return_value={}),patch.object(b,'evaluate',side_effect=ModelError('transport_timeout',True)):
            out=b.work(self.run,workers=1,client=object());self.assertEqual(out['counts'],{'failed':1})
        with patch.object(b,'open_run',return_value=(data,m)):self.assertEqual(b.retry_failed(self.run)['requeued'],['s:t'])
        with patch.object(b,'open_run',return_value=(data,m)),patch.object(b,'inventory',return_value={'s:t':{}}),patch.object(b,'evaluate') as ev:
            self.assertEqual(b.work(self.run,workers=1,client=object())['counts'],{'completed':1});ev.assert_not_called()
    def test_policy_refusal_never_requeued(self):
        atomic_json(self.run/'queue.json',{'s:t':dict(status='policy_blocked',retryable=False,history=[])})
        with patch.object(b,'open_run',return_value=({},{})):self.assertEqual(b.retry_failed(self.run)['requeued'],[])
    def test_frozen_dataset_drift_rejected(self):
        file=self.run/'data.json';atomic_json(file,{})
        atomic_json(self.run/'manifest.json',dict(execution_version=b.EXECUTION_VERSION,version=r.VERSION,dataset=str(file),dataset_sha256='wrong'))
        with self.assertRaises(ValueError):b.open_run(self.run)
    def test_missing_success_not_silently_retried(self):
        atomic_json(self.run/'queue.json',{'s:t':dict(status='completed',history=[])})
        with patch.object(b,'open_run',return_value=({'tasks':{}},{})),patch.object(b,'inventory',return_value={}):
            with self.assertRaises(ValueError):b.work(self.run,workers=1,client=object())
    def test_completed_period_survives_later_transport_failure(self):
        week=dict(self.u['periods'][0],period_id='week',start='2026-09-07T00:00:00+08:00',end='2026-09-14T00:00:00+08:00')
        self.u['periods'].insert(0,week);wv=copy.deepcopy(self.v);wv['period_reviews'][0]['period_ids']=['week']
        data={'tasks':{'s:t':{'periods':self.u['periods']}}};m={'run_id':'test'}
        tr={'calls':[{'synthetic':'request'}]}
        with patch.object(b,'evaluate_one',side_effect=[(wv,[],tr),ModelError('transport_timeout',True)]):
            with self.assertRaises(ModelError):b.evaluate(data,'s:t',self.run,m,object())
        self.assertEqual(len(list(self.run.glob('parts/*/*.json'))),1)
        def packet(data,key,period):return dict(self.u,periods=[period]),{},{}
        with patch.object(b,'packet',side_effect=packet),patch.object(b,'verify_model_calls') as cached,patch.object(b.source,'task',return_value=self.u),patch.object(b,'evaluate_one',return_value=(self.v,[],tr)) as ev:
            value,rows,trace=b.evaluate(data,'s:t',self.run,m,object())
        cached.assert_called_once();ev.assert_called_once();self.assertEqual(len(rows),2);self.assertEqual(len(trace['parts']),2)


if __name__=='__main__':unittest.main()
