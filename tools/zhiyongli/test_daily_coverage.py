"""验证覆盖拆批保留原文、未读失败和明确采纳，不用合成结果冒充实际模型质量。"""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from daily_coverage import read_facts
from daily_assessment import workflow_selected_in_task


class CoverageTests(unittest.TestCase):
    def test_batched_read_keeps_whole_task_and_all_user_records(self):
        rows=[dict(id='u'+str(i),role='user',text='要求'+str(i)) for i in range(13)]
        task=dict(id='t',records=rows,workflow_index=[]);calls=[]
        def send(client,root,stage,data,schema,instructions):
            self.assertEqual(data['tasks'],[task]);calls.append(data['focus_user_refs'])
            return dict(task_summaries={'t':dict(title='任务',goal='处理要求',outcome='',workflow=[])},
                actions=[dict(record_id=ref,action='提出要求',quote=next(row['text'] for row in rows if row['id']==ref),
                    result_decision_quote='',selected_asset=[]) for ref in data['focus_user_refs']],no_action_refs=[])
        with TemporaryDirectory() as tmp:
            root=Path(tmp);result=read_facts(None,root,[task],{row['id']:row for row in rows},send)
            self.assertEqual([len(x) for x in calls],[6,6,1])
            self.assertEqual(len(result['actions']),13)
            self.assertEqual(json.loads((root/'coverage-read.json').read_text())['status'],'complete')

    def test_capacity_recursively_splits_and_never_accepts_truncation(self):
        rows=[dict(id='u'+str(i),role='user',text='要求'+str(i)) for i in range(2)]
        task=dict(id='t',records=rows,workflow_index=[]);stages=[]
        def send(client,root,stage,data,schema,instructions):
            stages.append(stage);refs=data['focus_user_refs'];count=40 if len(refs)>1 else 1
            return dict(task_summaries={'t':dict(title='任务',goal='处理要求',outcome='',workflow=[])},
                actions=[dict(record_id=refs[i%len(refs)],action='行为'+str(i),quote='要求'+refs[i%len(refs)][1:],result_decision_quote='',selected_asset=[]) for i in range(count)],no_action_refs=[])
        with TemporaryDirectory() as tmp:
            result=read_facts(None,Path(tmp),[task],{r['id']:r for r in rows},send)
            self.assertEqual(len(result['actions']),2)
            self.assertEqual(len(stages),3)

    def test_missing_records_fail_after_one_repair(self):
        row=dict(id='u',role='user',text='执行具体安排');task=dict(id='t',records=[row],workflow_index=[]);calls=[]
        def send(*args):
            calls.append(args[2]);return dict(task_summaries={'t':dict(title='任务',goal='处理要求',outcome='',workflow=[])},actions=[],no_action_refs=[])
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'覆盖不一致'):read_facts(None,Path(tmp),[task],{'u':row},send)
        self.assertEqual(len(calls),2)

    def test_quote_repair_copies_escaped_source_without_regenerating_actions(self):
        row=dict(id='u',role='user',text=r'请核对 https:\/\/example.test/a 路径。')
        task=dict(id='t',records=[row],workflow_index=[]);stages=[]
        def send(client,root,stage,data,schema,instructions):
            stages.append(stage)
            if stage.endswith('-quote-repair'):
                self.assertEqual(data['source_messages'],{'u':row['text']})
                self.assertEqual(set(data['problems']),{'0'})
                return dict(quotes={'0':row['text']})
            return dict(task_summaries={'t':dict(title='任务',goal='核对路径',outcome='',workflow=[])},
                actions=[dict(record_id='u',action='核对路径',quote='请核对 https://example.test/a 路径。',result_decision_quote='',selected_asset=[])],no_action_refs=[])
        with TemporaryDirectory() as tmp:
            result=read_facts(None,Path(tmp),[task],{'u':row},send)
            self.assertEqual(result['actions'][0]['quote'],row['text'])
            self.assertEqual(result['actions'][0]['action'],'核对路径')
        self.assertEqual(len(stages),3)

    def test_received_output_limit_splits_but_unknown_never_resends(self):
        from model_config import ModelError
        rows=[dict(id='u'+str(i),role='user',text='要求'+str(i)) for i in range(2)]
        task=dict(id='t',records=rows,workflow_index=[])
        for status in ['received','unknown']:
            calls=[]
            def send(client,root,stage,data,schema,instructions):
                calls.append(stage)
                if len(data['focus_user_refs'])==2:
                    (root/(stage+'.receipt.json')).write_text(json.dumps(dict(status=status,response=dict(status='incomplete',incomplete_details=dict(reason='max_output_tokens')))))
                    raise ModelError('response_not_completed')
                ref=data['focus_user_refs'][0]
                return dict(task_summaries={'t':dict(title='任务',goal='处理要求',outcome='',workflow=[])},
                    actions=[dict(record_id=ref,action='提出要求',quote=next(r['text'] for r in rows if r['id']==ref),result_decision_quote='',selected_asset=[])],no_action_refs=[])
            with TemporaryDirectory() as tmp:
                if status=='unknown':
                    with self.assertRaises(ModelError):read_facts(None,Path(tmp),[task],{r['id']:r for r in rows},send)
                    self.assertEqual(len(calls),1)
                else:
                    result=read_facts(None,Path(tmp),[task],{r['id']:r for r in rows},send)
                    self.assertEqual(len(result['actions']),2);self.assertEqual(len(calls),3)

    def test_failed_quote_copy_uses_exact_local_fragment(self):
        row=dict(id='u',role='user',text='请检查 a&#x20;b 的路径。')
        task=dict(id='t',records=[row],workflow_index=[]);stages=[]
        def send(client,root,stage,data,schema,instructions):
            stages.append(stage)
            if stage.endswith('-quote-locator'):
                self.assertEqual(data['source_fragments']['0']['0-0'],row['text'])
                return dict(locations={'0':'0-0'})
            if stage.endswith('-quote-repair'):return dict(quotes={'0':'请检查 a b 的路径。'})
            return dict(task_summaries={'t':dict(title='核查',goal='检查路径',outcome='',workflow=[])},
                actions=[dict(record_id='u',action='要求核查路径',quote='请检查 a b 的路径。',result_decision_quote='',selected_asset=[])],no_action_refs=[])
        with TemporaryDirectory() as tmp:
            result=read_facts(None,Path(tmp),[task],{'u':row},send)
            self.assertEqual(result['actions'][0]['quote'],row['text'])
        self.assertEqual(len(stages),4)

    def test_empty_extraction_checks_actual_outer_user_delegation(self):
        row=dict(id='u',role='user',text='请按JSON返回这段材料的摘要。')
        task=dict(id='t',records=[row],workflow_index=[]);stages=[]
        def send(client,root,stage,data,schema,instructions):
            stages.append(stage);confirm=stage.endswith('-no-action-confirm')
            return dict(task_summaries={'t':dict(title='摘要',goal='整理材料',outcome='',workflow=[])},
                actions=[dict(record_id='u',action='委派摘要整理',quote=row['text'],result_decision_quote='',selected_asset=[])] if confirm else [],
                no_action_refs=[] if confirm else ['u'])
        with TemporaryDirectory() as tmp:
            result=read_facts(None,Path(tmp),[task],{'u':row},send)
            self.assertEqual(len(result['actions']),1)
        self.assertEqual(len(stages),2)

    def test_single_record_output_limit_uses_one_explicit_capacity_retry(self):
        from model_config import ModelError
        row=dict(id='u',role='user',text='明确要求');task=dict(id='t',records=[row],workflow_index=[])
        client=SimpleNamespace(output_token_limit=7000);limits=[]
        def send(client,root,stage,data,schema,instructions):
            limits.append(client.output_token_limit)
            if not stage.endswith('-output-capacity'):
                (root/(stage+'.receipt.json')).write_text(json.dumps(dict(status='received',response=dict(status='incomplete',incomplete_details=dict(reason='max_output_tokens')))))
                raise ModelError('response_not_completed')
            return dict(task_summaries={'t':dict(title='任务',goal='处理要求',outcome='',workflow=[])},
                actions=[dict(record_id='u',action='提出要求',quote=row['text'],result_decision_quote='',selected_asset=[])],no_action_refs=[])
        with TemporaryDirectory() as tmp:read_facts(client,Path(tmp),[task],{'u':row},send)
        self.assertEqual(limits,[7000,12000]);self.assertEqual(client.output_token_limit,7000)

    def test_specific_adoption_is_selection_but_continue_or_autonomy_is_not(self):
        rows=[dict(id='a',role='assistant',text='我将使用 push 工作流提交已核改动。'),dict(id='u',role='user',text='按这个方案执行。')]
        self.assertTrue(workflow_selected_in_task('push',['u'],rows))
        for text in ['继续','知道了','你看着办']:
            self.assertFalse(workflow_selected_in_task('push',['u'],[rows[0],dict(rows[1],text=text)]))
        self.assertFalse(workflow_selected_in_task('push',['u'],[dict(rows[0],text='无需使用 push，先检查。'),rows[1]]))
        self.assertFalse(workflow_selected_in_task('another-skill',['u'],rows))
        self.assertFalse(workflow_selected_in_task('push',['u'],[rows[0],dict(id='u0',role='user',text='先别提交'),rows[1]]))

if __name__=='__main__':unittest.main()
