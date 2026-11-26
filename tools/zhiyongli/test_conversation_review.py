"""验证连续语义交付、全维终态和故障边界，合成结果不能作为质量验收。"""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import jsonschema

from conversation_review import assess, ask_conversation, pages, spans, PAGE_BYTES
from model_config import ModelError
from runtime import digest


def task():
    return dict(id='t', records=[dict(id='u', role='user', text='请比较两份方案的费用差异。'),
        dict(id='a', role='assistant', text='已列出费用，等待你确认。'),
        dict(id='u2', role='user', text='先别采用，报价含税口径不同，请统一后再比较。')])


def final():
    from task_evidence_flow import no_observation
    from task_review_policy import RUBRIC
    items={c:no_observation(c, '本范围未见独立行为') for c in RUBRIC['items']}
    items['P1'].update(state='observed', band='outstanding', reason='比较对象和交付目标明确。',
        next_band='本范围未体现更突出的简洁准确性。',
        evidence=[dict(record_id='u', quote='请比较两份方案的费用差异。',claim='明确比较目标',origin='personal',primary_item='P1')])
    return dict(summary='给出比较目标后，又针对含税口径纠正了比较安排。',
        task_summaries=dict(t=dict(title='报价比较', goal='统一口径后比较',outcome='尚未采用结果',workflow=[])),items=items,
        requests=[], queries=[])


class ConversationReviewTests(unittest.TestCase):
    def test_changed_material_gets_stable_stage_identity_only_after_received_parent(self):
        from daily_pilot import request_payload
        from conversation_review import JSON_ONLY
        from review_schema import obj
        schema=obj(dict(answer=dict(type='string')))
        with TemporaryDirectory() as folder:
            root=Path(folder)/'new';root.mkdir();old=Path(folder)/'old';old.mkdir()
            client=self.client();client.reuse_root=old;client.daily_natural_json=True
            prior=request_payload(client,dict(records=[],context='旧材料',output_contract=schema),schema,'核对'+JSON_ONLY)
            name='repair-fixture-1';(old/(name+'.input.json')).write_text(json.dumps(prior))
            receipt=dict(status='received',model='fixture',endpoint='fixture',request_sha256=digest(dict(endpoint='fixture',payload=prior)),
                wire_body_sha256=hashlib.sha256(json.dumps(prior,ensure_ascii=False).encode()).hexdigest(),
                response=dict(status='completed',output=[dict(content=[dict(type='output_text',text='{"answer":"旧判断"}')])]))
            (old/(name+'.receipt.json')).write_text(json.dumps(receipt));sent=[]
            def send(client,folder,stage,*unused):sent.append(stage);return dict(answer='新材料判断')
            value=ask_conversation(client,root,name,dict(records=[],context='新材料'),schema,'核对',send,6000)
            self.assertEqual(value,dict(answer='新材料判断'));self.assertEqual(len(sent),1)
            self.assertIn('-payload-',sent[0]);self.assertTrue(sent[0].endswith('-1'))
            proof=json.loads((root/(sent[0]+'.lineage.json')).read_text())
            self.assertEqual(proof['prior_status'],'received');self.assertNotEqual(proof['prior_request_sha256'],proof['request_sha256'])
            receipt['status']='unknown';(old/(name+'.receipt.json')).write_text(json.dumps(receipt))
            with self.assertRaises(ModelError):ask_conversation(client,root,name,dict(records=[],context='再次改变'),schema,'核对',send,6000)
            self.assertEqual(len(sent),1)

    def test_missing_receipt_with_original_send_intent_cannot_migrate_directory(self):
        from request_budget import RequestBudget
        from review_schema import obj
        from daily_pilot import request_payload
        from conversation_review import JSON_ONLY
        schema=obj(dict(answer=dict(type='string')))
        with TemporaryDirectory() as folder:
            root=Path(folder)/'new';root.mkdir();old=Path(folder)/'old';old.mkdir()
            client=self.client();client.reuse_root=old;client.daily_natural_json=True;client.budget=RequestBudget(Path(folder)/'ledger',10,10)
            name='repair-fixture-1';prior=request_payload(client,dict(records=[],output_contract=schema),schema,'核对'+JSON_ONLY)
            (old/(name+'.input.json')).write_text(json.dumps(prior));client.budget.reserve(old/(name+'.attempt-0.receipt.json'),'original-hash')
            sender=Mock()
            with self.assertRaisesRegex(ModelError,'发送意图'):ask_conversation(client,root,name,dict(records=[],new_context=True),schema,'核对',sender,6000)
            sender.assert_not_called();self.assertEqual(client.budget.used(),1)

    def client(self):
        return SimpleNamespace(config=SimpleNamespace(model='fixture',responses_url='fixture'),output_token_limit=6000)

    def run_case(self, send, t=None, originals=None, audit_send=None):
        t=t or task()
        originals=originals or {r['id']:r for r in t['records']}
        with TemporaryDirectory() as d:
            def receive(*args):
                if args[2].startswith('conversation-quality-'):return audit_send(*args) if audit_send else dict(issues=[])
                if args[2].startswith('reading-'):
                    return dict(note='用户提供目标，后续要求统一含税口径。未采用结果。',evidence_refs=[],capacity_complete=True)
                value=send(*args)
                if value.get('items') is None:return value
                # 模型夹具选择实际原文编号；测试不把程序生成的分数冒充真实模型。
                candidates={text:key for row in args[3]['records'] for key,text in row.get('text_parts',[]) if key}
                items={}
                for code,cell in value['items'].items():
                    if cell['state']!='observed':continue
                    items[code]={key:cell[key] for key in ['band','reason','next_band','context_refs','negative']}
                    items[code]['evidence']=[dict(fragment_id=candidates.get(e['quote'],'forbidden'),claim=e['claim'],origin=e['origin']) for e in cell['evidence']]
                    if code=='E3':items[code]['e3_claim']=cell['e3_claim']
                if args[2].startswith('repair-'):
                    if args[3].get('task_workflows_in_scope'):
                        return dict(items={},summary=None,task_workflows={task:value['task_summaries'][task]['workflow'] for task in args[3]['task_workflows_in_scope']})
                    return dict(items={code:items.get(code) for code in args[3]['items_in_scope']},summary=None)
                return dict(value,items=items)
            return assess(self.client(),Path(d),[t],originals,receive)

    def test_whole_context_reaches_one_joint_judgment_with_later_correction(self):
        calls=[]
        def send(client,root,name,data,schema,instructions):
            calls.append(name)
            self.assertEqual([''.join(text for _,text in r['text_parts']) if 'text_parts' in r else r['text'] for r in data['records']],
                [r['text'] for r in task()['records']])
            return final()
        result,_=self.run_case(send)
        self.assertEqual(len(calls),1);self.assertTrue(calls[0].startswith('synthesis-'))
        self.assertEqual(len(result['items']),12)
        self.assertEqual(result['task_summaries']['t']['outcome'],'尚未采用结果')

    def test_binding_and_factual_errors_reach_the_same_targeted_repair(self):
        calls=[]
        def send(client,root,name,data,*unused):
            calls.append(name);value=final()
            value['items']['J1']=copy.deepcopy(value['items']['P1'])
            value['items']['J1']['evidence']=[dict(record_id='u2',quote='报价含税口径不同，',
                claim='识别含税口径差异',origin='personal',primary_item='J1')]
            if len(calls)==1:
                value['items']['P1']['reason']='错误的事实解释'
                value['items']['J3']=copy.deepcopy(value['items']['J1'])
                value['items']['J3']['evidence'][0].update(claim='纠正同一含税差异',primary_item='J3')
            else:
                self.assertEqual(set(data['items_in_scope']),{'P1','J1','J3'})
                correction=data['correction']
                self.assertTrue(correction['validation_errors']);self.assertTrue(correction['quality_findings'])
                self.assertEqual({r['item'] for r in correction['binding_conflicts'][0]['judgments']},{'J1','J3'})
            return value
        def audit(client,root,name,data,*unused):
            cell=data['candidate']['items'].get('P1')
            issues=[]
            if cell and cell['reason']=='错误的事实解释':
                issues=[dict(item='P1',claim_pointer='/items/P1/reason',criterion='P1.outstanding',
                    problem='候选的事实解释与目标原文不同',source_refs=[])]
            return dict(issues=issues)
        result,_=self.run_case(send,audit_send=audit)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['items']['J1']['band'],'outstanding')
        self.assertIsNone(result['items']['J3']['band'])

    def test_product_workflow_error_is_repaired_before_factual_acceptance(self):
        t=task();t['workflow_index']=[dict(name='push',record_id='tool',chars=10)]
        originals={row['id']:row for row in t['records']}
        originals['tool']=dict(id='tool',role='tool',text='加载push。',task_id='t')
        calls=[]
        def send(client,root,name,data,schema,instructions):
            calls.append(name);value=final()
            if len(calls)>1:
                self.assertIn('AI自主调用不能挂本人选择依据',str(data['correction']))
            value['task_summaries']['t']['workflow']=[dict(name='push',purpose='提交已核改动',
                selection='AI自主调用',user_refs=['r1'] if len(calls)==1 else [],tool_ref='r4')]
            return value
        result,_=self.run_case(send,t,originals)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['task_summaries']['t']['workflow'][0]['user_refs'],[])
        self.assertEqual(result['items']['P1']['band'],'outstanding')

    def test_unicode_and_long_rows_are_delivered_without_losing_characters(self):
        records=[dict(id='u',role='user',text='中😀\n'*20000,start=0,end=60000,excerpt=False)]
        output=list(pages(records))
        self.assertGreater(len(output),1)
        self.assertTrue(all(len(json.dumps(p,ensure_ascii=False).encode())<=PAGE_BYTES for p in output))
        self.assertEqual(''.join(r['text'] for p in output for r in p),records[0]['text'])
        last=0
        for p in output:
            for r in p:
                self.assertEqual(r['start'],last);last=r['end']
        self.assertEqual(last,len(records[0]['text']))

    def test_large_working_set_defers_complete_records_without_losing_recall_access(self):
        from conversation_review import working_set,unread_ids
        full=[dict(id='u'+str(i),role='user',start=0,end=8000,text='原'*8000,excerpt=False) for i in range(3)]
        before=copy.deepcopy(full);retained,deferred=working_set(full,[])
        self.assertEqual(full,before);self.assertTrue(retained);self.assertLessEqual(len(json.dumps(retained,ensure_ascii=False).encode()),PAGE_BYTES)
        originals={r['id']:r for r in full};self.assertEqual(set(unread_ids(originals,retained)),{r['record_id'] for r in deferred})
        for row in retained:self.assertEqual(row,originals[row['id']])
        for row in deferred:self.assertEqual((row['start'],row['end']),(0,8000))

    def test_task_boundaries_remain_present_in_multi_task_reading(self):
        first=task();second=copy.deepcopy(first);second['id']='second'
        for row in second['records']:row['id']='second-'+row['id']
        rows=spans([first,second])
        self.assertEqual([r['task_id'] for r in rows],['t']*3+['second']*3)

    def test_requested_context_stays_while_read_mainline_returns_to_searchable_directory(self):
        from conversation_review import defer_read_record,unread_ids
        full=[dict(id='old-user',role='user',text='完整用户原话',start=0,end=6),
              dict(id='old-tool',role='tool',text='完整执行反馈',start=0,end=6),
              dict(id='requested',role='user',text='刚刚补读原话',start=0,end=6)]
        before=copy.deepcopy(full);records=copy.deepcopy(full);requested={('requested',0,6)}
        self.assertEqual(defer_read_record(records,full,requested)['id'],'old-tool')
        self.assertEqual(defer_read_record(records,full,requested)['id'],'old-user')
        self.assertIsNone(defer_read_record(records,full,requested))
        self.assertEqual(records,[full[-1]]);self.assertEqual(full,before)
        self.assertEqual(set(unread_ids({r['id']:r for r in full},records)),{'old-tool','old-user'})

    def test_adjacent_tasks_share_identical_background_without_merging_user_input(self):
        first=task();second=copy.deepcopy(first);second['id']='second'
        second['records']=[dict(second['records'][0],id='second-u'),copy.deepcopy(first['records'][1])]
        rows=spans([first,second])
        self.assertEqual(sum(r['id']==first['records'][1]['id'] for r in rows),1)
        self.assertEqual({r['task_id'] for r in rows},{'t','second'})
        second['records'][1].update(boundary_context=True,excerpt=True)
        rows=spans([first,second]);shared=next(r for r in rows if r['id']==first['records'][1]['id'])
        self.assertTrue(shared['boundary_context']);self.assertTrue(shared['excerpt'])
        second['records'][1]['text']+='冲突'
        with self.assertRaisesRegex(ValueError,'来源ID重复'):spans([first,second])
        second['records']=[copy.deepcopy(first['records'][0])]
        with self.assertRaisesRegex(ValueError,'来源ID重复'):spans([first,second])

    def test_quality_findings_require_real_claim_and_real_criterion(self):
        from conversation_quality import validate
        from task_review_policy import RUBRIC
        candidate=final()
        issue=dict(item='P1',claim_pointer='/items/P1/reason',
            criterion='P1.reasonable',problem='测试定位',source_refs=[])
        self.assertIn('candidate_sha256',validate(dict(issues=[issue]),candidate,RUBRIC))
        for field in ('claim_pointer','criterion'):
            invalid=dict(issue,**{field:'不存在的原话'})
            with self.subTest(field=field),self.assertRaises(ValueError):
                validate(dict(issues=[invalid]),candidate,RUBRIC)

    def test_fragment_and_record_background_refs_resolve_without_guessing(self):
        from conversation_transport import encode,decode
        t=task();originals={r['id']:r for r in t['records']}
        data,_,transport=encode([t],spans([t]),originals,[],[],[])
        fragment=next(key for key,(ref,_) in transport['fragments'].items() if ref=='u')
        cell=final()['items']['P1']
        wire={k:cell[k] for k in ['band','reason','next_band','negative']}
        wire.update(evidence=[dict(fragment_id=fragment,claim='明确目标',origin='personal')],context_refs=[fragment,'r1'])
        value=decode(dict(items=dict(P1=wire),requests=[],task_summaries={}),transport)
        self.assertEqual(value['items']['P1']['context_refs'],['u'])

    def test_changed_source_is_blocked_before_model_call(self):
        t=task();originals={r['id']:copy.deepcopy(r) for r in t['records']}
        originals['u']['text']='不同来源'
        def send(*args): self.fail('来源校验失败后不能发请求')
        with self.assertRaisesRegex(ValueError,'来源合同'):
            self.run_case(send,t,originals)

    def test_ai_quote_cannot_be_scored_as_user(self):
        value=final();value['items']['P1']['evidence'][0].update(record_id='a',quote='已列出费用，等待你确认。')
        with self.assertRaises((ValueError,jsonschema.ValidationError)):
            self.run_case(lambda *args:value)

    def test_boundary_background_cannot_be_scored_as_user(self):
        t=task();t['records'][0]['boundary_context']=True
        with self.assertRaises((ValueError,jsonschema.ValidationError)):
            self.run_case(lambda *args:final(),t)

    def test_unknown_or_repeat_reads_do_not_loop(self):
        for record_id in ['missing','u']:
            def send(*args): return dict(summary=None,task_summaries=None,items=None,requests=[dict(record_id=record_id,start=0,chars=8)],queries=[])
            with self.subTest(record_id=record_id),self.assertRaises((ValueError,jsonschema.ValidationError)):
                self.run_case(send)

    def test_failure_keeps_received_intent_without_a_success_candidate(self):
        t=task()
        with TemporaryDirectory() as d:
            def send(*args): raise RuntimeError('unknown transport')
            with self.assertRaises(RuntimeError):
                assess(self.client(),Path(d),[t],{r['id']:r for r in t['records']},send)
            self.assertTrue((Path(d)/'reading-0.intent.json').exists())
            self.assertFalse((Path(d)/'conversation-result.json').exists())

    def test_same_behavior_cannot_be_scored_twice(self):
        value=final();value['items']['M1']=copy.deepcopy(value['items']['P1'])
        value['items']['M1']['item_code']='M1'
        value['items']['M1']['evidence'][0]['primary_item']='M1'
        with self.assertRaisesRegex(ValueError,'重复|重叠'):
            self.run_case(lambda *args:value)

    def test_reading_does_not_spend_final_judgment_reasoning_budget(self):
        client=self.client();client.reasoning_effort='low'
        def send(client,*args):
            self.assertIsNone(client.reasoning_effort)
            return dict(ok=True)
        with TemporaryDirectory() as d:
            value=ask_conversation(client,Path(d),'reading-0',{},
                dict(type='object',properties=dict(ok=dict(type='boolean')),required=['ok']),
                '阅读',send,200)
        self.assertTrue(value['ok']);self.assertEqual(client.reasoning_effort,'low')

    def test_failed_reading_keeps_existing_variant_instead_of_renaming_the_request(self):
        from conversation_review import read_mainline, JSON_ONLY
        client=self.client();rows=spans([task()])
        with TemporaryDirectory() as d:
            root=Path(d);reuse=root/'prior';reuse.mkdir()
            response=dict(note='已经阅读',evidence_refs=[],capacity_complete=True)
            sender=Mock(return_value=response)
            with patch('conversation_review.ask_conversation',sender):
                read_mainline(client,root,rows,Mock())
            args=sender.call_args.args;name='reading-0-page-contract-v2'
            (reuse/(name+'.intent.json')).write_text(json.dumps(dict(model='fixture',data=dict(args[3],previous=[]),
                schema=args[4],instructions=args[5]+JSON_ONLY)))
            client.reuse_root=reuse
            with patch('conversation_review.ask_conversation',sender):
                read_mainline(client,root,rows,Mock())
            self.assertEqual(sender.call_args.args[2],name)

    def test_unknown_reading_cannot_be_bypassed_by_contract_migration(self):
        from conversation_review import read_mainline, JSON_ONLY
        client=self.client()
        with TemporaryDirectory() as d:
            root=Path(d);reuse=root/'prior';reuse.mkdir();client.reuse_root=reuse
            (reuse/'reading-0.intent.json').write_text(json.dumps(dict(model='fixture',
                data=dict(records=[],previous=[]),schema={},instructions=JSON_ONLY)))
            with patch('conversation_review.received_stage',return_value=dict(status='unknown')):
                with self.assertRaisesRegex(ModelError,'禁止通过阶段迁移重发'):
                    read_mainline(client,root,spans([task()]),Mock())

    def test_empty_terminal_after_readback_is_a_contract_error(self):
        from conversation_transport import encode
        t=task();originals={r['id']:r for r in t['records']}
        originals['tool']=dict(id='tool',role='tool',text='实际背景')
        _,schema,_=encode([t],spans([t]),originals,[],[],['tool'])
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(dict(summary=None,task_summaries=None,items=None,requests=[],queries=[]),schema)

    def test_navigation_envelope_repair_never_changes_business_judgments(self):
        from conversation_transport import normalize_terminal
        raw=dict(summary='完整终判',task_summaries={},items=dict(P1=dict(band='good'),requests=[],queries=[]))
        before=copy.deepcopy(raw);fixed,moves=normalize_terminal(raw)
        self.assertEqual(raw,before);self.assertEqual(fixed['items'],dict(P1=dict(band='good')))
        self.assertEqual(fixed['summary'],raw['summary']);self.assertEqual(len(moves),2)
        for invalid in [dict(raw,summary=None),dict(raw,requests=[]),
                        dict(raw,items=dict(raw['items'],queries=['还需补读']))]:
            normalized,steps=normalize_terminal(invalid)
            self.assertEqual(normalized,invalid);self.assertEqual(steps,[])

    def test_large_tool_directory_stays_bounded_and_unlisted_records_remain_readable(self):
        from conversation_transport import encode,decode
        t=task();originals={r['id']:r for r in t['records']}
        originals.update({f'tool-{i}':dict(id=f'tool-{i}',role='tool',text='真实背景') for i in range(3000)})
        data,_,transport=encode([t],spans([t]),originals,[],[],list(originals)[3:])
        self.assertLessEqual(len(data['directory']),160)
        self.assertEqual(data['directory_window']['total'],3003)
        self.assertLess(len(json.dumps(data,ensure_ascii=False).encode()),50000)
        value=decode(dict(summary=None,task_summaries=None,items=None,
            requests=[dict(record_id='r3003',start=0,chars=4)],queries=[]),transport)
        self.assertEqual(value['requests'][0]['record_id'],'tool-2999')

    def test_terminal_overload_respects_server_cooldown_and_does_not_retry_unknown(self):
        from pipeline import Paused
        for status,sent,expected_calls in [('received',0,2),('received',10**12,1),('unknown',0,1)]:
            with self.subTest(status=status,sent=sent),TemporaryDirectory() as d:
                client=self.client()
                receipt=dict(status=status,sent_at=sent,response=dict(status='failed',output=[],
                    error=dict(type='service_unavailable_error',code='server_error',headers={'retry-after':'350'})))
                (Path(d)/'reading-0.receipt.json').write_text(json.dumps(receipt))
                sender=Mock(side_effect=[ModelError('response_not_completed'),dict(ok=True)])
                with patch('request_retry.read_receipt',return_value=receipt):
                    if status=='unknown' or sent:
                        with self.assertRaises(ModelError if status=='unknown' else Paused):
                            ask_conversation(client,Path(d),'reading-0',{},dict(type='object'),'阅读',sender,200)
                    else:
                        self.assertTrue(ask_conversation(client,Path(d),'reading-0',{},dict(type='object'),'阅读',sender,200)['ok'])
                self.assertEqual(sender.call_count,expected_calls)

    def test_received_upstream_failure_is_bounded_and_never_retries_output_or_unknown(self):
        for status,output,message,allowed in [('received',[],'Upstream request failed',True),('unknown',[],'Upstream request failed',False),('received',[{'type':'message'}],'Upstream request failed',False),('received',[],'different failure',False)]:
            with self.subTest(status=status,output=output,message=message),TemporaryDirectory() as d:
                receipt=dict(status=status,sent_at=0,response=dict(status='failed',output=output,error=dict(code='upstream_error',message=message)))
                (Path(d)/'reading-0.receipt.json').write_text(json.dumps(receipt))
                sender=Mock(side_effect=[ModelError('response_not_completed'),dict(ok=True)])
                with patch('request_retry.read_receipt',return_value=receipt):
                    if allowed:self.assertTrue(ask_conversation(self.client(),Path(d),'reading-0',{},dict(type='object'),'阅读',sender,200)['ok'])
                    else:
                        with self.assertRaises(ModelError):ask_conversation(self.client(),Path(d),'reading-0',{},dict(type='object'),'阅读',sender,200)
                self.assertEqual(sender.call_count,2 if allowed else 1)

    def test_one_extra_service_recovery_is_bound_to_the_same_request(self):
        import time
        from daily_pilot import request_payload
        from conversation_review import JSON_ONLY
        schema=dict(type='object');client=self.client();client.daily_natural_json=True
        payload=request_payload(client,dict(output_contract=schema),schema,'阅读'+JSON_ONLY)
        payload['max_output_tokens']=200
        sha=digest(dict(endpoint='fixture',payload=payload))
        receipt=dict(status='received',sent_at=0,response=dict(status='failed',output=[],
            error=dict(type='service_unavailable_error',code='server_error')))
        for expected_sha in [sha,'不同原请求']:
            with self.subTest(request_sha=expected_sha),TemporaryDirectory() as d:
                root=Path(d)
                for name in ['reading-0']+['reading-0-service-retry-'+str(i) for i in range(1,4)]:
                    (root/(name+'.receipt.json')).write_text(json.dumps(receipt))
                client.service_recovery=dict(stage='reading-0',request_sha256=expected_sha,verified_at=time.time())
                sender=Mock(side_effect=[ModelError('response_not_completed')]*4+[dict(ok=True)])
                with patch('request_retry.read_receipt',return_value=receipt):
                    if expected_sha==sha:
                        self.assertTrue(ask_conversation(client,root,'reading-0',{},schema,'阅读',sender,200)['ok'])
                        self.assertEqual(sender.call_count,5)
                    else:
                        with self.assertRaisesRegex(ModelError,'原请求不符'):
                            ask_conversation(client,root,'reading-0',{},schema,'阅读',sender,200)
                        self.assertEqual(sender.call_count,4)

    def test_received_truncation_can_resume_without_reasoning_but_unknown_cannot(self):
        for status in ['received','unknown']:
            with self.subTest(status=status),TemporaryDirectory() as d:
                root=Path(d);reuse=root/'old';reuse.mkdir()
                (reuse/'reading-3.input.json').write_text(json.dumps(dict(reasoning=dict(effort='low'))))
                client=self.client();client.reasoning_effort='low';client.reuse_root=reuse
                sender=Mock(side_effect=[ModelError('response_not_completed'),dict(ok=True)])
                receipt=dict(status=status,response=dict(status='incomplete',incomplete_details=dict(reason='max_output_tokens')))
                (reuse/'reading-3.receipt.json').write_text(json.dumps(receipt))
                with patch('request_retry.read_receipt',return_value=receipt):
                    if status=='unknown':
                        with self.assertRaises(ModelError):
                            ask_conversation(client,root/'new','reading-3',{},dict(type='object'),'阅读',sender,200)
                        self.assertEqual(sender.call_count,1)
                    else:
                        result=ask_conversation(client,root/'new','reading-3',{},dict(type='object'),'阅读',sender,200)
                        self.assertTrue(result['ok'])
                        self.assertEqual([call.args[2] for call in sender.call_args_list],['reading-3','reading-3-without-reasoning'])
                self.assertEqual(client.reasoning_effort,'low')

    def test_quality_packet_reuse_is_exact_and_restores_client_after_preflight_failure(self):
        from daily_pilot import request_payload
        from conversation_review import JSON_ONLY
        schema=dict(type='object');old='conversation-quality-original';new='conversation-quality-new'
        for status,changed in [('received',False),('received',True),('unknown',False)]:
            with self.subTest(status=status,changed=changed),TemporaryDirectory() as d:
                root=Path(d);reuse=root/'old';reuse.mkdir();client=self.client()
                client.daily_natural_json=True;client.output_token_limit=200
                packet=dict(candidate='原核查包',output_contract=schema)
                payload=request_payload(client,packet,schema,'核查'+JSON_ONLY)
                (reuse/(old+'.intent.json')).write_text(json.dumps(dict(endpoint='fixture',payload=payload)))
                (reuse/(old+'.receipt.json')).write_text(json.dumps(dict(status=status,
                    request_sha256=digest(dict(endpoint='fixture',payload=payload)),
                    response=dict(status='completed',output=[]))))
                client.daily_natural_json=False;client.output_token_limit=6000;client.reuse_root=reuse
                sender=Mock(return_value=dict(issues=[]))
                data=dict(candidate='不同核查包' if changed else '原核查包')
                with patch('conversation_review.ask',sender):
                    if status=='unknown':
                        with self.assertRaisesRegex(ModelError,'未决请求'):
                            ask_conversation(client,root,new,data,schema,'核查',Mock(),200)
                        sender.assert_not_called()
                    else:
                        ask_conversation(client,root,new,data,schema,'核查',Mock(),200)
                        self.assertEqual(sender.call_args.args[2],new if changed else old)
                self.assertFalse(client.daily_natural_json)
                self.assertEqual(client.output_token_limit,6000)

    def test_quality_packet_keeps_original_failed_retry_chain(self):
        from daily_pilot import request_payload
        from conversation_review import JSON_ONLY
        schema=dict(type='object');base='conversation-quality-original'
        with TemporaryDirectory() as d:
            root=Path(d);reuse=root/'old';reuse.mkdir();client=self.client()
            client.daily_natural_json=True;client.output_token_limit=200
            payload=request_payload(client,dict(output_contract=schema),schema,'核查'+JSON_ONLY)
            for name in [base]+[base+'-service-retry-'+str(i) for i in range(1,4)]:
                (reuse/(name+'.intent.json')).write_text(json.dumps(dict(endpoint='fixture',payload=payload)))
                (reuse/(name+'.receipt.json')).write_text(json.dumps(dict(status='received',sent_at=0,
                    request_sha256=digest(dict(endpoint='fixture',payload=payload)),
                    response=dict(status='failed',output=[],error=dict(type='service_unavailable_error',code='server_error')))))
            client.daily_natural_json=False;client.output_token_limit=6000;client.reuse_root=reuse
            sender=Mock(side_effect=ModelError('response_not_completed'))
            with patch('conversation_review.ask',sender),self.assertRaisesRegex(ModelError,'次数已达上限'):
                ask_conversation(client,root,'conversation-quality-renamed',{},schema,'核查',Mock(),200)
            self.assertEqual([c.args[2] for c in sender.call_args_list],
                [base]+[base+'-service-retry-'+str(i) for i in range(1,4)])
            self.assertFalse(client.daily_natural_json)


if __name__=='__main__': unittest.main()
