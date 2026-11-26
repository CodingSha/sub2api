import copy
import unittest
from evidence_review import validate,cross_item_audit
from task_evidence_flow import no_observation
from test_task_card import task


def observed(code='E3'):
    return dict(item_code=code,state='observed',band='good',reason='选用与发布目标适配的既有流程，省去重复描述步骤。',
        next_band='尚无本次信息保全与免重复的进一步具体效果。',
        evidence=[dict(record_id='u1',quote='用发布Skill上线',claim='选择既有发布流程',origin='personal',primary_item=code)],
        context_refs=['a1'],negative=None,e3_claim=dict(kind='skill',asset_name='发布Skill',selection_refs=['u1'],
        execution_refs=['a1'],effect_refs=['a1'],effect='既定发布流程承担重复步骤'),limitations=['AI反馈不代表外部验收'])


class EvidenceReviewTests(unittest.TestCase):
    def setUp(self):self.rows=task()['records']
    def test_ai_execution_context_does_not_require_user_claiming_the_execution(self):
        self.assertEqual(validate(observed(),'E3',self.rows)['band'],'good')
    def test_ai_selection_and_foreign_evidence_fail(self):
        review=observed();review['e3_claim']['selection_refs']=['a1']
        with self.assertRaisesRegex(ValueError,'选择'):validate(review,'E3',self.rows)
        review=observed();review['evidence'][0]['quote']='不存在的原话'
        with self.assertRaisesRegex(ValueError,'逐字'):validate(review,'E3',self.rows)
    def test_missing_state_never_gains_score_or_negative_responsibility(self):
        review=no_observation('J2','没有本项独立行为')
        validate(review,'J2',self.rows)
        review['band']='partial'
        with self.assertRaisesRegex(ValueError,'无分状态'):validate(review,'J2',self.rows)
    def test_overlap_requires_review_and_never_silently_deletes_score(self):
        first=observed();second=copy.deepcopy(first);second['item_code']='M1';second['evidence'][0]['primary_item']='M1'
        self.assertEqual(len(cross_item_audit([first,second],{r['id']:r for r in self.rows})),1)
        self.assertEqual(first['band'],'good')
