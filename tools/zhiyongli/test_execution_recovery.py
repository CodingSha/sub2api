"""连接恢复只接续原阶段，未知模型结果、合同失败和次数上限均不可绕过。"""
import tempfile
from pathlib import Path
import unittest
from urllib.error import HTTPError,URLError

from execution_recovery import reserve,transient_connection
from runtime import atomic


class ExecutionRecoveryTests(unittest.TestCase):
    def test_only_source_or_idempotent_import_connections_are_transient(self):
        for phase in ('collecting','importing'):
            self.assertTrue(transient_connection(ConnectionRefusedError(),phase))
            self.assertTrue(transient_connection(URLError('synthetic'),phase))
            self.assertFalse(transient_connection(ValueError('contract'),phase))
        for phase in ('understanding','reviewing'):
            self.assertFalse(transient_connection(TimeoutError(),phase))
        self.assertFalse(transient_connection(HTTPError('https://test',401,'',{},None),'importing'))
        self.assertTrue(transient_connection(HTTPError('https://test',503,'',{},None),'importing'))

    def test_restart_preserves_backoff_counts_and_original_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);failure=dict(category='transient_connection',phase='collecting',at=100)
            atomic(root/'failure.json',failure)
            before=(root/'failure.json').read_bytes()
            self.assertFalse(reserve(root,now=159));self.assertTrue(reserve(root,now=160))
            self.assertFalse(reserve(root,now=279));self.assertTrue(reserve(root,now=280))
            self.assertFalse(reserve(root,now=519));self.assertTrue(reserve(root,now=520))
            self.assertFalse(reserve(root,now=10000))
            self.assertEqual(before,(root/'failure.json').read_bytes())

    def test_old_failure_or_model_contract_never_automatically_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for value in (dict(phase='collecting',at=0),
                    dict(category='transient_connection',phase='reviewing',at=0),
                    dict(category='execution_contract',phase='collecting',at=0),
                    dict(category='transient_connection',phase='collecting',at='old-format')):
                atomic(root/'failure.json',value)
                self.assertFalse(reserve(root,now=1000))
            self.assertFalse((root/'connection-recovery.json').exists())
