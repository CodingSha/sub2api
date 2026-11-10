"""跨页延续、独立目标及重复/漏配的合同检查；语义归并仍由模型判断。"""
import copy
import unittest
from goal_batches import plan,reconcile,merge_input

class GoalBatchTests(unittest.TestCase):
    def setUp(self):
        self.packet=dict(user_id='test',since='2026-09-07T00:00:00+08:00',until='2026-09-14T00:00:00+08:00',issues=[],
            events=[dict(event=str(i),text=text,at='2026-09-07T10:00:00+08:00',timestamp_unknown=False,actor_class='candidate_user',session_id='s')
                    for i,text in enumerate(('修复这个字段问题','接着把验证补上','再解释另一个接口'))])
        self.graphs=[]
        for i,e in enumerate(self.packet['events']):
            self.graphs.append(dict(materials={str(i):dict(classification='human',human_quotes=[e['text']],reason='本人要求',duplicate_of=None,
                goal_id='local',step='推进目标',assignment_reason='局部归属')},goals=[dict(goal_id='local',title=e['text'],purpose=e['text'],boundary='confirmed',boundary_reason='明确要求',context_event_ids=[])]))
        self.merged=dict(goals=[dict(goal_id='a',title='修复及验证字段',purpose='修复后核验',boundary='confirmed',boundary_reason='后句明确承接',
                context_event_ids=[],local_goal_ids=['0:local','1:local']),
            dict(goal_id='b',title='解释接口',purpose='独立咨询',boundary='confirmed',boundary_reason='新对象',context_event_ids=[],local_goal_ids=['2:local'])])

    def test_continuation_across_batches_merges_without_losing_person_inputs(self):
        r=reconcile(self.packet,self.graphs,self.merged)
        self.assertEqual(2,len(r['goals']));self.assertEqual(['0','1'],r['goals'][0]['event_ids'])
        self.assertEqual(3,len(r['materials']))
        data=merge_input(self.packet,self.graphs)
        self.assertEqual(['修复这个字段问题'],data['local_goals'][0]['expressions'][0]['human_quotes'])

    def test_duplicate_or_missing_local_goal_cannot_be_silently_dropped(self):
        for ids in (['0:local'],['0:local','1:local','2:local'],['0:local','0:local'],['other']):
            bad=copy.deepcopy(self.merged);bad['goals'][0]['local_goal_ids']=ids
            with self.assertRaises(ValueError):reconcile(self.packet,self.graphs,bad)

    def test_pages_keep_complete_long_user_text(self):
        self.packet['events'][0]['text']='原文'*10000
        p=plan(self.packet,65000)
        self.assertEqual(self.packet['events'][0]['text'],p[0]['events'][0]['text'])
        self.assertEqual(3,sum(len(page['events']) for page in p))

    def test_batch_identification_keeps_global_context_and_rejects_oversized_navigation(self):
        self.packet['reading_notes']=[dict(event_id='0',summary='含反证的全局阅读导航')]
        pages=plan(self.packet)
        self.assertTrue(all(p['reading_notes']==self.packet['reading_notes'] for p in pages))
        self.packet['reading_notes'][0]['summary']='不可静默裁掉的导航'*10000
        with self.assertRaises(ValueError):plan(self.packet)

if __name__=='__main__':unittest.main()
