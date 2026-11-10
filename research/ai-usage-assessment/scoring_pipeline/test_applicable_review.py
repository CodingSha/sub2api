"""v10核心边界：简单充分、主体时点、去重权重与缺采保护。"""
import copy
import unittest
from fractions import Fraction
from scoring_pipeline import applicable_review as engine


class ApplicableTests(unittest.TestCase):
 def setUp(self):
  self.period={'period_id':'week-2026-09-07','start':'2026-09-07T00:00:00+08:00','end':'2026-09-14T00:00:00+08:00'}
  self.e={'event':'e1','event_id':'original','at':'2026-09-08T10:00:00+08:00','actor_class':'human','human_spans':[[0,8]],'text':'解释什么是接口。'}
  self.data={'user_id':'test','goal_id':'t1','title':'概念咨询','person_events':['e1'],'events':[self.e]}
  self.cells=[dict(item=i,status='no_opportunity',reason='简单概念咨询无此类独立处理机会',opportunity_complexity={'status':'none','reason':'无联合约束'})for i in engine.ITEMS]
  self.cells[0]=dict(item='P1',status='scored',verdict='complete',assessment_stage='input',fact='咨询对象与动作清楚',reason='简单咨询明确',person_evidence=[{'event':'e1','quote':self.e['text']}],evidence=[],requirements=[{'requirement':'当前咨询对象与动作','status':'met','assessment':'均明确','evidence':[{'event':'e1','quote':self.e['text']}]}],adaptation_basis='只要求解释概念，与当前任务匹配',adjacent_reason='适用目标要求均明确，无需人为补阶段')
 def row(self):return engine.validate_period(self.data,self.period,dict(period_id=self.period['period_id'],cells=self.cells))
 def test_simple_complete_does_not_require_followup(self):self.assertEqual(self.row()['cells'][0]['band'],100)
 def test_future_person_proof_rejected(self):
  self.e['at']='2026-09-15T10:00:00+08:00'
  with self.assertRaises(ValueError):self.row()
 def test_ai_not_person(self):
  self.e['actor_class']='assistant'
  with self.assertRaises(ValueError):self.row()
 def test_unknown_not_imputed(self):
  self.cells[0]=dict(item='P1',status='unknown',reason='主体存在争议',missing={'type':'attribution_conflict','needed':'输入者','why_changes_judgment':'决定个人归属'})
  r=self.row();s=engine.summarize_period([r]);self.assertIsNone(s['exact']);self.assertEqual(s['items']['P1']['unknown'],1)
 def test_related_cannot_terminate_unknown(self):
  self.cells[1]=dict(item='P2',status='related_only',reason='同一事实',primary_item='J1')
  with self.assertRaises(ValueError):self.row()
 def test_duplicate_parent_rejected(self):
  r=self.row()
  with self.assertRaises(ValueError):engine.summarize_period([r,r])
 def test_known_parent_equal_weight(self):
  a=self.row();b=copy.deepcopy(a);b['goal_id']='t2';b['cells'][0]['band']=60
  s=engine.summarize_period([a,b]);self.assertEqual(s['items']['P1']['exact'],'80')
 def test_incomplete_requirement_cannot_complete(self):
  self.cells[0]['requirements'][0]['status']='unknown'
  with self.assertRaises(ValueError):self.row()
 def test_participation_respects_coverage_and_fragments(self):
  p={'start':'2026-09-01T00:00:00+08:00','end':'2026-10-01T00:00:00+08:00'}
  w=[dict(start=f'2026-09-{d:02d}T00:00:00+08:00',end=f'2026-09-{d+7:02d}T00:00:00+08:00',coverage='confirmed',activity='substantive')for d in(7,14,21)]
  self.assertEqual(engine.apply_monthly('90',p,w)['monthly_exact'],'90')
  w[0]['activity']='none';self.assertEqual(engine.apply_monthly('90',p,w)['monthly_exact'],'85')
  w[0]['coverage']='unknown';self.assertIsNone(engine.apply_monthly('90',p,w)['monthly_exact'])
  w[0]['coverage']='confirmed';w.append(dict(start='2026-09-28T00:00:00+08:00',end='2026-10-01T00:00:00+08:00',coverage='confirmed',activity='none'))
  self.assertEqual(engine.apply_monthly('90',p,w)['monthly_exact'],'85')

if __name__=='__main__':unittest.main()
