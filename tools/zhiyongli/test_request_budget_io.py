"""预算只读查询不得改账簿，预留仍严格互斥且保留崩溃门槛。"""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import request_budget
from request_budget import RequestBudget
from pipeline import Paused

class RequestBudgetIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_reads_and_reopening_preserve_bytes_and_mtime(self):
        budget=RequestBudget(self.root,10,10);budget.reserve(self.root/'one.receipt.json','hash')
        before=(budget.path.read_bytes(),budget.path.stat().st_mtime_ns)
        with patch.object(request_budget,'atomic',wraps=request_budget.atomic) as writes:
            for _ in range(5):
                self.assertEqual(1,budget.used());budget.check(self.root/'next')
            RequestBudget(self.root,10,10)
            self.assertEqual(0,writes.call_count)
        self.assertEqual(before,(budget.path.read_bytes(),budget.path.stat().st_mtime_ns))
        with self.assertRaises(Paused) as error:budget.reserve(self.root/'one.receipt.json','hash')
        self.assertEqual('unknown',error.exception.state)

    def test_missing_and_corrupt_ledger_are_never_reset_by_operations(self):
        budget=RequestBudget(self.root,10,10)
        for value in (None,'[]','{bad','{"version":1,"entries":[]}'):
            if value is None:budget.path.unlink()
            else:budget.path.write_text(value)
            for operation in (budget.used,lambda:budget.check(self.root/'next'),lambda:budget.reserve(self.root/'next','h')):
                with self.assertRaises((ValueError,json.JSONDecodeError)):operation()
            if value is None:self.assertFalse(budget.path.exists())
            else:self.assertEqual(value,budget.path.read_text())

    def test_initial_creation_inherits_legacy_only_once(self):
        receipt=self.root/'old.receipt.json';receipt.write_text(json.dumps(dict(status='unknown',request_sha256='old')))
        with patch.object(request_budget,'atomic',wraps=request_budget.atomic) as writes:
            budget=RequestBudget(self.root,10,10);self.assertEqual(1,budget.used())
            RequestBudget(self.root,10,10);self.assertEqual(3,writes.call_count)
        with self.assertRaises(Paused):budget.reserve(receipt,'old')

    def test_restart_after_reserved_missing_receipt_and_lost_ledger_never_resets(self):
        budget=RequestBudget(self.root,10,10);budget.reserve(self.root/'not-written.receipt.json','h')
        budget.path.unlink()
        with self.assertRaisesRegex(ValueError,'缺失'):RequestBudget(self.root,10,10)
        self.assertFalse(budget.path.exists())
        # 初始化标记也丢失时，旧锁仍不允许自动把损坏任务当新任务。
        budget.initialization.unlink()
        with self.assertRaisesRegex(ValueError,'旧锁'):RequestBudget(self.root,10,10)
        self.assertFalse(budget.path.exists())

    def test_first_initialization_crash_before_ledger_or_ready_can_resume(self):
        for failure in ('ledger','ready'):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);real=request_budget.atomic
                def crash(path,value):
                    if (failure=='ledger' and path.name=='request-budget.json' or
                            failure=='ready' and path.name=='request-budget-initialization.json' and value['state']=='ready'):
                        raise OSError('合成初始化崩溃')
                    return real(path,value)
                with patch.object(request_budget,'atomic',side_effect=crash):
                    with self.assertRaises(OSError):RequestBudget(root,10,10)
                budget=RequestBudget(root,10,10);self.assertEqual(0,budget.used())
                self.assertEqual('ready',json.loads(budget.initialization.read_text())['state'])
                budget.reserve(root/'intent.receipt.json','h')
                self.assertEqual(1,RequestBudget(root,10,10).used())

    def test_legacy_existing_ledger_backfills_marker_without_rewriting_and_old_empty_lock_rejects(self):
        path=self.root/'request-budget.json';path.write_text('{"version":1,"entries":{"old":{"request_sha256":"h"}}}')
        before=(path.read_bytes(),path.stat().st_mtime_ns)
        budget=RequestBudget(self.root,10,10);self.assertEqual(1,budget.used())
        self.assertEqual(before,(path.read_bytes(),path.stat().st_mtime_ns))
        self.assertTrue(budget.initialization.exists())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'request-budget.lock').touch()
            with self.assertRaisesRegex(ValueError,'旧锁'):RequestBudget(root,10,10)
            self.assertFalse((root/'request-budget.json').exists())

    def test_parallel_reserve_enforces_shared_total_and_turn_limits(self):
        for total,turn in ((7,20),(20,7)):
            root=self.root/str(total);budget=RequestBudget(root,total,turn)
            def reserve(index):
                try:budget.reserve(root/(str(index)+'.receipt.json'),'h');return 'reserved'
                except Paused as error:return error.state
            with ThreadPoolExecutor(max_workers=12) as pool:states=list(pool.map(reserve,range(30)))
            self.assertEqual(7,states.count('reserved'));self.assertEqual(7,budget.used());self.assertEqual(7,budget.turn_used)
            self.assertEqual({'reserved','needs_review' if total==7 else 'queued'},set(states))

if __name__=='__main__':unittest.main()
