"""v13证据边界回归，不用实现自身当语义正确性的证明。"""
import copy
import unittest
from scoring_pipeline import challenge_review as c


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        text='解释什么是API。'
        self.p={'period_id':'month','start':'2026-09-01T00:00:00+08:00','end':'2026-10-01T00:00:00+08:00'}
        self.u={'key':'a','user_id':'test','goal_id':'a','title':'测试','person_events':['e'],
                'events':[{'event':'e','event_id':'e','at':'2026-09-10T12:00:00+08:00','text':text,'actor_class':'human','human_spans':[[0,len(text)]]}]}
        self.ref={'event':'e','quote':text}
        self.ctx={'challenge':{'level':'routine'}}
        self.cells=[{'item':k,'status':'no_opportunity','reason':'本任务无该机会','evidence':[]} for k in c.ITEMS]
        for x in self.cells:
            if x['item'].startswith('M'):x['opportunity_complexity']={'status':'none','reason':'无独立机会'}
        self.cells[0].update(status='scored',verdict='complete',assessment_stage='input',fact='概念解释对象及动作清楚',person_evidence=[self.ref],requirements=[{'requirement':'概念及解释动作明确','status':'met','assessment':'本人已明确','evidence':[self.ref]}],adjacent_reason='实际要求充分，不要求额外复杂化',depth={'level':'execution_acceptance','reason':'明确提问，无独立取舍','person_action':'提问','ai_role':'解释','evidence':[self.ref]})
    def row(self):return c.validate_period(self.u,self.p,{'period_id':'month','cells':self.cells},self.ctx)
    def test_simple_full_quality_not_depth_bonus(self):
        x=self.row()['cells'][0];self.assertEqual(x['band'],100);self.assertEqual(x['depth']['level'],'execution_acceptance')
    def test_unknown_is_not_imputed(self):
        self.cells[0]={'item':'P1','status':'unknown','reason':'前文缺失','missing':{'type':'missing_source','needed':'原要求','why_changes_judgment':'决定目标'},'evidence':[]}
        self.assertIsNone(self.row()['cells'][0]['band'])
    def test_full_quality_cannot_ignore_gap(self):
        self.cells[0]['requirements'][0]['status']='gap'
        with self.assertRaises(ValueError):self.row()
    def test_below_full_requires_concrete_gap(self):
        self.cells[0]['verdict']='partial'
        with self.assertRaises(ValueError):self.row()
    def test_gap_requires_personal_responsibility(self):
        self.cells[0]['verdict']='necessary_gap';self.cells[0]['requirements'][0]['status']='gap';self.cells[0]['evidence']=[self.ref]
        with self.assertRaises(ValueError):self.row()
    def test_ai_cannot_be_person(self):
        self.u['events'][0]['actor_class']='assistant'
        with self.assertRaises(ValueError):self.row()
    def test_future_is_not_current_evidence(self):
        self.u['events'][0]['at']='2026-10-01T12:00:00+08:00'
        with self.assertRaises(ValueError):self.row()
    def test_other_task_person_cannot_be_credited(self):
        self.u['person_events']=[]
        with self.assertRaises(ValueError):self.row()
    def test_complex_task_without_personal_depth_not_mastery(self):
        x=copy.deepcopy(self.cells[0]);x.update(item='M1',independent_method='选择方法',opportunity_complexity={'status':'confirmed','reason':'有挑战','person_handling':'接受','challenge_addressed':'整合','evidence':[self.ref]})
        self.cells[3]=x;self.cells[0]['status']='no_opportunity';self.cells[0].pop('verdict');self.ctx['challenge']['level']='complex'
        with self.assertRaises(ValueError):self.row()
    def test_mature_method_can_support_challenge_handling(self):
        x=copy.deepcopy(self.cells[0]);x.update(item='M1',independent_method='使用成熟方案组织整体迁移',opportunity_complexity={'status':'confirmed','reason':'本人整合','person_handling':'安排迁移依赖','challenge_addressed':'跨模块兼容','evidence':[self.ref]})
        x['depth']['level']='integrative_direction';self.cells[3]=x;self.cells[0]['status']='no_opportunity';self.cells[0].pop('verdict');self.ctx['challenge']['level']='complex'
        self.assertEqual(self.row()['cells'][3]['band'],100)
    def test_false_reference_rejected(self):
        self.ref['quote']='不存在的原话'
        with self.assertRaises(ValueError):self.row()
    def test_related_must_end_at_scored_item(self):
        self.cells[1].update(status='related_only',primary_item='P3')
        with self.assertRaises(ValueError):self.row()


if __name__=='__main__':unittest.main()
