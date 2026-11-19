"""会话执行器的完整算分、原文归属、合同纠错及中断恢复；不替代真实模型质量验收。"""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from runtime import RUBRIC, atomic, digest
from pipeline import Paused, evaluate_week
from session_evidence import SessionEvidence
from session_review import EvidenceState, action_schema, check_schema, decode_record, evaluate, seed_state, score_contract
from test_execution_paths import FixtureClient, packet, record


def sample():
    value=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
    value['events'][0]['source']=dict(frame='audit:1:request:0',pointer='captured_user')
    return value


def ownership(data):
    available={e['id'] for e in data['evidence'] if e['origin'] in {'direct_expression','explicit_adoption'}}
    claims=[dict(id='act-'+code,fact='合成本项独立事实'+code,
        atom_ids=[a['id'] for a in data['atoms'] if 'person-'+code in a['refs']],primary_item=code,
        related_items=[],independence='合成独立原句'+code) for code in RUBRIC['items'] if 'person-'+code in available]
    used={i for c in claims for i in c['atom_ids']}
    return dict(claims=claims,unscored_atoms=[dict(atom_ids=[a['id']],reason='合成不计分背景') for a in data['atoms'] if a['id'] not in used],
        item_notes={code:'合成原句主归，未确认本人时不生成行为。' for code in RUBRIC['items']})


def anchor_response(cell):
    """将合成夹具的既定正向档转为本次模型传输格式，不评价真实材料。"""
    verdict=cell.pop('performance')['verdict'];reason=cell.pop('reason')
    cell['anchor_selection']=dict(criterion=RUBRIC['performance_anchors'][cell['item']][verdict],
        reason=reason,refs=cell['quality']['refs'])
    cell['quality'].pop('reason');cell['quality'].pop('refs')


class BehaviorClaimTests(unittest.TestCase):
    def test_duplicate_transport_ids_do_not_create_another_model_request_or_hide_unknowns(self):
        from session_scoring import canonical_score_response
        original=dict(review=dict(period_reviews=[dict(cells=[dict(item='P1',behavior_ids=['act-one','act-one','unknown','unknown'])])]))
        value,changes=canonical_score_response(original)
        self.assertEqual(['act-one','unknown'],value['review']['period_reviews'][0]['cells'][0]['behavior_ids'])
        self.assertEqual(['act-one','act-one','unknown','unknown'],original['review']['period_reviews'][0]['cells'][0]['behavior_ids'])
        self.assertEqual(1,len(changes))

    def test_contract_feedback_locates_state_and_never_echoes_material(self):
        from review_schema import obj,enum
        schema=obj(dict(cell=dict(anyOf=[
            obj(dict(state=enum('related'),reason=dict(type='string'))),
            obj(dict(state=enum('unobservable'),missing=dict(type='string')))])))
        with self.assertRaises(ValueError) as caught:
            check_schema(dict(cell=dict(state='observed',reason='私有员工材料'*1000)),schema)
        message=str(caught.exception)
        self.assertIn('cell/state',message);self.assertIn('related',message)
        self.assertNotIn('私有员工材料',message);self.assertLess(len(message),300)

    def test_contract_feedback_follows_matching_quality_branch(self):
        from review_schema import obj,enum
        positive=obj(dict(state=enum('observed'),quality=obj(dict(label=enum('sufficient'),refs=dict(type='array',minItems=1))),
            reason=dict(type='string')))
        negative=obj(dict(state=enum('observed'),quality=obj(dict(label=enum('minor_gap'),
            responsibility=obj(dict(actual_gap=dict(type='string',pattern='[\\u4e00-\\u9fff]'))))),reason=dict(type='string')))
        schema=obj(dict(cell=dict(anyOf=[positive,negative])))
        with self.assertRaises(ValueError) as caught:
            check_schema(dict(cell=dict(state='observed',quality=dict(label='minor_gap',
                responsibility=dict(actual_gap='true')),reason='私有正文')),schema)
        message=str(caught.exception)
        self.assertIn('cell/quality/responsibility/actual_gap',message)
        self.assertIn('pattern=',message);self.assertNotIn('refs',message);self.assertNotIn('私有正文',message)

    def test_original_anchor_mapping_keeps_positive_absence_separate_from_defect(self):
        from session_scoring import expand_anchor_selection
        choice=dict(criterion=RUBRIC['performance_anchors']['P2']['good'],
            reason='原文支持针对当前条件的适配优势。',refs=['person-a'])
        cell=dict(item='P2',state='observed',quality=dict(label='sufficient'),anchor_selection=copy.deepcopy(choice))
        expand_anchor_selection(cell,False)
        self.assertEqual('good',cell['performance']['verdict']);self.assertEqual('sufficient',cell['quality']['label'])
        self.assertEqual(choice['reason'],cell['reason'])
        with self.assertRaisesRegex(ValueError,'本项原评分尺'):
            expand_anchor_selection(dict(item='P2',quality=dict(label='sufficient'),anchor_selection=dict(choice,criterion='自造高档门槛')),False)
        negative=dict(item='P2',state='observed',quality=dict(label='minor_gap'),
            anchor_selection=dict(choice,criterion=RUBRIC['quality']['minor_gap'],reason='原文已证的实际非关键缺陷。'))
        expand_anchor_selection(negative,True)
        self.assertEqual('partial',negative['performance']['verdict'])
        self.assertEqual('scored_fragment_scope',negative['performance']['scope'])

    def test_schema_rejects_primary_as_related_and_supplied_as_mje(self):
        from session_scoring import behavior_atoms,claim_schema
        evidence=[dict(id='supplied-a',quote='已有材料',origin='quoted_or_forwarded'),
            dict(id='person-b',quote='本人安排',origin='direct_expression')]
        atoms=behavior_atoms(evidence,{'supplied-a':('source-a',0),'person-b':('source-b',0)})
        schema=claim_schema(atoms,evidence)
        claim=dict(id='act-one',fact='本人安排',atom_ids=['a2'],primary_item='M1',related_items=['P1'],independence='独立事实')
        check_schema([claim],schema)
        with self.assertRaises(ValueError):check_schema([dict(claim,related_items=['M1'])],schema)
        with self.assertRaises(ValueError):check_schema([dict(claim,atom_ids=['a1'])],schema)

    def test_related_schema_binds_behavior_ids_to_one_primary(self):
        from session_scoring import bind_behavior_ids
        from review_schema import obj,enum,array
        cell=obj(dict(state=enum('related'),opportunity=obj(dict(description=dict(type='string'))),
            primary_item=enum('M1','M3')))
        schema=obj(dict(cell=dict(anyOf=[cell])))
        claims=[dict(id='act-method',primary_item='M1'),dict(id='act-adjust',primary_item='M3')]
        bind_behavior_ids(schema,claims,'M2')
        value=dict(cell=dict(state='related',opportunity=dict(description='合成关联'),primary_item='M3',behavior_ids=['act-adjust']))
        check_schema(value,schema)
        value['cell']['behavior_ids'].append('act-method')
        with self.assertRaises(ValueError) as caught:check_schema(value,schema)
        self.assertIn('behavior_ids/1',str(caught.exception))

    def test_one_sentence_can_supply_distinct_facts_but_one_action_cannot_gain_two_owners(self):
        from session_scoring import behavior_atoms,expand_claims
        evidence=[dict(id='person-a',quote='先核对金额，再停止旧路径',origin='direct_expression')]
        atoms=behavior_atoms(evidence,{'person-a':('original-event',100)})
        self.assertEqual(['先核对金额，','再停止旧路径'],[a['text'] for a in atoms])
        def claim(identifier,code,selected):
            return dict(id=identifier,fact=identifier,atom_ids=selected,primary_item=code,related_items=[],independence='独立事实')
        value=dict(claims=[claim('act-check','J2',['a1']),claim('act-stop','E2',['a2'])],unscored_atoms=[])
        result=expand_claims(value,evidence,atoms)
        self.assertEqual('先核对金额，',result[0]['sources'][0]['quote'])
        self.assertEqual(100,result[0]['sources'][0]['start'])
        value['claims'][0]=claim('act-adjust','M3',['a2'])
        with self.assertRaisesRegex(ValueError,'同一原文动作重复主归'):
            expand_claims(value,evidence,atoms)

    def test_another_evidence_id_cannot_hide_the_same_original_action(self):
        from session_scoring import behavior_atoms,expand_claims
        evidence=[dict(id=ref,quote='停止旧路径',origin='direct_expression') for ref in ('person-a','person-b')]
        atoms=behavior_atoms(evidence,{ref:('same-original',30) for ref in ('person-a','person-b')})
        self.assertEqual(1,len(atoms));self.assertEqual(['person-a','person-b'],atoms[0]['refs'])
        with self.assertRaisesRegex(ValueError,'尚未处置'):
            expand_claims(dict(claims=[],unscored_atoms=[]),evidence,atoms)

    def test_amount_separators_and_overlapping_quotes_keep_original_bytes(self):
        from session_scoring import behavior_atoms
        text='核对39,624.27，再取数。'
        evidence=[dict(id='person-a',quote=text,origin='direct_expression'),
            dict(id='person-b',quote='再取数。',origin='direct_expression')]
        atoms=behavior_atoms(evidence,{'person-a':('same',0),'person-b':('same',text.index('再'))})
        self.assertEqual(text,''.join(a['text'] for a in atoms))
        self.assertTrue(any('39,624.27' in a['text'] for a in atoms))
        self.assertTrue(all(a['end']<=b['start'] for a,b in zip(atoms,atoms[1:])))


class ScriptedClient:
    session_review=True
    def __init__(self, actions, *, interrupt=None):
        self.actions=actions;self.interrupt=interrupt;self.sent=0
        self.config=SimpleNamespace(model='synthetic-session-contract',responses_url='https://ai.apsatcom.com/v1/responses')

    def _call(self,payload,receipt):
        signature=digest(payload)
        if receipt.exists():
            value=json.loads(receipt.read_text())
            if value['request_hash']!=signature:raise ValueError('测试回执请求签名变化')
            return value
        if self.sent==self.interrupt:raise KeyboardInterrupt('合成发送前中断')
        self.sent+=1
        data=json.loads(payload['input'])
        if payload['text']['format']['name']=='zhiyongli_session_claims':
            value=ownership(data)
        elif payload['text']['format']['name']=='zhiyongli_session_score':
            unit=dict(key=data['key'],periods=data['periods'],events=[dict(event=data['person_events'][0],actor_class='human')])
            review=json.loads(FixtureClient().call(unit,None)['response']['output'][0]['content'][0]['text'])
            del review['evidence']
            for cell in review['period_reviews'][0]['cells']:
                if cell['item'].startswith('P'):cell['performance']['verdict']='reasonable'
                if cell['item'].startswith('J'):cell['performance']['verdict']='outstanding'
            value=dict(review=review,presentation=dict(case=None,items=[dict(item_code=code,
                basis='仅合成用例：说明该项已确认的本人行为及对应判据。',higher_band='本例没有证明紧邻高档要求的完整表现。') for code in RUBRIC['items']]))
            for cell in review['period_reviews'][0]['cells']:
                cell['behavior_ids']=['act-'+cell['item']]
                cell['action'].pop('refs');cell['action'].pop('fact')
            codes=data.get('item_codes',list(RUBRIC['items']))
            review['period_reviews'][0]['cells']=[c for c in review['period_reviews'][0]['cells'] if c['item'] in codes]
            value['presentation']['items']=[i for i in value['presentation']['items'] if i['item_code'] in codes]
            case=data.get('presentation_case')
            if case:
                value['presentation']['case']=dict(task_key=case['task_key'],outcome='合成用例没有独立验收回执。',
                    claims=[dict(text='合成'+part,item_codes=['P1'],record_ids=case['record_ids'][:1],
                        assessment=dict(kind='support',reason='仅核合成原句与解读对应。')) for part in ('目标','过程','结果边界')])
            if data.get('evaluation_scope')=='person_week_fragment':
                value['presentation']=None
                period=value['review']['period_reviews'][0]
                period['context']['scope']='scored_fragment_scope'
                for cell in period['cells']:cell['performance']['scope']='scored_fragment_scope'
            for cell in review['period_reviews'][0]['cells']:
                anchor_response(cell)
        else:
            turn=int(receipt.parent.name)
            value=self.actions[turn]
            if callable(value):value=value(data)
        if isinstance(value,str):
            result=record({})
            result['response']['output'][0]['content'][0]['text']=value
        else:result=record(value)
        result['request_hash']=signature
        result['request_sha256']=digest(dict(endpoint=self.config.responses_url,payload=payload))
        atomic(receipt,result)
        return result


class SessionReviewTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.packet=sample()
        self.store=SessionEvidence.build(self.packet,self.root/'store');self.addCleanup(self.store.close)
        self.state=EvidenceState(self.packet,self.store,self.root)
        self.key=self.state.candidates[0]
        self.text=self.packet['events'][0]['text']

    def loc(self,start=0,end=None,read_id=0):
        return dict(read_id=read_id,event_key=self.key,start=start,end=len(self.text) if end is None else end)

    def save(self,read_id=0):
        sources=[]
        for code in RUBRIC['items']:
            quote='合成合同夹具'+code;start=self.text.index(quote)
            sources.append(dict(id='person-'+code,location=self.loc(start,start+len(quote),read_id),
                origin='direct_expression',origin_reason='合成原句核验'))
        return dict(materials=[dict(event_key=self.key,classification='human',spans=[self.loc(read_id=read_id)],
            task_id='goal',reason='合成本人原句')],tasks=[dict(task_id='goal',title='合同回归',purpose='验证串联而非评价真人',
            boundary='confirmed',boundary_reason='合成明确目标')],sources=sources,summary='合成：十二项独立原句及有限反馈。')

    def actions(self):
        return [dict(action=dict(operation='read_event',arguments=dict(event_key=self.key,start=0,max_characters=6000))),
            dict(action=dict(operation='save',arguments=self.save(10001))),
            dict(action=dict(operation='ready',arguments=dict(source_ids=['person-'+c for c in RUBRIC['items']],coverage_note='仅合成完整合同')))]

    def read(self):self.state.reads[0]=self.store.read_event(self.key,0,6000)

    def test_new_path_completes_twelve_items_without_any_old_reader_or_merge(self):
        client=ScriptedClient(self.actions());path=self.root/'run'
        with patch('pipeline._evaluate_week',side_effect=AssertionError('禁止旧链路')):
            result=evaluate_week(self.packet,path,client)
        self.assertEqual(16,client.sent)
        self.assertEqual('81.0',result['reports'][0]['score'])
        self.assertEqual(12,len(result['reports'][0]['items']))
        self.assertTrue((path/'session-completed.json').exists())
        self.assertFalse((path/'reading').exists())

    def test_restart_uses_all_saved_receipts_and_does_not_repeat_model(self):
        path=self.root/'run';client=ScriptedClient(self.actions(),interrupt=2)
        with self.assertRaises(KeyboardInterrupt):evaluate(self.packet,path,client)
        before={p:p.read_bytes() for p in path.rglob('*') if p.is_file() and p.name!='session.lock'}
        resumed=ScriptedClient(self.actions());result=evaluate(self.packet,path,resumed)
        self.assertEqual(14,resumed.sent)
        self.assertEqual('81.0',result['reports'][0]['score'])
        for p,raw in before.items():self.assertEqual(raw,p.read_bytes())
        repeated=ScriptedClient(self.actions());self.assertEqual(result,evaluate(self.packet,path,repeated))
        self.assertEqual(0,repeated.sent)

    def test_dimension_restart_replays_completed_judgments_and_preserves_claim_ownership(self):
        path=self.root/'dimension-restart'
        with self.assertRaises(KeyboardInterrupt):
            evaluate(self.packet,path,ScriptedClient(self.actions(),interrupt=6))
        prior={p:p.read_bytes() for p in (path/'dimensions').rglob('model.receipt.json')}
        self.assertEqual(2,len(prior))
        client=ScriptedClient(self.actions());original=client._call;inputs=[]
        def call(payload,target):
            data=json.loads(payload['input'])
            if data.get('dimension'):inputs.append(data)
            return original(payload,target)
        client._call=call
        self.assertEqual('81.0',evaluate(self.packet,path,client)['reports'][0]['score'])
        self.assertEqual(10,client.sent)
        self.assertEqual(list(range(12)),[len(d['prior_claims']) for d in inputs])
        self.assertTrue(all('performance' not in c and 'quality' not in c for d in inputs for c in d['prior_claims']))
        self.assertEqual(prior,{p:p.read_bytes() for p in prior})
        self.assertEqual(12,len(json.loads((path/'review.json').read_text())['value']['period_reviews'][0]['cells']))

    def test_unknown_dimension_never_repeats_or_assembles_partial_score(self):
        path=self.root/'dimension-unknown'
        with self.assertRaises(KeyboardInterrupt):
            evaluate(self.packet,path,ScriptedClient(self.actions(),interrupt=6))
        target=path/'dimensions/P2/0/model.receipt.json';value=json.loads(target.read_text())
        value['status']='unknown';atomic(target,value)
        client=ScriptedClient(self.actions())
        with self.assertRaises(Paused) as error:evaluate(self.packet,path,client)
        self.assertEqual('unknown',error.exception.state);self.assertEqual(0,client.sent)
        self.assertFalse((path/'bundle.json').exists())

    def test_same_behavior_cannot_be_scored_twice_by_renaming_its_benefit(self):
        client=ScriptedClient(self.actions());original=client._call;bad=[]
        def call(payload,target):
            result=original(payload,target);data=json.loads(payload['input'])
            if data.get('dimension')=='E2' and not bad:
                value=decode_record(result);value['review']['period_reviews'][0]['cells'][0]['behavior_ids']=['act-M3']
                result['response']['output'][0]['content'][0]['text']=json.dumps(value)
                atomic(target,result);bad.append(value)
            elif data.get('dimension')=='E2':
                self.assertEqual(bad[0],data['previous_response'])
                self.assertTrue(any(word in data['contract_feedback']['error'] for word in ('主归','behavior_ids')))
            return result
        client._call=call;root=self.root/'claim-repair'
        self.assertEqual('81.0',evaluate(self.packet,root,client)['reports'][0]['score'])
        self.assertEqual(17,client.sent)
        self.assertEqual(1,len(list((root/'dimensions/M3').rglob('model.receipt.json'))))
        self.assertTrue((root/'behavior-claims.json').exists())

    def test_negative_responsibility_requires_fact_explanations_instead_of_boolean_placeholders(self):
        client=ScriptedClient(self.actions());original=client._call;bad=[]
        def call(payload,target):
            result=original(payload,target);data=json.loads(payload['input'])
            if data.get('dimension')=='P1' and not bad:
                value=decode_record(result);cell=value['review']['period_reviews'][0]['cells'][0]
                cell['quality'].update(label='minor_gap',responsibility=dict(known_at_time='true',person_control=':',
                    feasible_alternative='合成可行替代',actual_gap='合成实际影响'))
                cell['anchor_selection']['criterion']=RUBRIC['quality']['minor_gap']
                result['response']['output'][0]['content'][0]['text']=json.dumps(value);atomic(target,result);bad.append(True)
            return result
        client._call=call;root=self.root/'responsibility-facts'
        result=evaluate(self.packet,root,client)
        self.assertEqual('81.0',result['reports'][0]['score']);self.assertEqual(17,client.sent)
        self.assertTrue((root/'dimensions/P1/0/contract-error.json').exists())

    def test_related_item_can_precede_its_primary_without_changing_final_judgment(self):
        client=ScriptedClient(self.actions());original=client._call
        def call(payload,target):
            result=original(payload,target);data=json.loads(payload['input']);value=decode_record(result)
            if payload['text']['format']['name']=='zhiyongli_session_claims':
                claim=next(c for c in value['claims'] if c['id']=='act-M1')
                claim.update(primary_item='M3',related_items=['M1'])
            elif data.get('dimension')=='M1':
                cell=value['review']['period_reviews'][0]['cells'][0]
                for field in ('action','quality','performance','anchor_selection','difference'):cell.pop(field,None)
                cell.update(state='related',primary_item='M3',behavior_ids=['act-M1'],reason='同一行为主归调整执行，本项仅关联。')
                cell['challenge_handling']=dict(status='not_demonstrated',reason='仅关联主归行为',refs=[])
            else:return result
            result['response']['output'][0]['content'][0]['text']=json.dumps(value);atomic(target,result)
            return result
        client._call=call;root=self.root/'forward-primary'
        evaluate(self.packet,root,client)
        cells=json.loads((root/'review.json').read_text())['value']['period_reviews'][0]['cells']
        self.assertEqual('related',next(c for c in cells if c['item']=='M1')['state'])
        self.assertEqual('observed',next(c for c in cells if c['item']=='M3')['state'])

    def test_explicit_new_root_resumes_only_identical_scoring_requests(self):
        parent=self.root/'score-parent'
        with self.assertRaises(KeyboardInterrupt):
            evaluate(self.packet,parent,ScriptedClient(self.actions(),interrupt=15))
        before={p:p.read_bytes() for p in parent.rglob('*.receipt.json')}
        client=ScriptedClient([]);client.session_seed_from=parent
        client.session_seed_allow_terminal=True;client.session_resume_scoring=True
        root=self.root/'score-child';bundle=evaluate(self.packet,root,client)
        self.assertEqual('81.0',bundle['reports'][0]['score']);self.assertEqual(1,client.sent)
        self.assertEqual(12,len(list(root.rglob('inherited.json'))))
        repeated=ScriptedClient([]);repeated.session_seed_from=parent;repeated.session_seed_allow_terminal=True
        self.assertEqual(bundle,evaluate(self.packet,root,repeated));self.assertEqual(0,repeated.sent)
        self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_explicit_resume_after_ownership_started_before_any_dimension(self):
        parent=self.root/'ownership-parent'
        with self.assertRaises(KeyboardInterrupt):
            evaluate(self.packet,parent,ScriptedClient(self.actions(),interrupt=3))
        self.assertTrue((parent/'ownership').is_dir());self.assertFalse((parent/'dimensions').exists())
        client=ScriptedClient([]);client.session_seed_from=parent
        client.session_seed_allow_terminal=True;client.session_resume_scoring=True
        bundle=evaluate(self.packet,self.root/'ownership-child',client)
        self.assertEqual('81.0',bundle['reports'][0]['score']);self.assertEqual(13,client.sent)

    def test_explicit_seed_cannot_skip_unknown_scoring_attempt(self):
        parent=self.root/'unknown-ownership-parent'
        with self.assertRaises(KeyboardInterrupt):
            evaluate(self.packet,parent,ScriptedClient(self.actions(),interrupt=4))
        target=parent/'ownership/2/model.receipt.json'
        value=json.loads((parent/'ownership/0/model.receipt.json').read_text())
        value['status']='unknown';atomic(target,value)
        client=ScriptedClient([]);client.session_seed_from=parent
        client.session_seed_allow_terminal=True;client.session_resume_scoring=True
        with self.assertRaises(Paused) as caught:
            evaluate(self.packet,self.root/'unknown-ownership-child',client)
        self.assertEqual('unknown',caught.exception.state);self.assertEqual(0,client.sent)

    def test_invalid_case_repairs_last_item_without_repeating_previous_items(self):
        client=ScriptedClient(self.actions());original=client._call;bad=[]
        def call(payload,target):
            result=original(payload,target)
            data=json.loads(payload['input'])
            if data.get('dimension')=='E3' and not bad:
                value=decode_record(result);value['presentation']['case']['claims'][0]['item_codes']=[]
                result['response']['output'][0]['content'][0]['text']=json.dumps(value)
                atomic(target,result);bad.append(value)
            elif data.get('dimension')=='E3':
                self.assertEqual(bad[0],data['previous_response'])
                self.assertIn('presentation/case/claims/0',data['contract_feedback']['error'])
            return result
        client._call=call;root=self.root/'case-repair'
        bundle=evaluate(self.packet,root,client)
        self.assertEqual('81.0',bundle['reports'][0]['score']);self.assertEqual(17,client.sent)
        self.assertEqual(2,len(list((root/'dimensions/E3').rglob('model.receipt.json'))))
        self.assertEqual(1,len(list((root/'dimensions/M3').rglob('model.receipt.json'))))

    def test_unknown_receipt_never_restarts_or_generates_score(self):
        path=self.root/'run';client=ScriptedClient(self.actions(),interrupt=1)
        with self.assertRaises(KeyboardInterrupt):evaluate(self.packet,path,client)
        target=path/'turns/0/model.receipt.json';value=json.loads(target.read_text());value['status']='unknown';atomic(target,value)
        retry=ScriptedClient(self.actions())
        with self.assertRaises(Paused) as ex:evaluate(self.packet,path,retry)
        self.assertEqual('unknown',ex.exception.state);self.assertEqual(0,retry.sent)
        self.assertFalse((path/'bundle.json').exists())

    def test_invalid_json_and_schema_enter_bounded_correction(self):
        actions=['{broken',dict(action=dict(operation='save',arguments={})),dict(action=dict(operation='invent',arguments={}))]
        path=self.root/'bad';client=ScriptedClient(actions)
        with self.assertRaises(Paused) as ex:evaluate(self.packet,path,client)
        self.assertEqual('model_contract_invalid',ex.exception.reason_code)
        self.assertEqual(3,client.sent)
        self.assertEqual(3,len(list(path.rglob('contract-error.json'))))

    def test_known_truncation_accepts_only_complete_object_and_whitespace(self):
        value=record({'action': {'operation': 'memory'}})
        value['response'].update(status='incomplete',incomplete_details={'reason':'max_output_tokens'})
        content=value['response']['output'][0]['content'][0]
        content['text']+=' \n\t'*500
        self.assertEqual({'action': {'operation':'memory'}},decode_record(value))
        for body in ('{"action":', '{"action":{}} trailing', '{"action":{}} {}', '[]'):
            content['text']=body
            with self.subTest(body=body),self.assertRaises(ValueError):decode_record(value)
        value['response']['status']='failed'
        with self.assertRaises(Paused) as ex:decode_record(value)
        self.assertEqual('model_request_rejected',ex.exception.reason_code)

    def test_scoring_ids_are_fixed_and_correction_receives_previous_judgment(self):
        self.read();self.state.save(self.save())
        ready=dict(source_ids=['person-'+c for c in RUBRIC['items']],coverage_note='仅测试证据合同')
        _,_,_,schema,_=score_contract(self.state,ready)
        client=ScriptedClient(self.actions());original=client._call;draft=[]
        def call(payload,target):
            data=json.loads(payload['input'])
            if payload['text']['format']['name']!='zhiyongli_session_score':return original(payload,target)
            good=original(payload,target)
            if not draft:
                bad=decode_record(good)
                bad['review']['period_reviews'][0]['context']['refs']=['person-invented']
                with self.assertRaises(ValueError):check_schema(bad,schema)
                draft.append(copy.deepcopy(bad))
                atomic(target,record(bad))
                return record(bad)
            if data['dimension']=='P1':
                self.assertEqual(draft[0],data['previous_response'])
                self.assertIn('context/refs/0',data['contract_feedback']['error'])
                self.assertIn('允许值=',data['contract_feedback']['error'])
            return good
        client._call=call
        result=evaluate(self.packet,self.root/'corrected',client)
        self.assertEqual('81.0',result['reports'][0]['score'])
        self.assertEqual(17,client.sent)

    def test_directory_alone_cannot_classify_and_unread_span_cannot_be_owned(self):
        with self.assertRaisesRegex(ValueError,'实际读取'):self.state.save(self.save())
        self.state.reads[0]=self.store.read_event(self.key,0,5)
        with self.assertRaisesRegex(ValueError,'实际交付'):self.state.save(self.save())
        value=self.save();value['materials'][0].update(classification='system',spans=[],task_id=None);value['sources']=[]
        self.state.reads.clear()
        with self.assertRaisesRegex(ValueError,'实际读取'):self.state.save(value)

    def test_invalid_save_is_atomic_and_evidence_id_cannot_change(self):
        self.read();value=self.save();value['sources'][0]['location']['end']=9999
        with self.assertRaises(ValueError):self.state.save(value)
        self.assertEqual(({}, {}, {}),(self.state.materials,self.state.tasks,self.state.sources))
        self.state.save(self.save());before=copy.deepcopy(self.state.sources)
        value=self.save();value['sources'][0]['location']['end']-=1
        with self.assertRaisesRegex(ValueError,'不得改指'):self.state.save(value)
        self.assertEqual(before,self.state.sources)

    def test_repeated_ids_and_context_as_person_are_rejected(self):
        self.read()
        for field in ('materials','tasks','sources'):
            value=self.save();value[field].append(copy.deepcopy(value[field][0]))
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'重复'):self.state.save(value)
        value=self.save();value['sources'][0]['origin']='context'
        with self.assertRaisesRegex(ValueError,'前缀'):self.state.save(value)

    def test_ready_needs_coverage_and_actual_in_scope_person_sources(self):
        with self.assertRaisesRegex(ValueError,'全部处置'):self.state.native(['person-P1'])
        self.read();self.state.save(self.save())
        for ids in ([],['absent'],['person-P1','person-P1']):
            with self.assertRaises(ValueError):self.state.native(ids)
        self.packet['events'][0]['timestamp_unknown']=True
        with self.assertRaisesRegex(ValueError,'本期有效'):self.state.native(['person-P1'])

    def test_selected_material_does_not_retype_quote_or_confuse_same_text(self):
        self.read();update=self.save();update['sources']=[];self.state.save(update)
        chosen=dict(event_key=self.key,span_index=0,origin='direct_expression',origin_reason='模型明确选择已确认本人原句')
        result=self.state.cite_materials([chosen]);sid=result['sources'][0]['id']
        self.assertEqual(self.text,self.state.location(self.state.sources[sid]['location'])[1])
        with self.assertRaisesRegex(ValueError,'已确认'):self.state.cite_materials([dict(chosen,span_index=99)])
        self.assertEqual(1,len(self.state.sources))

    def test_batch_generates_source_ids_and_nested_task_without_duplicate_identifier_fields(self):
        self.read();saved=self.save();m=saved['materials'][0]
        local=lambda loc:{k:v for k,v in loc.items() if k!='event_key'}
        record=dict(event_key=m['event_key'],classification='human',human_spans=[local(m['spans'][0])],
            task=saved['tasks'][0],evidence=[dict(location=local(saved['sources'][0]['location']),
                origin='direct_expression',origin_reason='选中本人原句')],reason=m['reason'])
        result=self.state.record_batch([record],'完整合成归属和目标')
        self.assertEqual((1,1,1),(result['saved_materials'],result['tasks'],result['sources']))
        self.assertTrue(result['source_ids'][0].startswith('person-'))
        source=self.state.sources[result['source_ids'][0]]
        self.assertEqual('合成合同夹具P1',self.state.location(source['location'])[1])
        encoded=self.state.translate(dict(event_key=self.key,text=self.key))
        self.assertEqual('e1',encoded['event_key']);self.assertEqual(self.key,encoded['text'])
        self.assertEqual(self.key,self.state.translate(encoded,decode=True)['event_key'])

    def test_final_source_author_is_rejudged_without_overwriting_old_evidence(self):
        self.read();update=self.save();update['sources'][0].update(id='supplied-old',origin='quoted_or_forwarded')
        self.state.save(update);before=copy.deepcopy(self.state.sources['supplied-old'])
        ready=self.state.finalize([dict(id='supplied-old',origin='direct_expression',origin_reason='模型终定本人直接提出要求')],'完整覆盖')
        native,evidence=self.state.native(ready['source_ids'])
        self.assertEqual('direct_expression',evidence[0]['origin'])
        self.assertIn(evidence[0]['event'],native['person_events'])
        self.assertEqual(before,self.state.sources['supplied-old'])
        self.assertNotEqual('supplied-old',ready['source_ids'][0])

    def test_automatic_reply_delivery_can_be_cited_as_context_only(self):
        value=copy.deepcopy(self.packet);first=value['events'][0]
        value['events'].append(dict(first,event='reply',actor_class='context',protocol_role='assistant',text='测试仍失败，未完成'))
        store=SessionEvidence.build(value,self.root/'with-reply');self.addCleanup(store.close)
        state=EvidenceState(value,store,self.root/'reply-run');reading=state.initial_reading(10000)
        reply=next(piece for piece in reading['items'] if piece['text']=='测试仍失败，未完成')
        selection=dict(location=dict(read_id=10000,event_key=reply['event_key'],start=0,end=reply['end']),
            origin='context',origin_reason='AI反馈不能当作用户已完成')
        # 本人归属尚未完成时也可先保存实际已读的反证。
        state.summary='原文支持的执行反馈'
        state.cite_context([selection])
        self.assertTrue(next(iter(state.sources)).startswith('context-'))
        self.assertFalse(state.materials)

    def test_scoring_retains_delivered_feedback_when_model_only_selected_person_sources(self):
        value=copy.deepcopy(self.packet);first=value['events'][0]
        value['events'].append(dict(first,event='reply',actor_class='context',protocol_role='assistant',text='合成：执行仍然失败，成果未确认。'))
        client=ScriptedClient(self.actions());root=self.root/'feedback-to-scoring';original=client._call;delivered=[]
        def call(payload,target):
            data=json.loads(payload['input'])
            if payload['text']['format']['name']=='zhiyongli_session_claims':
                self.assertTrue(data['context_evidence'])
                self.assertEqual('context',data['context_evidence'][0]['origin'])
            if payload['text']['format']['name']=='zhiyongli_session_score':
                feedback=[e for e in data['evidence'] if e['id'].startswith('context-feedback-')]
                self.assertTrue(feedback);self.assertEqual('context',feedback[0]['origin'])
                self.assertEqual('合成：执行仍然失败，成果未确认。',feedback[0]['quote'])
                positions={r['source_id']:r for r in data['evidence_context']}
                self.assertEqual({e['id'] for e in data['evidence']},set(positions))
                self.assertEqual('assistant',positions[feedback[0]['id']]['protocol_role'])
                self.assertEqual(positions['person-P1']['request_key'],positions[feedback[0]['id']]['request_key'])
                delivered.append(data['dimension'])
            return original(payload,target)
        client._call=call;evaluate(value,root,client)
        self.assertEqual(12,len(delivered))
        review=json.loads((root/'review.json').read_text())['value']
        self.assertTrue(any(e['id'].startswith('context-feedback-') for e in review['evidence']))
        self.assertTrue(all(not ref.startswith('context-') for c in review['period_reviews'][0]['cells'] for ref in c['action']['refs']))
        repeated=ScriptedClient([]);evaluate(value,root,repeated);self.assertEqual(0,repeated.sent)

        before={p:p.read_bytes() for p in root.rglob('*.json')}
        child=self.root/'feedback-child'
        resumed=ScriptedClient([]);resumed.session_seed_from=root
        resumed.session_seed_allow_terminal=True;resumed.session_resume_scoring=True
        evaluate(value,child,resumed);self.assertEqual(0,resumed.sent)
        grandchild=ScriptedClient([]);grandchild.session_seed_from=child
        grandchild.session_seed_allow_terminal=True;grandchild.session_resume_scoring=True
        evaluate(value,self.root/'feedback-grandchild',grandchild);self.assertEqual(0,grandchild.sent)
        self.assertEqual(before,{p:p.read_bytes() for p in before})

        feedback_path=root/'scoring-feedback.json';manifest=json.loads(feedback_path.read_text())
        manifest['sources'][0]['location']['end']-=1;atomic(feedback_path,manifest)
        invalid=ScriptedClient([]);invalid.session_seed_from=root
        invalid.session_seed_allow_terminal=True;invalid.session_resume_scoring=True
        with self.assertRaisesRegex(ValueError,'评分反馈与原文'):
            evaluate(value,self.root/'feedback-tampered',invalid)
        self.assertEqual(0,invalid.sent)

    def test_same_phrase_at_multiple_positions_uses_actual_coordinates_including_spaces(self):
        value=copy.deepcopy(self.packet);value['events'][0]['text']='系统问题：已登录。本人：已 登录，继续。已登录'
        store=SessionEvidence.build(value,self.root/'repeated');self.addCleanup(store.close)
        state=EvidenceState(value,store,self.root/'repeat-run');key=state.candidates[0]
        piece=store.read_event(key,0,6000);state.reads[0]=piece
        start=value['events'][0]['text'].index('已 登录')
        event,quote=state.location(dict(read_id=0,event_key=key,start=start,end=start+4))
        self.assertEqual('已 登录',quote)
        self.assertEqual(value['events'][0]['event'],event['event'])

    def test_empty_source_and_source_failure_are_distinct_and_make_no_model_calls(self):
        client=ScriptedClient([]);value=dict(self.packet,events=[])
        with self.assertRaises(Paused) as ex:evaluate(value,self.root/'empty',client)
        self.assertEqual('no_activity',ex.exception.state)
        value['issues']=[dict(code='source_failed')]
        with self.assertRaises(Paused) as ex:evaluate(value,self.root/'source-failed',client)
        self.assertEqual('source_incomplete',ex.exception.reason_code)
        self.assertEqual('failed',ex.exception.state);self.assertEqual(0,client.sent)

    def test_unclear_attribution_generates_twelve_final_unscored_items_without_employee_review(self):
        from runtime import VERSION,REVIEW_POLICY
        source=dict(id='context-unclear',location=self.loc(read_id=10000),origin='uncertain',origin_reason='合成主体无法确认')
        update=dict(materials=[dict(event_key=self.key,classification='unresolved',spans=[],task_id=None,
            reason='合成共享输入没有主体标记')],tasks=[],sources=[source],summary='合成材料主体无法确认，不推定低表现。')
        action=dict(action=dict(operation='finish',arguments=dict(update=update,
            final=dict(source_ids=[source['id']],coverage_note='全部原文已交付，明确本次无分'))))
        class Unscored(ScriptedClient):
            def _call(client,payload,receipt):
                if payload['text']['format']['name']!='zhiyongli_session_score':
                    return super()._call(payload,receipt)
                client.sent+=1;data=json.loads(payload['input']);cells=[]
                for code in data['item_codes']:
                    cell=dict(item=code,state='attribution_unclear',reason='来源没有主体标记，本项不计分。',
                        opportunity=dict(description='合成候选表达',refs=['context-unclear']),
                        missing=dict(needed='输入主体身份',why_changes_judgment='无法认领个人表现',
                            checked_events=[data['evidence'][0]['event']]))
                    if code.startswith('M'):cell['challenge_handling']=dict(status='not_demonstrated',reason='未确认本人行为',refs=[])
                    cells.append(cell)
                review=dict(version=VERSION,review_policy=REVIEW_POLICY,key=data['key'],needs_events=[],
                    period_reviews=[dict(period_ids=[data['periods'][0]['period_id']],cells=cells,
                    context=dict(scope='scored_week_scope',effective_request='候选表达主体不明',necessary_scope='合成周范围',
                        observed_execution='没有可确认的本人记录',boundary='uncertain',boundary_reason='主体缺失',refs=['context-unclear']))])
                value=dict(review=review,presentation=dict(case=None,items=[dict(item_code=code,
                    basis='记录没有输入主体标记，本项不计分。',higher_band='主体未确认，本次没有可评分的个人表现依据。') for code in data['item_codes']]))
                for cell in cells:cell['behavior_ids']=[]
                return record(value,receipt)
        client=Unscored([action]);bundle=evaluate(self.packet,self.root/'unscored',client)
        self.assertEqual(14,client.sent);self.assertIsNone(bundle['reports'][0]['score'])
        self.assertEqual(12,len(bundle['reports'][0]['items']))
        self.assertTrue(all(row['score'] is None for row in bundle['reports'][0]['items']))

    def test_frozen_input_or_mode_cannot_drift_on_resume(self):
        path=self.root/'run';client=ScriptedClient(self.actions(),interrupt=0)
        with self.assertRaises(KeyboardInterrupt):evaluate(self.packet,path,client)
        changed=copy.deepcopy(self.packet);changed['events'][0]['text']+='改写'
        with self.assertRaises(ValueError):evaluate(changed,path,client)
        with self.assertRaises(ValueError):evaluate_week(self.packet,path,SimpleNamespace())

    def test_action_schema_rejects_boolean_offsets_and_extra_fields(self):
        value=self.actions()[0];value['action']['arguments']['start']=True
        with self.assertRaises(ValueError):check_schema(value,action_schema())

    def test_program_delivery_can_save_and_finish_without_directory_model_calls(self):
        action=dict(action=dict(operation='finish',arguments=dict(update=self.save(10000),
            final=dict(source_ids=['person-'+c for c in RUBRIC['items']],coverage_note='本次全部合成原文已交付'))))
        client=ScriptedClient([action]);result=evaluate(self.packet,self.root/'batch',client)
        self.assertEqual(14,client.sent)
        self.assertEqual('81.0',result['reports'][0]['score'])

    def test_daily_facts_are_consumed_by_week_without_repeating_attribution(self):
        daily=dict(self.packet,preparation_only=True,preparation_observed_until='2026-09-08T21:00:00+08:00')
        first=ScriptedClient(self.actions());daily_root=self.root/'daily'
        result=evaluate(daily,daily_root,first)
        self.assertEqual('facts_prepared',result['state']);self.assertEqual(3,first.sent)
        self.assertFalse((daily_root/'bundle.json').exists())
        before={p:p.read_bytes() for p in daily_root.rglob('*.receipt.json')}
        ready=self.actions()[-1]
        week=ScriptedClient([ready]);week.session_fact_from=daily_root
        bundle=evaluate(self.packet,self.root/'week',week)
        self.assertEqual('81.0',bundle['reports'][0]['score']);self.assertEqual(14,week.sent)
        self.assertTrue((self.root/'week/inherited-facts.json').exists())
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        repeated=ScriptedClient([ready]);repeated.session_fact_from=daily_root
        self.assertEqual(bundle,evaluate(self.packet,self.root/'week',repeated));self.assertEqual(0,repeated.sent)

    def test_compact_inputs_and_neutral_catalog_rejudge_old_origin(self):
        self.read()
        value=self.save();source=value['sources'][0]
        source.update(id='supplied-old',origin='quoted_or_forwarded',origin_reason='旧标签仅为历史判断')
        self.state.save(value)
        catalog=self.state.source_catalog()['items']
        selected=next(row for row in catalog if row['location']==source['location'])
        self.assertNotIn('origin',selected);self.assertNotIn('id',selected)
        ready=self.state.submit([dict(source_key=selected['source_key'],origin='direct_expression',
            origin_reason='根据实际原句重新确认是本人要求')],coverage_note='旧标签不当真值')
        self.assertEqual('direct_expression',self.state.sources[ready['source_ids'][0]]['origin'])
        self.assertEqual('quoted_or_forwarded',self.state.sources['supplied-old']['origin'])
        self.assertEqual(source['location'],self.state.sources[ready['source_ids'][0]]['location'])

    def test_compact_recording_does_not_duplicate_task_or_quote(self):
        self.read();value=self.save();material=value['materials'][0]
        spans=[dict({k:loc[k] for k in ('read_id','start','end')},origin='direct_expression',origin_reason='本人原句')
            for loc in material['spans']]
        result=self.state.record_inputs([dict(material,spans=spans)],value['tasks'],value['summary'])
        self.assertEqual(0,result['pending']);self.assertEqual(1,len(self.state.sources))
        self.assertEqual(self.text,self.state.source_catalog()['items'][0]['quote'])

    def test_submit_requires_every_catalog_page_to_be_delivered(self):
        self.read();self.state.save(self.save())
        first=self.state.source_catalog(0,1)['items'][0]
        selection=[dict(source_key=first['source_key'],origin='direct_expression',origin_reason='仅选择首个有效依据')]
        with self.assertRaisesRegex(ValueError,'只选第一页'):
            self.state.submit(selection,'未完整交付目录',require_complete=True)
        self.state.source_catalog(1,20)
        self.assertEqual(1,len(self.state.submit(selection,'完整核目录后选择',require_complete=True)['source_ids']))

    def test_unchanged_daily_refresh_finishes_without_any_model_call(self):
        first=dict(self.packet,preparation_only=True)
        source=self.root/'day-original';evaluate(first,source,ScriptedClient(self.actions()))
        refreshed=dict(first,source_snapshot={'sha256':'b'*64})
        client=ScriptedClient([]);client.session_fact_from=source
        result=evaluate(refreshed,self.root/'day-refresh',client)
        self.assertEqual('facts_prepared',result['state']);self.assertEqual(0,client.sent)
        week=ScriptedClient([self.actions()[-1]]);week.session_fact_from=self.root/'day-refresh'
        self.assertEqual('81.0',evaluate(self.packet,self.root/'week-refresh',week)['reports'][0]['score'])

    def test_fragment_uses_same_review_and_never_builds_formal_week_report(self):
        packet=dict(self.packet,until='2026-09-10T00:00:00+08:00')
        client=ScriptedClient(self.actions());original=client._call
        def call(payload,target):
            if payload['text']['format']['name']!='zhiyongli_session_score':return original(payload,target)
            # 合成响应仅验证月内范围和正式报告隔离，不替代真实月评。
            data=json.loads(payload['input']);result=original(payload,target);value=decode_record(result)
            period=value['review']['period_reviews'][0]
            period['period_ids']=[data['periods'][0]['period_id']]
            period['context']['scope']='scored_fragment_scope'
            value['presentation']=None
            result['response']['output'][0]['content'][0]['text']=json.dumps(value,ensure_ascii=False)
            atomic(target,result);return result
        client._call=call;root=self.root/'fragment'
        result=evaluate(packet,root,client)
        self.assertEqual('fragment_reviewed',result['state'])
        self.assertFalse((root/'native-report.json').exists())
        self.assertEqual('person_week_fragment',json.loads((root/'unit.json').read_text())['evaluation_scope'])

    def test_daily_source_revision_invalidates_semantics_and_added_candidate_remains_pending(self):
        from session_facts import inherit
        daily=dict(self.packet,preparation_only=True)
        source=self.root/'daily-source';evaluate(daily,source,ScriptedClient(self.actions()))
        for changed in (False,True):
            current=copy.deepcopy(self.packet)
            current['events'].insert(0,dict(current['events'][0],event='new-candidate'))
            if changed:current['events'][1]['text']+='真实来源新修订'
            store=SessionEvidence.build(current,self.root/('current-'+str(changed)));self.addCleanup(store.close)
            state=EvidenceState(current,store,self.root/('transfer-'+str(changed)))
            result=inherit(state,source)
            self.assertEqual(not changed,result['reused'])
            self.assertEqual(2 if changed else 1,state.pending()['remaining'])
            if not changed:
                self.assertTrue(state.sources)
                self.assertNotEqual(self.key,next(iter(state.materials)))
            else:self.assertFalse(state.sources)

    def test_empty_day_completes_without_model_and_source_issue_does_not(self):
        empty=dict(self.packet,events=[],preparation_only=True)
        client=ScriptedClient([]);result=evaluate(empty,self.root/'empty-day',client)
        self.assertEqual('facts_prepared',result['state']);self.assertEqual(0,client.sent)
        empty['issues']=[dict(code='source_failed')]
        with self.assertRaises(Paused):evaluate(empty,self.root/'failed-day',client)

    def test_long_unwrapped_input_delivers_both_ends_without_claiming_middle(self):
        value=copy.deepcopy(self.packet)
        value['events']=[dict(value['events'][0],event='long-'+str(i),text='长资料😀'*5000) for i in range(100)]
        store=SessionEvidence.build(value,self.root/'large');self.addCleanup(store.close)
        state=EvidenceState(value,store,self.root/'large-run');reading=state.initial_reading(10000)
        self.assertLess(len(json.dumps(reading,ensure_ascii=False).encode()),65000)
        self.assertTrue(reading['not_delivered_sections'])
        self.assertTrue(any(piece['next_start'] is not None for piece in reading['items']))
        self.assertTrue(any(piece['end']==len(value['events'][0]['text']) for piece in reading['items']))
        self.assertEqual(100,state.pending()['remaining'])

    def test_explicit_old_prefix_seeds_only_verified_reading_and_rejects_changed_scope(self):
        source=self.root/'old';atomic(source/'packet.json',self.packet)
        atomic(source/'session-input.json',dict(mode='session-review-20261118.1',packet_sha256=digest(self.packet)))
        args=dict(event_key=self.key,start=0,max_characters=6000)
        result=self.store.tool_call('read_event',args,source/'turns/0/read.json')
        self.state.reads[0]=result
        for turn,action,outcome in [(0,dict(operation='read_event',arguments=args),result),
                (1,dict(operation='save',arguments=self.save()),self.state.save(self.save()))]:
            target=source/'turns'/str(turn);record(dict(action=action),target/'model.receipt.json')
            atomic(target/'checked.json',dict(action_sha256=digest(action),result_sha256=digest(outcome)))
        state=EvidenceState(self.packet,self.store,self.root/'seed');seed_state(state,source,reading_only=True)
        self.assertFalse(state.materials);self.assertFalse(state.sources)
        self.assertEqual(1,len(state.reads))
        self.assertTrue(state.prior_context()==[])
        self.assertEqual(self.text,state.location(self.loc())[1])
        changed=copy.deepcopy(self.packet);changed['since']='2026-09-08T00:00:00+08:00'
        with self.assertRaisesRegex(ValueError,'评价范围'):
            seed_state(EvidenceState(changed,self.store,self.root/'bad-seed'),source)
        value=self.actions()[1];value['action']['arguments']['extra']='未约定字段'
        with self.assertRaises(ValueError):check_schema(value,action_schema())


if __name__=='__main__':unittest.main()
