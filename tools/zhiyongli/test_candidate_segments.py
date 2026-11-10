"""完整分段、原消息身份、混合归属、原文追溯与复用；合成模型不证明语义正确。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bounded_read import raw_size
from candidate_segments import split,verify
from goal_batches import identify,plan,merge_input
from pipeline import assemble,received,evaluate_week
from runtime import atomic
from task_identification import model_packet
from test_execution_paths import FixtureClient,packet,record


class SegmentClient(FixtureClient):
    def identify(self,p,path):
        if path.exists():return json.loads(path.read_text())
        self.calls+=1
        own=[e for e in p['events'] if e['actor_class']=='candidate_user']
        return record(dict(materials={e['event']:dict(classification='human',human_quotes=[e['text']],
            reason='合成本人片段',duplicate_of=None,goal_id='same',step='合成目标过程',assignment_reason='同一合成请求') for e in own},
            goals=[dict(goal_id='same',title='同一合成目标',purpose='完整保留原消息',boundary='confirmed',
                boundary_reason='按合成夹具归并，非语义验证',context_event_ids=[])] if own else []),path)

    def merge_goals(self,data,schema,path):
        if path.exists():return json.loads(path.read_text())
        self.calls+=1
        return record(dict(goals=[dict(goal_id='all',title='同一合成目标',purpose='完整保留原消息',
            boundary='confirmed',boundary_reason='合成跨批连续过程',context_event_ids=[],
            local_goal_ids=[g['local_goal_id'] for g in data['local_goals']])]),path)


class SegmentTests(unittest.TestCase):
    def source(self,text=None):
        p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        p['events'][0]['text']=text or ('原始用户输入\\n"😀\x00\t\n'*4000+'最后的完整要求')
        return p

    def test_every_character_survives_with_original_identity_and_bounded_size(self):
        p=self.source();before=copy.deepcopy(p);result=split(p,max_bytes=4096)
        self.assertEqual(before,p);self.assertTrue(verify(p,result))
        self.assertGreater(len(result['events']),10)
        self.assertEqual(p['events'][0]['text'],''.join(e['text'] for e in result['events']))
        self.assertTrue(all(raw_size(e)<=4096 for e in result['events']))
        self.assertEqual(1,result['candidate_segmentation']['original_candidate_messages'])
        self.assertEqual(len(result['events']),result['candidate_segmentation']['candidate_fragments'])
        self.assertLess(raw_size(result['candidate_segmentation']),2000)
        for e in result['events']:
            self.assertEqual(p['events'][0]['at'],e['at'])
            self.assertEqual('candidate_user',e['actor_class'])
            self.assertEqual(p['events'][0]['event'],e['source_segment']['original_event'])

    def test_short_input_background_and_explicit_truncation_are_preserved(self):
        p=self.source('短句原文');p['events'][0]['truncated']=True
        p['events'].append(dict(event='long-context',text='不能自动认领为本人'*10000,actor_class='context'))
        self.assertEqual(p,split(p));self.assertNotIn('candidate_segmentation',split(p))
        p['events'][0]['text']='已保存但上游截断的用户内容'*3000
        result=split(p)
        self.assertEqual(p['events'][-1],result['events'][-1])
        self.assertTrue(all(e['truncated'] for e in result['events'][:-1]))

    def test_omission_reordering_overlap_role_or_text_change_is_rejected(self):
        p=self.source();good=split(p,max_bytes=4096)
        variants=[]
        missing=copy.deepcopy(good);missing['events'].pop();variants.append(missing)
        reordered=copy.deepcopy(good);reordered['events'].reverse();variants.append(reordered)
        for change in (dict(text='不符原文'),dict(actor_class='human'),dict(at=p['until'])):
            bad=copy.deepcopy(good);bad['events'][0].update(change);variants.append(bad)
        count=copy.deepcopy(good);count['candidate_segmentation']['original_candidate_messages']=200;variants.append(count)
        person=copy.deepcopy(good);person['user_id']='other';variants.append(person)
        for value in variants:
            with self.subTest(value=value['events'][0]['event']),self.assertRaises(ValueError):verify(p,value)
        with self.assertRaisesRegex(ValueError,'禁止重复'):split(good)

    def test_fragment_id_collision_is_rejected(self):
        p=self.source();segmented=split(p)
        p['events'].append(dict(event=segmented['events'][0]['event'],text='其他来源',actor_class='context'))
        with self.assertRaisesRegex(ValueError,'标识重复'):split(p)

    def test_batch_planning_preserves_numeric_fragment_order_and_model_provenance(self):
        p=self.source();segmented=split(p,max_bytes=4096);pages=plan(segmented,max_bytes=13000)
        actual=[e for page in pages for e in page['events']]
        self.assertEqual(segmented['events'],actual)
        self.assertEqual(segmented['events'][0]['source_segment'],model_packet(pages[0])['events'][0]['source_segment'])
        self.assertEqual(p['events'][0]['text'],''.join(e['text'] for e in actual))

    def test_large_navigation_adjusts_fragment_and_batch_budgets_without_deleting_context(self):
        p=self.source('完整用户输入'*6000);p['reading_notes']=[dict(summary='须保留上下文'*2300)]
        result=split(p);pages=plan(result)
        self.assertEqual(p['reading_notes'],result['reading_notes'])
        self.assertTrue(all(page['reading_notes']==p['reading_notes'] for page in pages))
        self.assertLess(result['candidate_segmentation']['max_event_bytes'],24000)
        self.assertEqual(p['events'][0]['text'],''.join(e['text'] for page in pages for e in page['events']))
        self.assertTrue(all(raw_size(page)<=85000 for page in pages))

    def test_segmentation_metadata_does_not_invent_goals_and_human_spans_trace_original(self):
        p=self.source('真实本人表达😀\n'*7000);segmented=split(p);client=SegmentClient()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);identified=identify(segmented,root,client,received)
            self.assertEqual(1,len(identified['goals']))
            self.assertEqual(len(segmented['events']),len(identified['materials']))
            unit=assemble(segmented,identified);self.assertEqual(1,len(unit['source_units']))
            humans=[e for e in unit['events'] if e['actor_class']=='human']
            for e in humans:
                a,b=e['source_original_span']
                self.assertEqual(p['events'][0]['text'][a:b],e['text'])
                self.assertEqual(p['events'][0]['event'],e['source_original_event'])
            self.assertEqual(p['events'][0]['text'],''.join(e['text'] for e in humans))
            calls=client.calls;self.assertEqual(identified,identify(segmented,root,client,received));self.assertEqual(calls,client.calls)
            graph=json.loads((root/'0.graph.json').read_text())
            firstpage=plan(segmented)[0];merged=merge_input(firstpage,[graph])
            self.assertTrue(merged['local_goals'][0]['expressions'][0]['source_segment'])

    def test_shared_pipeline_keeps_segment_manifest_and_rejects_tampering(self):
        p=self.source('\n'.join('合成合同夹具'+key for key in __import__('runtime').RUBRIC['items'])+'\n'+('背景材料\n'*2300))
        # 归属和评分使用合成夹具；本用例只检查入口、出处与共享复用。
        client=SegmentClient()
        def call(u,path):
            if path.exists():return json.loads(path.read_text())
            return FixtureClient.call(client,u,path)
        client.call=call
        client.audit_review=lambda u,r,path:json.loads(path.read_text()) if path.exists() else FixtureClient.audit_review(client,u,r,path)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);first=root/'first';result=evaluate_week(p,first,client,shared_root=root/'shared')
            self.assertTrue((first/'candidate-segments.json').exists());calls=client.calls
            self.assertEqual(result,evaluate_week(p,root/'reuse',client,shared_root=root/'shared'));self.assertEqual(calls,client.calls)
            path=first/'candidate-segments.json';value=json.loads(path.read_text());value['changed_source_messages']=42;atomic(path,value)
            with self.assertRaisesRegex(ValueError,'签名变化'):evaluate_week(p,root/'changed',client,shared_root=root/'shared')
            self.assertEqual(calls,client.calls)

    def test_preflight_still_protects_unfinished_full_week_capacity(self):
        from pipeline import reading_view,Paused
        p=self.source('实际完整用户消息'*20000)
        with tempfile.TemporaryDirectory() as tmp,patch('bounded_read.read_all') as reader:
            with self.assertRaises(Paused):reading_view(p,Path(tmp),SegmentClient(),lambda *args:None)
            reader.assert_not_called()

    def test_direct_packet_with_oversized_single_event_does_not_pay_for_incomplete_downstream(self):
        from pipeline import Paused
        p=self.source('完整用户原文'*5000);client=SegmentClient()
        self.assertGreater(raw_size(p['events'][0]),85000)
        self.assertLess(raw_size(p),120000)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(Paused,'整周有界综合'):evaluate_week(p,Path(tmp),client)
            self.assertEqual(0,client.calls)


if __name__=='__main__':unittest.main()
