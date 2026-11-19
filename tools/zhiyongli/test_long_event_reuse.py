"""长事件跨片复用只免重复哈希；原件、全部叶及回读字节仍逐次核验。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from context_navigation import Navigation
from page_navigation import FIELDS, MODE, verify_tree
from pipeline import received
from runtime import digest
import test_context_navigation as fixtures


class LongEventReuseTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.packet=fixtures.NavigationTests().packet(2)
        self.notes=[]
        for event in self.packet['events']:
            event['text']='失败\\\n😀未恢复。'*80
            for start in range(0,len(event['text']),80):
                self.notes.append(dict(event_id=event['event'],start=start,end=min(start+80,len(event['text'])),
                    summary='合成失败，主体与结局仍待核。',limitations=['非语义验收'],open_questions=[]))

    def fusion(self):
        groups=[]
        for start in range(0,len(self.notes),24):
            ids=[n['event_id']+'@'+str(n['start']) for n in self.notes[start:start+24]]
            groups.append(dict(fragment_ids=ids,navigation={key:'合成失败与未决。' for key in FIELDS},proof=dict(mode=MODE)))
        nav=Navigation(self.root/'fusion',self.packet,page_navigation=True)
        nav.build(self.notes,fixtures.Reader(),received,page_groups=groups)
        return nav,groups

    def test_fusion_checks_every_leaf_but_hashes_each_original_once_per_invocation(self):
        # 本项只隔离树验签的复杂度；真实首读回执重验由原融合测试覆盖。
        nav,groups=self.fusion()
        events=self.packet['events']
        counts={e['event']:0 for e in events}
        def counted(value):
            for event in events:
                if value is event:counts[event['event']]+=1
            return digest(value)
        with patch('page_navigation.inputs',return_value=(self.notes,groups)),patch('page_navigation.digest',side_effect=counted):
            verify_tree(nav,{})
            self.assertEqual({e['event']:1 for e in events},counts)
            verify_tree(nav,{})
            self.assertEqual({e['event']:2 for e in events},counts)
        leaves=json.loads((nav.root/'leaves.json').read_text())['node_ids']
        self.assertEqual(len(self.notes),len(leaves))

    def test_fusion_does_not_reuse_old_event_signature_after_source_changes(self):
        nav,groups=self.fusion();tree=(nav.root/'tree.json').read_bytes()
        with patch('page_navigation.inputs',return_value=(self.notes,groups)):
            verify_tree(nav,{})
            self.packet['events'][0]['text']+='新增反证'
            with self.assertRaisesRegex(ValueError,'完整叶索引'):verify_tree(nav,{})
        self.assertEqual(tree,(nav.root/'tree.json').read_bytes())

    def test_raw_read_preserves_spans_and_hashes_original_once_per_call(self):
        nav=Navigation(self.root/'raw',self.packet);keys=nav.leaves(self.notes)
        selected=keys[:24];events=self.packet['events'];counts={e['event']:0 for e in events}
        def counted(value):
            for event in events:
                if value is event:counts[event['event']]+=1
            return digest(value)
        with patch('context_navigation.digest',side_effect=counted):
            raw=nav.read(selected,raw=True)
            self.assertEqual({e['event']:1 for e in events},counts)
            self.assertEqual(raw,nav.read(selected,raw=True))
            self.assertEqual({e['event']:2 for e in events},counts)
        for value,note in zip(raw['nodes'],self.notes):
            event=nav.events[note['event_id']]
            self.assertEqual(event['text'][note['start']:note['end']],value['text'])
            self.assertEqual(digest(event),value['source_event_sha256'])


if __name__=='__main__':unittest.main()
