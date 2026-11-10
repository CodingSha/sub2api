"""导航全量覆盖、跨层反证定位、签名和续跑；合成端点不证明摘要语义无遗漏。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from context_navigation import Navigation, FIELDS, checked
from pipeline import received
from runtime import atomic


class Reader:
    def __init__(self):
        self.calls = 0
        self.inputs = []

    def navigate_context(self, data, schema, receipt):
        if receipt.exists():
            return json.loads(receipt.read_text())
        self.calls += 1;self.inputs.append(data)
        value = dict({k:'合成摘要，失败反证和未决仍需沿子节点核原文。' for k in FIELDS},
            covered_child_ids=[c['node_id'] for c in data['children']])
        record = dict(status='received', response=dict(status='completed',
            output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
        atomic(receipt,record)
        return record


class NavigationTests(unittest.TestCase):
    def packet(self,n=100):
        return dict(user_id='synthetic',user_email='synthetic@example.test',
            since='2026-09-07T00:00:00+08:00',until='2026-09-14T00:00:00+08:00',
            events=[dict(event=str(i),text='工具失败，不能宣布验证通过。\n特殊字符\\n😀',
                actor_class='context',at='2026-09-07T10:00:00+08:00',session_id='s'+str(i%3)) for i in range(n)])

    def notes(self,p):
        return [dict(event_id=e['event'],start=0,end=len(e['text']),summary='保留失败与未决。'*40,
            limitations=['未验证'],open_questions=['需核后续是否恢复']) for e in p['events']]

    def test_multilevel_tree_preserves_every_raw_fragment_and_reuses_receipts(self):
        with tempfile.TemporaryDirectory() as tmp,patch('context_navigation.INPUT_BYTES',6000):
            packet=self.packet();nav=Navigation(tmp,packet);client=Reader();notes=self.notes(packet)
            first=nav.build(notes,client,received);self.assertGreaterEqual(first['levels'],2)
            pending=[first['root']];leaves=[]
            while pending:
                key=pending.pop();node=nav.read([key])['nodes'][0]
                if node['kind']=='branch':pending.extend(node['children'])
                else:
                    raw=nav.read([key],raw=True)['nodes'][0]
                    self.assertEqual(packet['events'][int(raw['event_id'])]['text'],raw['text']);leaves.append(raw['event_id'])
            self.assertEqual(set(str(i) for i in range(100)),set(leaves));self.assertEqual(100,len(leaves))
            calls=client.calls;self.assertEqual(first,nav.build(notes,client,received));self.assertEqual(calls,client.calls)

    def test_missing_duplicate_overlap_and_outside_spans_are_rejected(self):
        packet=self.packet(2);notes=self.notes(packet)
        variants=[notes[:1],notes+[notes[0]],copy.deepcopy(notes),copy.deepcopy(notes)]
        variants[2][0]['start']=1;variants[3][0]['end']+=1
        for variant in variants:
            with self.subTest(variant=variant),tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):Navigation(tmp,packet).build(variant,Reader(),received)

    def test_bad_coverage_and_oversized_summary_are_rejected(self):
        data=dict(children=[dict(node_id='a'),dict(node_id='b')])
        value=dict({k:'已说明' for k in FIELDS},covered_child_ids=['a','b'])
        for ids in (['a'],['a','a'],['a','b','c']):
            with self.assertRaises(ValueError):checked(data,dict(value,covered_child_ids=ids))
        with self.assertRaises(ValueError):checked(data,dict(value,summary='不能截断'*5000))

    def test_cross_identity_tampering_and_path_read_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet=self.packet(1);nav=Navigation(tmp,packet);root=nav.build(self.notes(packet),Reader(),received)['root']
            with self.assertRaises(ValueError):Navigation(tmp,dict(packet,user_id='other'))
            for key in ('../input',root.upper()):
                with self.assertRaises(ValueError):nav.read([key])
            with nav.connect() as db:
                node=json.loads(db.execute('SELECT body FROM nodes WHERE id=?',(root,)).fetchone()[0]);node['scope']='other'
                db.execute('UPDATE nodes SET body=? WHERE id=?',(json.dumps(node),root))
            with self.assertRaises(ValueError):nav.read([root])

    def test_raw_budget_failure_does_not_truncate_and_note_change_needs_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet=self.packet(1);nav=Navigation(tmp,packet);notes=self.notes(packet)
            key=nav.build(notes,Reader(),received)['root']
            with self.assertRaises(ValueError):nav.read([key],raw=True,max_bytes=10)
            self.assertEqual(packet['events'][0]['text'],nav.read([key],raw=True)['nodes'][0]['text'])
            notes[0]['summary']='另一个解释'
            with self.assertRaises(ValueError):nav.build(notes,Reader(),received)

    def test_unknown_receipt_is_not_resent_or_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp,patch('context_navigation.INPUT_BYTES',6000):
            packet=self.packet(12);nav=Navigation(tmp,packet);notes=self.notes(packet);client=Reader()
            nav.build(notes,client,received)
            path=next(Path(tmp).glob('*.receipt.json'));atomic(path,dict(status='unknown'))
            calls=client.calls
            with self.assertRaisesRegex(ValueError,'结果不明'):nav.build(notes,client,received)
            self.assertEqual(calls,client.calls)
            self.assertEqual('unknown',json.loads(path.read_text())['status'])

    def test_large_roster_navigation_keeps_bounded_fanout_and_single_node_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet=self.packet(2200);nav=Navigation(tmp,packet);client=Reader()
            tree=nav.build(self.notes(packet),client,received)
            self.assertGreaterEqual(tree['levels'],3)
            self.assertTrue(all(len(d['children'])<=32 for d in client.inputs))
            self.assertLess(len(json.dumps(nav.descriptor(tree['root'])).encode()),12000)
            self.assertEqual(1,len(list(Path(tmp).glob('*.sqlite'))))
            self.assertEqual([],list(Path(tmp).glob('*.node.json')))


if __name__=='__main__':unittest.main()
