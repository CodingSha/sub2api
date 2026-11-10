"""串行执行回归：不伪造团队、断点接续、旧稿边界和输入清理。"""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scoring_pipeline import dsh_serial as serial
from scoring_pipeline import test_dsh_v11 as fixtures

class SerialTests(unittest.TestCase):
 def setUp(self):
  f=fixtures.HandoffTests();f.setUp();self.u=f.u;self.a=f.a
  self.a['self_check']=dict(decision='completed',reason='按固定合同核对当期引用及全部12项')
  self.data=dict(policy_version=serial.VERSION,tasks={'t1':self.u},source_groups={},frozen_files=[])
  self.tmp=tempfile.TemporaryDirectory();self.run=Path(self.tmp.name)/'run'
  self.task_patch=patch.object(serial.base,'task',side_effect=lambda d,k:dict(self.u,key=k,goal_id=k))
  self.task_patch.start();self.addCleanup(self.task_patch.stop);self.addCleanup(self.tmp.cleanup)
 def init(self):return serial.initialize(self.data,self.run,None)
 def test_init_has_no_team_identity(self):
  s=self.init();self.assertEqual(s['pending_keys'],['t1']);self.assertEqual(s['acceptance_status'],'pending_codex')
 def test_reinit_cannot_overwrite(self):
  self.init()
  with self.assertRaises(ValueError):self.init()
 def test_standalone_freeze_without_fabricated_session(self):
  self.init();serial.freeze(self.data,self.run,self.a,'standalone')
  s=serial.progress(self.data,self.run);self.assertEqual(s['completed_contract_checks'],1)
  x=json.loads(next((self.run/'reviews').glob('*.json')).read_text());self.assertNotIn('reviewer',x);self.assertNotIn('auditor',x)
  self.assertIsNone(x['execution']['session_id']);self.assertEqual(x['execution']['session_id_status'],'unavailable')
 def test_changed_score_after_receipt_rejected(self):
  self.init();serial.freeze(self.data,self.run,self.a,'standalone')
  p=next((self.run/'reviews').glob('*.json'));x=json.loads(p.read_text());x['usage'][0]['reason']='改写';p.write_text(json.dumps(x))
  self.assertEqual(len(serial.progress(self.data,self.run)['invalid_files']),1)
 def test_self_check_is_required(self):
  self.init();self.a.pop('self_check')
  with self.assertRaises(ValueError):serial.freeze(self.data,self.run,self.a,'standalone')
 def test_standalone_cannot_invent_auditor(self):
  self.init();self.a['auditor']={'context_id':'invented'}
  with self.assertRaises(ValueError):serial.freeze(self.data,self.run,self.a,'standalone')
 def test_local_only_is_blocked(self):
  self.u['local_only']=True;self.init();s=serial.progress(self.data,self.run);self.assertEqual(s['blocked_keys'],['t1'])
  with self.assertRaises(ValueError):serial.freeze(self.data,self.run,self.a,'standalone')
 def test_next_reuses_unfinished_batch(self):
  self.init()
  with patch.object(serial.base,'packet',return_value={'input':'原文包'}):
   a=serial.next_batch(self.data,self.run,5);b=serial.next_batch(self.data,self.run,5)
  self.assertEqual(a['batch'],b['batch']);self.assertTrue(b['resume'])
 def test_empty_progress_does_not_claim_complete(self):
  self.init();s=serial.progress(self.data,self.run);self.assertFalse(s['semantics_or_fairness_validated']);self.assertEqual(s['completed_contract_checks'],0)
 def test_clean_keeps_pending_and_removes_completed_inputs(self):
  self.init()
  with patch.object(serial.base,'packet',return_value={'input':'原文包'}):serial.next_batch(self.data,self.run,5)
  self.assertEqual(serial.clean_inputs(self.data,self.run)['removed_regenerable_inputs'],0)
  serial.freeze(self.data,self.run,self.a,'standalone')
  self.assertEqual(serial.clean_inputs(self.data,self.run)['removed_regenerable_inputs'],1)
  self.assertEqual(len(list((self.run/'receipts').glob('*.json'))),1)
 def test_changed_input_not_deleted(self):
  self.init()
  with patch.object(serial.base,'packet',return_value={'input':'原文包'}):serial.next_batch(self.data,self.run,5)
  serial.freeze(self.data,self.run,self.a,'standalone');p=next((self.run/'inputs').rglob('*.json'));p.write_text('变化')
  with self.assertRaises(ValueError):serial.clean_inputs(self.data,self.run)
  self.assertTrue(p.exists())
 def test_legacy_draft_keeps_origin_and_pending_acceptance(self):
  old=Path(self.tmp.name)/'old';p=old/'drafts/rev-a/t1.json';p.parent.mkdir(parents=True);a=copy.deepcopy(self.a);a.pop('self_check');p.write_text(json.dumps(a))
  s=serial.initialize(self.data,self.run,old);self.assertEqual(s['completed_contract_checks'],1)
  x=json.loads(next((self.run/'reviews').glob('*.json')).read_text());self.assertEqual(x['execution']['mode'],'legacy_draft');self.assertNotIn('auditor',x)
 def test_old_policy_not_reused(self):
  old=Path(self.tmp.name)/'old';p=old/'drafts/rev-a/t1.json';p.parent.mkdir(parents=True);p.write_text(json.dumps(dict(self.a,policy_version='old')))
  s=serial.initialize(self.data,self.run,old);self.assertEqual(s['completed_contract_checks'],0)
 def test_batch_limit(self):
  self.init()
  with self.assertRaises(ValueError):serial.next_batch(self.data,self.run,11)
 def test_incomplete_regular_queue_never_summarizes(self):
  self.init()
  with self.assertRaises(ValueError):serial.summarize(self.data,self.run,allow_blocked=True)
 def test_blocked_summary_explicitly_reports_actual_coverage(self):
  self.init();serial.freeze(self.data,self.run,self.a,'standalone')
  u=copy.deepcopy(self.u);u.update(key='t2',local_only=True);self.data['tasks']['t2']=u
  self.data.update(scope={},period_templates=[dict(user_id='test',period=dict(self.u['periods'][0],kind='weekly'))],participation_coverage={},non_task_dispositions=[])
  with self.assertRaises(ValueError):serial.summarize(self.data,self.run)
  s=serial.summarize(self.data,self.run,allow_blocked=True)
  self.assertFalse(s['complete']);self.assertEqual(s['task_count'],1);self.assertEqual(s['expected_task_count'],2)
  self.assertEqual(s['rows'][0]['result_status'],'provisional_with_unreviewed_units')

if __name__=='__main__':unittest.main()
