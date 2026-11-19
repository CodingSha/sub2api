"""恢复时保留既有证据及崩溃现场；全部使用本地合成回执。"""
import json
from pathlib import Path
import tempfile
import unittest

from context_navigation import Navigation
from context_readback import complete
from pipeline import received
from runtime import atomic, VERSION, REVIEW_POLICY
import test_context_navigation as navigation_tests


class CachedReadback:
    def __init__(self):self.calls=0

    def call(self,unit,receipt):
        if receipt.exists():return json.loads(receipt.read_text())
        self.calls+=1
        value=({'version':VERSION,'review_policy':REVIEW_POLICY,'key':unit['key'],
                'evidence':[],'period_reviews':[],'needs_events':['ctx']}
               if not unit['events'] else {'done':True})
        atomic(receipt,value)
        return value


class LifecycleRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.packet=dict(user_id='synthetic',until='2026-09-14T00:00:00+08:00',
            events=[dict(event='ctx',text='合成反证与未决',at='2026-09-08T00:00:00+08:00')])
        self.unit=dict(user_id='synthetic',key='synthetic:week',events=[])
        self.client=CachedReadback()

    def run_readback(self):
        return complete(self.unit,self.packet,self.root,self.client,lambda record:record)

    def freeze_bytes(self,paths):
        # 使用等价紧凑JSON验证恢复既不改写格式，也不更新时间。
        for path in paths:path.write_text(json.dumps(json.loads(path.read_text()),separators=(',',':')))
        return {p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths}

    def test_readback_reentry_preserves_bytes_and_uses_existing_receipts(self):
        expected=self.run_readback();calls=self.client.calls
        paths=[self.root/'context-readback/input.json',self.root/'context-readback/1/unit.json',
               self.root/'context-readback/1/request.json']
        before=self.freeze_bytes(paths)
        self.assertEqual(expected,self.run_readback())
        self.assertEqual(calls,self.client.calls)
        self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths})

    def test_tampered_readback_unit_and_request_are_preserved_without_new_send(self):
        for name in ('unit.json','request.json'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                self.root=Path(tmp);self.client=CachedReadback();self.run_readback()
                path=self.root/'context-readback/1'/name;path.write_text('{"tampered":true}')
                before=path.read_bytes();calls=self.client.calls
                with self.assertRaisesRegex(ValueError,'检查点内容变化'):self.run_readback()
                self.assertEqual((before,calls),(path.read_bytes(),self.client.calls))

    def test_missing_request_after_unit_write_is_safely_completed_from_cached_receipts(self):
        self.run_readback();calls=self.client.calls
        unit=self.root/'context-readback/1/unit.json';before=self.freeze_bytes([unit])
        request=self.root/'context-readback/1/request.json';expected=request.read_bytes();request.unlink()
        self.run_readback()
        self.assertEqual(expected,request.read_bytes());self.assertEqual(calls,self.client.calls)
        self.assertEqual(before[unit],(unit.read_bytes(),unit.stat().st_mtime_ns))

    def navigation(self,n=1):
        helper=navigation_tests.NavigationTests();packet=helper.packet(n)
        return Navigation(self.root/'navigation',packet),helper.notes(packet),navigation_tests.Reader()

    def test_navigation_tree_and_leaves_reentry_preserves_original_bytes(self):
        for n in (1,40):
            with self.subTest(events=n),tempfile.TemporaryDirectory() as tmp:
                self.root=Path(tmp);nav,notes,client=self.navigation(n)
                expected=nav.build(notes,client,received);calls=client.calls
                paths=[nav.root/'leaves.json',nav.root/'tree.json'];before=self.freeze_bytes(paths)
                self.assertEqual(expected,nav.build(notes,client,received))
                self.assertEqual(calls,client.calls)
                self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths})

    def test_tampered_navigation_tree_and_leaves_reject_without_overwriting_or_new_send(self):
        for name in ('tree.json','leaves.json'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                self.root=Path(tmp);nav,notes,client=self.navigation(40)
                nav.build(notes,client,received);calls=client.calls
                path=nav.root/name;path.write_text('{"tampered":true}');before=path.read_bytes()
                with self.assertRaises(ValueError):nav.build(notes,client,received)
                self.assertEqual((before,calls),(path.read_bytes(),client.calls))

    def test_missing_tree_rebuilds_from_existing_navigation_receipts(self):
        nav,notes,client=self.navigation(40);expected=nav.build(notes,client,received)
        path=nav.root/'tree.json';before=path.read_bytes();calls=client.calls;path.unlink()
        self.assertEqual(expected,nav.build(notes,client,received))
        self.assertEqual((before,calls),(path.read_bytes(),client.calls))


if __name__=='__main__':unittest.main()
