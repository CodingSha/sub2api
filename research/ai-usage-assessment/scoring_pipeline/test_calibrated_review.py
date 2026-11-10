"""检验混合对象不能借容器冒充档位、拆分放大权重或掩盖未知。"""
import copy,itertools,unittest
from fractions import Fraction
from .calibrated_review import validate_period,summarize_period,observations
from .policy import ITEMS

class CalibratedReviewTests(unittest.TestCase):
 def setUp(self):
  self.period={'period_id':'month-2026-09','start':'2026-09-01T00:00:00+08:00','end':'2026-10-01T00:00:00+08:00'}
  self.data={'goal_id':'g','user_id':'u','title':'混合集合','unattributed_bucket':True,'events':[
   {'event':'a','event_id':'a','at':'2026-09-02T00:00:00+08:00','actor_class':'human','human_spans':[[0,6]],'text':'甲乙丙丁戊己'}]}
  self.row={'period_id':self.period['period_id'],'boundary':{'status':'local_scope','reason':'独立局部'},'cells':[
   {'item':i,'status':'no_opportunity','opportunity':'当前无该机会','reason':'独立目标中未发生','complex':{'status':'none'}} for i in ITEMS]}
 def scored(self,item='P1',quote='甲',band='adequate',unit='x'):
  c=dict(item=item,status='scored',opportunity='明确当前目标',reason='原句足以识别目标',verdict=band,fact='目标'+quote,
   person_evidence=[{'event':'a','quote':quote}],evidence=[],adjacent_reason='常规充分',limits=[],local_scope='目标'+quote,unit_id=unit)
  if band=='necessary_gap':c.update(evidence=[{'event':'a','quote':'乙'}],responsibility=dict(known_at_time='已知',person_control='可控',feasible_alternative='可纠正',actual_gap='未处理'))
  return c
 def test_container_has_no_representative_band_and_unknown_retained(self):
  parts=[self.scored(),dict(item='P1',unit_id='y',status='unknown',opportunity='另一局部',reason='缺反馈',local_scope='乙',missing=dict(type='missing_source',needed='原反馈',why_changes_judgment='关系决定当前判断'))]
  self.row['cells'][0]=dict(item='P1',parts=parts)
  out=validate_period(self.data,self.period,self.row);c=out['cells'][0]
  self.assertEqual(c['status'],'mixed');self.assertIsNone(c['band'])
  s=summarize_period([out])['items']['P1'];self.assertEqual(s['scored'],1);self.assertEqual(s['unknown'],1);self.assertEqual(s['score'],'80.0')
 def test_duplicate_proof_cannot_multiply_parts(self):
  self.row['cells'][0]=dict(item='P1',parts=[self.scored(unit='x'),self.scored(unit='y')])
  with self.assertRaisesRegex(ValueError,'重复加权'):validate_period(self.data,self.period,self.row)
 def test_family_weight_not_message_count(self):
  self.row['cells'][0]=dict(item='P1',parts=[self.scored(quote='甲',unit='x'),self.scored(quote='乙',unit='y',band='necessary_gap')])
  first=validate_period(self.data,self.period,self.row)
  other=copy.deepcopy(self.row);other['cells'][0]=self.scored(quote='丙');other=validate_period(self.data,self.period,other)
  s=summarize_period([first,other])['items']['P1'];self.assertEqual(Fraction(s['exact']),75)
 def test_shared_fact_across_local_parts_requires_difference(self):
  self.row['cells'][0]=dict(item='P1',parts=[self.scored(quote='甲'),self.scored(quote='乙',unit='y')])
  self.row['cells'][1]=self.scored(item='P2',quote='乙');self.row['cells'][1]['fact']='提供背景乙'
  with self.assertRaisesRegex(ValueError,'independent_fact_difference'):validate_period(self.data,self.period,self.row)
 def test_pending_sensitivity_matches_enumeration(self):
  rows=[]
  for n,b in enumerate([60,80,90]):
   c=self.scored(item='M1',quote='甲',band={60:'necessary_gap',80:'adequate',90:'reasoned'}[b]);c.update(independent_method='独立方法',anchor_basis='具体选择',complex={'status':'pending'})
   row=copy.deepcopy(self.row);row['cells'][3]=c;rows.append(validate_period(self.data,self.period,row))
  result=summarize_period(rows)['items']['M1']
  possible=[sum(Fraction(b)*w for b,w in zip([60,80,90],weights))/sum(weights) for weights in itertools.product([Fraction(1),Fraction(3,2)],repeat=3)]
  self.assertEqual(Fraction(result['lower_exact']),min(possible));self.assertEqual(Fraction(result['upper_exact']),max(possible))
 def test_explicit_local_boundaries_allow_parts_without_unattributed_flag(self):
  self.data['unattributed_bucket']=False
  self.row['cells'][0]=dict(item='P1',parts=[self.scored(quote='甲'),self.scored(quote='乙',unit='y')])
  self.assertEqual(validate_period(self.data,self.period,self.row)['cells'][0]['status'],'mixed')
  self.row['boundary']={'status':'confirmed','reason':'一个整体目标'}
  with self.assertRaisesRegex(ValueError,'独立局部边界'):validate_period(self.data,self.period,self.row)
 def test_revalidation_is_idempotent(self):
  self.row['cells'][0]=dict(item='P1',parts=[self.scored(quote='甲'),self.scored(quote='乙',unit='y')])
  first=validate_period(self.data,self.period,self.row);self.assertEqual(validate_period(self.data,self.period,first),first)
 def test_non_scored_candidate_proof_does_not_enter_scored_deduplication(self):
  self.row['cells'][0].update(person_evidence=[{'event':'a','quote':'甲'}])
  result=validate_period(self.data,self.period,self.row)
  self.assertEqual(result['cells'][0]['status'],'no_opportunity')
  self.assertIsNone(summarize_period([result])['items']['P1']['exact'])
 def test_invalid_status_names_item_and_frozen_allowed_states(self):
  self.row['cells'][7]['status']='not_observed'
  with self.assertRaisesRegex(ValueError,'J2状态不在冻结合同中'):
   validate_period(self.data,self.period,self.row)
 def test_local_parts_cannot_inherit_unmapped_family_candidate(self):
  first=self.scored(quote='甲');first['reuse_facts']=True;first.pop('fact')
  self.row['cells'][0]=dict(item='P1',parts=[first,self.scored(quote='乙',unit='y')])
  with self.assertRaisesRegex(ValueError,'P1/x：局部parts不能用reuse_facts'):
   validate_period(self.data,self.period,self.row)
if __name__=='__main__':unittest.main()
