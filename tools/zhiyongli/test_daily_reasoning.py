"""检验来源关联、锁定分数后的解释合同与引用集合恢复；不代替真实模型质量验收。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import jsonschema

from daily_pilot import call, RUBRIC
from daily_reasoning import allocate, assess, allocated_schema, check_fact_coverage, check_quality, checked_judgment, checked_workflows, fact_coverage, judge_call, judge_schema, normalize_facts, presentation_contract, read_asset_evidence, record_source_contract
from daily_behaviors import source_fragments
from runtime import atomic


def behavior_wire(data,rows):
    sources={record['id']:record['text'] for task in data['tasks'] for record in task['records']}
    return {identity:dict(primary_item=row['primary_item'],origin='personal',fragment_id=next(key for key,fragment
        in data['fragments'].items() if fragment[0]==action['record_id'] and row['quote'] in
        sources[fragment[0]][fragment[1]:fragment[1]+fragment[2]]),
        **({'asset_query':''} if row['primary_item']!='J3' else {})) for (identity,action),row in zip(data['actions'].items(),rows)}


def fact(record_id, action):
    return dict(record_id=record_id,action=action,explicit_reason='',new_fact='',new_fact_record='',
                claimed_completed_by_user='',result_decision_quote='',selected_asset=[])


class DailyReasoningTests(unittest.TestCase):
    def test_workflow_repair_changes_only_call_attribution_and_has_one_attempt(self):
        tasks=[dict(id='task',records=[dict(id='u',role='user',text='请整理材料。')],
            workflow_index=[dict(name='some-skill',record_id='tool')])]
        invalid=dict(name='some-skill',purpose='整理材料',selection='用户明确选择',user_refs=['u'],tool_ref='tool')
        facts=dict(task_summaries={'task':dict(title='原题',workflow=[invalid])},actions=[fact('u','整理材料')])
        calls=[]
        def send(client,root,name,data,schema,instructions):
            calls.append(name)
            value={'task':[dict(invalid,selection='AI自主调用',user_refs=[])]}
            jsonschema.validate(value,schema)
            return value
        with tempfile.TemporaryDirectory() as tmp:
            result=checked_workflows(None,Path(tmp),facts,tasks,send)
            self.assertEqual(result['actions'],facts['actions'])
            self.assertEqual(result['task_summaries']['task']['title'],'原题')
            self.assertEqual(result['task_summaries']['task']['workflow'][0]['selection'],'AI自主调用')
            self.assertEqual(facts['task_summaries']['task']['workflow'][0],invalid)
            self.assertEqual(checked_workflows(None,Path(tmp),result,tasks,send),result)
            self.assertEqual(calls,['workflow-attribution-repair'])
            def bad(*args):return {'task':[invalid]}
            with self.assertRaisesRegex(ValueError,'有界纠正后仍不符'):
                checked_workflows(None,Path(tmp),facts,tasks,bad)

    def test_received_coverage_repair_remains_reusable_after_diagnostic_upgrade(self):
        from daily_pilot import request_payload
        from runtime import CompanyClient, digest
        delivered={ref:dict(id=ref,role='user',text=ref) for ref in ('u1','u2')}
        facts=dict(actions=[fact('u1','安排核对')],no_action_refs=[])
        original_error="事实阅读覆盖不一致：缺失=['u2']，动作与无动作冲突=[]，无动作列表不得重复"
        data=dict(tasks=[],previous=facts,coverage_error=original_error)
        schema=dict(type='object',properties=dict(ok=dict(type='boolean')),required=['ok'],additionalProperties=False)
        client=SimpleNamespace(config=SimpleNamespace(model='test',responses_url='https://example.test/v1/responses'))
        client._call=lambda payload,target:CompanyClient._call(client,payload,target)
        saved=dict(status='received',request_sha256=digest(dict(endpoint=client.config.responses_url,
            payload=request_payload(client,data,schema,'覆盖修复'))),
            response=dict(status='completed',output=[dict(content=[dict(type='output_text',text='{"ok":true}')])]))
        with tempfile.TemporaryDirectory() as path:
            root=Path(path); target=root/'facts-coverage-repair.receipt.json'; atomic(target,saved)
            with self.assertRaises(ValueError) as error:
                check_fact_coverage(facts,delivered)
            result=call(client,root,'facts-coverage-repair',dict(data,coverage_error=str(error.exception)),schema,'覆盖修复')
            self.assertEqual(result,dict(ok=True))
            self.assertEqual(json.loads(target.read_text()),saved)

    def test_autonomous_workflow_drops_only_inapplicable_user_selection_refs(self):
        uses=[dict(name='skill',selection=selection,user_refs=['u'],tool_ref='tool')
            for selection in ('AI自主调用','用户明确选择')]
        facts=dict(task_summaries={'task':dict(workflow=uses)},actions=[fact('u','指定既有工作流')])
        with tempfile.TemporaryDirectory() as path:
            result=normalize_facts(facts,Path(path))
            self.assertEqual(result['task_summaries']['task']['workflow'][0]['user_refs'],[])
            self.assertEqual(result['task_summaries']['task']['workflow'][0]['selection'],'AI自主调用')
            self.assertEqual(result['task_summaries']['task']['workflow'][1],uses[1])
            self.assertEqual(result['actions'],facts['actions'])
            self.assertEqual(uses[0]['user_refs'],['u'])
            self.assertEqual(len(json.loads((Path(path)/'facts-normalization.json').read_text())['changes']),1)

    def test_full_material_and_forty_actions_fit_without_repeating_evidence(self):
        from daily_pilot import request_payload
        from daily_reasoning import OBJECTS
        from daily_tasks import MAIN_BYTES, size
        rows=[];actions={}
        for index in range(40):
            ref='u'+str(index)
            text='目标，安排，核对，反馈。' * 20
            rows.append(dict(id=ref,role='user',text=text))
            actions['action-'+str(index)]=dict(fact(ref,'安排核对并根据反馈继续处理。' * 8),
                explicit_reason='不能遗漏已知差异。' * 10,new_fact='AI报告发现差异。' * 10)
        rows.append(dict(id='ai',role='assistant',text=''))
        tasks=[dict(id='task-1',records=rows)]
        rows[-1]['text']='a'*(MAIN_BYTES-size(tasks))
        client=SimpleNamespace(config=SimpleNamespace(model='test'))
        def send(client,root,name,data,schema,instructions):
            self.assertEqual(data['tasks'],tasks)
            self.assertLessEqual(len(json.dumps(request_payload(client,data,schema,instructions),ensure_ascii=False).encode()),96000)
            sources={row['id']:row['text'] for row in rows}
            for fragment in data['fragments'].values():
                self.assertEqual(len(fragment),3)
                ref,start,chars=fragment
                self.assertTrue(sources[ref][start:start+chars])
            return dict(routes={identity:dict(primary_item='J2',origin='personal',asset_query='',
                fragment_id=next(key for key,fragment in data['fragments'].items() if fragment[0]==action['record_id']))
                for identity,action in data['actions'].items()})
        with tempfile.TemporaryDirectory() as path:
            value=allocate(client,Path(path),dict(tasks=tasks,actions=actions,evaluation_objects=OBJECTS,
                anchors={k:v['reasonable'] for k,v in RUBRIC['performance_anchors'].items()},
                counterexamples=RUBRIC['item_counterexamples']),tasks,send)
        self.assertEqual(len(value['behaviors']),40)
        self.assertTrue(all(row['quote'] in rows[index]['text'] for index,row in enumerate(value['behaviors'])))

    def test_each_fact_action_has_one_route_with_evidence_from_the_same_record(self):
        tasks=[dict(id='task-1',records=[dict(id='u1',role='user',text='启动项目。')]),
            dict(id='task-2',records=[dict(id='u2',role='user',text='按失败步骤回归。')])]
        refs=[next(iter(source_fragments({t['records'][0]['id']:t['records'][0]}))) for t in tasks]
        row=lambda ref:dict(fragment_id=ref,primary_item='P1',origin='personal',asset_query='')
        fragments=source_fragments({record['id']:record for task in tasks for record in task['records']})
        actions={'action-1':fact('u1','启动项目。'),'action-2':fact('u2','按失败步骤回归。')}
        schema=allocated_schema(actions,fragments)
        value=dict(routes={'action-1':row(refs[0]),'action-2':None})
        jsonschema.validate(value,schema)
        with tempfile.TemporaryDirectory() as path, self.assertRaisesRegex(ValueError,'入选任务没有实际行为归属'):
            allocate(SimpleNamespace(),Path(path),dict(actions=actions),tasks,lambda *args:value,unscored_refs=['other'])
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
            check=next(key for key,fragment in data['fragments'].items() if fragment[0]=='condition')
            check_route=dict(primary_item='J2',origin='personal',fragment_id=check,asset_query='')
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
                judgment=dict(J2=dict(band='reasonable',basis=basis,next_band=next_band,
                    refs=[] if expected=='P3-unscored' else ['check']))
                def audit():
                    return check_quality(None,root,[task],behaviors,judgment,
                        dict(items=dict(J2=dict(basis=basis,next_band=next_band))),[],send,
                        lambda _:self.fail('分类意见不应触发事实纠正'))
                audit()
                quality=json.loads((root/'quality.json').read_text())
                self.assertEqual(quality['dismissed_findings'][0]['reason'],
                    'classification_advisory' if expected=='P3' else 'invalid_wrong_item_finding')

    def test_wrong_item_and_actor_findings_can_withdraw_the_same_unsupported_score_once(self):
        task=dict(records=[dict(id='u',role='user',text='这条用例是什么问题？'),
            dict(id='a',role='assistant',text='选中规则与详情展示是两个问题。')])
        behaviors={'ask':dict(primary_item='J1',source_ref='u',claim='追问用例问题')}
        old=dict(band='good',refs=['ask'],basis='你区分了选中规则和详情展示。',next_band='区分的依据还不充分。')
        fixed=dict(band=None,refs=[],basis='本次只有追问，未体现独立的归因判断。',next_band='')
        findings=[dict(item='J1',kind='wrong_item',problem='本人只提出问题，不能据此声称本人完成区分。',
            claim_quote=old['basis'],source_ref='u',source_quote='这条用例是什么问题？',behavior_ids=['ask'],correct_item='P1'),
            dict(item='J1',kind='wrong_actor',problem='区分由AI提出。',claim_quote=old['basis'],
            source_ref='a',source_quote='选中规则与详情展示是两个问题。',behavior_ids=['ask'])]
        for resolved in (True,False):
            stages=[]
            def send(client,root,name,data,schema,instructions):
                stages.append(name)
                value=dict(issues=findings if name=='quality-0' or not resolved else [])
                jsonschema.validate(value,schema)
                return value
            def repair(issues):
                self.assertEqual(issues,[findings[1]])
                return {'J1':fixed if resolved else old}
            with self.subTest(resolved=resolved),tempfile.TemporaryDirectory() as path:
                def audit():
                    return check_quality(None,Path(path),[task],behaviors,{'J1':old},
                        dict(items=dict(J1=dict(basis=old['basis'],next_band=old['next_band']))),[],send,repair)
                if resolved:
                    judgments,_=audit()
                    self.assertIsNone(judgments['J1']['band'])
                    self.assertEqual(judgments['J1']['refs'],[])
                else:
                    with self.assertRaisesRegex(ValueError,'归属检查未通过'):
                        audit()
                self.assertEqual(stages,['quality-0','quality-1'])

    def test_classification_advisory_keeps_the_score_without_another_model_judgment(self):
        text='只跑P0用例还包含必填项校验吗？'
        task=dict(records=[dict(id='u',role='user',text=text)])
        behaviors={'ask':dict(primary_item='P2',source_ref='u',claim='澄清用例集包含的内容')}
        judgment=dict(band='reasonable',refs=['ask'],basis='你询问了P0用例集包含的内容。',next_band='本次没有进一步材料核对。')
        finding=dict(item='P2',kind='wrong_item',correct_item='J2',problem='更接近检查范围，但不足以排除原归项。',
            source_ref='u',source_quote=text,claim_quote=judgment['basis'],behavior_ids=['ask'])
        stages=[]
        def send(client,root,name,data,schema,instructions):
            stages.append(name)
            return dict(issues=[finding] if name=='quality-0' else [])
        def repair(issues):
            self.fail('分类分歧不应触发新模型调用')
        with tempfile.TemporaryDirectory() as path:
            judgments,_=check_quality(None,Path(path),[task],behaviors,{'P2':judgment},
                dict(items=dict(P2=dict(basis=judgment['basis'],next_band=judgment['next_band']))),[],send,repair)
            self.assertEqual(judgments['P2']['band'],'reasonable')
            self.assertEqual(judgments['P2']['refs'],['ask'])
        self.assertEqual(stages,['quality-0'])

    def test_responsibility_facts_are_audited_without_scores_and_cannot_be_fixed_by_field_filling(self):
        text='参考这张截图。'
        task=dict(records=[dict(id='u',role='user',text=text)])
        behaviors={'b':dict(primary_item='P2',source_ref='u',claim='提供截图')}
        negative=dict(known_at_time='用户发了截图。',person_control='用户可以提供说明。',
            feasible_alternative='可以补充说明。',actual_gap='评审材料没有展示截图内容。')
        judgment=dict(band='partial',refs=['b'],basis='你提供了截图。',next_band='尚不能证明具体适配。',negative=negative)
        finding=dict(item='P2',kind='unsupported_responsibility',problem='评审没有图片内容，不证明用户有实际不足。',
            claim_quote=negative['actual_gap'],source_ref='u',source_quote=text,behavior_ids=['b'])
        for remains_invalid in (False,True):
            with self.subTest(remains_invalid=remains_invalid),tempfile.TemporaryDirectory() as path:
                stages=[];repairs=[]
                def send(client,root,name,data,schema,instructions):
                    stages.append(name)
                    self.assertNotIn('"band":',json.dumps(data,ensure_ascii=False))
                    if 'responsibility_claims' in data:
                        self.assertEqual(data['responsibility_claims'],{'P2':negative})
                        result=dict(issues=[finding])
                    else:result=dict(issues=[])
                    jsonschema.validate(result,schema)
                    return result
                def repair(issues):
                    repairs.append(issues)
                    return {'P2':dict(judgment) if remains_invalid else dict(judgment,band='reasonable',negative=None)}
                args=(None,Path(path),[task],behaviors,{'P2':dict(judgment)},
                    dict(items=dict(P2=dict(basis=judgment['basis'],next_band=judgment['next_band']))),[],send,repair)
                if remains_invalid:
                    with self.assertRaisesRegex(ValueError,'事实及归属检查未通过'):check_quality(*args)
                    self.assertEqual(stages,['quality-responsibility-0','quality-responsibility-1'])
                else:
                    result,_=check_quality(*args)
                    self.assertEqual(result['P2']['band'],'reasonable')
                    self.assertEqual(stages,['quality-responsibility-0','quality-1'])
                self.assertEqual(len(repairs),1)

    def test_fact_coverage_distinguishes_no_action_from_an_omitted_record(self):
        delivered={'u1':dict(role='user'),'u2':dict(role='user'),'a':dict(role='assistant')}
        actions=[fact('u1','提供本次报价来源')]
        check_fact_coverage(dict(actions=actions,no_action_refs=['u2']),delivered)
        for skipped in ([],['u1','u2'],['u2','u2'],['u2','a']):
            with self.subTest(skipped=skipped),self.assertRaisesRegex(ValueError,'事实阅读覆盖不一致'):
                check_fact_coverage(dict(actions=actions,no_action_refs=skipped),delivered)

    def test_fact_coverage_repair_does_not_promote_uncovered_user_record_to_no_action(self):
        delivered={'u1':dict(role='user',text='明确目标。'),'u2':dict(role='user',text='补充反馈。'),
                   'a':dict(role='assistant',text='已收到。')}
        facts=dict(actions=[fact('u1','明确目标')],no_action_refs=[])
        coverage=fact_coverage(facts,delivered)
        self.assertEqual(coverage['missing'],['u2'])
        self.assertEqual(coverage['no_action'],[])
        self.assertRaisesRegex(ValueError,r"缺失=\['u2'\]",check_fact_coverage,facts,delivered)

    def test_record_source_contract_rejects_missing_or_changed_selected_record(self):
        tasks=[dict(records=[dict(id='u1',role='user',text='原文')])]
        record_source_contract(tasks,{'u1':dict(id='u1',role='user',text='原文',task_id='task-1')})
        with self.assertRaisesRegex(ValueError,r"缺失记录=\['u1'\]"):
            record_source_contract(tasks,{})
        with self.assertRaisesRegex(ValueError,r"正文或角色不一致=\['u1'\]"):
            record_source_contract(tasks,{'u1':dict(id='u1',role='user',text='改写后',task_id='task-1')})

    def test_actor_check_cannot_use_user_quote_as_ai_evidence(self):
        rows=[dict(id='u',role='user',text='我已在业务端核对总数。'),
              dict(id='a',role='assistant',text='我会根据总数检查差异。')]
        task=dict(records=rows)
        judgment=dict(band='reasonable',refs=['check'],basis='你在业务端核对了总数。',next_band='')
        prose=dict(items={'J2':{key:judgment[key] for key in ('basis','next_band')}})
        behaviors={'check':dict(primary_item='J2',source_ref='u',claim='已经核对总数')}
        issue=dict(item='J2',kind='wrong_actor',problem='误把用户原话当成 AI 操作。',claim_quote=judgment['basis'],
                   source_ref='a',source_quote=rows[0]['text'],behavior_ids=['check'])
        for quote,reason in ((rows[0]['text'],'actor_counterevidence_is_user_text'),('原件没有这句话。','unlocatable_counterevidence')):
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
                self.assertEqual(audit(),({'J2':judgment},prose))
                quality=json.loads((root/'quality.json').read_text())
                self.assertEqual(quality['dismissed_findings'][0]['reason'],reason)

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

    def test_repeated_fact_coverage_failure_keeps_uncovered_records_unresolved(self):
        rows=[dict(id='u',role='user',text='请修复登录。',excerpt=False),
              dict(id='u2',role='user',text='保留原失败步骤。',excerpt=False),
              dict(id='a',role='assistant',text='已修改，待回归。',excerpt=False)]
        task=dict(id='task-1',records=rows,workflow_index=[])
        unresolved=dict(id='task-2',records=[dict(id='u3',role='user',text='另一个任务仍待核对。',excerpt=False)],workflow_index=[])
        no_action=dict(id='task-3',records=[dict(id='u4',role='user',text='好。',excerpt=False)],workflow_index=[])
        sends=[]

        def send(client,root,name,data,schema,instructions):
            sends.append(name)
            if name in {'facts','facts-coverage-repair'}:
                value=dict(task_summaries={'task-1':dict(title='修复登录',goal='恢复登录',outcome='已修改，待回归。',workflow=[])},
                    actions=[fact('u','要求修复登录')],no_action_refs=[])
                value['task_summaries']['task-2']=dict(title='另一个任务',goal='核对',outcome='未决',workflow=[])
                value['task_summaries']['task-3']=dict(title='已知无动作任务',goal='保留上下文',outcome='未形成可评分行为',workflow=[])
                value['no_action_refs']=['u4']
                if name=='facts-coverage-repair':
                    self.assertIn("缺失=['u2', 'u3']",data['coverage_error'])
            elif name=='behaviors':
                value=dict(routes={'action-1':dict(fragment_id=next(iter(data['fragments'])),primary_item='P1',origin='personal',asset_query='')})
            elif name=='judge-P-0':
                identity=next(iter(data['behaviors']))
                value=dict(needs_records=[],items=dict(P1=dict(user_action='要求修复登录。',context_support='目标和对象明确。',band=80,
                    basis='你明确了修复目标。',next_band='本次尚未呈现更高档的具体质量。',refs=[identity],context_refs=[],negative=None)))
            elif name=='quality-0':
                value=dict(issues=[])
            else:
                self.fail('未登记的评审阶段：'+name)
            jsonschema.validate(value,schema)
            return value

        with tempfile.TemporaryDirectory() as path:
            value,_=assess(SimpleNamespace(),Path(path),[task,unresolved,no_action],
                {r['id']:r for r in rows+unresolved['records']+no_action['records']},send)
            coverage=json.loads((Path(path)/'facts-coverage.json').read_text())
        self.assertEqual(coverage['status'],'partial')
        self.assertEqual(coverage['missing_refs'],['u2','u3'])
        self.assertEqual(value['task_summaries']['task-2']['user_actions'],[])
        self.assertEqual(value['task_summaries']['task-3']['user_actions'],[])
        self.assertEqual(len(value['limitations']),2)
        self.assertEqual(sends,['facts','facts-coverage-repair','behaviors','judge-P-0','quality-0'])

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

    def test_omitted_inapplicable_negative_metadata_is_recovered_without_inventing_scores(self):
        schema=judge_schema({'P1':dict(eligible_refs=['b1'])})
        for band,valid in ((70,True),(80,True),(90,True),(100,True),(None,True),(60,False)):
            with self.subTest(band=band),tempfile.TemporaryDirectory() as path:
                root=Path(path);sent=[]
                cell=dict(user_action='明确比较目标。',context_support='比较对象可定位。',band=band,basis='你明确了比较对象。',
                    next_band='' if band in (None,100) else '本次尚未体现进一步的质量优势。',refs=[] if band is None else ['b1'],context_refs=[])
                value=dict(needs_records=[],items=dict(P1=cell))
                receipt=dict(status='received',request_sha256='a'*64,response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
                def model(payload,target):
                    sent.append(target);atomic(target,receipt);return receipt
                client=SimpleNamespace(config=SimpleNamespace(model='deepseek-v4.1-flash-local'),_call=model)
                if valid:
                    result=judge_call(client,root,'judge',{},schema,call)
                    self.assertIsNone(result['items']['P1']['negative'])
                    self.assertEqual(result['items']['P1']['band'],None if band is None else next(key for key,score in RUBRIC['performance_values'].items() if score==band))
                else:
                    with self.assertRaises(jsonschema.ValidationError):judge_call(client,root,'judge',{},schema,call)
                self.assertEqual(len(sent),1)
                self.assertEqual(json.loads((root/'judge.receipt.json').read_text()),receipt)

    def test_unscored_candidate_refs_are_not_retained_as_scoring_evidence(self):
        schema=judge_schema({'P2':dict(eligible_refs=['b1'])},['u1'])
        for refs,negative,next_band,valid in ((['b1'],None,'',True),(['foreign'],None,'',False),
                (['b1'],dict(actual_gap='不足'),'',False),(['b1'],None,'仍需改进',False)):
            with self.subTest(refs=refs,negative=negative,next_band=next_band),tempfile.TemporaryDirectory() as path:
                root=Path(path);sent=[]
                cell=dict(user_action='提供了截图路径。',context_support='截图正文不可见。',band=None,
                    basis='本次无法评价截图信息是否适配。',next_band=next_band,refs=refs,context_refs=['u1'],negative=negative)
                value=dict(needs_records=[],items=dict(P2=cell))
                receipt=dict(status='received',request_sha256='a'*64,response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
                def model(payload,target):
                    sent.append(target);atomic(target,receipt);return receipt
                client=SimpleNamespace(config=SimpleNamespace(model='test'),_call=model)
                data=dict(items={'P2':dict(eligible_refs=['b1'])})
                if valid:
                    result=judge_call(client,root,'judge',data,schema,call)
                    self.assertIsNone(result['items']['P2']['band'])
                    self.assertEqual(result['items']['P2']['refs'],[])
                    self.assertEqual(result['items']['P2']['basis'],cell['basis'])
                else:
                    with self.assertRaises(jsonschema.ValidationError):judge_call(client,root,'judge',data,schema,call)
                self.assertEqual(len(sent),1)
                self.assertEqual(json.loads((root/'judge.receipt.json').read_text()),receipt)

    def test_invalid_received_judgment_has_one_separate_repair_and_reuses_both_receipts(self):
        from runtime import CompanyClient,digest
        schema=judge_schema({'P2':dict(eligible_refs=['b1'])},['u1'])
        cell=dict(user_action='补充了截图。',context_support='只知道截图路径，未展示内容。',band=60,
            basis='材料未呈现截图内容。',next_band='尚不能说明适配情况。',refs=['b1'],context_refs=['u1'],negative=None)
        original=dict(needs_records=[],items=dict(P2=cell))
        for band in (70,None,60):
            with self.subTest(repaired_band=band),tempfile.TemporaryDirectory() as path:
                root=Path(path);sent=[];payloads=[]
                corrected=dict(needs_records=[],items=dict(P2=dict(cell,band=band,
                    refs=[] if band is None else ['b1'],next_band='' if band is None else cell['next_band'])))
                client=SimpleNamespace(config=SimpleNamespace(model='test',responses_url='https://example.test/v1/responses'))
                def model(payload,target):
                    if target.exists():return CompanyClient._call(client,payload,target)
                    sent.append(target.name);payloads.append(payload)
                    value=original if target.name=='judge-P-0.receipt.json' else corrected
                    receipt=dict(status='received',request_sha256=digest(dict(endpoint=client.config.responses_url,payload=payload)),
                        response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
                    atomic(target,receipt);return receipt
                client._call=model
                data=dict(tasks=[dict(records=[dict(id='u1',role='user',text='参考这张截图。')])],
                    items={'P2':dict(eligible_refs=['b1'])})
                for attempt in range(2):
                    if band==60:
                        with self.assertRaises(jsonschema.ValidationError):checked_judgment(client,root,'judge-P-0',data,schema,call)
                    else:
                        result=checked_judgment(client,root,'judge-P-0',data,schema,call)
                        self.assertEqual(result['items']['P2']['band'],'reasonable' if band==70 else None)
                self.assertEqual(sent,['judge-P-0.receipt.json','judge-P-0-contract-repair-v2.receipt.json'])
                self.assertNotEqual(payloads[0],payloads[1])
                receipt=json.loads((root/'judge-P-0.receipt.json').read_text())
                self.assertEqual(json.loads(receipt['response']['output'][0]['content'][0]['text']),original)
                proof=json.loads((root/'judge-P-0.contract-repair.json').read_text())
                self.assertEqual(proof['receipt_sha256'],digest(receipt))
                self.assertFalse((root/'judge-P-0-contract-repair-v2-contract-repair-v2.receipt.json').exists())

    def test_unused_item_summary_keeps_scores_and_original_receipt(self):
        schema=judge_schema({'E3':dict(eligible_refs=['b1'])})
        for extra,valid in ((dict(summary='冗余概述。'),True),(dict(unknown='未定义字段'),False)):
            with self.subTest(extra=extra),tempfile.TemporaryDirectory() as path:
                root=Path(path);sent=[]
                cell=dict(user_action='选择既有脚本。',context_support='脚本与当前工作对应。',band=80,
                    basis='你复用了既有脚本。',next_band='本次尚未呈现更完整的信息保留作用。',refs=['b1'],context_refs=[],negative=None,**extra)
                value=dict(needs_records=[],items=dict(E3=cell))
                receipt=dict(status='received',request_sha256='a'*64,response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
                def model(payload,target):
                    sent.append(target);atomic(target,receipt);return receipt
                client=SimpleNamespace(config=SimpleNamespace(model='deepseek-v4.1-flash-local'),_call=model)
                if valid:
                    result=judge_call(client,root,'judge',{},schema,call)
                    self.assertEqual(result['items']['E3'],dict({k:v for k,v in cell.items() if k!='summary'},band='good'))
                    self.assertEqual(json.loads((root/'judge.reference-set.json').read_text())['normalized_fields'],[['E3','summary','unused']])
                else:
                    with self.assertRaises(jsonschema.ValidationError):judge_call(client,root,'judge',{},schema,call)
                self.assertEqual(len(sent),1)
                self.assertEqual(json.loads((root/'judge.receipt.json').read_text()),receipt)

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
