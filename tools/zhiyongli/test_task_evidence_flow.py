import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from evidence_requests import ITEMS
from task_evidence_flow import run,no_observation
from test_task_card import card,task


class FlowTests(unittest.TestCase):
    def client(self):return SimpleNamespace(config=SimpleNamespace(model='fixed',responses_url='company'),output_token_limit=6000)
    def sender(self, fail_first=False):
        def send(client,root,name,data,schema,instructions):
            if name=='task-card':
                if fail_first and data['task_id']=='bad':raise ValueError('坏任务，保持其他任务可执行')
                return card(data['task_id'])
            if name=='evidence-plan':return {code:dict(reason='逐项回读核对',requests=[dict(record_id='u1',start=0,chars=25)],queries=[]) for code in ITEMS}
            if name=='audit':return dict(issues=[])
            code=data['item_code']
            return dict(result=no_observation(code,'测试夹具不产生真实人员分数'),requests=[],queries=[])
        return Mock(side_effect=send)
    def test_task_card_is_new_call_before_evidence_plan_and_twelve_individual_judgments(self):
        with tempfile.TemporaryDirectory() as directory:
            t=task();sender=self.sender();result=run(self.client(),Path(directory),[t],{r['id']:r for r in t['records']},sender)
            names=[call.args[2] for call in sender.call_args_list]
            self.assertEqual(names[:2],['task-card','evidence-plan']);self.assertEqual(names.count('judgment-0'),12)
            self.assertEqual(result['status'],'candidate');self.assertFalse(result['semantic_acceptance'])
            self.assertFalse(result['production_changed'])
    def test_bad_task_is_recorded_and_next_task_is_processed(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks=[task('bad'),task('good')];sender=self.sender(fail_first=True)
            result=run(self.client(),Path(directory),tasks,{r['id']:r for r in tasks[0]['records']},sender)
            self.assertEqual(len(result['failures']),1);self.assertEqual(len(result['results']),1)
            self.assertEqual(result['results'][0]['task_id'],'good');self.assertEqual(result['status'],'incomplete')
    def test_source_change_cannot_reuse_old_receipts_or_results(self):
        with tempfile.TemporaryDirectory() as directory:
            t=task();source={r['id']:r for r in t['records']};run(self.client(),Path(directory),[t],source,self.sender())
            changed=copy.deepcopy(t);changed['records'][0]['text']='新原话'
            with self.assertRaises(ValueError):run(self.client(),Path(directory),[changed],source,self.sender())

    def test_network_interruption_retains_metrics_and_incomplete_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            t=task();sender=Mock(side_effect=RuntimeError('网络终态未知'))
            with self.assertRaises(RuntimeError):
                run(self.client(),Path(directory),[t],{r['id']:r for r in t['records']},sender)
            metrics=json.loads((Path(directory)/'metrics.json').read_text())
            self.assertTrue(metrics['interrupted'])
            self.assertEqual(metrics['completed'],0)
            self.assertEqual(json.loads((Path(directory)/'result.json').read_text())['status'],'incomplete')
