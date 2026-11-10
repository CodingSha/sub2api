"""核验复杂处理不被普通数量冲淡、参与边界及真实未知保留。"""
import copy,unittest
from fractions import Fraction
from .stratified_review import VERSION,RUBRIC,summarize_period,apply_monthly,validate_period
from . import test_calibrated_review as calibrated_fixtures
from .policy import ITEMS

def row(gid,band=80,complexity=False,status='scored'):
 cells=[]
 for i in ITEMS:
  c=dict(item=i,status='no_opportunity',reason='无独立机会',opportunity='当前目标未发生',complex={'status':'none'})
  if i.startswith('M'):c['opportunity_complexity']={'status':'none','reason':'未见相互作用条件'}
  if i in ('P1','M1','J1','E1'):
   c.update(status=status,band=band if status=='scored'else None)
   if i=='M1'and complexity:c['opportunity_complexity']={'status':'confirmed','reason':'条件交互'}
  cells.append(c)
 return dict(user_id='u',goal_id=gid,period_id='month',policy_version=VERSION,cells=cells)

class StratifiedReviewTests(unittest.TestCase):
 def test_model_contract_has_no_obsolete_aggregation_principles(self):
  text='\n'.join(RUBRIC['principles'])
  self.assertNotIn('月度表现90%参与10%',text)
  self.assertNotIn('正反表现均按1.5处理',text)
 def test_complex_contribution_not_diluted_by_regular_volume(self):
  def result(n):return summarize_period([row(str(i),80)for i in range(n)]+[row('c',100,True)])
  self.assertEqual(result(1)['dimensions']['M']['exact'],'86')
  self.assertEqual(result(1)['dimensions']['M']['exact'],result(100)['dimensions']['M']['exact'])
 def test_complex_negative_has_same_share(self):
  s=summarize_period([row('a',80),row('b',60,True)])
  self.assertEqual(Fraction(s['dimensions']['M']['exact']),74)
 def test_no_complex_opportunity_not_zero_or_bonus(self):
  s=summarize_period([row('a',80)])['dimensions']['M']
  self.assertEqual(s['scope'],'regular_only');self.assertIsNone(s['complex_exact']);self.assertEqual(s['exact'],'80')
 def test_complex_unknown_stays_unknown(self):
  s=summarize_period([row('a',80),row('b',None,True,'unknown')])
  self.assertEqual(s['dimensions']['M']['exact'],'80');self.assertEqual(s['items']['M1']['complex']['unknown'],1)
 def test_unknown_not_imputed_and_coverage_retained(self):
  s=summarize_period([row('a',80),row('b',None,False,'unknown')])['items']['P1']
  self.assertEqual(s['exact'],'80');self.assertEqual(s['coverage_exact'],'1/2')
  self.assertEqual(s['source_sensitivity_low'],'40');self.assertEqual(s['source_sensitivity_high'],'90')
 def test_independent_local_split_cannot_increase_family_weight(self):
  a=row('a',80);p=a['cells'][0];a['cells'][0]=dict(item='P1',parts=[dict(p,unit_id='x'),dict(p,unit_id='y')])
  b=row('b',100)
  self.assertEqual(summarize_period([a,b])['items']['P1']['exact'],'90')
 def test_duplicate_family_rejected(self):
  with self.assertRaises(ValueError):summarize_period([row('a'),row('a')])
 def test_mixed_version_rejected(self):
  a=row('a');a['policy_version']='old'
  with self.assertRaises(ValueError):summarize_period([a])
 def test_non_mastery_null_complexity_does_not_break_summary(self):
  a=row('a');a['cells'][0]['opportunity_complexity']=None
  self.assertEqual(summarize_period([a])['score'],'80.0')
 def windows(self):
  return [dict(start='2026-09-01T00:00:00+08:00',end='2026-09-07T00:00:00+08:00',coverage='confirmed',activity='none'),
   dict(start='2026-09-07T00:00:00+08:00',end='2026-09-14T00:00:00+08:00',coverage='confirmed',activity='substantive'),
   dict(start='2026-09-14T00:00:00+08:00',end='2026-09-21T00:00:00+08:00',coverage='confirmed',activity='substantive'),
   dict(start='2026-09-21T00:00:00+08:00',end='2026-09-28T00:00:00+08:00',coverage='confirmed',activity='substantive'),
   dict(start='2026-09-28T00:00:00+08:00',end='2026-10-01T00:00:00+08:00',coverage='confirmed',activity='none')]
 def monthly(self,windows,score='84'):
  return apply_monthly(score,dict(start='2026-09-01T00:00:00+08:00',end='2026-10-01T00:00:00+08:00'),windows)
 def test_head_tail_fragments_do_not_penalize_or_add_points(self):
  s=self.monthly(self.windows());self.assertEqual(s['monthly_exact'],'84');self.assertEqual(s['participation_deduction'],'0.0')
  self.assertEqual(s['complete_week_count'],3)
 def test_full_week_missing_reduces_score(self):
  w=self.windows();w[2]['activity']='none';s=self.monthly(w)
  self.assertEqual(Fraction(s['monthly_exact']),Fraction(84)-Fraction(5,3))
 def test_unknown_capture_does_not_mean_unused(self):
  w=self.windows();w[1]['coverage']='unknown';s=self.monthly(w)
  self.assertIsNone(s['monthly_exact']);self.assertIsNone(s['trial_rating'])
 def test_omitted_complete_week_cannot_silently_raise_participation(self):
  w=self.windows();del w[2]
  self.assertIsNone(self.monthly(w)['monthly_exact'])
 def test_duplicate_week_rejected(self):
  w=self.windows();w.append(dict(w[1]))
  with self.assertRaises(ValueError):self.monthly(w)
 def test_limited_credit_and_no_negative_final_score(self):
  w=self.windows();w[1]['activity']='limited';s=self.monthly(w)
  self.assertEqual(Fraction(s['participation_deduction_exact']),Fraction(5,6))
  for p in w:p['activity']='none'
  self.assertEqual(self.monthly(w,'2')['monthly_exact'],'0')
 def test_unknown_complex_can_be_confirmed_without_person_score(self):
  fixture=calibrated_fixtures.CalibratedReviewTests();fixture.setUp();r=copy.deepcopy(fixture.row)
  for c in r['cells']:
   if c['item'].startswith('M'):c['opportunity_complexity']={'status':'none','reason':'其他子项无独立机会'}
  c=r['cells'][3];c.update(status='unknown',missing=dict(type='missing_source',needed='本人处理',why_changes_judgment='无本人处理可判断'))
  c['opportunity_complexity']=dict(status='confirmed',reason='两种限制相互约束',condition_a='甲',condition_b='乙',interaction='甲乙共同影响路径',routine_insufficient='原路径不能满足两者',evidence=[dict(event='a',quote='甲乙')])
  out=validate_period(fixture.data,fixture.period,r)
  self.assertEqual(out['cells'][3]['status'],'unknown');self.assertEqual(out['cells'][3]['opportunity_complexity']['status'],'confirmed')

if __name__=='__main__':unittest.main()
