"""缩小范围仍保留原个人请求累计额度；不发模型请求。"""
from pathlib import Path
import tempfile
import unittest

from runtime import atomic
from worker import inherit_request_budget
from request_budget import RequestBudget
from pipeline import Paused


class ScopeBudgetTests(unittest.TestCase):
    def test_new_scope_carries_old_send_intents_and_stops_at_original_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=RequestBudget(root/'1',3,20)
            old.reserve(root/'1/a.receipt.json','a');old.reserve(root/'1/b.receipt.json','b')
            original=old.path.read_bytes();new=root/'2';new.mkdir()
            remaining=inherit_request_budget(new,root,[1],3)
            self.assertEqual(1,remaining)
            budget=RequestBudget(new,remaining,20);budget.reserve(new/'c.receipt.json','c')
            with self.assertRaises(Paused):budget.reserve(new/'d.receipt.json','d')
            self.assertEqual(3,old.used()+budget.used())
            self.assertEqual(original,old.path.read_bytes())
            old.reserve(root/'1/late.receipt.json','late')
            with self.assertRaisesRegex(ValueError,'旧任务请求账簿变化'):inherit_request_budget(new,root,[1],3)

    def test_missing_or_invalid_old_budget_is_never_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);new=root/'2';new.mkdir()
            with self.assertRaises(ValueError):inherit_request_budget(new,root,[1],3)
            atomic(root/'1/request-budget.json',dict(version=1,entries=[]))
            with self.assertRaises(ValueError):inherit_request_budget(new,root,[1],3)


if __name__=='__main__':unittest.main()
