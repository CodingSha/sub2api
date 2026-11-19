"""原锚点的分数、理由和相邻档必须来自同一份证据判断。"""
import unittest
import json
import tempfile
from pathlib import Path
import jsonschema
from anchored_judgment import LEVELS,contract,resolve,material,readable_reason


class AnchoredJudgmentTests(unittest.TestCase):
    def cell(self):
        return dict(band='good',basis='你根据运行中这一事实撤回重启，保护当前任务。',
            next_band='这次没有体现暂停与其他工作的具体衔接。',refs=['q1'],negative=None)

    def test_score_basis_and_next_band_share_one_decision(self):
        cell=self.cell();result=resolve('M3',cell)
        self.assertEqual(result['band'],'good')
        self.assertEqual(result['basis'],cell['basis'])
        self.assertEqual(result['next_band'],cell['next_band'])
        self.assertEqual(result['refs'],['q1'])
        cell['next_band']=''
        with self.assertRaisesRegex(ValueError,'缺少相邻档说明'):resolve('M3',cell)

    def test_highest_band_has_no_next_band(self):
        cell=self.cell();cell['band']='exceptional'
        with self.assertRaisesRegex(ValueError,'最高档'):resolve('M3',cell)
        cell['next_band']=''
        self.assertEqual(resolve('M3',cell)['next_band'],'')

    def test_negative_comparison_does_not_trigger_language_keyword_repair(self):
        cell=self.cell()
        cell['basis']='你要求说明差异及原因，只复述现象不足以满足目标，因此交付指向明确。'
        self.assertEqual(resolve('P1',cell)['basis'],cell['basis'])
        # 程序不靠关键词宣称语义已正确；评语与事实仍须另行验收。
        cell['basis']='目前不足以证明针对性优势。'
        self.assertEqual(resolve('P1',cell)['basis'],cell['basis'])

    def test_missing_behavior_never_defaults_to_seventy(self):
        cell=self.cell();cell.update(band=None,refs=[],next_band='')
        self.assertIsNone(resolve('M3',cell)['band'])
        cell['band']='good'
        with self.assertRaisesRegex(ValueError,'缺少本人依据'):resolve('M3',cell)

    def test_request_schema_does_not_duplicate_prose_per_band(self):
        items={code:dict(eligible_refs=['q1','q2']) for code in ('P1','P2','P3')}
        # 为三项传引用约束应控制在几KB，避免把九个分支各重复整段schema。
        self.assertLess(len(json.dumps(contract(items))),6000)

    def test_internal_quote_number_is_resolved_without_rewriting_judgment(self):
        self.assertEqual(readable_reason('q1 只是询问配置，不额外计分。',{'q1':dict(quote='该技能用了什么模型？')}),
            '“该技能用了什么模型？” 只是询问配置，不额外计分。')
        with self.assertRaisesRegex(ValueError,'未知引文'):readable_reason('q99 证明了结论。',{})

    def test_invented_numeric_band_and_reviewer_state_are_rejected(self):
        schema=contract(dict(M3=dict(eligible_refs=['q1'])))
        for band in ('81','pending_review'):
            cell=self.cell();cell['band']=band
            with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(M3=cell),schema)

    def test_task_identity_survives_without_unrelated_actions(self):
        transcripts=[dict(session='web',records=[dict(id='u1',role='user',passages=[dict(id='q0',text='编辑器正在保存，'),dict(id='q1',text='先不要关闭。'),dict(id='q3',text='之后检查页面。')])]),
            dict(session='codex',records=[dict(id='u2',role='user',passages=[dict(id='q2',text='更新后消息不排队了。')]),dict(id='a',role='assistant',text='AI执行长总结不能作为用户行为')])]
        with tempfile.TemporaryDirectory() as target:
            root=Path(target)
            (root/'task-reading.input.json').write_text(json.dumps(dict(input=json.dumps(dict(transcripts=transcripts)))))
            (root/'task-reading.json').write_text(json.dumps(dict(conversations=dict(web=dict(title='网页登录排查',goal='查询分支差异'),codex=dict(title='Codex排队行为',goal='核对更新行为')))))
            (root/'behavior-allocation.json').write_text(json.dumps({q:dict(primary_item=code) for q,code in [('q0','context'),('q1','P2'),('q2','P2'),('q3','J2')]}))
            items,catalog=material(root)
        self.assertNotEqual(catalog['q1']['task'],catalog['q2']['task'])
        self.assertEqual(catalog['q1']['task_title'],'网页登录排查')
        self.assertEqual(catalog['q2']['task_goal'],'核对更新行为')
        contexts={c['session']:c for c in items['P2']['context']}
        self.assertEqual(contexts['codex']['preceding_assistant'],[])
        self.assertEqual(contexts['web']['task_title'],'网页登录排查')
        prefix=contexts['web']['user_message'][0]['passages']
        self.assertEqual([p['id'] for p in prefix],['q0','q1','q3'])
        self.assertTrue(prefix[0]['background'])
        self.assertFalse(prefix[1]['background'])
        self.assertTrue(prefix[2]['background'])
        self.assertNotIn('q3',items['P2']['eligible_refs'])
        self.assertNotIn('q0',items['P2']['eligible_refs'])
        self.assertNotIn('AI执行长总结',json.dumps(items,ensure_ascii=False))

    def test_actual_defect_requires_four_responsibility_facts(self):
        cell=self.cell();cell['negative']=dict(known_at_time='当时已看到错误',person_control='本人决定采用',
            feasible_alternative='可以保留结论',actual_gap='仍将错误结论交付')
        with self.assertRaisesRegex(ValueError,'正负判断冲突'):resolve('J3',cell)
        cell['band']='necessary_gap'
        self.assertEqual(resolve('J3',cell)['band'],'necessary_gap')
        cell['negative']['person_control']=' '
        with self.assertRaisesRegex(ValueError,'四项归责'):resolve('J3',cell)

    def test_dependency_keeps_next_action_as_background_without_scoring_it_again(self):
        transcript=dict(session='task',records=[dict(id='u',role='user',passages=[
            dict(id='q1',text='账号已登录，'),dict(id='q2',text='请继续核对订单字段。')])])
        with tempfile.TemporaryDirectory() as target:
            root=Path(target)
            (root/'task-reading.input.json').write_text(json.dumps(dict(input=json.dumps(dict(transcripts=[transcript])))))
            (root/'task-reading.json').write_text(json.dumps(dict(conversations=dict(task=dict(title='订单核对',goal='核对页面字段')))))
            (root/'behavior-allocation.json').write_text(json.dumps(dict(q1=dict(primary_item='M2'),q2=dict(primary_item='P1'))))
            items,_=material(root)
        item=items['M2'];self.assertEqual(item['eligible_refs'],['q1'])
        following=item['context'][0]['user_message'][0]['passages'][1:]
        self.assertEqual(following,[dict(id='q2',text='请继续核对订单字段。',background=True)])

    def test_confirmation_keeps_following_push_as_background(self):
        transcript=dict(session='dsh',records=[dict(id='u',role='user',passages=[
            dict(id='q1',text='安装并重启，'),dict(id='q2',text='确认没问题了，'),dict(id='q3',text='/push')])])
        with tempfile.TemporaryDirectory() as target:
            root=Path(target)
            (root/'task-reading.input.json').write_text(json.dumps(dict(input=json.dumps(dict(transcripts=[transcript])))))
            (root/'task-reading.json').write_text(json.dumps(dict(conversations=dict(dsh=dict(title='插件发布',goal='安装后验证并推送')))))
            (root/'behavior-allocation.json').write_text(json.dumps(dict(q1=dict(primary_item='M1'),q2=dict(primary_item='J2'),q3=dict(primary_item='E3'))))
            items,catalog=material(root)
        item=items['J2'];self.assertEqual(item['eligible_refs'],['q2'])
        self.assertEqual(item['evidence']['q2']['quote'],'安装并重启，确认没问题了，/push')
        self.assertEqual(item['evidence']['q2']['behavior_excerpt'],'确认没问题了，')
        self.assertEqual(catalog['q2']['quote'],'确认没问题了，')
        passages=item['context'][0]['user_message'][0]['passages']
        self.assertEqual(''.join(p['text'] for p in passages),'安装并重启，确认没问题了，/push')
        self.assertEqual([p['background'] for p in passages],[True,False,True])

    def test_foreign_and_duplicate_refs_are_rejected(self):
        schema=contract(dict(M3=dict(eligible_refs=['q1'])))
        cell=self.cell();jsonschema.validate(dict(M3=cell),schema)
        cell['refs']=['q2']
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(M3=cell),schema)
        cell['refs']=['q1','q1']
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(M3=cell),schema)
