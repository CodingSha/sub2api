"""共享周评验签不常驻无后续消费的大文件，保留全部原签名与JSON校验。"""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import weakref

import shared_weeks
from runtime import atomic


class SharedWeekMemoryTests(unittest.TestCase):
    def test_verified_auxiliary_material_is_released_before_semantic_checks(self):
        class Material(dict):
            pass
        class ContinueChecks(Exception):
            pass
        seen = {}; calls = []
        def read(ref):
            value = Material(name=ref['name'])
            calls.append(ref['name']); seen[ref['name']] = weakref.ref(value)
            return value
        def inspect(packet):
            self.assertEqual(list(shared_weeks.FILES), calls)
            self.assertEqual('packet.json', packet['name'])
            for name in ('input.json', 'identified.json', 'native-report.json', 'review.receipt.json'):
                self.assertIsNone(seen[name](), name+'不应叠加在语义校验内存中')
            raise ContinueChecks
        with tempfile.TemporaryDirectory() as tmp:
            entry=dict(state='accepted', policy_sha256='synthetic', directory=tmp,
                files={name:dict(name=name) for name in shared_weeks.FILES})
            with patch('shared_weeks.compatible_policy', return_value=True), \
                 patch('shared_weeks.read_ref', side_effect=read), \
                 patch('shared_weeks.source_identity', side_effect=inspect):
                with self.assertRaises(ContinueChecks):
                    shared_weeks.checked(entry)

    def test_auxiliary_json_and_signatures_are_still_validated(self):
        for target in ('input.json', 'identified.json', 'native-report.json', 'review.receipt.json'):
            for invalid_json in (False, True):
                with self.subTest(target=target, invalid_json=invalid_json), tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp)
                    for name in shared_weeks.FILES:
                        atomic(root/name, {})
                    refs={name:shared_weeks.reference(root/name) for name in shared_weeks.FILES}
                    (root/target).write_text('{invalid-json')
                    if invalid_json:
                        refs[target]['sha256']=hashlib.sha256((root/target).read_bytes()).hexdigest()
                    entry=dict(state='accepted', policy_sha256='synthetic', directory=tmp, files=refs)
                    with patch('shared_weeks.compatible_policy', return_value=True):
                        with self.assertRaises(ValueError):
                            shared_weeks.checked(entry)


if __name__ == '__main__':
    unittest.main()
