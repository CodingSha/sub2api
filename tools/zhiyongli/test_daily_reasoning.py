"""检验来源关联、锁定分数后的解释合同与引用集合恢复；不代替真实模型质量验收。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import jsonschema

from daily_pilot import call
from daily_reasoning import allocate, assess, allocated_schema, check_fact_coverage, check_quality, judge_call, judge_schema, presentation_contract, read_asset_evidence
from daily_behaviors import source_fragments
from runtime import atomic


def behavior_wire(data,rows):
    return {identity:dict(primary_item=row['primary_item'],origin='personal',fragment_id=next(key for key,fragment
        in data['fragments'].items() if fragment['record_id']==action['record_id'] and row['quote'] in fragment['quote']),
        **({'asset_query':''} if row['primary_item']=='E3' else {})) for (identity,action),row in zip(data['actions'].items(),rows)}


def fact(record_id, action):
    return dict(record_id=record_id,action=action,explicit_reason='',new_fact='',new_fact_record='',
                claimed_completed_by_user='',result_decision_quote='',selected_asset=[])


class DailyReasoningTests(unittest.TestCase):
    def test_each_fact_action_has_one_route_with_evidence_from_the_same_record(self):
        tasks=[dict(id='task-1',records=[dict(id='u1',role='user',text='启动项目。')]),
            dict(id='task-2',records=[dict(id='u2',role='user',text='按失败步骤回归。')])]
        refs=[next(iter(source_fragments({t['records'][0]['id']:t['records'][0]}))) for t in tasks]
        row=lambda ref:dict(fragment_id=ref,primary_item='P1',origin='personal')
        fragments=source_fragments({record['id']:record for task in tasks for record in task['records']})
        actions={'action-1':fact('u1','启动项目。'),'action-2':fact('u2','按失败步骤回归。')}
        schema=allocated_schema(actions,fragments)
        value=dict(routes={'action-1':row(refs[0]),'action-2':None})
        jsonschema.validate(value,schema)
        for bad in (dict(routes={'action-1':row(refs[0])}),
                    dict(routes={**value['routes'],'invented':None}),
                    dict(routes={**value['routes'],'action-1':[row(refs[0]),row(refs[0])]}),
                    dict(routes={**value['routes'],'action-1':row(refs[1])})):
            with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(bad,schema)

    def test_result_disposition_can_only_use_its_located_decision_fragment(self):
        condition='先核对总数，相符才提交。'
        decision='旧报告漏了撤销单，不采用旧报告，请按新口径重算。'
        rows=[dict(id='condition',role='user',text=condition),
              dict(id='result',role='assistant',text='已生成旧口径的报告。'),
              dict(id='decision',role='user',text=decision)]
        actions={'condition':fact('condition','设置了提交前的检查条件'),
                 'decision':dict(fact('decision','明确拒绝并要求重算'),result_decision_quote=decision),
                 'invented':dict(fact('condition','声称已验收'),result_decision_quote='总数已验收。')}
        def send(client, root, name, data, schema, instructions):
            self.assertEqual(set(data['decision_fragments']), {'decision'})
            location=data['decision_fragments']['decision']
            check=next(key for key,fragment in data['fragments'].items() if fragment['record_id']=='condition')
            check_route=dict(primary_item='J2',origin='personal',fragment_id=check)
            decision_route=dict(primary_item='J3',origin='personal',fragment_id=location)
            routes=dict(condition=check_route,decision=decision_route,invented=None)
            jsonschema.validate(dict(routes=routes),schema)
            for invalid in (dict(routes,condition=dict(check_route,primary_item='J3')),
                            dict(routes,invented=dict(check_route,primary_item='J3')),
                            dict(routes,decision=dict(decision_route,origin='supplied'))):
                with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(routes=invalid),schema)
            return dict(routes=routes)
        with tempfile.TemporaryDirectory() as path:
            result=allocate(SimpleNamespace(),Path(path),dict(actions=actions),[dict(records=rows)],send)
        self.assertEqual([(row['primary_item'],row['record_id']) for row in result['behaviors']],
                         [('J2','condition'),('J3','decision')])
        self.assertEqual(result['behaviors'][1]['quote'],decision)

    def test_context_references_must_be_delivered_and_cannot_use_tool_directory_ids(self):
        cell=dict(user_action='指定检查。',context_support='AI报告尚未检查。',band=70,
            basis='你安排了检查。',next_band='没有进一步针对性。',refs=['b'],context_refs=['a'],negative=None)
        schema=judge_schema({'J2':dict(eligible_refs=['b'])},['u','a'])
        jsonschema.validate(dict(needs_records=[],items={'J2':cell}),schema)
        for refs in (['unread-tool'],['made-up']):
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(dict(needs_records=[],items={'J2':dict(cell,context_refs=refs)}),schema)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(dict(needs_records=[],items={'J2':cell}),judge_schema({'J2':dict(eligible_refs=['b'])},[]))

    def test_task_tool_search_then_read_reaches_real_body_with_two_bounded_rounds(self):
        rows=[dict(id='u',role='user',text='参考 old_script.py 编写新脚本。',excerpt=False),
            dict(id='a',role='assistant',text='已复用旧脚本登录方法。',excerpt=False)]
        task=dict(id='task-1',records=rows,workflow_index=[])
        original={r['id']:r for r in rows}
        original['tool']=dict(id='tool',role='tool',task_id='task-1',text='old_script.py: def reuse_login(): return session')
        names=[]
        def send(client,root,name,data,schema,instructions):
            names.append(name)
            if name=='facts':value=dict(task_summaries={'task-1':dict(title='脚本复用',goal='编写脚本',outcome='',workflow=[])},actions=[fact('u','复用旧脚本')],no_action_refs=[])
            elif name=='behaviors':value=dict(routes=behavior_wire(data,[dict(quote='参考 old_script.py',primary_item='E3')]))
            elif name=='judge-E-0':value=dict(needs_records=[],search_queries=['old_script.py'],items=None)
            elif name=='judge-E-1':
                self.assertEqual(data['tool_search_results'][0]['directory']['records'][0]['record_id'],'tool')
                self.assertEqual(data['read_records'],[])
                value=dict(needs_records=[dict(record_id='tool',start=0,chars=100)],search_queries=[],items=None)
            elif name=='judge-E-2':
                self.assertEqual(data['read_records'][0]['text'],original['tool']['text'])
                value=dict(needs_records=[],search_queries=[],items={'E3':dict(user_action='选择旧脚本。',context_support='旧脚本提供登录方法。',band=80,basis='你复用旧脚本的登录方法，省去重复编写。',next_band='本次尚不足以核对更完整的信息保留作用。',refs=data['items']['E3']['eligible_refs'],context_refs=['tool'],negative=None)})
            elif name=='quality-0':value=dict(issues=[])
            else:self.fail(name)
            jsonschema.validate(value,schema);return value
        with tempfile.TemporaryDirectory() as path:
            value,read=assess(SimpleNamespace(),Path(path),[task],original,send)
        self.assertEqual(value['items']['E3']['band'],'good')
        self.assertEqual(len(read),1)
        self.assertEqual(names,['facts','behaviors','judge-E-0','judge-E-1','judge-E-2','quality-0'])

    def test_asset_read_supplies_bounded_original_excerpt_from_the_same_task(self):
        query='reference.py'
        body='前'*9000+'已读取 reference.py 中的登录适配。'+'后'*800
        originals={
            'user':dict(id='user',role='user',task_id='own',text='可参考 reference.py。'),
            'target':dict(id='target',role='tool',task_id='own',text=body),
            'other':dict(id='other',role='tool',task_id='other',text='Type: function_call_output\nreference.py'),
        }
        selection=dict(primary_item='E3',record_id='user',asset_query=query)
        records=[]
        read_asset_evidence(dict(behaviors=[selection,selection]),originals,records)
        self.assertEqual(len(records),1)
        record=records[0]
        self.assertEqual(record['id'],'target')
        self.assertTrue(record['excerpt'])
        self.assertIn(query,record['text'])
        self.assertEqual(record['text'],body[record['start']:record['start']+700])

    def test_conditional_request_is_not_completed_acceptance_and_repair_can_change_score(self):
        task=dict(records=[dict(id='u',role='user',text='如果全部修好，就改为通过。')])
        behavior={'b':dict(primary_item='J3',source_ref='u',claim='以修复完成为改状态的条件')}
        old=dict(band='good',basis='你已经接受所有修复。',next_band='尚未呈现更充分的验收。')
        fixed=dict(band=None,basis='这只是改状态前的核验条件，未观察到单独的采用行为。',next_band='',refs=[])
        issue=dict(item='J3',kind='unsupported_action',problem='条件性要求被写成已完成接受。',
            claim_quote=old['basis'],source_ref='u',source_quote='如果全部修好，就改为通过。',behavior_ids=['b'])
        stages=[]
        def send(client,root,name,data,schema,instructions):
            stages.append(name)
            if name=='quality-1':
                self.assertNotIn('judgments',data)
                self.assertIn('J3',data['next_criteria'])
                self.assertEqual(data['presentation']['items']['J3']['basis'],fixed['basis'])
            return dict(issues=[issue] if name=='quality-0' else [])
        with tempfile.TemporaryDirectory() as path:
            judgments,prose=check_quality(None,Path(path),[task],behavior,{'J3':old},
                dict(items={'J3':{k:old[k] for k in ('basis','next_band')}}),[],send,lambda issues:{'J3':fixed})
        self.assertIsNone(judgments['J3']['band'])
        self.assertEqual(judgments['J3']['refs'],[])
        self.assertEqual(prose['items']['J3']['basis'],fixed['basis'])
        self.assertEqual(stages,['quality-0','quality-1'])

    def test_cross_item_duplicate_stops_export_instead_of_rewriting_prose(self):
        original='修复失败项。我已验证表单正常，是提交时被拦截吗？'
        task=dict(records=[dict(id='u',role='user',text=original)])
        behaviors={
            'method':dict(primary_item='M1',source_ref='u',claim='安排修复并追问拦截位置'),
            'judgment':dict(primary_item='J1',source_ref='u',claim='用表单核验结果追查拦截位置'),
        }
        basis='你用表单核验结果把排障范围缩到提交阶段。'
        presentation=dict(items={'M1':dict(basis=basis,next_band='')})
        issue=dict(item='M1',kind='duplicate_behavior',problem='两项给同一排障追问计分。',claim_quote=basis,
            source_ref='u',source_quote='我已验证表单正常，是提交时被拦截吗？',behavior_ids=['method','judgment'])
        for actual, ids in ((True, ['method', 'judgment']), (False, ['method', 'judgment']), (True, ['method'])):
            with self.subTest(actual=actual, ids=ids),tempfile.TemporaryDirectory() as path:
                root=Path(path)
                scored={'M1':['method'],'J1':['judgment'] if actual else []}
                def send(client,root,name,data,schema,instructions):
                    self.assertEqual(name,'quality-0')
                    self.assertEqual(data['scored_behaviors'],scored)
                    result=dict(issues=[dict(issue, behavior_ids=ids)])
                    jsonschema.validate(result,schema)
                    return result
                def audit():
                    return check_quality(None,root,[task],behaviors,{code:dict(refs=refs) for code,refs in scored.items()},
                        presentation,[],send,lambda issues:self.fail('归项失败不能只改评语'))
                if actual and len(ids)==2:
                    with self.assertRaisesRegex(ValueError,'归属检查未通过'):
                        audit()
                    self.assertEqual(json.loads((root/'quality.json').read_text())['quality_status'],'failed')
                else:
                    judgments, prose = audit()
                    self.assertEqual(judgments['M1']['refs'], ['method'])
                    self.assertEqual(prose, presentation)
                    quality = json.loads((root/'quality.json').read_text())
                    self.assertEqual(quality['quality_status'], 'model_checked')
                    self.assertEqual(quality['dismissed_findings'][0]['reason'], 'invalid_duplicate_finding')

    def test_borrowed_behavior_requires_another_items_actual_source_and_repairs_only_the_borrower(self):
        task=dict(records=[dict(id='arrange',role='user',text='你实现，我负责验收。'),
            dict(id='check',role='user',text='复测原失败的提交步骤。')])
        behaviors={'method':dict(primary_item='M1',source_ref='arrange',claim='分配实现与验收职责'),
            'check':dict(primary_item='J2',source_ref='check',claim='指定回归范围')}
        method=dict(band='good',basis='你安排了回归原提交步骤。',next_band='分工优势未证实。',refs=['method'])
        check=dict(band='good',basis='你指定检查原失败步骤。',next_band='暂未展示具体采用边界。',refs=['check'])
        fixed=dict(method,band='reasonable',basis='你将实现交给AI，自己负责验收。')
        issue=dict(item='M1',kind='borrowed_behavior',problem='只引用了J2的回归安排。',
            claim_quote=method['basis'],source_ref='check',source_quote='复测原失败的提交步骤。',behavior_ids=['check'])
        for valid in (True,False):
            with self.subTest(valid=valid),tempfile.TemporaryDirectory() as path:
                root=Path(path)
                def send(client,root,name,data,schema,instructions):
                    finding=issue if valid else dict(issue,source_ref='arrange',source_quote='你实现，我负责验收。')
                    value=dict(issues=[finding] if name=='quality-0' else [])
                    jsonschema.validate(value,schema)
                    return value
                def repair(issues):
                    self.assertTrue(valid)
                    self.assertEqual(issues,[issue])
                    return {'M1':fixed}
                def audit():
                    return check_quality(None,root,[task],behaviors,{'M1':method,'J2':check},
                        dict(items={'M1':dict(basis=method['basis'])}),[],send,repair)
                if valid:
                    judgments,_=audit()
                    self.assertEqual(judgments['M1']['band'],'reasonable')
                    self.assertEqual(judgments['J2'],check)
                else:
                    judgments, _ = audit()
                    self.assertEqual(judgments, {'M1': method, 'J2': check})
                    quality = json.loads((root/'quality.json').read_text())
                    self.assertEqual(quality['quality_status'], 'model_checked')
                    self.assertEqual(quality['dismissed_findings'][0]['reason'], 'invalid_borrowed_finding')

    def test_disputed_next_band_explanation_does_not_hide_real_actor_error(self):
        task = dict(records=[dict(id='u', role='user', text='请核对总数。'),
                             dict(id='a', role='assistant', text='我已核对总数。')])
        behaviors = {'check': dict(primary_item='J2', source_ref='u', claim='安排核对总数')}
        judgment = dict(band='reasonable', refs=['check'], basis='你已经核对总数。',
                        next_band='这次未说明需排除哪种具体差错。', next_anchor='针对实际采用条件或需排除的失效选检查及范围')
        prose = dict(items={'J2': {key: judgment[key] for key in ('basis', 'next_band')}})
        calibration = dict(item='J2', kind='wrong_anchor', problem='我认为应该升档。',
                           claim_quote=judgment['next_band'], criterion_quote=judgment['next_anchor'], behavior_ids=['check'])
        actor = dict(item='J2', kind='wrong_actor', problem='执行者是AI。', claim_quote=judgment['basis'],
                     source_ref='a', source_quote='我已核对总数。', behavior_ids=['check'])
        fixed=dict(judgment,basis='你要求AI核对总数。')
        for state in ('advisory','corrected','unresolved'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as path:
                root = Path(path)
                stages=[]
                def send(client, root, name, data, schema, instructions):
                    stages.append(name)
                    if name=='quality-0':
                        issues=[calibration] if state=='advisory' else [calibration,actor]
                    else:
                        issues=[actor] if state=='unresolved' else []
                    result=dict(issues=issues)
                    jsonschema.validate(result, schema)
                    return result

                def repair(findings):
                    self.assertEqual(findings,[actor])
                    return {'J2':judgment if state=='unresolved' else fixed}

                def audit():
                    return check_quality(None, root, [task], behaviors, {'J2': judgment}, prose, [], send,repair)
                if state=='unresolved':
                    with self.assertRaisesRegex(ValueError, '归属检查未通过'):
                        audit()
                else:
                    judgments,_=audit()
                    self.assertEqual(judgments,presentation_contract({'J2':fixed})[0] if state=='corrected' else {'J2':judgment})
                self.assertEqual(stages,['quality-0'] if state=='advisory' else ['quality-0','quality-1'])
                quality = json.loads((root/'quality.json').read_text())
                self.assertEqual(quality['dismissed_findings'][0]['reason'], 'anchor_advisory')
                self.assertEqual(quality.get('issues', []), [actor] if state=='unresolved' else [])

    def test_wrong_item_requires_a_different_item_and_the_scored_behaviors_own_evidence(self):
        action='按上次失败的步骤检查导出。'
        task=dict(records=[dict(id='u',role='user',text=action),dict(id='other',role='user',text='辛苦了。')])
        basis='你指定了导出的检查办法。'
        next_band='还未说明合格条件。'
        behaviors={'check':dict(primary_item='J2',source_ref='u',claim=action)}
        for expected in (None,'J2','P3','P3-wrong-source','P3-next-band','P3-unscored'):
            with self.subTest(expected=expected),tempfile.TemporaryDirectory() as path:
                root=Path(path)
                finding=dict(item='J2',kind='wrong_item',problem='归项需要核对。',claim_quote=basis,
                    source_ref='u',source_quote=action,behavior_ids=['check'])
                if expected:
                    finding['correct_item']=expected.split('-')[0]
                if expected=='P3-wrong-source':
                    finding.update(source_ref='other',source_quote='辛苦了。')
                if expected=='P3-next-band':
                    finding['claim_quote']=next_band
                def send(client,root,name,data,schema,instructions):
                    value=dict(issues=[finding])
                    jsonschema.validate(value,schema)
                    return value
                judgment=dict(J2=dict(refs=[] if expected=='P3-unscored' else ['check']))
                def audit():
                    return check_quality(None,root,[task],behaviors,judgment,
                        dict(items=dict(J2=dict(basis=basis,next_band=next_band))),[],send,
                        lambda _:self.fail('错归不能仅通过修改表述来修复'))
                if expected=='P3':
                    with self.assertRaisesRegex(ValueError,'归属检查未通过'):
                        audit()
                else:
                    audit()
                    quality=json.loads((root/'quality.json').read_text())
                    self.assertEqual(quality['dismissed_findings'][0]['reason'],'invalid_wrong_item_finding')

    def test_fact_coverage_distinguishes_no_action_from_an_omitted_record(self):
        delivered={'u1':dict(role='user'),'u2':dict(role='user'),'a':dict(role='assistant')}
        actions=[fact('u1','提供本次报价来源')]
        check_fact_coverage(dict(actions=actions,no_action_refs=['u2']),delivered)
        for skipped in ([],['u1','u2'],['u2','u2'],['u2','a']):
            with self.subTest(skipped=skipped),self.assertRaisesRegex(ValueError,'事实阅读覆盖不一致'):
                check_fact_coverage(dict(actions=actions,no_action_refs=skipped),delivered)

    def test_actor_check_cannot_use_user_quote_as_ai_evidence(self):
        rows=[dict(id='u',role='user',text='我已在业务端核对总数。'),
              dict(id='a',role='assistant',text='我会根据总数检查差异。')]
        task=dict(records=rows)
        judgment=dict(band='reasonable',refs=['check'],basis='你在业务端核对了总数。',next_band='')
        prose=dict(items={'J2':{key:judgment[key] for key in ('basis','next_band')}})
        behaviors={'check':dict(primary_item='J2',source_ref='u',claim='已经核对总数')}
        issue=dict(item='J2',kind='wrong_actor',problem='误把用户原话当成 AI 操作。',claim_quote=judgment['basis'],
                   source_ref='a',source_quote=rows[0]['text'],behavior_ids=['check'])
        for quote,reason in ((rows[0]['text'],None),('原件没有这句话。','没有给出真实原文反证')):
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as path:
                root=Path(path)
                def send(client, root, name, data, schema, instructions):
                    self.assertEqual(name,'quality-0')
                    result=dict(issues=[dict(issue,source_quote=quote)])
                    jsonschema.validate(result,schema)
                    return result
                def audit():
                    return check_quality(None,root,[task],behaviors,{'J2':judgment},prose,[],send,
                                         lambda _: self.fail('错绑或缺失原文不能用于重判'))
                if reason:
                    with self.assertRaisesRegex(ValueError,reason):audit()
                else:
                    self.assertEqual(audit(),({'J2':judgment},prose))
                    quality=json.loads((root/'quality.json').read_text())
                    self.assertEqual(quality['dismissed_findings'][0]['reason'],'actor_counterevidence_is_user_text')

    def test_program_links_original_path_and_same_judgment_explanation(self):
        original='请修复 E:\\项目\\登录.py。'
        check='请按原失败步骤回归。'
        rows=[dict(id='u',role='user',text=original,excerpt=False),dict(id='u2',role='user',text=check,excerpt=False),
            dict(id='a',role='assistant',text='已修复，回归仍待执行。',excerpt=False)]
        task=dict(id='task-1',records=rows,workflow_index=[])
        sends=[]
        def send(client,root,name,data,schema,instructions):
            sends.append(name)
            if name in {'facts','facts-coverage-repair'}:
                value=dict(task_summaries={'task-1':dict(title='修复登录',goal='恢复登录',outcome='已修改，回归待执行。',workflow=[])},
                    actions=[fact('u','要求修复登录')],no_action_refs=[])
                if name=='facts-coverage-repair':
                    self.assertIn("缺失=['u2']",data['coverage_error'])
                    self.assertEqual(data['previous']['actions'],value['actions'])
                    value['actions'].append(fact('u2','指定原失败步骤回归'))
            elif name=='behaviors':
                value=dict(routes=behavior_wire(data,[dict(quote='请修复 E:\\项目\\登录.py',primary_item='P1'),
                    dict(quote='按原失败步骤回归',primary_item='J2')]))
            elif name.startswith('judge-'):
                value=dict(needs_records=[],items={code:dict(user_action='作出对应安排。',context_support='原话有具体对象和方法。',band=80,basis='你指定了具体对象和处理方法。',next_band='本次尚未呈现进一步的具体质量。',
                    refs=item['eligible_refs'],context_refs=['a'],negative=None) for code,item in data['items'].items()})
            elif name=='quality-0':
                value=dict(issues=[])
            else:
                self.fail('正常流程不应再调用单独的评语写手：'+name)
            jsonschema.validate(value,schema)
            return value
        with tempfile.TemporaryDirectory() as path:
            value,_=assess(SimpleNamespace(),Path(path),[task],{r['id']:r for r in rows},send)
        for code,source in (('P1',original),('J2',check)):
            self.assertEqual(value['items'][code]['band'],'good')
            self.assertIn(value['items'][code]['evidence'][0]['quote'],source)
        self.assertIn('E:\\项目\\登录.py',value['items']['P1']['evidence'][0]['quote'])
        self.assertEqual(sends,['facts','facts-coverage-repair','behaviors','judge-P-0','judge-J-0','quality-0'])

    def test_judgment_can_decline_candidate_without_evidence_and_low_score_needs_responsibility(self):
        schema=judge_schema({'P1':dict(eligible_refs=['b1'])})
        cell=dict(user_action='明确比较目标。',context_support='对象可定位。',band=70,basis='你明确了比较对象。',
            next_band='尚未呈现更具体的目标细化。',refs=['b1'],context_refs=[],negative=None)
        jsonschema.validate(dict(needs_records=[],items={'P1':cell}),schema)
        declined=dict(cell,band=None,refs=[],next_band='',basis='这不是独立的本人行为。')
        jsonschema.validate(dict(needs_records=[],items={'P1':declined}),schema)
        for bad in (dict(declined,refs=['b1']),dict(cell,refs=[]),dict(cell,band=60)):
            with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(needs_records=[],items={'P1':bad}),schema)
        _,prose_schema=presentation_contract({'P1':dict(cell,band='reasonable')})
        prose=dict(summary='目标明确。',items={'P1':dict(basis='你明确了比较对象。',next_band='尚未呈现更具体的目标细化。')})
        jsonschema.validate(prose,prose_schema)
        prose['items']['P1']['band']=100
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(prose,prose_schema)

    def test_duplicate_reference_recovery_keeps_the_receipt_and_does_not_resend(self):
        schema=judge_schema({'P1':dict(eligible_refs=['b1'])})
        for refs,valid in [(['b1','b1'],True),(['b1','missing','missing'],False)]:
            with tempfile.TemporaryDirectory() as path:
                root=Path(path);sent=[]
                value=dict(needs_records=[],items=dict(P1=dict(user_action='明确目标。',context_support='交付具体。',band=80,basis='你明确了比较目标。',next_band='还不能说明更高档的具体质量。',refs=refs,context_refs=[],negative=None)))
                receipt=dict(status='received',request_sha256='a'*64,response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
                def model(payload,target):
                    sent.append(target);atomic(target,receipt);return receipt
                client=SimpleNamespace(config=SimpleNamespace(model='deepseek-v4.1-flash-local'),_call=model)
                if valid:
                    result=judge_call(client,root,'judge',{},schema,call)
                    self.assertEqual(result['items']['P1']['refs'],['b1'])
                    self.assertTrue((root/'judge.reference-set.json').exists())
                else:
                    with self.assertRaises(jsonschema.ValidationError):judge_call(client,root,'judge',{},schema,call)
                self.assertEqual(len(sent),1)
                self.assertEqual(json.loads((root/'judge.receipt.json').read_text()),receipt)


if __name__=='__main__':unittest.main()
