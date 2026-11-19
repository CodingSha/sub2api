"""原文编号不能越界，引用归属不能借目录或AI回复冒充本人。"""
import tempfile
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import jsonschema
from daily_conversation_pilot import compact_contract,evaluate,lean_material,apply_verification,verification_contract,fact_contract,LEAN_INSTRUCTIONS
from daily_pilot import RUBRIC

class ConversationPilotTests(unittest.TestCase):
    def test_fact_reader_cannot_cite_ai_record_as_user_action(self):
        transcripts=[dict(session='s',records=[dict(id='u',role='user',text='计算一下。'),dict(id='a',role='assistant',text='我会使用表格技能。')])]
        packed,catalog,_=lean_material(transcripts);schema=fact_contract(packed,catalog)
        value=dict(conversations=dict(s=dict(title='计算',goal='计算函数',user_actions=[dict(refs=['q1'],action='委派计算')],ai_context=[dict(record_id='a',action='AI自行选技能')],relationships='无已知关系')))
        jsonschema.validate(value,schema)
        value['conversations']['s']['user_actions'][0]['refs']=['a']
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(value,schema)

    def test_verification_applies_structured_change_without_default_score(self):
        _,_,schema=lean_material([dict(session='s',records=[dict(id='u',role='user',text='请计算。')])])
        original=dict(score=40,basis='原错误扣分',higher_band='原升档说明',refs=['q1'])
        candidate=dict(needs_records=[],assessment=dict(summary='原说明',strengths='优点',improvements='建议',items={c:dict(original) for c in RUBRIC['items']}))
        audit=dict(needs_records=[],summary='新说明',strengths='真实优点',improvements='具体建议',items={c:dict(decision='keep') for c in RUBRIC['items']})
        audit['items']['J2']=dict(decision='replace',score=None,basis='没有实际采用错误结论的依据，不据此扣分。',higher_band='本次不计分。',refs=[])
        jsonschema.validate(audit,verification_contract(schema))
        result=apply_verification(candidate,audit,schema)
        self.assertIsNone(result['assessment']['items']['J2']['score'])
        self.assertEqual(result['assessment']['items']['P1'],original)
        self.assertEqual(candidate['assessment']['items']['J2']['score'],40)
        audit['items']['J2']['score']=71
        with self.assertRaises(jsonschema.ValidationError):apply_verification(candidate,audit,schema)

    def test_verification_rejects_changed_material_before_sending(self):
        user=dict(id='u',role='user',text='本次用户的原始任务。',truncated=False)
        with tempfile.TemporaryDirectory() as target,patch('daily_conversation_pilot.call') as send:
            root=Path(target);prior=root/'prior';prior.mkdir()
            (prior/'conversation-0.input.json').write_text(json.dumps(dict(input=json.dumps(dict(transcripts='别人的材料')))))
            with self.assertRaisesRegex(ValueError,'冻结材料不一致'):
                evaluate([dict(session='s',records=[user])],{'u':user},None,root,'2026-11-05',numeric=True,verify_from=prior)
            send.assert_not_called()

    def test_lean_material_preserves_full_text_and_excludes_ai_citations(self):
        original='第一条要求。第二条约束！\n第三条；未完的内容'
        transcripts=[dict(session='s',records=[dict(id='u',role='user',text=original),dict(id='a',role='assistant',text='AI自检成功。')])]
        packed,catalog,schema=lean_material(transcripts)
        self.assertEqual(''.join(p['text'] for p in packed[0]['records'][0]['passages']),original)
        self.assertTrue(all(original[q['char_start']:q['char_start']+len(q['quote'])]==q['quote'] for q in catalog.values()))
        self.assertTrue(all(q['record_id']=='u' for q in catalog.values()))
        item=dict(score=80,basis='具体说明',higher_band='下一档差距',refs=['q1'])
        result=dict(needs_records=[],assessment=dict(summary='总结',strengths='优点',improvements='建议',items={c:dict(item) for c in RUBRIC['items']}))
        jsonschema.validate(result,schema)
        result['assessment']['items']['P1']['refs']=['a']
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(result,schema)

    def test_numeric_uses_single_contract_and_reads_only_requested_tool(self):
        user=dict(id='u',role='user',text='请核对这个结果。',truncated=False)
        tool=dict(id='t',role='tool',text='具体工具结果',truncated=False)
        selected=[dict(session='s',records=[user,tool])];records={r['id']:r for r in (user,tool)}
        item=dict(score=None,basis='无独立判断依据',higher_band='本项不计分',refs=[])
        result=dict(needs_records=[],assessment=dict(summary='总结',strengths='优点',improvements='建议',items={c:dict(item) for c in RUBRIC['items']}))
        with tempfile.TemporaryDirectory() as target,patch('daily_conversation_pilot.call',side_effect=[dict(needs_records=['t'],assessment=None),result]) as send:
            evaluate(selected,records,None,Path(target),'2026-11-05',numeric=True)
            first,second=send.call_args_list
            self.assertEqual(first.args[5],LEAN_INSTRUCTIONS)
            self.assertNotIn('quote_catalog',first.args[3])
            self.assertEqual(first.args[3]['read_records'],[])
            self.assertEqual(second.args[3]['read_records'],[tool])

    def test_catalog_covers_original_and_rejects_unknown_reference(self):
        original='完整用户表达。'*50
        catalog,schema=compact_contract([dict(records=[dict(id='u1',role='user',text=original)])])
        self.assertEqual(''.join(q['quote'] for q in catalog.values()),original)
        cell=dict(code='P1',state='observed',verdict='good',basis='明确目标',higher_band='验收条件尚可细化',refs=[dict(id='q999',origin='personal')])
        value=dict(needs_records=[],assessment=dict(summary='说明',strengths='优点',improvements='建议',items=[cell]*12))
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(value,schema)

    def test_assistant_and_unread_tool_cannot_be_personal_quote(self):
        user=dict(id='u1',role='user',text='请计算插值。')
        assistant=dict(id='a1',role='assistant',text='已计算完成。')
        tool=dict(id='t1',role='tool',text='工具正文',truncated=False)
        selected=[dict(session='s1',records=[user,assistant,tool])];records={r['id']:r for r in (user,assistant,tool)}
        value=dict(needs_records=[],assessment=dict(items=[dict(quotes=[dict(record_id='a1',quote='已计算完成。',origin='personal')])]))
        with tempfile.TemporaryDirectory() as target,patch('daily_conversation_pilot.call',return_value=value):
            with self.assertRaises(ValueError):evaluate(selected,records,None,Path(target),'2026-11-05')
        value['assessment']['items'][0]['quotes']=[dict(record_id='t1',quote='工具正文',origin='context')]
        with tempfile.TemporaryDirectory() as target,patch('daily_conversation_pilot.call',return_value=value):
            with self.assertRaises(ValueError):evaluate(selected,records,None,Path(target),'2026-11-05')

if __name__=='__main__':unittest.main()
