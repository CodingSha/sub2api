"""交接边界测试：受限原文、上下文归属、全量门槛与参与未知。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fractions import Fraction
from scoring_pipeline import dsh_v11 as handoff
from scoring_pipeline import progressive_handoff as contract
from scoring_pipeline import test_progressive_review as fixtures

class HandoffTests(unittest.TestCase):
 def setUp(self):
  f=fixtures.ProgressiveTests();f.setUp();self.u=copy.deepcopy(f.data)
  self.u.update(key='t1',periods=[f.period],purpose='概念咨询',boundary_reason='独立目标',source_gaps=[],local_only=False)
  self.a=dict(key='t1',policy_version=handoff.VERSION,assessments=[dict(period_ids=[f.period['period_id']],cells=f.cells)],usage=[dict(period_id=f.period['period_id'],activity='substantive',reason='当期实际核验',evidence=[f.proof],engagements=[dict(kind='verification',fact='当期实际核验',evidence=[f.proof])])])
 def test_continuation_does_not_require_new_initiation(self):self.assertEqual(len(contract.check_review(self.u,self.a)),1)
 def test_duplicate_usage_kind_rejected(self):
  self.a['usage'][0]['engagements']*=2
  with self.assertRaises(ValueError):contract.check_review(self.u,self.a)
 def test_future_usage_rejected(self):
  self.u['events'][0]['at']='2026-09-15T00:00:00+08:00'
  with self.assertRaises(ValueError):contract.check_review(self.u,self.a)
 def test_other_task_person_is_context_only(self):
  e=copy.deepcopy(self.u['events'][0]);e.update(event='e2',text='另一任务的本人输入',human_spans=[[0,9]])
  self.u['events'].append(e);p=contract.raw_payload(self.u)
  self.assertEqual([x['event']for x in p['person_events']],['e1']);self.assertEqual(p['context_index'][0]['kind'],'other_task_person_context')
 def test_local_packet_never_reads_source(self):
  u=dict(self.u,local_only=True,source_groups=['sensitive'])
  with patch.object(handoff,'task',side_effect=AssertionError('禁止读受限正文')):p=handoff.packet({'tasks':{'t1':u}},'t1')
  self.assertIsNone(p['raw_material'])
 def test_write_refuses_overwrite(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'out.json';handoff.write(p,{'a':1})
   with self.assertRaises(ValueError):handoff.write(p,{'a':2})
 def test_real_receipts_and_independent_context_required(self):
  with tempfile.TemporaryDirectory()as d:
   p=Path(d)/'receipt';p.write_text('原始执行记录')
   r=dict(team_id='test',agent_id='one',context_id='one',method='dsh_agent',receipt=dict(path=str(p),sha256=handoff.sha(p)))
   a=dict(reviewer=r,auditor=dict(r,context_id='two'),audit=dict(decision='accepted',reason='逐项复核'));handoff.check_provenance(a)
   a['auditor']['context_id']='one'
   with self.assertRaises(ValueError):handoff.check_provenance(a)
   a['auditor']['context_id']='two';p.write_text('被改写')
   with self.assertRaises(ValueError):handoff.check_provenance(a)
 def test_incomplete_reviews_block_summary(self):
  with tempfile.TemporaryDirectory()as d:
   with self.assertRaises(ValueError):handoff.load_reviews({'tasks':{'t1':self.u}},d)
   self.assertEqual(handoff.load_reviews({'tasks':{'t1':self.u}},d,allow_partial=True),{})
 def window(self,n=1,extra=0):return dict(period_id='week',start='2026-09-07T00:00:00+08:00',end='2026-09-14T00:00:00+08:00',participation_units=n,possible_additional_units=extra,coverage='confirmed')
 def test_unknown_participation_is_bounded_not_imputed(self):
  b=handoff.adjusted_bounds('90',[self.window(1,3)]);self.assertEqual(b['low_exact'],'165/2');self.assertEqual(b['high_exact'],'87');self.assertNotEqual(b['low_rating'],b['high_rating'])
 def test_no_unknown_has_exact_point(self):
  b=handoff.adjusted_bounds('90',[self.window(3)]);self.assertEqual(b['low_exact'],b['high_exact'])
 def test_month_fragments_not_penalized(self):
  w=self.window(0);w['end']='2026-09-10T00:00:00+08:00';self.assertEqual(handoff.adjusted_bounds('90',[self.window(3),w])['low_exact'],'345/4')
 def test_mixed_version_rejected(self):
  with tempfile.TemporaryDirectory()as d:
   Path(d,'t1.json').write_text(json.dumps(dict(self.a,policy_version='old')))
   with self.assertRaises(ValueError):handoff.load_reviews({'tasks':{'t1':self.u}},d)
 def test_unknown_never_increases_over_performance(self):
  for n in [0,1,10]:self.assertLessEqual(Fraction(handoff.adjusted_bounds('90',[self.window(n,30)])['high_exact']),90)
 def summary_fixture(self,unknown=False):
  period=dict(kind='monthly',period_id='month',start='2026-09-01T00:00:00+08:00',end='2026-10-01T00:00:00+08:00')
  windows=[dict(self.window(),period_id='w'+str(d),start=f'2026-09-{d:02d}T00:00:00+08:00',end=f'2026-09-{d+7:02d}T00:00:00+08:00')for d in [7,14,21]]
  row=dict(policy_version=handoff.VERSION,user_id='test',period_id='month',goal_id='t1',cells=[dict(item=i,status='scored',band=90)for i in ['P1','M1','J1','E1']])
  data=dict(scope={},tasks={'t1':dict(user_id='test')},period_templates=[dict(user_id='test',period=period)],participation_coverage={'test':windows},non_task_dispositions=[dict(user_id='test',status='unknown_source',at=w['start'])for w in windows]if unknown else [])
  usage=[dict(period_id=w['period_id'],activity='substantive',engagements=[dict(kind=k)for k in ['initiation','development','verification']])for w in windows]
  return handoff.run_summary(data,{'t1':dict(rows=[row],usage=usage)})['rows'][0]
 def test_monthly_exact_recalculation(self):
  s=self.summary_fixture();self.assertEqual(s['monthly_exact'],str(Fraction(90)-15*Fraction(1,4)))
  self.assertEqual(s['monthly_score'],'86.3');self.assertEqual(s['trial_rating']['id'],'L4')
 def test_unknown_participation_preserves_stable_reference_rating(self):
  s=self.summary_fixture(unknown=True);self.assertIsNone(s['monthly_score'])
  self.assertEqual(s['performance_reference_rating']['id'],'L4');self.assertEqual(s['trial_rating']['id'],'L4')
  self.assertEqual(s['monthly_bounds']['low_exact'],'345/4')

if __name__=='__main__':unittest.main()
