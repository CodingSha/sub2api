"""验证任务边界、实际阅读范围及日评分确定性合同，不以测试代替模型质量验收。"""
import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from daily_tasks import turns, directory, materialize, windows, workflow_rows, tool_directory
from daily_assessment import check_assessment, evaluate, frozen_plan, select_candidates, plan_schema
from daily_pilot import RUBRIC
from runtime import digest


def row(key,role,text,**extra):
    return dict(id=key,role=role,text=text,truncated=False,at='2026-11-05T12:00:00+08:00',**extra)


class DailyAssessmentTests(unittest.TestCase):
    def test_tool_directory_exposes_command_and_real_output_location_without_claiming_read(self):
        records={'c':dict(id='c',role='tool',task_id='task-1',text='Tool: exec\nCall ID: abc\nType: custom_tool_call\n\ntext(await tools.exec_command({cmd:"Get-Content old_script.py"}))'),
            'o':dict(id='o',role='tool',task_id='task-1',text='Call ID: abc\nType: custom_tool_call_output\n\nScript completed\nOutput:\n'+json.dumps(dict(output='def reuse_login():\n    return existing_session'))),
            'other':dict(id='other',role='tool',task_id='task-2',text='另一任务工具')}
        listing=tool_directory(records,{'task-1'})
        self.assertFalse(listing['preview_is_evidence'])
        self.assertEqual(listing['total'],2)
        indexed={r['record_id']:r for r in listing['records']}
        self.assertEqual(indexed['c']['preview'],'Get-Content old_script.py')
        self.assertIn('reuse_login',indexed['o']['preview'])
        self.assertNotIn('Call ID',indexed['o']['preview'])
        self.assertEqual(tool_directory(records,{'task-1'},maximum=10)['listed'],0)
        found=tool_directory(records,{'task-1'},query='reuse_login')
        self.assertEqual([r['record_id'] for r in found['records']],['o'])
        self.assertEqual(tool_directory(records,{'task-2'},query='reuse_login')['total'],0)

    def test_tool_search_locates_match_beyond_first_read_window(self):
        body='普通日志。'*1600+'参考 old_script.py 的 reuse_login 方法，保留登录态。'+'后续日志。'*200
        raw='Type: custom_tool_call_output\n\nScript completed\nOutput:\n'+json.dumps(dict(output=body),ensure_ascii=False)
        records={'o':dict(id='o',role='tool',task_id='task-1',text=raw)}
        listing=tool_directory(records,{'task-1'},query='REUSE_LOGIN')
        self.assertEqual(listing['total'],1)
        found=listing['records'][0]
        self.assertGreater(found['match_start'],6000)
        self.assertEqual(raw[found['match_start']:found['match_start']+11],'reuse_login')
        self.assertIn('reuse_login',found['preview'])
        self.assertNotIn('match_start',tool_directory(records,{'task-1'})['records'][0])
        # 检索偏移能直接用于现有补读合同；只有实际补读的原文才成为依据。
        from daily_reasoning import read_tools
        read=[]
        read_tools(dict(items=None,needs_records=[dict(record_id='o',start=found['match_start']-30,chars=200)]),records,read)
        self.assertIn('reuse_login',read[0]['text'])
        self.assertTrue(read[0]['excerpt'])
        self.assertFalse(listing['preview_is_evidence'])

    def test_tool_output_skill_catalog_is_not_actual_skill_use(self):
        rows=[row('list','tool','Call ID: a\nType: custom_tool_call_output\n\n/skills/documents/SKILL.md\ntools.mcp__catalog__read()'),
            row('load','tool','Tool: exec\nType: custom_tool_call\n\ncat /skills/push/SKILL.md'),
            row('mcp','tool','Tool: exec\nType: custom_tool_call\n\ntext(await tools.mcp__yunxiao__get_testcase({id: 1}))')]
        self.assertEqual([(r['name'],r['record_id']) for r in workflow_rows(rows)],[('push','load'),('mcp__yunxiao__get_testcase','mcp')])

    def test_window_directory_keeps_richer_ranges_and_other_sessions_when_capped(self):
        groups=[dict(session='long',records=[r for i in range(5) for r in (
            row('u'+str(i),'user','安排任务并反馈。'*40),row('a'+str(i),'assistant','实际执行结果。'*40))]),
            dict(session='short',records=[row('su','user','处理一个独立任务。'),row('sa','assistant','已给出结果。')])]
        ts=turns(groups)
        # 降低单任务预算制造长会话窗口；目录只留两个位置时仍须包含两个会话。
        with patch('daily_tasks.TASK_BYTES',4500),patch('daily_tasks.WINDOW_OPTIONS',2):
            options=windows(ts,directory(ts))
        self.assertEqual({ts[o['start']]['session'] for o in options},{'long','short'})
        rich=next(o for o in options if ts[o['start']]['session']=='long')
        self.assertGreater(rich['user_turns'],1)
        self.assertEqual(rich['feedback_turns'],rich['user_turns'])
        self.assertEqual(next(o for o in options if ts[o['start']]['session']=='short')['scope'],'complete_session')

    def test_fewer_rich_candidates_are_allowed_without_filling_all_five(self):
        import jsonschema
        options=[dict(id='w'+str(i)) for i in range(5)]
        jsonschema.validate(dict(candidates=[dict(window_id='w0',title='完整任务',goal='核对完整来回')]),plan_schema(options))

    def test_small_session_cannot_offer_only_final_debugging_and_lose_reuse_choice(self):
        groups=[dict(session='s',records=[
            row('u1','user','按照方案编写脚本。'),row('a1','assistant','准备编写。'),
            row('u2','user','可以参考现有入网脚本。'),row('a2','assistant','已参考旧脚本编写。'),
            row('u3','user','脚本写完之后调试一下。'),row('a3','assistant','调试结果已整理。')])]
        ts=turns(groups)
        options=windows(ts,directory(ts))
        self.assertEqual([(w['start'],w['end'],w['scope']) for w in options],[('u1','u3','complete_session')])
        plan=dict(candidates=[dict(window_id=options[0]['id'],title='编写与调试脚本',goal='按方案复用旧脚本并调试')])
        tasks,_,_=select_candidates(plan,options,ts)
        self.assertEqual([r['id'] for r in tasks[0]['records'] if r['role']=='user'],['u1','u2','u3'])
        packet=dict(user_id=1,events=[])
        old,_=materialize(dict(tasks=[dict(start='u3',end='u3',title='编写与调试脚本',goal='只选收尾')]),ts)
        with tempfile.TemporaryDirectory() as target:
            root=Path(target)
            (root/'contract.json').write_text(json.dumps(dict(day='2026-11-05',packet_sha256=digest(packet))))
            (root/'selected-tasks.json').write_text(json.dumps(old))
            with self.assertRaisesRegex(ValueError,'当前完整会话'):
                frozen_plan(root,packet,'2026-11-05',ts)

    def test_unscored_image_or_automatic_input_still_ends_previous_reply(self):
        groups=[dict(session='s',records=[
            row('u','user','提交字段限制Bug。'),row('a','assistant','已提交字段限制Bug。'),
            row('image','context','</image>',turn_boundary=True),
            row('other','assistant','已补充另一个Bug的截图。'),
            row('title','context','后台标题请求',turn_boundary=True),
            row('title_reply','assistant','新标题')])]
        task,records=materialize(dict(tasks=[dict(start='u',end='u',title='字段限制',goal='提交Bug')]),turns(groups))
        self.assertEqual([r['id'] for r in task[0]['records']],['u','a'])
        self.assertNotIn('other',records)

    def test_candidate_ranking_is_packed_without_repair_calls_or_partial_text(self):
        all_turns=turns(self.groups())
        options=[dict(id='w1',start='u1',end='u1'),dict(id='w2',start='u1',end='u2'),dict(id='w3',start='u2',end='u2')]
        plan=dict(candidates=[dict(window_id=k,title='安装',goal='完成安装并保留任务') for k in ('w1','w2','w3')])
        tasks,_,selection=select_candidates(plan,options,all_turns)
        self.assertEqual(len(tasks),2)
        self.assertEqual([x['window_id'] for x in selection['selected']],['w1','w3'])
        self.assertEqual(selection['rejected'][0]['window_id'],'w2')
        self.assertIn('重叠',selection['rejected'][0]['reason'])
        self.assertTrue(all(not r['excerpt'] for task in tasks for r in task['records']))

    def groups(self):
        return [dict(session='s',records=[
            row('u1','user','先安装，确认没问题后再推送。'),row('t','tool','读取 /skills/push/SKILL.md'),
            row('a1','assistant','正在安装。'),row('a2','assistant','已安装，仍须检查运行效果。'),
            row('c','context','现在是否重启？',kind='clarification_question'),
            row('u2','user','任务还在执行，先不要重启。'),row('a3','assistant','保留当前任务，不重启。')])]

    def plan(self,start='u1',end='u2'):
        return dict(tasks=[dict(start=start,end=end,title='安装并保护运行中任务',goal='安装后检查并推送')])

    def test_whole_user_messages_and_feedback_survive_without_atomic_clause_allocation(self):
        tasks,records=materialize(self.plan(),turns(self.groups()))
        transcript=tasks[0]['records']
        self.assertEqual([r['id'] for r in transcript],['u1','a2','c','u2','a3'])
        self.assertEqual(transcript[0]['text'],'先安装，确认没问题后再推送。')
        self.assertNotIn('a1',{r['id'] for r in transcript})
        self.assertEqual(records['t']['task_id'],'task-1')
        self.assertEqual(tasks[0]['workflow_index'][0]['name'],'push')

    def test_task_boundaries_cannot_cross_sessions_or_overlap(self):
        groups=self.groups()+[dict(session='other',records=[row('u3','user','计算汇率。'),row('a4','assistant','汇率结果。')])]
        all_turns=turns(groups)
        with self.assertRaisesRegex(ValueError,'跨会话'):materialize(self.plan('u1','u3'),all_turns)
        with self.assertRaisesRegex(ValueError,'不存在'):materialize(self.plan('missing','u1'),all_turns)
        plan=self.plan();plan['tasks'].append(plan['tasks'][0])
        with self.assertRaisesRegex(ValueError,'重叠'):materialize(plan,all_turns)

    def test_large_actual_body_is_rejected_even_when_preview_is_short(self):
        groups=[dict(session='s',records=[row('u','user','请处理'+('完整原文'*5000)),row('a','assistant','处理回复')])]
        all_turns=turns(groups)
        self.assertLess(len(directory(all_turns)[0]['user_preview']),401)
        with self.assertRaisesRegex(ValueError,'超过选材预算'):materialize(self.plan('u','u'),all_turns)

    def test_feedback_absent_is_not_invented_as_completed_task(self):
        groups=[dict(session='s',records=[row('u','user','整理需求。')])]
        with self.assertRaisesRegex(ValueError,'缺少AI实际反馈'):materialize(self.plan('u','u'),turns(groups))

    def test_every_advertised_window_delivers_full_original_material(self):
        groups=self.groups()+[dict(session='large',records=[row('huge','user','完整原文'*10000),row('ha','assistant','反馈')])]
        all_turns=turns(groups)
        options=windows(all_turns,directory(all_turns))
        self.assertTrue(options)
        self.assertFalse(any(w['start']=='huge' or w['end']=='huge' for w in options))
        for window in options:
            tasks,_=materialize(self.plan(window['start'],window['end']),all_turns)
            for r in tasks[0]['records']:
                self.assertFalse(r['excerpt'])

    def test_frozen_selection_is_verified_against_same_person_day_and_actual_source(self):
        all_turns=turns(self.groups());tasks,_=materialize(self.plan(),all_turns)
        packet=dict(user_id=1,events=[])
        with tempfile.TemporaryDirectory() as target:
            root=Path(target)
            (root/'contract.json').write_text(json.dumps(dict(day='2026-11-05',packet_sha256=digest(packet))))
            (root/'selected-tasks.json').write_text(json.dumps(tasks))
            self.assertEqual(frozen_plan(root,packet,'2026-11-05',all_turns)[0],tasks)
            with self.assertRaisesRegex(ValueError,'原件不一致'):
                frozen_plan(root,dict(packet,user_id=2),'2026-11-05',all_turns)
            with self.assertRaisesRegex(ValueError,'日期或冻结'):
                frozen_plan(root,packet,'2026-11-06',all_turns)
            all_turns['u1']['user']['text']='被改写的原文'
            with self.assertRaisesRegex(ValueError,'当前变换不一致'):
                frozen_plan(root,packet,'2026-11-05',all_turns)

    def test_source_quote_must_be_delivered_user_and_not_repeated_across_items(self):
        cell=dict(band='good',basis='你安排了明确检查。',next_band='检查条件还可以更具体。',negative=None,
            evidence=[dict(origin='personal',record_id='u1',quote='确认没问题后再推送。')])
        delivered={'u1':row('u1','user','先安装，确认没问题后再推送。'),'ai':row('ai','assistant','已确认没问题。')}
        value=dict(needs_records=[],items={'J2':cell})
        check_assessment(value,delivered)
        value['items']['P1']=cell.copy()
        with self.assertRaisesRegex(ValueError,'重复计分'):check_assessment(value,delivered)
        value['items']={'J2':dict(cell,evidence=[dict(origin='personal',record_id='ai',quote='已确认没问题。')])}
        with self.assertRaisesRegex(ValueError,'不属于实际交付'):check_assessment(value,delivered)

    def test_null_does_not_create_seventy_or_pending_reviewer(self):
        cell=dict(band=None,basis='本次没有看到独立结果处理行为。',next_band='',negative=None,evidence=[])
        check_assessment(dict(needs_records=[],items={'J3':cell}),{})
        with self.assertRaisesRegex(ValueError,'缺少本人依据'):
            check_assessment(dict(needs_records=[],items={'J3':dict(cell,band='reasonable')}),{})

    def test_forwarded_material_is_kept_as_supplied_not_claimed_as_personal_judgment(self):
        cell=dict(band='reasonable',basis='你提供了当前需要的材料。',next_band='还不能证明材料适配的更高质量。',negative=None,
            evidence=[dict(origin='supplied',record_id='u',quote='原日志内容')])
        delivered={'u':row('u','user','请分析这份原日志内容。')}
        check_assessment(dict(needs_records=[],items={'P2':cell}),delivered)
        with self.assertRaisesRegex(ValueError,'转贴材料不能代替本人'):
            check_assessment(dict(needs_records=[],items={'J1':cell}),delivered)

    def test_shared_quote_requires_explicit_independent_behavior_not_character_separation(self):
        cell=dict(band='good',basis='安排合理。',next_band='仍需更具体的安排。',negative=None,
            evidence=[dict(origin='personal',record_id='u',quote='先拉取最新代码，再决定是否升级。')])
        other=dict(cell,evidence=[dict(origin='personal',record_id='u',quote='再决定是否升级。')])
        missing=dict(cell,evidence=[dict(origin='personal',record_id='missing',quote='虚构原文')])
        with self.assertRaises(ValueError) as result:
            check_assessment(dict(needs_records=[],items=dict(P1=cell,M2=other,J1=missing)),
                {'u':row('u','user','先拉取最新代码，再决定是否升级。')})
        self.assertIn('不属于实际交付原文：J1',str(result.exception))
        shared=dict(needs_records=[],items=dict(P1=cell,M2=dict(other,evidence=[dict(other['evidence'][0],quote=cell['evidence'][0]['quote'])])))
        with self.assertRaisesRegex(ValueError,'独立行为说明'):
            check_assessment(shared,{'u':row('u','user','先拉取最新代码，再决定是否升级。')})
        shared['behaviors']={code:dict(primary_item=code,source_ref='u',quote=cell['evidence'][0]['quote'],claim=claim)
            for code,claim in [('P1','要求拉取最新代码'),('M2','依据拉取结果决定升级') ]}
        for code,item in shared['items'].items():item['evidence'][0]['behavior_id']=code
        check_assessment(shared,{'u':row('u','user','先拉取最新代码，再决定是否升级。')})
        # 同一完整消息里的两项独立动作，可用互不重叠的短引文分别说明。
        check_assessment(dict(needs_records=[],items=dict(P1=dict(cell,evidence=[dict(origin='personal',record_id='u',quote='先拉取最新代码，')]),M2=other)),
            {'u':row('u','user','先拉取最新代码，再决定是否升级。')})

    def assessment(self,tasks):
        items={code:dict(band=None,basis='本次未发生该项行为。',next_band='',negative=None,evidence=[])
               for code in RUBRIC['items']}
        return dict(task_summaries={task['id']:dict(title=task['title'],goal=task['goal'],
            user_actions=[dict(record_id='u1',action='安排安装与核验。')],workflow=[]) for task in tasks},
            needs_records=[],items=items,summary='已安排安装与检查。')

    def test_selected_tasks_with_all_null_items_are_execution_failure_not_person_score(self):
        tasks,records=materialize(self.plan(),turns(self.groups()))
        with self.assertRaisesRegex(ValueError,'选材或判档失败'):
            check_assessment(self.assessment(tasks),records,tasks)

    def test_task_action_cannot_be_borrowed_from_another_task_or_ai(self):
        tasks,records=materialize(self.plan(),turns(self.groups()))
        value=self.assessment(tasks)
        value['items']['P1'].update(band='reasonable',basis='你明确了当前目标。',next_band='仍需更清楚的交付口径。',
            evidence=[dict(origin='personal',record_id='u1',quote='先安装，')])
        value['task_summaries']['task-1']['user_actions'][0]['record_id']='a3'
        with self.assertRaisesRegex(ValueError,'不属于该任务实际用户'):
            check_assessment(value,records,tasks)

    def test_evaluate_uses_day_contract_and_exact_original_weights(self):
        groups=[dict(session='s',records=[row('u1','user','输出插件清单。逐项检查兼容性。先记录，再安装。沿用已确认的上下文。'),
                                          row('a1','assistant','完成清单，下一步检查。')])]
        tasks,_=materialize(self.plan('u1','u1'),turns(groups))
        value=self.assessment(tasks)
        for code,band,quote in [('P1','outstanding','输出插件清单。'),('M1','good','先记录，再安装。'),
                                ('J2','reasonable','逐项检查兼容性。'),('E1','reasonable','沿用已确认的上下文。')]:
            value['items'][code].update(band=band,basis='你作出了对应安排。',next_band='还不能证明更高档质量。',
                evidence=[dict(origin='personal',record_id='u1',quote=quote,behavior_id=code)])
        value['behaviors']={code:dict(primary_item=code,source_ref='u1',quote=cell['evidence'][0]['quote'],claim=cell['evidence'][0]['quote'])
            for code,cell in value['items'].items() if cell['evidence']}
        with tempfile.TemporaryDirectory() as target,patch('daily_assessment.prepare',return_value=(groups,{},{})), \
                patch('daily_assessment.assess',return_value=(value,[])), \
                patch('daily_assessment.checked_call',return_value=dict(candidates=[dict(window_id='w1',title='插件安装',goal='安装并检查')])):
            root=Path(target)
            metrics=evaluate(dict(user_id=1,user_email='test@example.com'),'2026-11-05',root,SimpleNamespace(reasoning_effort=None))
            result=json.loads((root/'report.json').read_text())
        self.assertEqual((result['period_type'],result['period_end']),('day','2026-11-06'))
        self.assertEqual(result['exact'],'155/2')
        self.assertEqual(result['score'],'77.5')
        self.assertEqual(result['pilot']['quality_status'],'not_checked')
        self.assertEqual(metrics['selected_tasks'],1)

    def test_two_tool_readbacks_cannot_turn_into_fourth_score_or_repair(self):
        from daily_reasoning import assess
        groups=self.groups()
        task,records=materialize(self.plan('u1','u1'),turns(groups))
        from daily_behaviors import source_fragments
        fragments=source_fragments({r['id']:r for t in task for r in t['records']})
        fragment=next(k for k,f in fragments.items() if '确认没问题' in f['quote'])
        facts=dict(actions=[dict(record_id='u1',action='安排核验后推送',result_decision_quote='')],no_action_refs=[])
        allocation=dict(routes={'action-1':dict(fragment_id=fragment,primary_item='J2',origin='personal')})
        needs=[dict(items=None,needs_records=[dict(record_id='t',start=i,chars=1)]) for i in range(3)]
        with tempfile.TemporaryDirectory() as target,patch('daily_assessment.checked_call',side_effect=[facts,allocation,*needs]) as call:
            with self.assertRaisesRegex(ValueError,'补读已达两轮'):
                assess(SimpleNamespace(reasoning_effort=None),Path(target),task,records,call)
            self.assertEqual(call.call_count,5)  # 一次阅读、一次归属、三次判档，没有第四次判档。


if __name__=='__main__':unittest.main()
