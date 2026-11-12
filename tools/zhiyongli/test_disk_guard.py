"""用合成磁盘状态验证保护在写原文和发送模型请求前生效。"""
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from disk_guard import require_space
from pipeline import Paused


class DiskGuardTests(unittest.TestCase):
    def test_unconfigured_local_execution_does_not_query_disk(self):
        with patch.dict(os.environ, {}, clear=True), patch('disk_guard.shutil.disk_usage') as usage:
            require_space('/unused')
            usage.assert_not_called()

    def test_absolute_and_percentage_reserves_include_pending_write(self):
        gib = 1024**3
        with patch.dict(os.environ, {'ZHIYONGLI_MIN_FREE_GIB':'20', 'ZHIYONGLI_MIN_FREE_PERCENT':'15'}, clear=True):
            for total,free,pending in [(100*gib,21*gib,2*gib),(500*gib,74*gib,0)]:
                with self.subTest(total=total), patch('disk_guard.shutil.disk_usage', return_value=SimpleNamespace(total=total,free=free)):
                    with self.assertRaises(Paused):require_space('/tmp',pending)
            with patch('disk_guard.shutil.disk_usage', return_value=SimpleNamespace(total=500*gib,free=100*gib)):
                require_space('/tmp',gib)

    def test_source_write_stops_without_creating_partial_file(self):
        from source_snapshot import save_bytes
        with tempfile.TemporaryDirectory() as tmp, patch('disk_guard.require_space', side_effect=Paused('needs_review','磁盘保护')):
            path=Path(tmp)/'objects'/'source.json'
            with self.assertRaises(Paused):save_bytes(path,b'synthetic-only')
            self.assertFalse(path.parent.exists())

    def test_model_request_stops_before_budget_or_network(self):
        from worker import JobClient
        client=object.__new__(JobClient)
        client.root=Path('/tmp')
        with patch('disk_guard.require_space', side_effect=Paused('needs_review','磁盘保护')):
            with self.assertRaises(Paused):client._call({},Path('/unused-receipt'))

    def test_pause_prevents_new_reservation_without_touching_existing_receipts(self):
        from worker import JobClient
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            client=object.__new__(JobClient);client.root=root/'1'
            (root/'pause-new-requests').touch()
            with self.assertRaises(Paused):
                with client.send_slot({},root/'receipt.json','synthetic',b''):
                    self.fail('暂停后不得获得新请求发送许可')


if __name__=='__main__':unittest.main()
