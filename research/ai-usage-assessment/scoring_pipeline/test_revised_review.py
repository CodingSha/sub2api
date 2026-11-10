"""对v6易误判边界做合同回归，不把测试当量表效度证明。"""
import copy
import unittest
from fractions import Fraction

from .revised_review import validate_period, summarize_period
from .policy import ITEMS


def fixture():
    period = {'period_id': 'week-2026-09-07', 'start': '2026-09-07T00:00:00+08:00', 'end': '2026-09-14T00:00:00+08:00'}
    events = [dict(event='e1', event_id='human1', actor_class='human', text='测试通过，可以提交。', at='2026-09-08T10:00:00+08:00', human_spans=[[0,11]]),
              dict(event='e2', event_id='result1', actor_class='assistant', text='具体测试结果：5项通过。', at='2026-09-08T09:00:00+08:00', human_spans=[])]
    data = {'user_id': 'person', 'goal_id': 'task', 'title': '修复', 'events': events}
    cells = [{'item': i, 'status': 'no_opportunity', 'opportunity': '本任务没有该项处理机会', 'reason': '仅处理局部提交'} for i in ITEMS]
    c = next(c for c in cells if c['item']=='J3')
    c.update(status='scored', opportunity='根据测试结果决定是否接受', fact='本人基于已得测试结果接受提交',
             verdict='reasoned', reason='已实际作出接受决定', adjacent_reason='有测试依据，高于普通接受；未见完整验收边界，不判完整',
             person_evidence=[{'event':'e1','quote':'可以提交'}], evidence=[{'event':'e2','quote':'5项通过'}])
    return data, period, {'period_id':period['period_id'], 'boundary':{'status':'confirmed'}, 'cells':cells}


class RevisedReviewTest(unittest.TestCase):
    def test_existing_feedback_may_precede_j3_decision(self):
        data, period, out = fixture()
        value=validate_period(data,period,out)
        self.assertEqual(next(c for c in value['cells'] if c['item']=='J3')['band'],90)

    def test_future_feedback_rejected(self):
        data, period, out=fixture();data['events'][1]['at']='2026-09-14T01:00:00+08:00'
        with self.assertRaisesRegex(ValueError,'未来'):validate_period(data,period,out)

    def test_tool_cannot_be_person(self):
        data,period,out=fixture();out['cells'][8]['person_evidence']=[{'event':'e2','quote':'5项通过'}]
        with self.assertRaisesRegex(ValueError,'非本人'):validate_period(data,period,out)

    def test_unknown_requires_material_and_consequence(self):
        data,period,out=fixture();c=out['cells'][0];c.update(status='unknown',missing={'type':'missing_source','needed':'缺结果'})
        with self.assertRaisesRegex(ValueError,'未知'):validate_period(data,period,out)

    def test_gap_requires_contemporary_responsibility(self):
        data,period,out=fixture();out['cells'][8]['verdict']='necessary_gap'
        with self.assertRaisesRegex(ValueError,'归责'):validate_period(data,period,out)

    def test_mixed_boundary_retains_only_explicit_local_scope(self):
        data,period,out=fixture();out['boundary']={'status':'local_scope','reason':'只确认局部提交决定'}
        with self.assertRaisesRegex(ValueError,'局部'):validate_period(data,period,out)
        out['cells'][8]['local_scope']='具体修复的测试后提交决定'
        self.assertEqual(validate_period(data,period,out)['cells'][8]['band'],90)

    def test_complex_pending_does_not_erase_proven_item(self):
        data,period,out=fixture();v=validate_period(data,period,out)
        c=v['cells'][0];c.update(status='scored',band=80,weight_exact='1',weight_pending=True)
        second=copy.deepcopy(v);second['cells'][0].update(band=100,weight_pending=False)
        score=summarize_period([v,second])
        self.assertEqual(score['items']['P1']['score'],'90.0')
        self.assertEqual(Fraction(score['items']['P1']['lower_exact']),88)
        self.assertEqual(Fraction(score['items']['P1']['upper_exact']),90)

    def test_partial_dimension_is_explicit_and_no_cross_dimension_fill(self):
        data,period,out=fixture();v=validate_period(data,period,out);s=summarize_period([v])
        self.assertIsNone(s['score'])
        self.assertEqual(s['dimensions']['J']['covered_items'],1)

    def test_partial_coverage_not_zero(self):
        data,period,out=fixture();v=validate_period(data,period,out)
        other=copy.deepcopy(v);other['cells'][8].update(status='unknown',band=None)
        s=summarize_period([v,other]);self.assertEqual(s['items']['J3']['score'],'90.0');self.assertEqual(s['items']['J3']['unknown'],1)


if __name__=='__main__':unittest.main()
