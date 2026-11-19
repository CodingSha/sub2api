"""预算不足先核真实缓存，不先耗尽首读；仅用合成端点与隔离账簿。"""
import json
from unittest.mock import patch
import unittest

from bounded_read import read_all
from model_flow import ModelFlow
from pipeline import Paused
from runtime import atomic
import test_reading_cache as fixtures


class ReadingCapacityTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    client=fixtures.ReadingCacheTests.client
    packet=fixtures.ReadingCacheTests.packet
    read=fixtures.ReadingCacheTests.read

    def observation(self,client):
        return json.loads(next((client.root/'reading').rglob('capacity-preflight.json')).read_text())['result']

    def test_cold_insufficient_budget_stops_before_first_new_send(self):
        client=self.client('cold',limit=2)
        with self.assertRaises(Paused) as caught:self.read(self.packet(49),client)
        self.assertEqual('needs_review',caught.exception.state)
        self.assertEqual((0,0),(self.calls,client.budget.used()))
        result=self.observation(client)
        self.assertEqual((3,2),(result['known_new_request_lower_bound'],result['personal_remaining']))
        self.assertFalse(result['whole_pipeline_feasibility']);self.assertIn('semantic_audit',result['unestimated_stages'])
        self.assertFalse(list(client.root.rglob('*.receipt.json')))

    def test_existing_personal_use_is_not_treated_as_fresh_budget(self):
        client=self.client('partial',limit=4)
        client.budget.reserve(client.root/'unrelated-a.receipt.json','a')
        client.budget.reserve(client.root/'unrelated-b.receipt.json','b')
        before=client.budget.path.read_bytes()
        with self.assertRaises(Paused):self.read(self.packet(49),client)
        self.assertEqual((0,before),(self.calls,client.budget.path.read_bytes()))
        self.assertEqual(2,self.observation(client)['personal_used'])

    def test_large_hot_plan_passes_with_zero_personal_remaining(self):
        packet=self.packet(49);self.read(packet,self.client('prime'))
        client=self.client('hot',limit=0)
        result=self.read(packet,client)
        self.assertEqual('reading_complete_pending_reconciliation',result['state'])
        self.assertEqual((3,0),(self.calls,client.budget.used()))
        result=self.observation(client)
        self.assertEqual((0,3),(result['known_new_request_lower_bound'],result['existing_receipts_pending_normal_validation']))
        self.assertFalse(result['whole_pipeline_feasibility'])

    def test_mixed_plan_only_counts_missing_pages_and_preserves_prefix(self):
        self.read(self.packet(),self.client('prime'));client=self.client('mixed',limit=1)
        before={p:p.read_bytes() for p in (self.root/'shared').rglob('*.receipt.json')}
        with self.assertRaises(Paused):self.read(self.packet(49),client)
        self.assertEqual((1,0),(self.calls,client.budget.used()))
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        self.assertEqual(2,self.observation(client)['known_new_request_lower_bound'])

    def test_enough_for_exact_new_tail_is_not_rejected_by_total_pages(self):
        self.read(self.packet(),self.client('prime'));client=self.client('tail',limit=1)
        self.assertEqual('reading_complete_pending_reconciliation',self.read(self.packet(25),client)['state'])
        self.assertEqual((2,1),(self.calls,client.budget.used()))
        self.assertEqual(1,self.observation(client)['known_new_request_lower_bound'])

    def test_other_request_contract_is_not_a_hot_hit(self):
        source=self.packet(49);self.read(source,self.client('prime'));client=self.client('other-contract',limit=1)
        original=client.reading_payload
        def changed(*args):return dict(original(*args),instructions='另一个真实请求合同')
        with patch.object(client,'reading_payload',side_effect=changed):
            with self.assertRaises(Paused):self.read(source,client)
        self.assertEqual((3,0),(self.calls,client.budget.used()))
        self.assertEqual(3,self.observation(client)['known_new_request_lower_bound'])

    def test_global_existing_use_blocks_before_personal_budget_is_spent(self):
        config=dict(version=1,budget_key='capacity-test',approved_by='合成确认人',reason='合成容量测试',
            max_concurrency=2,requests_per_minute=100,reserved_tokens_per_minute=10000000,
            max_requests=4,transient_failure_limit=2,cooldown_seconds=60)
        flow=ModelFlow(self.root/'flow',config);first=self.client('prime');first.flow=flow
        self.read(self.packet(49),first)
        other=self.client('next',limit=100);other.flow=flow
        source=self.packet(25);source['user_id']='2'
        with self.assertRaises(Paused):self.read(source,other)
        self.assertEqual((3,0),(self.calls,other.budget.used()))
        self.assertEqual(dict(budget_key='capacity-test',used=3,limit=4,remaining=1),flow.remaining_requests())
        self.assertEqual(1,self.observation(other)['global_budget']['remaining'])

    def test_unknown_later_cached_page_cannot_follow_a_new_send(self):
        source=self.packet(49);self.read(source,self.client('prime'))
        from reading_cache import ReadingCache
        from bounded_read import plan
        from runtime import digest
        first=self.client('probe');cache=ReadingCache(first.reading_cache_root,
            dict(user_id=source['user_id'],user_email=source['user_email']),first)
        page=plan(source)['pages'][1];receipt=next((cache.root/digest(page)).glob('*.receipt.json'))
        raw=json.loads(receipt.read_text());raw['status']='unknown';atomic(receipt,raw)
        source['events'][0]['text']+='新增反证';other=self.client('blocked',limit=1)
        with self.assertRaises(Paused) as caught:self.read(source,other)
        self.assertEqual('unknown',caught.exception.state);self.assertEqual((3,0),(self.calls,other.budget.used()))

    def test_insufficient_observation_can_recheck_real_cache_without_raising_budget(self):
        source=self.packet(49);client=self.client('limited',limit=1)
        with self.assertRaises(Paused):self.read(source,client)
        self.assertEqual(0,self.calls)
        self.read(source,self.client('prime'))
        self.assertEqual('reading_complete_pending_reconciliation',self.read(source,client)['state'])
        self.assertEqual((3,0),(self.calls,client.budget.used()))
        self.assertEqual(1,client.limit)

    def test_successful_observation_is_stable_and_not_rescanned_each_turn(self):
        source=self.packet(49);client=self.client('stable',limit=3,turn=1)
        with self.assertRaises(Paused) as caught:read_all(source,client.root/'reading',client=client,limit=None)
        self.assertEqual('queued',caught.exception.state)
        path=next((client.root/'reading').rglob('capacity-preflight.json'));before=(path.read_bytes(),path.stat().st_mtime_ns)
        client=self.client('stable',limit=3,turn=1)
        with patch('reading_cache.ReadingCache.inspect',side_effect=AssertionError('不重复扫描整个缓存')):
            with self.assertRaises(Paused) as caught:read_all(source,client.root/'reading',client=client,limit=None)
        self.assertEqual('queued',caught.exception.state)
        self.assertEqual(before,(path.read_bytes(),path.stat().st_mtime_ns))
        self.assertEqual(2,self.calls)

    def test_capacity_artifact_corruption_is_not_ignored(self):
        client=self.client('bad-proof');self.read(self.packet(),client)
        path=next((client.root/'reading').rglob('capacity-preflight.json'));value=json.loads(path.read_text())
        value['result']['whole_pipeline_feasibility']=True;atomic(path,value)
        with self.assertRaisesRegex(ValueError,'容量记录'):self.read(self.packet(),client)
        self.assertEqual(1,self.calls)

    def test_saved_observation_never_reserves_or_overrides_later_global_consumption(self):
        config=dict(version=1,budget_key='shared-observation',approved_by='合成确认人',reason='合成共享余额测试',
            max_concurrency=2,requests_per_minute=100,reserved_tokens_per_minute=10000000,
            max_requests=3,transient_failure_limit=2,cooldown_seconds=60)
        flow=ModelFlow(self.root/'flow',config);prime=self.client('prime');prime.flow=flow
        self.read(self.packet(),prime)
        first=self.client('rolling',limit=100,turn=1);first.flow=flow
        with self.assertRaises(Paused) as caught:read_all(self.packet(49),first.root/'reading',client=first,limit=None)
        self.assertEqual('queued',caught.exception.state)
        self.assertEqual(2,self.observation(first)['known_new_request_lower_bound'])
        other=self.client('another-person');other.flow=flow
        source=self.packet(1);source['user_id']='2';self.read(source,other)
        self.assertEqual(0,flow.remaining_requests()['remaining'])
        resumed=self.client('rolling',limit=100,turn=1);resumed.flow=flow
        with self.assertRaises(Paused) as caught:read_all(self.packet(49),resumed.root/'reading',client=resumed,limit=None)
        self.assertEqual('needs_review',caught.exception.state)
        self.assertEqual((3,1),(self.calls,resumed.budget.used()))


if __name__=='__main__':unittest.main()
