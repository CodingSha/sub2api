"""有界归并的跨批连续性、独立性、非传递冲突、覆盖和续跑；不证明模型语义。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from bounded_read import raw_size
from goal_reconciliation import plan, reconcile, checked
from pipeline import received, Paused
from runtime import atomic
from test_execution_paths import record


class Comparator:
    def __init__(self, groups=None, nontransitive=False, unresolved=False):
        self.calls=0;self.inputs=[];self.groups=groups or {};self.nontransitive=nontransitive;self.unresolved=unresolved

    def merge_goals(self,data,schema,path):
        if path.exists():return json.loads(path.read_text())
        self.calls+=1;self.inputs.append(copy.deepcopy(data));buckets={}
        ids=[g['local_goal_id'] for g in data['local_goals']]
        for goal in data['local_goals']:
            key=goal['local_goal_id']
            bucket=self.groups.get(key,key)
            if self.nontransitive and set(ids) in ({'a','b'},{'b','c'}):bucket='same'
            buckets.setdefault(bucket,[]).append(key)
        return record(dict(goals=[dict(goal_id=str(index),title='合成目标'+key,purpose='仅验证结构',
            boundary='unresolved' if self.unresolved and len(ids)>1 else 'confirmed',boundary_reason='合成语义夹具',
            local_goal_ids=values,context_event_ids=[]) for index,(key,values) in enumerate(buckets.items())]),path)


class GoalReconciliationTests(unittest.TestCase):
    def data(self, count=3):
        return dict(since='2026-09-07',until='2026-09-14',reading_notes=[dict(summary='失败与未决完整保留')],
            local_goals=[dict(local_goal_id=chr(97+i),goal=dict(title='目标'+str(i),purpose='合成',
                boundary='confirmed',boundary_reason='明确表达',context_event_ids=[]),
                expressions=[dict(event_id=str(i),human_quotes=['完整表达'+str(i)+'😀\\n'*200])]) for i in range(count)])

    def limit(self,data):
        base={k:v for k,v in data.items() if k!='local_goals'}
        return raw_size(dict(base,local_goals=[]))+2*(max(raw_size([g]) for g in data['local_goals'])+20)+32

    def test_every_block_pair_keeps_exact_expressions_and_independent_goals(self):
        data=self.data(9);limit=self.limit(data);base,groups=plan(data,limit)
        self.assertEqual(9,len(groups))
        client=Comparator()
        with tempfile.TemporaryDirectory() as tmp:
            result=reconcile(data,tmp,client,received,max_bytes=limit)
            self.assertEqual(45,client.calls);self.assertEqual(9,len(result['goals']))
            originals={g['local_goal_id']:g for g in data['local_goals']}
            for request in client.inputs:
                self.assertLessEqual(raw_size(request),limit)
                self.assertEqual(data['reading_notes'],request['reading_notes'])
                for goal in request['local_goals']:self.assertEqual(originals[goal['local_goal_id']],goal)
            self.assertTrue(all(g['boundary']=='confirmed' for g in result['goals']))
            calls=client.calls;self.assertEqual(result,reconcile(data,tmp,client,received,max_bytes=limit))
            self.assertEqual(calls,client.calls)

    def test_cross_block_continuation_combines_and_nontransitive_conflict_stays_unresolved(self):
        data=self.data();limit=self.limit(data)
        for client,number,state in ((Comparator(dict(a='same',b='same',c='same')),1,'confirmed'),
                                    (Comparator(nontransitive=True),1,'unresolved'),
                                    (Comparator(unresolved=True),3,'unresolved')):
            with self.subTest(state=state,number=number),tempfile.TemporaryDirectory() as tmp:
                result=reconcile(data,tmp,client,received,max_bytes=limit)
                self.assertEqual(number,len(result['goals']))
                self.assertTrue(all(g['boundary']==state for g in result['goals']))
                self.assertEqual(['a','b','c'],sorted(i for g in result['goals'] for i in g['local_goal_ids']))

    def test_invalid_pair_partition_or_context_is_rejected(self):
        data=self.data();goal=dict(goal_id='x',title='合成',purpose='合成',boundary='confirmed',
            boundary_reason='合成',local_goal_ids=['a','b','c'],context_event_ids=[])
        for change in (dict(local_goal_ids=['a']),dict(local_goal_ids=['a','a','c']),
                       dict(context_event_ids=['other-user']),dict(boundary='guess')):
            with self.subTest(change=change),self.assertRaises(ValueError):checked(data,dict(goals=[dict(goal,**change)]))

    def test_navigation_or_single_goal_capacity_fails_before_any_model(self):
        data=self.data();client=Comparator()
        for changed in (dict(data,reading_notes=[dict(summary='不可删的导航'*10000)]),
                        dict(data,local_goals=[dict(data['local_goals'][0],expressions=['不可截断'*10000])])):
            with tempfile.TemporaryDirectory() as tmp,self.assertRaises(ValueError):
                reconcile(changed,tmp,client,received,max_bytes=4000)
        self.assertEqual(0,client.calls)

    def test_unknown_or_changed_plan_never_resends_previous_comparisons(self):
        data=self.data();client=Comparator();limit=self.limit(data)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);reconcile(data,root,client,received,max_bytes=limit);calls=client.calls
            path=next(root.glob('*.receipt.json'));atomic(path,dict(status='unknown'))
            with self.assertRaises(Paused) as error:reconcile(data,root,client,received,max_bytes=limit)
            self.assertEqual('unknown',error.exception.state);self.assertEqual(calls,client.calls)
            with self.assertRaisesRegex(ValueError,'来源或容量合同变化'):
                reconcile(data,root,client,received,max_bytes=limit+1)
            self.assertEqual(calls,client.calls)

    def test_actual_batch_identifier_routes_large_merge_and_keeps_every_original_input(self):
        from goal_batches import identify
        events=[dict(event=str(i),text='本人完整表达'+str(i)+'原文'*1500,actor_class='candidate_user',
            at='2026-09-07T10:00:00+08:00',timestamp_unknown=False) for i in range(24)]
        packet=dict(user_id='test',since='2026-09-07T00:00:00+08:00',until='2026-09-14T00:00:00+08:00',
            events=events,reading_notes=[dict(summary='全局导航中的失败与未决保留')])
        class Identifier(Comparator):
            def identify(self,page,path):
                if path.exists():return json.loads(path.read_text())
                return record(dict(materials={e['event']:dict(classification='human',human_quotes=[e['text']],
                    reason='合成本人表达',duplicate_of=None,goal_id='g'+e['event'],step='合成步骤',assignment_reason='合成归属')
                    for e in page['events']},goals=[dict(goal_id='g'+e['event'],title='合成目标'+e['event'],purpose='合成独立目标',
                        boundary='confirmed',boundary_reason='合成边界',context_event_ids=[]) for e in page['events']]),path)
        client=Identifier()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);result=identify(packet,root,client,received)
            self.assertEqual(24,len(result['materials']));self.assertEqual(24,len(result['goals']))
            originals={e['event']:e['text'] for e in events}
            self.assertEqual(originals,{m['event_id']:m['human_quotes'][0] for m in result['materials']})
            manifest=json.loads((root/'global-comparisons/plan.json').read_text())
            self.assertGreater(manifest['comparisons'],1)
            self.assertTrue(all(raw_size(p)<=85000 for p in client.inputs))
            calls=client.calls;self.assertEqual(result,identify(packet,root,client,received));self.assertEqual(calls,client.calls)

    def test_shared_week_rejects_changed_global_comparison_receipt(self):
        from pipeline import evaluate_week
        from test_execution_paths import FixtureClient, packet
        source=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'week';path=directory/'identification/global-comparisons/test.receipt.json'
            record(dict(goals=[]),path)
            client=FixtureClient();evaluate_week(source,directory,client,shared_root=root/'shared');calls=client.calls
            changed=json.loads(path.read_text());changed['modified']='签名测试';atomic(path,changed)
            with self.assertRaisesRegex(ValueError,'签名变化'):
                evaluate_week(source,root/'reuse',client,shared_root=root/'shared')
            self.assertEqual(calls,client.calls)

    def test_large_single_goal_can_compare_with_small_goals_without_half_budget_gate(self):
        data=self.data(12)
        for g in data['local_goals']:g['expressions']=['短表达']
        data['local_goals'][0]['expressions']=['大目标完整表达'*500]
        largest=raw_size([data['local_goals'][0]])
        base={k:v for k,v in data.items() if k!='local_goals'}
        limit=raw_size(dict(base,local_goals=[]))+largest+600
        client=Comparator()
        with tempfile.TemporaryDirectory() as tmp:
            result=reconcile(data,tmp,client,received,max_bytes=limit)
            self.assertEqual(12,len(result['goals']))
            self.assertGreater(largest,limit//2)
            self.assertTrue(all(raw_size(p)<=limit for p in client.inputs))

    def test_one_new_request_per_turn_resumes_same_comparisons(self):
        data=self.data();limit=self.limit(data)
        class OneTurn(Comparator):
            def merge_goals(self,data,schema,path):
                if not path.exists() and self.calls>=1:raise Paused('queued','合成每轮仅一个新请求')
                return super().merge_goals(data,schema,path)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);total=0;result=None
            for _ in range(6):
                client=OneTurn()
                try:result=reconcile(data,root,client,received,max_bytes=limit)
                except Paused as ex:self.assertEqual('queued',ex.state)
                total+=client.calls
            self.assertEqual(6,total);self.assertEqual(3,len(result['goals']))
            self.assertEqual(6,len(list(root.glob('*.receipt.json'))))


if __name__=='__main__':unittest.main()
