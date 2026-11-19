"""冷导航成本必须在首读前可见；合成请求不证明真实质量或期限。"""
import json
from pathlib import Path
import unittest

from bounded_read import plan,read_all
from context_navigation import local_identity,Navigation
from model_flow import ModelFlow
from pipeline import Paused,reading_view
from reading_cache import ReadingCache
from runtime import atomic,digest
import test_reading_cache as fixtures


class WorkflowCapacityTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp

    def packet(self):
        value=fixtures.ReadingCacheTests.packet(self,73)
        value.update(since='2026-11-02T00:00:00+08:00',until='2026-11-09T00:00:00+08:00')
        for index,event in enumerate(value['events']):
            event['text']='合成失败反证'+str(index)+'不省略原文。'*100
        return value

    def client(self,name,limit=100):
        client=fixtures.ReadingCacheTests.client(self,name,limit)
        client.reading_plan_root=self.root/'plans'
        client.navigation_cache_root=self.root/'navigation'
        return client

    def view(self,client,packet=None):
        return reading_view(packet or self.packet(),client.root,client,lambda *args:None)

    def observation(self,client):
        return json.loads((client.root/'workflow-capacity.json').read_text())['result']

    def test_cold_navigation_blocks_even_when_reading_pages_alone_fit(self):
        client=self.client('cold',4)
        self.assertEqual(4,len(plan(self.packet())['pages']))
        before=client.budget.path.read_bytes()
        with self.assertRaises(Paused) as error:self.view(client)
        self.assertEqual('needs_review',error.exception.state)
        self.assertEqual((0,before),(self.calls,client.budget.path.read_bytes()))
        proof=self.observation(client)
        self.assertEqual((4,3,7),(proof['known_new_reading_request_lower_bound'],
            proof['known_new_navigation_request_lower_bound'],proof['known_new_request_lower_bound']))
        self.assertFalse(proof['whole_pipeline_feasibility'])
        self.assertIn('semantic_audit',proof['unestimated_stages'])
        self.assertFalse((client.root/'context-navigation').exists())
        self.assertFalse(list(client.root.rglob('*.receipt.json')))

    def test_fusion_counts_pages_and_preserves_independent_later_work(self):
        client=self.client('fusion',5)
        client.reading_efficiency=client.reading_content_identity=True
        client.reading_page_navigation=True
        self.view(client)
        proof=self.observation(client)
        self.assertEqual((4,1),(proof['semantic_navigation_inputs'],proof['structural_navigation_request_lower_bound']))
        self.assertEqual(5,client.budget.used())
        self.assertFalse(proof['whole_pipeline_feasibility'])
        self.assertFalse((client.root/'bundle.json').exists())

    def test_hot_reading_and_cold_navigation_count_only_new_work(self):
        prime=self.client('prime');read_all(self.packet(),prime.root/'reading',client=prime,limit=None)
        self.assertEqual(4,self.calls)
        client=self.client('hot-reading',4);self.view(client)
        proof=self.observation(client)
        self.assertEqual((0,3),(proof['known_new_reading_request_lower_bound'],proof['known_new_navigation_request_lower_bound']))
        self.assertEqual((8,4),(self.calls,client.budget.used()))

    def test_hot_navigation_is_not_rejected_with_zero_remaining(self):
        self.view(self.client('prime'));calls=self.calls
        client=self.client('hot',0);self.view(client)
        proof=self.observation(client)
        self.assertEqual('existing_evidence_pending_normal_validation',proof['navigation_cache_state'])
        self.assertEqual((calls,0),(self.calls,client.budget.used()))
        self.assertEqual(0,proof['known_new_navigation_request_lower_bound'])
        self.assertIn('navigation',proof['unestimated_stages'])

    def test_public_plan_receipts_without_shared_reading_cache_are_not_counted_new(self):
        prime=self.client('prime');prime.reading_cache_root=None
        read_all(self.packet(),prime.root/'reading',client=prime,limit=None)
        client=self.client('local-plan',4);client.reading_cache_root=None
        self.view(client)
        proof=self.observation(client)
        self.assertEqual((4,0),(proof['existing_reading_receipts_pending_normal_validation'],
            proof['known_new_reading_request_lower_bound']))
        self.assertEqual((8,4),(self.calls,client.budget.used()))

    def test_unknown_later_shared_page_stops_before_new_first_page(self):
        packet=self.packet();prime=self.client('prime')
        read_all(packet,prime.root/'reading',client=prime,limit=None)
        cache=ReadingCache(prime.reading_cache_root,dict(user_id=packet['user_id'],user_email=packet['user_email']),prime)
        receipt=next((cache.root/digest(plan(packet)['pages'][2])).glob('*.receipt.json'))
        value=json.loads(receipt.read_text());value['status']='unknown';atomic(receipt,value)
        packet['events'][0]['text']+='新增未决'
        client=self.client('unknown',4)
        with self.assertRaises(Paused) as error:self.view(client,packet)
        self.assertEqual('unknown',error.exception.state)
        self.assertEqual((4,0),(self.calls,client.budget.used()))

    def test_direct_small_packet_does_not_acquire_fictitious_navigation_cost(self):
        packet=fixtures.ReadingCacheTests.packet(self,73);client=self.client('direct',0)
        self.assertEqual(packet,self.view(client,packet))
        self.assertEqual(0,self.calls)
        self.assertFalse((client.root/'workflow-capacity.json').exists())

    def test_global_balance_is_used_without_spending_personal_budget(self):
        config=dict(version=1,budget_key='workflow-capacity',approved_by='合成',reason='容量回归',
            max_concurrency=1,requests_per_minute=100,reserved_tokens_per_minute=10000000,
            max_requests=4,transient_failure_limit=2,cooldown_seconds=60)
        client=self.client('global');client.flow=ModelFlow(self.root/'flow',config)
        with self.assertRaises(Paused):self.view(client)
        proof=self.observation(client)
        self.assertEqual((4,7),(proof['global_budget']['remaining'],proof['known_new_request_lower_bound']))
        self.assertEqual((0,0,0),(self.calls,client.budget.used(),client.flow.remaining_requests()['used']))

    def test_insufficient_observation_rechecks_warmed_cache_without_increasing_limit(self):
        client=self.client('limited',4)
        with self.assertRaises(Paused):self.view(client)
        blocks={p:p.read_bytes() for p in (client.root/'capacity-blocks').glob('*.json')}
        self.view(self.client('prime'));calls=self.calls
        self.view(client)
        self.assertEqual((calls,0,4),(self.calls,client.budget.used(),client.limit))
        self.assertEqual(blocks,{p:p.read_bytes() for p in blocks})

    def test_tampered_observation_is_not_overwritten_as_success(self):
        client=self.client('tamper',4)
        with self.assertRaises(Paused):self.view(client)
        path=client.root/'workflow-capacity.json';value=json.loads(path.read_text())
        value['result']['whole_pipeline_feasibility']=True;atomic(path,value)
        before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'容量观测签名'):self.view(client)
        self.assertEqual((0,before),(self.calls,path.read_bytes()))

    def test_preflight_namespace_is_exactly_the_navigation_namespace(self):
        packet=self.packet();client=self.client('identity',4)
        with self.assertRaises(Paused):self.view(client)
        proof=self.observation(client)
        expected=Navigation(self.root/'separate-tree',packet).local_identity
        self.assertEqual(local_identity(packet),expected)
        self.assertEqual(digest(expected),proof['navigation_scope_sha256'])
        # 其他身份的缓存不能让本人的冷导航被标成可复用。
        unrelated=client.navigation_cache_root/'another-user';unrelated.mkdir(parents=True)
        (unrelated/'evidence').write_text('合成其他身份记录')
        with self.assertRaises(Paused):self.view(client)
        self.assertEqual('observed_empty',self.observation(client)['navigation_cache_state'])

    def test_single_large_candidate_still_uses_hierarchical_guard_below_direct_limit(self):
        packet=self.packet();packet['events']=packet['events'][:1]
        packet['events'][0].update(text='x'*86_000,actor_class='candidate_user',timestamp_unknown=False)
        client=self.client('large-single',1)
        with self.assertRaises(Paused):self.view(client,packet)
        proof=self.observation(client)
        self.assertEqual((1,1),(proof['pages'],proof['known_new_navigation_request_lower_bound']))
        self.assertEqual(0,self.calls)

    def test_program_leaves_do_not_inflate_paid_navigation_lower_bound(self):
        packet=self.packet()
        packet['events'] += [dict(packet['events'][0],event='empty-'+str(i),text='') for i in range(1000)]
        client=self.client('program-leaves',4);client.reading_efficiency=True
        with self.assertRaises(Paused):self.view(client,packet)
        self.assertEqual(73,self.observation(client)['semantic_navigation_inputs'])
        self.assertEqual(3,self.observation(client)['known_new_navigation_request_lower_bound'])
        self.assertEqual(0,self.calls)

    def test_capacity_observations_belong_to_each_budget_and_preserve_legacy_record(self):
        packet=self.packet();prime=self.client('prime')
        read_all(packet,prime.root/'reading',client=prime,limit=None)
        first=next((self.root/'plans').rglob('capacity-preflight.json'))
        legacy=first.parents[2]/'capacity-preflight.json'
        legacy.write_text('{"legacy_record":"保留旧观测，不用于当前预算授权"}')
        before=(first.read_bytes(),legacy.read_bytes())
        other=self.client('different-budget',0)
        read_all(packet,other.root/'reading',client=other,limit=None)
        observations=list((self.root/'plans').glob('*/'+first.parents[2].name+'/capacity-preflights/*/capacity-preflight.json'))
        self.assertEqual(2,len(observations))
        self.assertEqual(before,(first.read_bytes(),legacy.read_bytes()))
        self.assertEqual({str(prime.budget.path.resolve()),str(other.budget.path.resolve())},
            {json.loads(p.read_text())['identity']['request_budget_path'] for p in observations})
        self.assertEqual((4,0),(self.calls,other.budget.used()))

    def test_existing_navigation_identity_bytes_are_not_rewritten_by_inspection(self):
        for fusion in (False,True):
            with self.subTest(fusion=fusion):
                root=self.root/('fusion-tree' if fusion else 'legacy-tree')
                navigation=Navigation(root,self.packet(),page_navigation=fusion)
                path=root/'input.json'
                # 既有签名绑定原字节；字段相同也不能仅为序列化样式重新写入。
                path.write_text(json.dumps(navigation.identity,separators=(',',':')))
                before=(path.read_bytes(),path.stat().st_mtime_ns)
                Navigation(root,self.packet(),page_navigation=fusion)
                self.assertEqual(before,(path.read_bytes(),path.stat().st_mtime_ns))


if __name__=='__main__':unittest.main()
