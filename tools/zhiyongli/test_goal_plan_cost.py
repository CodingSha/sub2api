"""准确字节累计须保留原规划边界、完整原文、异常及排序。"""
import copy
import random
import unittest
from unittest.mock import patch

import goal_batches
import goal_reconciliation
from bounded_read import raw_size
from runtime import digest, moment
import test_goal_batches as batch_fixtures
import test_goal_reconciliation as merge_fixtures


def old_batches(packet, max_bytes=85000):
    """保留优化前逐次编码算法，作为固定行为对照。"""
    base={k:copy.deepcopy(v) for k,v in packet.items() if k not in {'events','reading_provenance'}}
    if packet.get('reading_provenance'):
        base['reading_provenance_sha256']=digest(packet['reading_provenance'])
    base['source_scope']='partition_of_frozen_window'
    if packet.get('candidate_segmentation'):
        size=raw_size(dict(base,events=[]))
        max_bytes=min(max_bytes,size+(85_000-size)//2-2_000)
    if raw_size(dict(base,events=[]))>=max_bytes:
        raise ValueError('全局上下文导航已超过分批容量，须继续分层整理；不能删除导航后冒称完整上下文')
    pages=[];current=[]
    for event in sorted(packet['events'],key=lambda e:moment(e['at'])):
        if raw_size(dict(base,events=[event]))>max_bytes:
            raise ValueError('单条完整输入超过归属批次容量，须专门分段核归属，不能截断')
        if current and raw_size(dict(base,events=current+[event]))>max_bytes:
            pages.append(dict(base,events=current));current=[]
        current.append(copy.deepcopy(event))
    if current:pages.append(dict(base,events=current))
    return pages


def old_groups(data, max_bytes=85000):
    """原两组比较装配；保留原组内和组间判断集合。"""
    base={k:copy.deepcopy(v) for k,v in data.items() if k!='local_goals'}
    goals=data['local_goals'];ids=[g['local_goal_id'] for g in goals]
    if len(ids)!=len(set(ids)):raise ValueError('全局归并局部目标标识重复')
    base_bytes=raw_size(dict(base,local_goals=[]))
    if base_bytes>=max_bytes:raise ValueError('目标归并的导航本身超容量，须先完成分层导航访问')
    if not goals:return base,[]
    if raw_size(data)<=max_bytes:return base,[copy.deepcopy(goals)]
    sizes=[raw_size([goal]) for goal in goals]
    largest=max(sizes);half=(max_bytes-base_bytes+3)//2
    allowance=min(half,max_bytes-base_bytes-largest+3)
    groups=[];current=[]
    for goal,size in zip(goals,sizes):
        if size>half:
            if base_bytes+size-2>max_bytes:
                raise ValueError('单个目标的完整表达超出比较容量，须分层核对，不裁掉原句')
            if current:groups.append(current);current=[]
            groups.append([copy.deepcopy(goal)]);continue
        if size>allowance:
            raise ValueError('两目标的完整表达无法共同装配，须分层核对，不裁掉原句')
        if current and raw_size(current+[goal])>allowance:groups.append(current);current=[]
        current.append(copy.deepcopy(goal))
    if current:groups.append(current)
    largest_groups=sorted((raw_size(g) for g in groups),reverse=True)[:2]
    if len(largest_groups)==2 and base_bytes+sum(largest_groups)-3>max_bytes:
        raise ValueError('两目标组的完整表达无法共同装配，不发送部分比较')
    return base,groups


def outcome(function,*args):
    try:return ('value',function(*args))
    except ValueError as error:return ('error',str(error))


class GoalPlanCostTests(unittest.TestCase):
    def packet(self):
        fixture=batch_fixtures.GoalBatchTests();fixture.setUp();return fixture.packet

    def test_batches_keep_exact_boundaries_and_one_byte_capacity_edges(self):
        packet=self.packet();packet['events']*=7
        packet=copy.deepcopy(packet)
        for i,event in enumerate(packet['events']):
            packet['events'][i]=dict(event,event=str(i),text='中文🙂\n\t"\\'*(i+1),
                at='2026-09-07T'+('11' if i%2 else '10')+':00:00+08:00',extra=[None,True,{'a':'\u0000'}])
        for segmented in (False,True):
            value=copy.deepcopy(packet)
            if segmented:value['candidate_segmentation']={'version':'synthetic'}
            for cap in (600,1000,4000,85000):
                self.assertEqual(outcome(old_batches,value,cap),outcome(goal_batches.plan,value,cap))
        single=dict(packet,events=packet['events'][:1]);page=old_batches(single)[0];size=raw_size(page)
        for cap in (size-1,size,size+1):
            self.assertEqual(outcome(old_batches,single,cap),outcome(goal_batches.plan,single,cap))

    def test_seeded_plans_and_errors_match_original_without_mutating_source(self):
        rng=random.Random(116)
        for _ in range(40):
            packet=self.packet();packet['reading_notes']=[dict(summary='公共导航🙂"\\'*rng.randrange(30))]
            packet['reading_provenance']={'完整来源':[None,{'fact':'保留反证'}]}
            packet['events']=[dict(packet['events'][0],event=str(i),text='原文🙂"\\'*rng.randrange(80))
                              for i in range(rng.randrange(24))]
            before=copy.deepcopy(packet);cap=rng.randrange(150,7000)
            self.assertEqual(outcome(old_batches,packet,cap),outcome(goal_batches.plan,packet,cap))
            self.assertEqual(before,packet)

    def test_comparison_groups_match_original_including_mixed_large_goals(self):
        rng=random.Random(117)
        for count in (0,1,3,12):
            for _ in range(12):
                data=merge_fixtures.GoalReconciliationTests().data(count)
                for goal in data['local_goals']:
                    goal['expressions']=[{'quote':'完整原文🙂\n"\\'*rng.randrange(2,180)}]
                before=copy.deepcopy(data)
                for cap in (500,2500,6000,85000):
                    self.assertEqual(outcome(old_groups,data,cap),outcome(goal_reconciliation.plan,data,cap))
                self.assertEqual(before,data)
        data=merge_fixtures.GoalReconciliationTests().data(3);data['local_goals'][1]['local_goal_id']='a'
        self.assertEqual(outcome(old_groups,data),outcome(goal_reconciliation.plan,data))

    def test_batch_capacity_encodes_each_record_once_and_navigation_once(self):
        packet=self.packet();packet['reading_notes']=[{'summary':'完整公共导航'*3000}]
        packet['events']=[dict(packet['events'][0],event=str(i),text='短输入') for i in range(160)]
        with patch.object(goal_batches,'raw_size',wraps=raw_size) as measured:
            result=goal_batches.plan(packet)
        self.assertEqual(161,measured.call_count)
        self.assertEqual(old_batches(packet),result)

    def test_group_capacity_no_longer_reencodes_growing_group(self):
        data=merge_fixtures.GoalReconciliationTests().data(160)
        for goal in data['local_goals']:goal['expressions']=['完整表达'*40]
        expected=old_groups(data,12000)
        with patch.object(goal_reconciliation,'raw_size',wraps=raw_size) as measured:
            result=goal_reconciliation.plan(data,12000)
        self.assertEqual(expected,result)
        self.assertEqual(2+len(data['local_goals'])+len(result[1]),measured.call_count)


if __name__=='__main__':unittest.main()
