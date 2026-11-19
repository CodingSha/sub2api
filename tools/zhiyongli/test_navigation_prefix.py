"""导航闭前缀与完整构树请求身份等价；合成回执不证明真实语义质量。"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from context_navigation import Navigation
from pipeline import received, Paused
from runtime import atomic
import test_context_navigation as navigation
import test_reading_cache as fixtures


class NavigationPrefixTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    client=fixtures.ReadingCacheTests.client

    def run_tree(self,name,packet,*,prefix=False,cache='shared-prefix',notes=None):
        client=self.client(name);client.navigation_cache_root=self.root/cache
        nav=Navigation(client.root/'navigation',packet)
        value=(nav.build_prefix if prefix else nav.build)(
            navigation.NavigationTests().notes(packet) if notes is None else notes,client,received)
        return nav,value

    def request_ids(self,cache):
        return {path.name for path in (self.root/cache).rglob('*.receipt.json')}

    def test_append_prefix_and_weekend_have_identical_unique_requests_and_tree(self):
        packet=navigation.NavigationTests().packet(1100)
        _,baseline=self.run_tree('baseline',packet,cache='baseline-cache')
        expected=self.request_ids('baseline-cache')
        count=0
        for day,size in enumerate((31,32,33,400,1024,1100)):
            daily=copy.deepcopy(packet);daily['events']=daily['events'][:size]
            nav,proof=self.run_tree('day'+str(day),daily,prefix=True)
            self.assertFalse(proof['complete'])
            self.assertFalse((nav.root/'tree.json').exists())
            self.assertFalse((nav.root/'leaves.json').exists())
            self.assertLessEqual(self.request_ids('shared-prefix'),expected)
            count=len(self.request_ids('shared-prefix'))
        _,final=self.run_tree('weekend',packet)
        self.assertGreater(count,0)
        self.assertEqual(expected,self.request_ids('shared-prefix'))
        self.assertEqual(baseline,final)

    def test_tail_is_not_closed_by_bytes_until_a_successor_is_seen(self):
        with patch('context_navigation.INPUT_BYTES',6000):
            packet=navigation.NavigationTests().packet(2)
            nav,proof=self.run_tree('unclosed',packet,prefix=True)
            self.assertEqual([],proof['levels']);self.assertFalse(self.request_ids('shared-prefix'))
            packet=navigation.NavigationTests().packet(10)
            _,baseline=self.run_tree('all',packet,cache='baseline-cache')
            nav,proof=self.run_tree('closed',packet,prefix=True)
            self.assertTrue(proof['levels'])
            self.assertTrue(all(level['closed_groups']>0 for level in proof['levels']))
            self.assertLessEqual(self.request_ids('shared-prefix'),self.request_ids('baseline-cache'))
            _,final=self.run_tree('end',packet)
            self.assertEqual(baseline,final)

    def mixed(self,count):
        from reading_efficiency import partition,STRATEGY
        packet=navigation.NavigationTests().packet(count)
        normal=navigation.NavigationTests().notes(packet)
        for note in normal:note['summary']='完整已核局部事实。'*180
        for i in range(count):
            packet['events'].append(dict(event='empty-'+str(i),text='',actor_class='context',
                at='2026-09-07T10:00:00+08:00'))
        records=partition(packet)[1]
        tail=[dict(event_id=r['event_id'],start=0,end=0,summary='空正文程序记录',limitations=[],open_questions=[],
            procedural=dict(strategy=STRATEGY,**r)) for r in records]
        return packet,normal+tail

    def test_program_tail_is_deferred_before_new_real_notes_insert_ahead_of_it(self):
        final,notes=self.mixed(96)
        _,baseline=self.run_tree('mixed-baseline',final,cache='baseline-cache',notes=notes)
        expected=self.request_ids('baseline-cache')
        for size in (64,96):
            packet,current=self.mixed(size)
            nav,proof=self.run_tree('mixed-'+str(size),packet,prefix=True,notes=current)
            self.assertEqual((size,size),(proof['stable_prefix_leaves'],proof['deferred_leaves']))
            self.assertLessEqual(self.request_ids('shared-prefix'),expected)
        _,tree=self.run_tree('mixed-final',final,notes=notes)
        self.assertEqual(baseline,tree);self.assertEqual(expected,self.request_ids('shared-prefix'))

    def test_interspersed_program_record_stops_without_skipping_later_real_notes(self):
        packet,notes=self.mixed(64)
        reordered=notes[:5]+[notes[64]]+notes[5:64]+notes[65:]
        nav,proof=self.run_tree('interspersed',packet,prefix=True,notes=reordered)
        self.assertEqual((5,123),(proof['stable_prefix_leaves'],proof['deferred_leaves']))
        self.assertEqual([],proof['levels']);self.assertFalse(self.request_ids('shared-prefix'))

    def test_source_insertion_and_revision_do_not_reuse_changed_branch(self):
        packet=navigation.NavigationTests().packet(64)
        self.run_tree('first',packet,prefix=True)
        original=self.request_ids('shared-prefix')
        changed=copy.deepcopy(packet);changed['events'][0]['text']+='真实新增反证'
        self.run_tree('revision',changed,prefix=True)
        self.assertGreater(len(self.request_ids('shared-prefix')-original),0)
        inserted=copy.deepcopy(packet);event=copy.deepcopy(packet['events'][0]);event['event']='new-first'
        inserted['events'].insert(0,event)
        before=self.request_ids('shared-prefix')
        self.run_tree('insertion',inserted,prefix=True)
        self.assertGreater(len(self.request_ids('shared-prefix')-before),0)

    def test_unknown_prefix_request_cannot_be_replayed_from_another_snapshot(self):
        packet=navigation.NavigationTests().packet(32)
        self.run_tree('first',packet,prefix=True)
        path=next((self.root/'shared-prefix').rglob('*.receipt.json'))
        value=json.loads(path.read_text());value['status']='unknown';atomic(path,value)
        calls=self.calls
        packet['events'].append(navigation.NavigationTests().packet(33)['events'][-1])
        with self.assertRaises(Paused) as caught:self.run_tree('second',packet,prefix=True)
        self.assertEqual('unknown',caught.exception.state);self.assertEqual(calls,self.calls)

    def test_missing_spans_and_changed_notes_do_not_override_partial_proof(self):
        packet=navigation.NavigationTests().packet(32);notes=navigation.NavigationTests().notes(packet)
        with self.assertRaises(ValueError):self.run_tree('missing',packet,prefix=True,notes=notes[:-1])
        nav,proof=self.run_tree('proof',packet,prefix=True,notes=notes)
        before=(nav.root/'prefix.json').read_bytes()
        notes=copy.deepcopy(notes);notes[0]['summary']='已改变的阅读结论'
        with self.assertRaises(ValueError):self.run_tree('proof',packet,prefix=True,notes=notes)
        self.assertEqual(before,(nav.root/'prefix.json').read_bytes())
