"""隔离候选父包绑定回归，仅合成fixture、本地临时目录。"""
import unittest
import test_week_basis as fixtures
import week_basis as candidate
from runtime import atomic,digest
class ParentBindingTests(unittest.TestCase):
 setUp=fixtures.Cases.setUp
 tearDown=fixtures.Cases.tearDown
 run_stage=fixtures.Cases.run_stage
 def test_mismatched_parent_refuses_before_any_generation(self):
  atomic(self.parent,dict(unrelated_parent=True));client=fixtures.Client()
  with self.assertRaisesRegex(ValueError,'父包与周依据输入不符'):self.run_stage(client)
  self.assertEqual(0,client.calls);self.assertFalse((self.root/'new').exists())
 def test_parent_changed_during_generation_is_rejected_without_result(self):
  def mutate(kind,value):
   if kind=='zhiyongli_week_basis':atomic(self.parent,dict(changed_during_generation=True))
   return value
  client=fixtures.Client(mutate)
  with self.assertRaisesRegex(ValueError,'生成周依据期间原评分父包变化'):self.run_stage(client)
  self.assertEqual(2,client.calls)
  self.assertTrue((self.root/'new/generation.receipt.json').exists())
  self.assertTrue((self.root/'new/review.receipt.json').exists())
  self.assertFalse((self.root/'new/verified-basis.json').exists())
  self.assertFalse((self.root/'new/manifest.json').exists())
 def test_same_parent_replay_is_zero_send_and_preserves_all_frozen_bytes(self):
  client=fixtures.Client();expected=self.run_stage(client)
  before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in (self.root/'new').glob('*.json')}
  replay=fixtures.Client();self.assertEqual(expected,self.run_stage(replay));self.assertEqual(0,replay.calls)
  self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in before})
  self.assertEqual(expected,candidate.verify(self.bundle,self.unit,self.parent,self.root/'new',client.config))
 def test_unit_or_bundle_is_not_mutated_by_binding_validation(self):
  before=(digest(self.bundle),digest(self.unit));self.run_stage(fixtures.Client())
  self.assertEqual(before,(digest(self.bundle),digest(self.unit)))
 def test_matching_parent_still_preserves_local_expression_gate(self):
  self.bundle['reports'][0]['eligibility']['observations']=[]
  atomic(self.parent,self.bundle);client=fixtures.Client()
  with self.assertRaises(candidate.Paused):self.run_stage(client)
  self.assertEqual(0,client.calls)
if __name__=='__main__':unittest.main()
