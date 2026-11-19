"""页计划复用必须保留逐字、请求身份及旧unknown门禁。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bounded_read as reading
from runtime import atomic
from test_reading_efficiency import packet
import test_reading_cache as fixtures


class ReadingPlanIndexTests(unittest.TestCase):
    def test_repeat_uses_same_signed_plan_and_exact_page_request_identities(self):
        source=packet()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            expected=reading.plan(source,include_text=False,content_identity=True,efficiency=True)
            first=reading.indexed_plan(source,root,content_identity=True,efficiency=True)
            with patch.object(reading,'plan',side_effect=AssertionError('重复轮次不能重新分页')):
                second=reading.indexed_plan(source,root,content_identity=True,efficiency=True)
            self.assertEqual(expected,first);self.assertEqual(first,second)
            events={e['event']:e for e in source['events']}
            self.assertEqual(reading.reading_identity(expected,events),reading.reading_identity(second,events))
            with patch.object(reading,'reading_identity',side_effect=AssertionError('命中不再重建全部请求schema')):
                indexed,key=reading.indexed_plan(source,root,content_identity=True,efficiency=True,with_identity=True)
            self.assertEqual(first,indexed)
            self.assertEqual(reading.reading_identity(expected,events),key)
            self.assertTrue(all('text' not in f for p in second['pages'] for f in p['fragments']))

    def test_source_policy_and_page_parameters_never_share_wrong_plan(self):
        source=packet()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);first=reading.indexed_plan(source,root,efficiency=True)
            changed=copy.deepcopy(source);changed['events'][0]['text']+='新增失败信息'
            second=reading.indexed_plan(changed,root,efficiency=True)
            third=reading.indexed_plan(source,root,4096,efficiency=True)
            self.assertNotEqual(first['packet_sha256'],second['packet_sha256'])
            self.assertEqual(4096,third['page_bytes'])
            with patch.object(reading,'policy',return_value='new-contract'):
                reading.indexed_plan(source,root,efficiency=True)
            self.assertEqual(4,len(list((root/'.plan-index').glob('*/index.json'))))

    def test_corrupted_or_missing_signed_index_is_not_rebuilt_as_success(self):
        for missing in (False,True):
            with self.subTest(missing=missing),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);reading.indexed_plan(packet(),root,efficiency=True)
                path=next(root.glob('.plan-index/*/pages.json'))
                if missing:path.unlink()
                else:path.write_text('{}')
                with patch.object(reading,'plan',side_effect=AssertionError('损坏不能重建掩盖')):
                    with self.assertRaises((ValueError,OSError)):
                        reading.indexed_plan(packet(),root,efficiency=True)

    def test_cached_plan_does_not_bypass_prior_unknown_and_legacy_keeps_original_path(self):
        source=packet()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);reading.indexed_plan(source,root,efficiency=True)
            atomic(root/'old/plan.json',reading.plan(source,include_text=False))
            path=root/'old/0.receipt.json';atomic(path,dict(status='unknown'))
            before=path.read_bytes()
            result=reading.read_all(source,root,client=SimpleNamespace(reading_efficiency=True),limit=None)
            self.assertEqual('unknown',result['blocked'][0]['state'])
            self.assertEqual(before,path.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertEqual(reading.plan(source,include_text=False),reading.indexed_plan(source,root))
            self.assertFalse((root/'.plan-index').exists())

    def test_derived_reading_identity_corruption_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);reading.indexed_plan(packet(),root,efficiency=True)
            path=next(root.glob('.plan-index/*/index.json'));proof=json.loads(path.read_text())
            proof['reading_identity']='0'*64;atomic(path,proof)
            with self.assertRaisesRegex(ValueError,'执行身份签名'):
                reading.indexed_plan(packet(),root,efficiency=True,with_identity=True)


class ReadingTurnBudgetTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    client=fixtures.ReadingCacheTests.client
    packet=fixtures.ReadingCacheTests.packet

    def test_cached_pages_do_not_scan_budget_twice_per_page_or_spend_again(self):
        source=self.packet(240);client=self.client('reader')
        client.reading_efficiency=True
        first=reading.read_all(source,client.root/'reading',client=client,limit=None)
        sent=self.calls;before=client.budget.path.read_bytes()
        with patch.object(client.budget,'used',wraps=client.budget.used) as used:
            second=reading.read_all(source,client.root/'reading',client=client,limit=None)
        self.assertEqual(10,first['pages']);self.assertEqual(first,second)
        self.assertEqual(2,used.call_count);self.assertEqual(sent,self.calls)
        self.assertEqual(before,client.budget.path.read_bytes())

    def test_turn_and_total_limits_still_block_after_reusing_old_pages(self):
        from pipeline import Paused
        source=self.packet(48)
        client=self.client('limited',limit=2,turn=1);client.reading_efficiency=True
        with self.assertRaises(Paused) as caught:
            reading.read_all(source,client.root/'reading',client=client,limit=None)
        self.assertEqual('queued',caught.exception.state);self.assertEqual(1,self.calls)
        client=self.client('limited',limit=2,turn=1);client.reading_efficiency=True
        result=reading.read_all(source,client.root/'reading',client=client,limit=None)
        self.assertEqual('reading_complete_pending_reconciliation',result['state'])
        self.assertEqual((2,2),(self.calls,client.budget.used()))
        # 新尾页确需第3次发送；累计预算已满，重入不能借前两页缓存重置额度。
        for _ in range(2):
            client=self.client('limited',limit=2,turn=1);client.reading_efficiency=True
            with self.assertRaises(Paused) as caught:
                reading.read_all(self.packet(49),client.root/'reading',client=client,limit=None)
            self.assertEqual('needs_review',caught.exception.state);self.assertEqual(2,self.calls)
            self.assertEqual(2,client.budget.used())

    def test_deleted_budget_during_all_cached_read_is_not_marked_complete(self):
        source=self.packet();client=self.client('reader');client.reading_efficiency=True
        reading.read_all(source,client.root/'reading',client=client,limit=None)
        original=client.read_page
        def changed(*args,**kwargs):
            value=original(*args,**kwargs);client.budget.path.unlink(missing_ok=True)
            return value
        with patch.object(client,'read_page',side_effect=changed),self.assertRaises(ValueError):
            reading.read_all(source,client.root/'reading',client=client,limit=None)
        self.assertFalse(client.budget.path.exists());self.assertEqual(1,self.calls)


if __name__=='__main__':unittest.main()
