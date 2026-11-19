"""固定归属/归并证据不被恢复覆写；合法缺件可凭原回执确定补齐。"""
import json
from pathlib import Path
import tempfile
import unittest

from goal_batches import identify
from goal_reconciliation import reconcile
from pipeline import received
from test_execution_paths import FixtureClient,packet
from test_goal_reconciliation import Comparator
import test_goal_reconciliation as merge_fixtures


class CachedClient(FixtureClient):
    def identify(self,page,path):
        return json.loads(path.read_text()) if path.exists() else super().identify(page,path)


class GoalEvidenceRecoveryTests(unittest.TestCase):
    def execute(self,stage,root,client):
        if stage=='identify':
            return identify(packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00'),root,client,received)
        return reconcile(merge_fixtures.GoalReconciliationTests().data(3),root,client,received)

    def test_changed_fixed_stage_files_are_preserved_and_rejected_without_new_model(self):
        for stage,names in (('identify',['plan.json','0.graph.json','identified.json']),
                            ('merge',['plan.json','result.json'])):
            for name in names:
                with self.subTest(stage=stage,name=name),tempfile.TemporaryDirectory() as temp:
                    root=Path(temp);client=CachedClient() if stage=='identify' else Comparator()
                    self.execute(stage,root,client);calls=client.calls
                    path=root/name;path.write_text('{"tampered":true}');before=(path.read_bytes(),path.stat().st_mtime_ns)
                    with self.assertRaises(ValueError):self.execute(stage,root,client)
                    self.assertEqual(before,(path.read_bytes(),path.stat().st_mtime_ns))
                    self.assertEqual(calls,client.calls)

    def test_equal_compact_files_keep_original_bytes_and_mtime(self):
        for stage,names in (('identify',['plan.json','0.graph.json','identified.json']),
                            ('merge',['plan.json','result.json'])):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);client=CachedClient() if stage=='identify' else Comparator()
                expected=self.execute(stage,root,client);calls=client.calls
                for name in names:
                    path=root/name;path.write_text(json.dumps(json.loads(path.read_text()),ensure_ascii=False,separators=(',',':')))
                before={name:((root/name).read_bytes(),(root/name).stat().st_mtime_ns) for name in names}
                self.assertEqual(expected,self.execute(stage,root,client));self.assertEqual(calls,client.calls)
                self.assertEqual(before,{name:((root/name).read_bytes(),(root/name).stat().st_mtime_ns) for name in names})

    def test_missing_fixed_files_are_rebuilt_from_original_receipts_without_new_model(self):
        for stage,names in (('identify',['plan.json','0.graph.json','identified.json']),
                            ('merge',['plan.json','result.json'])):
            with self.subTest(stage=stage),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);client=CachedClient() if stage=='identify' else Comparator()
                expected=self.execute(stage,root,client);calls=client.calls
                before={name:json.loads((root/name).read_text()) for name in names}
                for name in names:(root/name).unlink()
                self.assertEqual(expected,self.execute(stage,root,client));self.assertEqual(calls,client.calls)
                self.assertEqual(before,{name:json.loads((root/name).read_text()) for name in names})


if __name__=='__main__':unittest.main()
