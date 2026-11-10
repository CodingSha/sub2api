"""v11回归：拦住满分跳档、稀疏子项放大和低参与无差异。"""
import copy
import unittest
from fractions import Fraction

from scoring_pipeline import progressive_review as engine
from scoring_pipeline.test_applicable_review import ApplicableTests


class ProgressiveTests(unittest.TestCase):
 def setUp(self):
  base=ApplicableTests();base.setUp()
  self.data,self.period,self.cells=base.data,base.period,base.cells
  self.proof={'event':'e1','quote':base.e['text']}
  self.cells[0]['progression']={
   'effective':{'met':True,'fact':'目标对象与动作明确','evidence':[self.proof]},
   'adapted':{'met':True,'fact':'针对实际歧义明确阶段边界','actual_condition':'存在范围歧义','choice':'限定当前解释范围','difference_from_basic':'排除误读方向','evidence':[self.proof]},
   'verified':{'met':True,'fact':'已核对输入范围与原始问题相容','check_object':'范围歧义','actual_check':'对照原始对象','observed_result':'二者相容','supports_choice':'限定未丢必要范围','evidence':[self.proof]},
  }
 def row(self):return engine.validate_period(self.data,self.period,dict(period_id=self.period['period_id'],cells=self.cells))
 def test_full_checklist_alone_does_not_allow_100(self):
  self.cells[0].pop('progression')
  with self.assertRaises(ValueError):self.row()
 def test_100_requires_90(self):
  self.cells[0]['progression']['adapted']['met']=False
  with self.assertRaises(ValueError):self.row()
 def test_100_requires_actual_check_result(self):
  self.cells[0]['progression']['verified'].pop('observed_result')
  with self.assertRaises(ValueError):self.row()
 def test_90_requires_increment_beyond_basic(self):
  self.cells[0]['verdict']='tailored';self.cells[0]['progression']['verified']['met']=False
  self.cells[0]['progression']['adapted'].pop('difference_from_basic')
  with self.assertRaises(ValueError):self.row()
 def test_unknown_higher_level_does_not_abstain(self):
  self.cells[0]['verdict']='sufficient'
  self.cells[0]['progression']['adapted']['met']=False;self.cells[0]['progression']['verified']['met']=False
  self.assertEqual(self.row()['cells'][0]['band'],80)
 def test_simple_task_not_hard_capped(self):self.assertEqual(self.row()['cells'][0]['band'],100)
 def test_80_with_confirmed_higher_level_rejected(self):
  self.cells[0]['verdict']='sufficient'
  with self.assertRaises(ValueError):self.row()
 def test_verified_cannot_skip_adaptation_even_at_80(self):
  self.cells[0]['verdict']='sufficient';self.cells[0]['progression']['adapted']['met']=False
  with self.assertRaises(ValueError):self.row()
 def test_requirement_bad_status_rejected(self):
  self.cells[0]['verdict']='tailored';self.cells[0]['progression']['verified']['met']=False
  self.cells[0]['requirements'][0]['status']='probably'
  with self.assertRaises(ValueError):self.row()
 def test_progression_cannot_rely_on_ai_only(self):
  e=copy.deepcopy(self.data['events'][0]);e.update(event='ai',actor_class='assistant',human_spans=[])
  self.data['events'].append(e);self.cells[0]['progression']['verified']['evidence']=[dict(event='ai',quote=e['text'])]
  with self.assertRaises(ValueError):self.row()
 def test_future_support_rejected(self):
  self.data['events'][0]['at']='2026-09-15T01:00:00+08:00'
  with self.assertRaises(ValueError):self.row()
 def test_no_opportunity_not_imputed(self):
  row=self.row();s=engine.summarize_period([row]);self.assertIsNone(s['score']);self.assertEqual(s['dimensions']['P']['score'],'100.0')
 def test_sparse_item_does_not_receive_third_dimension(self):
  rows=[]
  for i in range(10):
   r=copy.deepcopy(self.row());r['goal_id']=str(i)
   r['cells']=[dict(item='E1',status='scored',band=80)]
   if i==0:r['cells'].append(dict(item='E3',status='scored',band=100))
   rows.append(r)
  self.assertEqual(engine.summarize_period(rows)['dimensions']['E']['score'],'81.0')
 def test_necessary_gap_not_diluted_inside_same_task(self):
  r=self.row();r['cells']=[dict(item='P1',status='scored',band=100),dict(item='P2',status='scored',band=60)]
  self.assertEqual(engine.summarize_period([r])['dimensions']['P']['score'],'60.0')
 def test_complex_weight_applies_to_good_and_bad(self):
  a=self.row();a['cells']=[dict(item='M1',status='scored',band=60,opportunity_complexity={'status':'confirmed'})]
  b=copy.deepcopy(a);b['goal_id']='b';b['cells'][0].update(band=100,opportunity_complexity={'status':'none'})
  self.assertEqual(engine.summarize_period([a,b])['dimensions']['M']['score'],'76.0')
 def monthly(self,n,coverage='confirmed'):
  p={'kind':'monthly','start':'2026-09-01T00:00:00+08:00','end':'2026-10-01T00:00:00+08:00'}
  ws=[dict(period_id='week-'+str(d),start=f'2026-09-{d:02d}T00:00:00+08:00',end=f'2026-09-{d+7:02d}T00:00:00+08:00',participation_units=n,participation_known=True,coverage=coverage)for d in [7,14,21]]
  return engine.apply_monthly('90',p,ws)
 def test_low_participation_continuous_and_no_score_gate(self):
  self.assertEqual(self.monthly(0)['monthly_score'],'75.0')
  self.assertEqual(self.monthly(1)['monthly_score'],'82.5')
  self.assertEqual(self.monthly(3)['monthly_score'],'86.3')
  self.assertGreater(Fraction(self.monthly(10)['monthly_exact']),Fraction(self.monthly(3)['monthly_exact']))
 def test_participation_unknown_not_zero(self):self.assertIsNone(self.monthly(0,'unknown')['monthly_score'])
 def test_participation_never_adds_points(self):self.assertLessEqual(Fraction(self.monthly(1000)['monthly_exact']),90)
 def test_negative_participation_units_rejected(self):
  with self.assertRaises(ValueError):self.monthly(-1)
 def test_duplicate_parent_rejected(self):
  a=self.row()
  with self.assertRaises(ValueError):engine.summarize_period([a,a])


if __name__=='__main__':unittest.main()
