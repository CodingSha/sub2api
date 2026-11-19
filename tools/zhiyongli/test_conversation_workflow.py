"""任务阅读必须覆盖本人原话，工作流证据不得跨会话借用。"""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import jsonschema
from conversation_workflow import reading_schema,assessment_schema,check_workflows,convert,with_task_context
from daily_conversation_pilot import lean_material,evaluate
from daily_pilot import RUBRIC


class WorkflowTests(unittest.TestCase):
    def test_context_uses_delivered_originals_in_source_order_with_excerpt_limit(self):
        selected=[dict(session='s',records=[dict(id='u',role='user',text='先安装，确认后推送。'),
            dict(id='t',role='tool',text='调用及结果正文很长'),dict(id='a',role='assistant',text='尚未完成检查')])]
        data=dict(transcripts=[dict(session='s',records=[
            dict(id='u',role='user',passages=[dict(text='先安装，'),dict(text='确认后推送。')]),
            dict(id='a',role='assistant',text='尚未完成检查')])],
            workflow_records=[dict(id='t',session='s',text='调用及结果',excerpt=True,skills=['push'],mcp=[])])
        presentation=dict(conversations=dict(s=dict(title='安装任务',goal='检查后推送')))
        result=with_task_context(presentation,data,selected)
        records=result['conversations']['s']['records']
        self.assertEqual([r['id'] for r in records],['u','t','a'])
        self.assertEqual(records[0]['text'],'先安装，确认后推送。')
        self.assertEqual(records[1]['text'],'调用及结果')
        self.assertTrue(records[1]['excerpt'])
        from runtime import digest
        self.assertEqual(records[1]['source_hash'],digest('调用及结果正文很长'))
        self.assertEqual(records[2]['role'],'assistant')
        self.assertNotIn('records',presentation['conversations']['s'])
        data['workflow_records'][0]['text']='伪造工具结果'
        with self.assertRaisesRegex(ValueError,'原文不一致'):with_task_context(presentation,data,selected)
        data['workflow_records'][0]['text']='调用及结果'
        selected[0]['records'][0]['role']='assistant'
        with self.assertRaisesRegex(ValueError,'角色不一致'):with_task_context(presentation,data,selected)

    def test_context_refuses_unknown_or_cross_session_record(self):
        selected=[dict(session='s',records=[dict(id='u',role='user',text='只查询')])]
        data=dict(transcripts=[dict(session='s',records=[dict(id='other',role='user',passages=[dict(text='其他用户原文')])])],workflow_records=[])
        with self.assertRaisesRegex(ValueError,'其他会话'):with_task_context(dict(conversations=dict(s={})),data,selected)

    def setUp(self):
        self.transcripts,self.catalog,_=lean_material([
            dict(session='s',records=[dict(id='u',role='user',text='先不要重启。'),dict(id='a',role='assistant',text='收到。')]),
            dict(session='other',records=[dict(id='u2',role='user',text='/push')])])
        self.tools=[dict(id='t',session='s'),dict(id='t2',session='other')]

    def test_clause_ids_preserve_original_and_separate_check_from_push(self):
        original='安装并重启，确认没问题了，/push'
        packed,catalog,_=lean_material([dict(session='s',records=[dict(id='u',role='user',text=original)])],atomic_clauses=True)
        self.assertEqual(''.join(q['quote'] for q in catalog.values()),original)
        self.assertEqual([q['quote'] for q in catalog.values()],['安装并重启，','确认没问题了，','/push'])
        self.assertEqual(''.join(q['text'] for q in packed[0]['records'][0]['passages']),original)

    def test_reading_requires_each_user_record_and_same_session_tool(self):
        schema=reading_schema(self.transcripts,self.catalog,self.tools)
        case=dict(title='任务',goal='目标',outcome='AI回复收到',user_actions=dict(u=dict(action='暂缓重启',refs=['q1'])),workflow=[dict(name='工具',purpose='检查',selection='AI自主',user_refs=[],tool_refs=['t'])])
        other=dict(case,user_actions=dict(u2=dict(action='选择提交工作流',refs=['q2'])),workflow=[])
        value=dict(conversations=dict(s=case,other=other))
        jsonschema.validate(value,schema)
        case['user_actions']={}
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(value,schema)
        case['user_actions']=dict(u=dict(action='暂缓重启',refs=['q1']))
        case['workflow'][0]['tool_refs']=['t2']
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(value,schema)

    def test_original_band_mapping_and_null_have_single_exit(self):
        cell=dict(band='good',behavior='选择方法',basis='与目标对应',next_band='下一档',refs=['q1'],tool_refs=[])
        value=dict(summary='概述',strengths='优点',improvements='建议',items={c:dict(cell) for c in RUBRIC['items']})
        value['items']['J3']=dict(cell,band=None,next_band='',refs=[])
        jsonschema.validate(value,assessment_schema(self.catalog,self.tools))
        converted=convert(value,self.catalog)
        self.assertEqual(converted['items'][0]['verdict'],'good')
        self.assertEqual(next(c for c in converted['items'] if c['code']=='J3')['verdict'],'unresolved')
        value['items']['P1']['band']='81'
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(value,assessment_schema(self.catalog,self.tools))

    def test_scorer_receives_user_originals_without_ai_execution_transcript(self):
        user=dict(id='u',role='user',text='先不要重启。',truncated=False)
        ai=dict(id='a',role='assistant',text='AI自述运行了87项检查。',truncated=False)
        selected=[dict(session='s',records=[user,ai])]
        reading=dict(conversations=dict(s=dict(title='暂缓重启',goal='保留正在运行的任务',outcome='AI回复收到',workflow=[])))
        cell=dict(band=None,behavior='无独立行为',basis='本项不计分',next_band='',refs=[],tool_refs=[])
        assessment=dict(summary='用户暂缓重启',strengths='保护运行中任务',improvements='可明确恢复条件',items={c:dict(cell) for c in RUBRIC['items']})
        allocation=dict(q1=dict(primary_item='M3',why='新事实调整'))
        judgments=[dict(assessment,items={c:v for c,v in assessment['items'].items() if c.startswith(d)}) for d in 'PMJE']
        with tempfile.TemporaryDirectory() as target,patch('daily_conversation_pilot.call',side_effect=[reading,allocation,*judgments,dict(s=[])]) as send:
            evaluate(selected,{'u':user,'a':ai},None,Path(target),'2026-11-05',numeric=True,workflows=True)
        read_data=send.call_args_list[0].args[3]
        score_data=send.call_args_list[2].args[3]
        self.assertEqual(read_data['transcripts'][0]['records'][1]['text'],ai['text'])
        self.assertEqual(score_data['person_messages'][0]['records'][0]['passages'][0]['text'],user['text'])
        self.assertEqual(len(score_data['person_messages'][0]['records']),1)
        self.assertNotIn('transcripts',score_data)
        self.assertIn('evaluated_person',score_data)

    def test_workflow_name_and_explicit_choice_match_actual_sources(self):
        tools=[dict(id='t',session='s',skills=['push'],mcp=[]),dict(id='t2',session='s',skills=['gary-ui'],mcp=[])]
        catalog=dict(q1=dict(quote='/push'),q2=dict(quote='请安装客户端'))
        usage=dict(name='push',selection='用户明确选择',purpose='提交',user_refs=['q1'],tool_refs=['t'])
        check_workflows(dict(s=[usage]),catalog,tools)
        usage['user_refs']=['q2']
        with self.assertRaisesRegex(ValueError,'明确选择'):check_workflows(dict(s=[usage]),catalog,tools)
        usage['user_refs']=['q1'];usage['tool_refs']=['t2']
        with self.assertRaisesRegex(ValueError,'实际调用'):check_workflows(dict(s=[usage]),catalog,tools)

    def test_same_passage_cannot_score_two_items_or_fill_unallocated_item(self):
        allocation=dict(q1=dict(primary_item='M3',why='调整'),q2=dict(primary_item='E3',why='复用'))
        schema=assessment_schema(self.catalog,self.tools,allocation)
        cell=dict(band=None,behavior='无独立行为',basis='本项不计分',next_band='',refs=[],tool_refs=[])
        value=dict(summary='概述',strengths='优点',improvements='建议',items={c:dict(cell) for c in RUBRIC['items']})
        value['items']['M3']=dict(cell,band='good',next_band='更充分的作用依据',refs=['q1'])
        jsonschema.validate(value,schema)
        value['items']['E3']=dict(cell,band='good',next_band='更充分的作用依据',refs=['q1'])
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(value,schema)
        value['items']['E3']=dict(cell)
        value['items']['J2']=dict(cell,band='reasonable',next_band='方法具体化',refs=['q1'])
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(value,schema)
