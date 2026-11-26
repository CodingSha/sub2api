import copy
import unittest
import jsonschema
from task_card import schema,validate


def task(identity='task-1'):
    return dict(id=identity,records=[dict(id='u1',role='user',text='用发布Skill上线，然后核对页面。'),
        dict(id='a1',role='assistant',text='我会按发布流程执行，并保留待核项。')])


def card(identity='task-1'):
    claim=dict(text='用户选择发布流程并安排上线后核对页面。',evidence_refs=['u1'])
    return dict(task_id=identity,goal='发布并核对页面',evidence_refs=['u1'],deliverables=[copy.deepcopy(claim)],
        phase=[dict(text='处于发布安排阶段',evidence_refs=['u1'])],user_actions=[copy.deepcopy(claim)],
        ai_actions=[dict(text='AI计划执行流程',evidence_refs=['a1'])],constraints=[],feedback=[],
        open_questions=[dict(text='尚无外部验收',evidence_refs=['a1'])],capacity_complete=True)


class TaskCardTests(unittest.TestCase):
    def test_no_score_and_goal_stage_open_questions_are_preserved(self):
        value=card();self.assertEqual(validate(value,task()),value)
        value['score']=100
        with self.assertRaises(jsonschema.ValidationError):validate(value,task())
    def test_ai_cannot_be_user_action(self):
        value=card();value['user_actions'][0]['evidence_refs']=['a1']
        with self.assertRaisesRegex(ValueError,'混淆'):validate(value,task())
    def test_foreign_source_and_capacity_loss_are_rejected(self):
        value=card();value['evidence_refs']=['foreign']
        with self.assertRaises(jsonschema.ValidationError):validate(value,task())
        value=card();value['capacity_complete']=False
        with self.assertRaisesRegex(ValueError,'容量'):validate(value,task())

    def test_candidate_e3_scope_is_separate_from_existing_rubric(self):
        from runtime import RUBRIC as base
        from task_review_policy import RUBRIC as candidate
        self.assertNotIn('e3_scope_version',base)
        self.assertIn('普通文件',candidate['performance_anchors']['E3']['reasonable'])
        self.assertEqual(candidate['performance_values'],base['performance_values'])
        self.assertEqual(candidate['dimension_weights'],base['dimension_weights'])
