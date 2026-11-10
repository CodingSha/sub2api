"""自动长来源主路径的离线验收；模型回执为合成，不评真实员工。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bounded_read import anchors,raw_size
from pipeline import evaluate_week,evaluate_month,received,Paused
from runtime import atomic,digest
from test_execution_paths import FixtureClient,packet,record
from test_candidate_segments import SegmentClient
import test_context_navigation as navtests


class Client(SegmentClient):
    def __init__(self):super().__init__();self.inputs=[];self.readback=False;self.maximum=0
    def read_page(self,page,path):
        if path.exists():return json.loads(path.read_text())
        self.calls+=1
        result=record(dict(notes={f['fragment_id']:dict(summary='合成长材料的主要事实与未决',
            facts=[dict(anchor_id=anchors(f)[0]['anchor_id'],fact='仅合成测试')],limitations=[],open_questions=[])
            for f in page['fragments']}))
        result['request_sha256']=digest(page);atomic(path,result);return result
    def navigate_context(self,data,schema,path):return navtests.Reader.navigate_context(self,data,schema,path)
    def identify(self,p,path):
        self.maximum=max(self.maximum,raw_size(p))
        if path.exists():return json.loads(path.read_text())
        if self.readback and not p['context_access']['raw_read_fragments']:
            access=p['context_access'];observed={o['node_id']:o['node'] for o in access['observations'] if o['mode']=='inspect'}
            key=access['root']['node_id']
            while key in observed and observed[key]['kind']=='branch':key=observed[key]['children'][-1]
            mode='raw' if key in observed and observed[key]['kind']=='leaf' else 'inspect'
            self.calls+=1
            return record(dict(materials={},goals=[],needs_nodes=[dict(node_id=key,mode=mode)]),path)
        result=super().identify(p,path)
        if self.readback:
            value=received(result)
            for goal in value['goals']:goal['context_event_ids']=[p['events'][-1]['event']]
            return record(value,path)
        return result
    def call(self,u,path):
        self.maximum=max(self.maximum,raw_size(u))
        return json.loads(path.read_text()) if path.exists() else FixtureClient.call(self,u,path)
    def audit_review(self,u,r,path):
        self.maximum=max(self.maximum,raw_size(dict(unit=u,review=r)))
        return json.loads(path.read_text()) if path.exists() else FixtureClient.audit_review(self,u,r,path)


def source():
    p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
    p['events'][0]['text'] += '\n'+''.join('完整保留第'+str(i)+'条上下文及原句。\n' for i in range(5000))
    p['events'] += [dict(event='context-'+str(i),text='合成失败与未决'+str(i)+'x'*300,
        actor_class='context',at='2026-09-07T12:00:00+08:00',timestamp_unknown=False) for i in range(50)]
    return p


class HierarchicalTests(unittest.TestCase):
    def test_long_month_runs_week_contracts_and_bounded_gap_recovery_without_full_month_payload(self):
        class MonthClient(Client):
            def call(self,u,path):
                if path.exists():return json.loads(path.read_text())
                value=received(super().call(u,path))
                if u['periods'][0]['start'].startswith('2026-09-07'):
                    cell=next(c for c in value['period_reviews'][0]['cells'] if c['item']=='J2')
                    cell['quality'].update(label='necessary_gap',responsibility={k:'合成归责事实' for k in (
                        'known_at_time','person_control','feasible_alternative','actual_gap')})
                    cell['performance']['verdict']='necessary_gap'
                return record(value,path)
            def month_status_view(self,data,schema,path):
                if path.exists():return json.loads(path.read_text())
                self.calls+=1;self.maximum=max(self.maximum,raw_size(data))
                pid='week-2026-09-14';view=next(v for v in data['periods'] if v['period_id']==pid)
                proof=next(e for e in view['supplied_quotes'] if e['id']=='person-J2')
                decision=dict(status='recovered',reason='仅合成跨周恢复合同',recovery_period=pid,recovery_refs=[proof['id']],
                    source_quotes=[dict(period_id=pid,event=proof['event'],quote=proof['quote'])])
                return record(dict(slots={data['slot']:decision},needs_period_nodes=[]),path)
        def prepare(_root,_user,a,b):
            if a[:10] not in {'2026-09-07','2026-09-14','2026-09-21'}:
                p=packet(a,b);p['events']=[];return p
            p=source();p.update(since=a,until=b)
            for e in p['events']:
                e['at']=a[:10]+e['at'][10:];e['event']=a[:10]+':'+e['event']
            return p
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.prepare',side_effect=prepare):
            root=Path(tmp);client=MonthClient()
            result=evaluate_month('unused','test','2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'month',client,shared_root=root/'shared')
            self.assertEqual(3,result['reports'][0]['eligibility']['valid_weeks'])
            status=json.loads((root/'month/month-status.json').read_text())
            self.assertEqual('recovered',status['dispositions'][0]['status'])
            self.assertTrue(list((root/'month/month-readback').rglob('status.receipt.json')))
            self.assertLess(client.maximum,120000)
            calls=client.calls
            self.assertEqual(result,evaluate_month('unused','test','2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'month',client,shared_root=root/'shared'))
            self.assertEqual(calls,client.calls)

    def test_real_transport_structure_is_bounded_and_every_character_retained(self):
        from bounded_read import plan,request_size,REQUEST_BYTES
        for text in ('短行\n'*10000,'\n\x00"\\😀'*10000):
            p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00');p['events'][0]['text']=text
            pages=plan(p)['pages'];fragments=[f for page in pages for f in page['fragments']]
            self.assertEqual(text,''.join(f['text'] for f in fragments))
            self.assertTrue(all(request_size(page)<=REQUEST_BYTES for page in pages))
            self.assertGreater(len(fragments),1)

    def test_short_log_lines_do_not_expand_into_per_line_reference_objects(self):
        from bounded_read import plan, transport
        text=('ok\n'*3000)+'失败反证：不要丢弃\n'+('x\n'*3000)
        p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        p['events'][0]['text']=text
        pages=plan(p)['pages']
        fragments=[f for page in pages for f in transport(page)['fragments']]
        # 原文和失败反证仍可从受控引用逐字恢复；包装不能随日志行数膨胀几十倍。
        recovered=''.join(a['text'] for f in fragments for a in f['segments'])
        self.assertEqual(text,recovered)
        self.assertLess(sum(raw_size(transport(page)) for page in pages),len(text.encode())*3+4096)
        for f in fragments:
            for a in f['segments']:
                self.assertEqual(a['text'],text[f['start']+a['start']:f['start']+a['end']])

    def test_full_long_pipeline_automatically_builds_tree_attributes_scores_audits_exports_and_reuses(self):
        p=source();original=copy.deepcopy(p)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=Client();first=root/'first'
            result=evaluate_week(p,first,client,shared_root=root/'shared')
            self.assertEqual(original,p);self.assertEqual('80.0',result['reports'][0]['score'])
            self.assertTrue((first/'context-navigation/tree.json').exists())
            native=json.loads((first/'unit.json').read_text());self.assertGreater(raw_size(native),120000)
            self.assertEqual(1,len(native['source_units']));self.assertGreater(len(native['person_events']),5)
            self.assertLess(client.maximum,120000)
            raw={e['event']:e for e in native['events']}
            for e in p['events']:self.assertEqual(e['text'],raw[e['event']]['text'])
            calls=client.calls
            self.assertEqual(result,evaluate_week(p,root/'reuse',client,shared_root=root/'shared'))
            self.assertEqual(calls,client.calls)
            target=first/'identification/readback/0/context.json';target.unlink()
            with self.assertRaises(ValueError):evaluate_week(p,root/'tampered',client,shared_root=root/'shared')
            self.assertEqual(calls,client.calls)

    def test_attribution_can_read_context_without_creating_new_personal_candidates(self):
        p=source()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=Client();client.readback=True
            result=evaluate_week(p,root,client)
            graph=json.loads((root/'identified.json').read_text())
            self.assertTrue(graph['readback_context_events'])
            self.assertEqual('80.0',result['reports'][0]['score'])
            self.assertTrue(all(e['actor_class']=='context' for e in graph['readback_context_events']))
            native=json.loads((root/'unit.json').read_text())
            self.assertTrue(all('context-' not in eid for eid in native['person_events']))
            calls=client.calls;self.assertEqual(result,evaluate_week(p,root,client));self.assertEqual(calls,client.calls)

    def test_unknown_navigation_is_not_resent_and_keeps_all_completed_pages(self):
        class Unknown(Client):
            def navigate_context(self,data,schema,path):
                if path.exists():return json.loads(path.read_text())
                self.calls+=1;value=dict(status='unknown');atomic(path,value);return value
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=Unknown();p=source()
            for _ in range(2):
                with self.assertRaises(Paused) as caught:evaluate_week(p,root,client)
                self.assertEqual('unknown',caught.exception.state)
                if _==0:calls=client.calls
                else:self.assertEqual(calls,client.calls)
            self.assertFalse((root/'bundle.json').exists());self.assertTrue(list((root/'reading').rglob('*.checked.json')))


if __name__=='__main__':unittest.main()
