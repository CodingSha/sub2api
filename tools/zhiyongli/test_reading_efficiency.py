"""正文复用的覆盖、身份、失败反证和恢复边界；只使用合成端点。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from bounded_read import plan, read_all
from context_navigation import Navigation
from pipeline import received, evaluate_week, reading_preflight, Paused
from reading_efficiency import partition, procedural_notes, checked_plan, STRATEGY
from runtime import atomic, digest
from shared_weeks import efficient_reading_files, check_efficient_reading, reference
from test_context_navigation import Reader
from test_hierarchical_pipeline import Client, source
import test_reading_cache as cachetests


def packet():
    def event(eid, text, **extra):
        return dict(event=eid, text=text, actor_class='context', protocol_role='tool',
            session_id='session', source_user_id='1', at='2026-11-03T12:00:00+08:00', **extra)
    return dict(user_id='1', user_email='synthetic@example.test', since='2026-11-02', until='2026-11-09', issues=[],
        events=[event('first', '测试失败，不可发布。'*2000), event('again', '测试失败，不可发布。'*2000, truncated=True),
            event('empty', '')])


def model_notes(manifest):
    return [dict(event_id=f['event_id'], start=f['start'], end=f['end'],
        summary='合成阅读：测试失败。', limitations=[], open_questions=[])
        for page in manifest['pages'] for f in page['fragments']]


class EfficiencyTests(unittest.TestCase):
    def test_only_same_person_session_role_exact_context_text_is_reused(self):
        p=packet();original=digest(p)
        for eid, updates in [('person',dict(source_user_id='2')), ('session',dict(session_id='other')),
                ('missing-session',dict(session_id='')), ('missing-user',dict(source_user_id=None)),
                ('role',dict(protocol_role='assistant')), ('human',dict(actor_class='candidate_user')),
                ('changed',dict(text=p['events'][0]['text']+'后续恢复。'))]:
            p['events'].append(dict(p['events'][0], event=eid, **updates))
        p['events'].append(dict(p['events'][2],event='empty-human',actor_class='candidate_user'))
        before=digest(p);kept, records=partition(p)
        self.assertEqual(['again','empty'],[r['event_id'] for r in records])
        self.assertEqual(len(p['events'])-2,len(kept));self.assertEqual(before,digest(p))
        self.assertNotEqual(original,before)
        old=plan(p,include_text=False);new=plan(p,include_text=False,efficiency=True)
        self.assertNotIn('reading_strategy',old);self.assertNotIn('procedural_events',old)
        self.assertEqual(digest(p),new['packet_sha256']);self.assertEqual(STRATEGY,new['reading_strategy'])

    def test_alias_has_complete_ranges_but_no_inherited_judgment_and_raw_is_own_event(self):
        p=packet();manifest=plan(p,include_text=False,efficiency=True)
        notes=model_notes(manifest);procedural=procedural_notes(p,manifest,notes)
        again=[n for n in procedural if n['event_id']=='again']
        self.assertGreater(len(again),1)
        self.assertEqual(len(p['events'][1]['text']),sum(n['end']-n['start'] for n in again))
        self.assertTrue(all('不' in n['summary'] and 'facts' not in n and n['limitations'] for n in again))
        with tempfile.TemporaryDirectory() as tmp:
            nav=Navigation(tmp,p);nav.build(notes+procedural,Reader(),received)
            keys=json.loads((Path(tmp)/'leaves.json').read_text())['node_ids']
            leaves=[k for k in keys if nav.load(k)['note']['event_id']=='again']
            pieces=[nav.read([k],raw=True)['nodes'][0] for k in leaves]
            self.assertEqual(p['events'][1]['text'],''.join(v['text'] for v in pieces))
            self.assertTrue(all(v['event_id']=='again' and v['source_event_sha256']==digest(p['events'][1]) for v in pieces))
            descriptor=nav.descriptor(leaves[0]);self.assertTrue(descriptor['truncated'])
            self.assertEqual('tool',descriptor['protocol_role'])

    def test_tampered_plan_source_mapping_and_incomplete_canonical_reading_are_rejected(self):
        p=packet();manifest=plan(p,include_text=False,efficiency=True);notes=model_notes(manifest)
        for change in ('source','alias','identity'):
            changed=copy.deepcopy(manifest)
            if change=='source':changed['packet_sha256']='bad'
            elif change=='alias':changed['procedural_events'][0]['canonical_event_id']='empty'
            else:changed['procedural_events'][0]['source_event_sha256']='bad'
            with self.assertRaises(ValueError):procedural_notes(p,changed,notes)
        altered=copy.deepcopy(p);altered['events'][1]['text']+='恢复'
        with self.assertRaises(ValueError):checked_plan(altered,manifest)
        with self.assertRaisesRegex(ValueError,'完整阅读'):procedural_notes(p,manifest,[])
        broken=copy.deepcopy(notes);broken[0]['start']=1
        with self.assertRaisesRegex(ValueError,'缺口'):procedural_notes(p,manifest,broken)

    def test_empty_saved_text_needs_no_receipt_and_pure_index_needs_no_model_summary(self):
        p=packet();p['events']=[dict(p['events'][2],event='empty-'+str(i)) for i in range(100)]
        with tempfile.TemporaryDirectory() as tmp:
            client=SimpleNamespace(reading_efficiency=True)
            status=read_all(p,Path(tmp)/'reading',client=client,limit=None)
            self.assertEqual((0,[],100),(status['pages'],status['received_pages'],status['procedural_events']))
            self.assertEqual('reading_complete_pending_reconciliation',status['state'])
            manifest=json.loads((Path(status['evidence_directory'])/'plan.json').read_text())
            notes=procedural_notes(p,manifest,[]);reader=Reader();nav=Navigation(Path(tmp)/'tree',p)
            tree=nav.build(notes,reader,received)
            self.assertEqual(0,reader.calls);self.assertTrue(nav.load(tree['root'])['procedural'])
            self.assertEqual(100,len(json.loads((nav.root/'leaves.json').read_text())['node_ids']))
            self.assertFalse(list(Path(tmp).rglob('*.receipt.json')))

    def test_old_unknown_still_blocks_even_when_new_plan_needs_zero_pages(self):
        p=packet();p['events']=p['events'][2:]
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);atomic(base/'old/plan.json',plan(p,include_text=False))
            receipt=base/'old/0.receipt.json';atomic(receipt,dict(status='unknown',request_sha256='original'))
            before=receipt.read_bytes()
            result=read_all(p,base,client=SimpleNamespace(reading_efficiency=True),limit=None)
            self.assertEqual('reading_blocked',result['state']);self.assertEqual('unknown',result['blocked'][0]['state'])
            self.assertEqual(before,receipt.read_bytes())

    def test_unsupported_long_client_stops_before_paying(self):
        with self.assertRaises(Paused) as error:reading_preflight(source(),SimpleNamespace(reading_efficiency=True))
        self.assertEqual('needs_review',error.exception.state)

    def test_full_week_preserves_every_occurrence_issues_and_review_audit_paths(self):
        p=source();p['events'] += copy.deepcopy(packet()['events'])
        # 合成长周的本人身份与新背景匹配，保留原有评分测试合同。
        for event in p['events'][-3:]:
            event['source_user_id']=p['user_id'];event['at']='2026-09-07T12:00:00+08:00'
        original=digest(p)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'review';shared=Path(tmp)/'shared'
            client=Client();client.reading_efficiency=True;client.readback=True
            client.reading_plan_root=Path(tmp)/'reading-plans'
            minimum=[];client.preflight_minimum=minimum.append
            result=evaluate_week(p,root,client,shared_root=shared)
            self.assertEqual('80.0',result['reports'][0]['score']);self.assertTrue((root/'audit.json').exists())
            native=json.loads((root/'unit.json').read_text());events={e['event']:e for e in native['events']}
            for event in p['events']:
                self.assertEqual(event['text'],events[event['event']]['text'])
                self.assertEqual(event.get('truncated'),events[event['event']].get('truncated'))
            self.assertEqual(p['issues'],native['source_issues']);self.assertEqual(original,digest(p))
            proof=json.loads((root/'reading-efficiency.json').read_text())
            path=Path(proof['evidence_directory'])/'plan.json';manifest=json.loads(path.read_text())
            self.assertEqual(2,len(manifest['procedural_events']))
            self.assertEqual(len(manifest['pages'])+3,minimum[0])
            calls=client.calls;self.assertEqual(result,evaluate_week(p,root,client,shared_root=shared));self.assertEqual(calls,client.calls)
            self.assertEqual(result,evaluate_week(p,Path(tmp)/'another',client,shared_root=shared))
            self.assertEqual(calls,client.calls)
            entry=dict(directory=str(root),efficient_reading_files=[reference(p) for p in efficient_reading_files(root)])
            check_efficient_reading(entry,p)
            changed=json.loads(path.read_text())
            changed['procedural_events'][0]['canonical_event_id']='empty';atomic(path,changed)
            with self.assertRaises(ValueError):check_efficient_reading(entry,p)


class CacheEfficiencyTests(unittest.TestCase):
    setUp=cachetests.ReadingCacheTests.setUp
    client=cachetests.ReadingCacheTests.client

    def test_incremental_and_weekly_reuse_costs_once_and_keeps_both_failed_occurrences(self):
        p=packet();p['events'][0]['text']='测试失败，不可发布。';p['events'][1]['text']=p['events'][0]['text']
        first=self.client('day');first.reading_efficiency=True;first.reading_content_identity=True
        daily=dict(p,events=p['events'][:1])
        read_all(daily,first.root/'reading',client=first,limit=None)
        second=self.client('week');second.reading_efficiency=True;second.reading_content_identity=True
        result=read_all(p,second.root/'reading',client=second,limit=None)
        self.assertEqual((1,1,0),(self.calls,first.budget.used(),second.budget.used()))
        self.assertEqual(2,result['procedural_events']);self.assertEqual([0],result['received_pages'])
        calls=self.calls;self.assertEqual(result,read_all(p,second.root/'reading',client=second,limit=None))
        self.assertEqual(calls,self.calls)

    def test_shared_original_receipt_is_rechecked_and_unknown_never_becomes_a_free_alias(self):
        from reading_efficiency import check_evidence
        p=packet();first=self.client('first');first.reading_efficiency=True
        status=read_all(p,first.root/'reading',client=first,limit=None)
        directory=Path(status['evidence_directory']);check_evidence(p,directory)
        receipt=next((self.root/'shared').rglob('*.receipt.json'))
        original=receipt.read_bytes();value=json.loads(original);value['status']='unknown';atomic(receipt,value)
        calls=self.calls
        with self.assertRaises(ValueError):check_evidence(p,directory)
        second=self.client('second');second.reading_efficiency=True
        with self.assertRaises(Paused) as error:read_all(p,second.root/'reading',client=second,limit=None)
        self.assertEqual('unknown',error.exception.state)
        self.assertEqual((calls,0),(self.calls,second.budget.used()))


if __name__=='__main__':unittest.main()
