"""日常局部导航到封口的复用及损坏/未知隔离；仅使用合成端点。"""
import copy
import json
import unittest
from unittest.mock import patch
from pathlib import Path

from context_navigation import Navigation,check_cached_receipt
from pipeline import received,Paused
from runtime import atomic
import test_context_navigation as navtests
import test_reading_cache as fixtures


class NavigationCacheTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    client=fixtures.ReadingCacheTests.client
    def build(self,name,packet,notes=None):
        client=self.client(name);client.navigation_cache_root=self.root/'branches'
        nav=Navigation(client.root/'navigation',packet)
        result=nav.build(notes or navtests.NavigationTests().notes(packet),client,received)
        return nav,result,client

    def test_preparation_and_freeze_reuse_all_branches_without_new_job_budget(self):
        p=navtests.NavigationTests().packet(65);p['preparation_only']=True
        p['source_snapshot']='daily'
        first,tree,_=self.build('daily',p);sent=self.calls
        self.assertEqual(3,sent)
        frozen=copy.deepcopy(p);frozen.pop('preparation_only');frozen['source_snapshot']='frozen'
        nav,final,client=self.build('frozen',frozen)
        self.assertEqual((sent,0),(self.calls,client.budget.used()))
        self.assertEqual(tree['root'],final['root'])
        self.assertNotEqual(first.identity,nav.identity)
        # 每棵树仍绑定不同完整快照，且只能读取自身节点。
        with self.assertRaises(ValueError):Navigation(nav.root,p)

    def test_append_only_rebuilds_changed_tail_and_ancestors(self):
        self.build('daily',navtests.NavigationTests().packet(65));sent=self.calls
        p=navtests.NavigationTests().packet(66);nav,tree,client=self.build('freeze',p)
        self.assertEqual((sent+2,2),(self.calls,client.budget.used()))
        pending=[tree['root']];ids=[]
        while pending:
            key=pending.pop();node=nav.load(key)
            if node['kind']=='branch':pending.extend(node['children'])
            else:ids.append(nav.read([key],raw=True)['nodes'][0]['event_id'])
        self.assertEqual(set(str(i) for i in range(66)),set(ids))

    def test_raw_revision_invalidates_branch_even_when_reading_summary_is_unchanged(self):
        p=navtests.NavigationTests().packet(65);self.build('daily',p);sent=self.calls
        p['events'][0]['text']='原文已修订，摘要相同不能证明仍是同一来源'
        _,_,client=self.build('changed',p)
        self.assertEqual((sent+2,2),(self.calls,client.budget.used()))

    def test_other_person_and_calendar_never_reuse(self):
        p=navtests.NavigationTests().packet(2);self.build('a',p)
        p['user_id']='another';self.build('b',p)
        p['workday_scope']={'signed_calendar':'new'};self.build('c',p)
        self.assertEqual(3,self.calls)

    def test_unknown_shared_navigation_blocks_new_snapshot_without_resending(self):
        p=navtests.NavigationTests().packet(2);self.build('daily',p)
        receipt=next((self.root/'branches').rglob('*.receipt.json'))
        value=json.loads(receipt.read_text());value['status']='unknown';atomic(receipt,value)
        p['source_snapshot']='later'
        with self.assertRaises(Paused) as caught:self.build('frozen',p)
        self.assertEqual('unknown',caught.exception.state);self.assertEqual(1,self.calls)

    def test_tree_external_receipt_tamper_is_detected_on_reuse(self):
        p=navtests.NavigationTests().packet(2);nav,_,_=self.build('daily',p)
        proof=next(nav.root.glob('*.cache.json'))
        check_cached_receipt(proof)
        receipt=Path(json.loads(proof.read_text())['receipt'])
        record=json.loads(receipt.read_text());record['response']['changed']=True;atomic(receipt,record)
        with self.assertRaises(ValueError):check_cached_receipt(proof)
        p['source_snapshot']='later'
        with self.assertRaises(ValueError):self.build('frozen',p)
        self.assertEqual(1,self.calls)

    def test_crash_after_reservation_cannot_replay_from_another_snapshot(self):
        p=navtests.NavigationTests().packet(2)
        client=self.client('daily');client.navigation_cache_root=self.root/'branches'
        reserve=client.budget.reserve
        def crash(*args):
            reserve(*args)
            raise RuntimeError('合成预算预留后、回执落盘前中断')
        with patch.object(client.budget,'reserve',side_effect=crash):
            with self.assertRaises(RuntimeError):
                Navigation(client.root/'navigation',p).build(navtests.NavigationTests().notes(p),client,received)
        p['source_snapshot']='freeze'
        with self.assertRaises(Paused) as caught:self.build('frozen',p)
        self.assertEqual('unknown',caught.exception.state);self.assertEqual(0,self.calls)

    def test_existing_tree_request_contract_drift_blocks_before_new_send(self):
        p=navtests.NavigationTests().packet(2);nav,_,client=self.build('daily',p)
        payload=client.navigation_payload
        def changed(*args):return dict(payload(*args),instructions='合成合同变化')
        with patch.object(client,'navigation_payload',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'本次未发送新请求'):
                nav.build(navtests.NavigationTests().notes(p),client,received)
        self.assertEqual(1,self.calls)
