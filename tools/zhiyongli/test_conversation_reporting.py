"""验证真实产品合同的日周算分边界与五日来源身份隔离。"""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from conversation_reporting import scored, weekly_material, evaluate_week, evaluate_day
from daily_behaviors import catalog
from runtime import digest
from task_evidence_flow import no_observation
from task_review_policy import RUBRIC
from workday_scope import window,daily_window


def material():
    user=dict(id='u',role='user',text='请对比两份报价并解释差异。',at='2026-11-16T09:00:00+08:00',task_id='t')
    task=dict(id='t',title='报价对比',goal='解释报价差异',workflow_index=[],records=[user])
    return dict(tasks=[task],originals=dict(u=user))


def judgment():
    items={code:no_observation(code,'本次没有本项可独立评分的行为。') for code in RUBRIC['items']}
    for cell in items.values():
        cell['basis']=cell.pop('reason');cell.pop('item_code');cell.pop('context_refs');cell.pop('e3_claim');cell.pop('limitations')
    claim='指定比较对象和交付';identity='b'+digest(['u',claim])[:20]
    items['P1'].update(band='good',state='observed',basis='你指定了比较对象及差异说明。',next_band='本次材料未证明更高档质量。',
        evidence=[dict(record_id='u',quote=material()['originals']['u']['text'],origin='personal',behavior_id=identity)])
    return dict(needs_records=[],items=items,summary='根据比较目标开展任务。',
        behaviors=catalog([dict(record_id='u',claim=claim,primary_item='P1',quote=material()['originals']['u']['text'])],material()['originals']),
        task_summaries=dict(t=dict(title='报价对比',goal='解释报价差异',outcome='等待执行',workflow=[],user_actions=[])))


class ConversationReportingTests(unittest.TestCase):
    def test_product_comparison_only_normalizes_documented_empty_tool_index(self):
        from conversation_reporting import canonical_daily_review
        source=dict(conversations=dict(t=dict(tool_index=[],records=[dict(id='u',text='本人原话')],workflow=[])),request_digest='fixed')
        before=copy.deepcopy(source);stored=copy.deepcopy(source);stored['conversations']['t'].pop('tool_index')
        self.assertEqual(canonical_daily_review(source),canonical_daily_review(stored));self.assertEqual(source,before)
        for changed in [dict(tool_index=[dict(name='skill',record_id='tool',chars=10)]),dict(tool_index=None),dict(records=[dict(id='u',text='原文被改')])]:
            different=copy.deepcopy(stored);different['conversations']['t'].update(changed)
            self.assertNotEqual(canonical_daily_review(source),canonical_daily_review(different))
        changed=copy.deepcopy(stored);changed['request_digest']='changed'
        self.assertNotEqual(canonical_daily_review(source),canonical_daily_review(changed))

    def test_week_view_accepts_product_omission_without_inventing_tool_directory(self):
        from conversation_reporting import published_view
        task=dict(title='任务',goal='目标',outcome='',workflow=[],records=[dict(id='u',role='user',text='本人原话')])
        day=dict(day='2026-11-16',material=dict(tasks=[dict(id='t')]),review=dict(conversations=dict(t=task),source_labels=dict(u='任务'),source_sessions=dict(u='t')))
        before=copy.deepcopy(day);view=published_view([day]);self.assertEqual(day,before)
        actual=next(iter(view['conversations'].values()));self.assertNotIn('tool_index',actual);self.assertEqual(actual['records'][0]['text'],'本人原话')

    def background(self):
        m=material();m['originals']['u']['text']="<hindsight_page_refresh>\nReminder — this repo's Hindsight tools are available; use the registered tools.\n</hindsight_page_refresh>"
        return m

    def test_certified_background_exports_observation_without_model_and_rejects_tampering(self):
        from conversation_provenance import verify
        m=self.background();day='2026-11-16';packet=dict(user_id=7,user_email='fixture@example.test',workday_scope=daily_window(day))
        client=SimpleNamespace(config=SimpleNamespace(model='gpt-6.1-sol',responses_url='fixture'))
        with TemporaryDirectory() as folder,patch('conversation_reporting.checked_call',side_effect=AssertionError('纯背景不得请求模型')) as sender:
            root=Path(folder);bundle=evaluate_day(packet,day,root,client,m);report=bundle['reports'][0]
            sender.assert_not_called();self.assertEqual(report['score_status'],'observation_only');self.assertIsNone(report['score'])
            self.assertTrue(all(item['exact'] is None and not item['evidence'] for item in report['items']))
            rows=report['daily_review']['conversations']['t']['records'];self.assertEqual(rows[0]['text'],m['originals']['u']['text']);self.assertEqual(rows[0]['role'],'context')
            self.assertEqual(verify(root,m['tasks'],m['originals'])['origin'],'verified_background_source')
            path=root/'conversation-result.json';saved=json.loads(path.read_text());saved['value']['items']['P1']['band']='good';path.write_text(json.dumps(saved))
            with self.assertRaisesRegex(ValueError,'纯背景结论'):verify(root,m['tasks'],m['originals'])

    def test_appended_personal_request_is_not_silently_exported_as_background(self):
        m=self.background();m['originals']['u']['text']+='\n我现在要求比较这两份报价。'
        day='2026-11-16';packet=dict(user_id=7,user_email='fixture@example.test',workday_scope=daily_window(day))
        client=SimpleNamespace(config=SimpleNamespace(model='gpt-6.1-sol',responses_url='fixture'))
        with TemporaryDirectory() as folder,patch('conversation_reporting.checked_call',side_effect=RuntimeError('测试模型未决')) as sender:
            with self.assertRaisesRegex(RuntimeError,'未决'):evaluate_day(packet,day,Path(folder),client,m)
            self.assertTrue(sender.called);self.assertFalse((Path(folder)/'report.json').exists())

    def test_partial_day_keeps_missing_dimensions_and_exact_evidence(self):
        m=material();result=scored(dict(user_id=7,user_email='fixture@example.test'),'2026-11-16',judgment(),m['tasks'],m['originals'],[])
        self.assertEqual(result['score'],'80.0')
        self.assertEqual([d['score'] for d in result['dimensions']],['80.0',None,None,None])
        self.assertEqual(result['items'][0]['evidence'][0]['quote'],m['originals']['u']['text'])

    def test_week_does_not_multiply_partial_day_score_or_fill_missing_dimensions(self):
        m=material();r=scored(dict(user_id=7,user_email='fixture@example.test'),'2026-11-16',judgment(),m['tasks'],m['originals'],[])
        from weekly_scores import apply
        apply(r)
        self.assertEqual(r['score'],'16.0')
        self.assertEqual([d['score'] for d in r['dimensions']],['80.0',None,None,None])

    def test_chinese_adjacent_reference_is_rendered_verbatim_without_changing_judgment(self):
        m=material();value=judgment();value['items']['P1']['next_band']='q1只是提出目标。';before=copy.deepcopy(value)
        r=scored(dict(user_id=7,user_email='fixture@example.test'),'2026-11-16',value,m['tasks'],m['originals'],[],{'q1':m['originals']['u']['text']})
        self.assertIn('“'+m['originals']['u']['text']+'”只是提出目标。',r['items'][0]['reason'])
        self.assertNotIn('q1',r['items'][0]['reason']);self.assertEqual(r['score'],'80.0');self.assertEqual(value,before)
        value['items']['P1']['next_band']='q999只是提出目标。'
        with self.assertRaisesRegex(ValueError,'未交付'):
            scored(dict(user_id=7,user_email='fixture@example.test'),'2026-11-16',value,m['tasks'],m['originals'],[],{'q1':'原话'})

    def test_duplicate_personal_records_across_days_are_rejected(self):
        days=[dict(day=day,material=material()) for day in ['2026-11-16','2026-11-17']]
        with self.assertRaisesRegex(ValueError,'重复'):weekly_material(days,[d['day'] for d in days])

    def test_empty_week_still_has_zero_score_and_never_calls_model(self):
        scope=window('2026-11-16','2026-11-23')
        job=dict(user_id=7,user_email='fixture@example.test',period_start='2026-11-16',period_end='2026-11-23',workday_scope=scope)
        days=[dict(user_id=7,user_email=job['user_email'],day=day,score_status='no_activity',material=dict(tasks=[],originals={}),
            review=dict(conversations={},source_labels={},source_sessions={})) for day in scope['working_dates']]
        sources=[dict(report_id=i+1,period_start=d['day'],source_digest='a'*64,request_digest='b'*64) for i,d in enumerate(days)]
        with TemporaryDirectory() as folder,patch('conversation_reporting.assess',side_effect=AssertionError('空周不能调用模型')):
            result=evaluate_week(job,Path(folder),SimpleNamespace(config=SimpleNamespace(model='deepseek-v4.1-flash-local')),days,sources)['reports'][0]
        self.assertEqual(result['score'],'0.0')
        bad=copy.deepcopy(days);bad[0]['user_id']=9
        with TemporaryDirectory() as folder,self.assertRaisesRegex(ValueError,'身份'):
            evaluate_week(job,Path(folder),SimpleNamespace(),bad,sources)


if __name__=='__main__':unittest.main()
