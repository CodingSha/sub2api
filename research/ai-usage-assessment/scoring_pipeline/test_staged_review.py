"""验证阶段评分、证据边界与月度低参与的实际计算合同。"""
import copy
import unittest
from fractions import Fraction

from . import staged_review as engine
from . import test_stratified_review as previous
from . import test_calibrated_review as calibrated


class StagedReviewTests(unittest.TestCase):
 def windows(self): return previous.StratifiedReviewTests().windows()
 def month(self, windows, score='85'):
  return engine.apply_monthly(score, dict(start='2026-09-01T00:00:00+08:00', end='2026-10-01T00:00:00+08:00'), windows)
 def fixture(self, verdict='adequate'):
  f=calibrated.CalibratedReviewTests(); f.setUp(); r=copy.deepcopy(f.row)
  r['cells'][0]=f.scored()
  for c in r['cells']:
   if c['item'].startswith('M'): c['opportunity_complexity']=dict(status='none',reason='无具体交互条件')
   if c['status']=='scored':
    c.update(verdict=verdict,assessment_stage='decision',rating_basis=dict(met='当前已证安排',next_boundary='未见独立边界控制'),anchor_basis='依据当时条件选择')
  return f,r
 def test_basic_not_automatically_eighty(self):
  f,r=self.fixture();out=engine.validate_period(f.data,f.period,r)
  self.assertEqual([c['band']for c in out['cells']if c['status']=='scored'],[70]*sum(c['status']=='scored'for c in r['cells']))
 def test_reasoned_requires_concrete_basis(self):
  f,r=self.fixture('reasoned');c=next(c for c in r['cells']if c['status']=='scored');c.pop('anchor_basis')
  with self.assertRaises(ValueError):engine.validate_period(f.data,f.period,r)
 def test_controlled_requires_real_boundary(self):
  f,r=self.fixture('controlled')
  for c in r['cells']:c.pop('boundary_handling',None)
  with self.assertRaises(ValueError):engine.validate_period(f.data,f.period,r)
 def test_complete_requires_actual_handoff_evidence(self):
  f,r=self.fixture('complete')
  for c in r['cells']:
   if c['status']=='scored':c['boundary_handling']='处理真实边界'
  with self.assertRaises(ValueError):engine.validate_period(f.data,f.period,r)
 def test_no_stage_rejected(self):
  f,r=self.fixture();next(c for c in r['cells']if c['status']=='scored').pop('assessment_stage')
  with self.assertRaises(ValueError):engine.validate_period(f.data,f.period,r)
 def test_month_one_of_three_full_weeks_unused_costs_five(self):
  w=self.windows();w[2]['activity']='none'
  self.assertEqual(self.month(w)['monthly_exact'],'80')
 def test_limited_is_half_credit(self):
  w=self.windows();w[2]['activity']='limited'
  self.assertEqual(Fraction(self.month(w)['monthly_exact']),Fraction(165,2))
 def test_month_fragments_not_penalized(self):self.assertEqual(self.month(self.windows())['monthly_exact'],'85')
 def test_capture_unknown_not_unused(self):
  w=self.windows();w[2]['coverage']='unknown';self.assertIsNone(self.month(w)['monthly_exact'])
 def test_missing_full_week_not_silently_ignored(self):
  w=self.windows();del w[2];self.assertIsNone(self.month(w)['monthly_exact'])
 def test_exemption_requires_basis(self):
  w=self.windows();w[2]['applicability']='not_applicable'
  with self.assertRaises(ValueError):self.month(w)
 def test_confirmed_exemption_excluded_not_zero(self):
  w=self.windows();w[2].update(applicability='not_applicable',applicability_basis='已确认该完整周不在评价范围',activity='none')
  self.assertEqual(self.month(w)['monthly_exact'],'85')
 def test_all_exempt_or_unobserved_no_fake_performance(self):
  w=self.windows()
  self.assertIsNone(self.month(w,None)['monthly_exact'])
  for x in w:x.update(applicability='not_applicable',applicability_basis='已明确不适用')
  self.assertIsNone(self.month(w)['monthly_exact'])
 def test_nonnegative_and_deduction_cap(self):
  w=self.windows()
  for x in w:x['activity']='none'
  self.assertEqual(self.month(w,'10')['monthly_exact'],'0');self.assertEqual(self.month(w)['participation_deduction_exact'],'15')
 def test_reject_v8_results(self):
  with self.assertRaises(ValueError):engine.summarize_period([previous.row('g')])
 def test_complex_share_remains_fixed(self):
  rows=[previous.row(str(i),70)for i in range(100)]+[previous.row('complex',90,True)]
  for r in rows:r['policy_version']=engine.VERSION
  self.assertEqual(engine.summarize_period(rows)['dimensions']['M']['exact'],'76')


if __name__=='__main__':unittest.main()
