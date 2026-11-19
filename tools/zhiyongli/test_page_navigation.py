"""页导航融合的覆盖、真实回执链、预算和恢复；全部端点为合成。"""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from bounded_read import plan,read_all,raw_size,request_size,checked,anchors
from context_navigation import Navigation
from navigation_access import Access
from page_navigation import MODE,FIELDS,inputs,deferred_prefix,verify_tree
from pipeline import received,Paused
from reading_efficiency import check_evidence
from runtime import atomic,digest
import test_reading_cache as fixtures
from test_hierarchical_pipeline import Client,source


class FusionClient(Client):
    """使用原评分/复查合成器，仅扩展首读新字段。"""
    def __init__(self):
        super().__init__();self.reading_efficiency=True;self.reading_content_identity=True
        self.reading_page_navigation=True

    def read_page(self,page,path):
        record=super().read_page(page,path);value=received(record)
        if 'page_navigation' not in value:
            value['page_navigation']=dict({key:'合成局部事实，失败未决仍保留。' for key in FIELDS},
                covered_fragment_ids=[f['fragment_id'] for f in page['fragments']])
            record['response']['output'][0]['content'][0]['text']=json.dumps(value)
            atomic(path,record)
        return record


def output(page):
    return dict(notes={f['fragment_id']:dict(summary='合成失败待核，不补造恢复。',
        facts=[dict(anchor_id=anchors(f)[0]['anchor_id'],fact='合成逐字失败依据')],
        limitations=['结局未知'],open_questions=['需核后续']) for f in page['fragments']},
        page_navigation=dict({key:'本页失败与未决，不推断全周。' for key in FIELDS},
            covered_fragment_ids=[f['fragment_id'] for f in page['fragments']]))


class PageNavigationTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    base_client=fixtures.ReadingCacheTests.client

    def packet(self,count=24):
        value=fixtures.ReadingCacheTests.packet(self,count)
        value.update(since='2026-11-02T00:00:00+08:00',until='2026-11-09T00:00:00+08:00')
        return value

    def client(self,name='fusion',limit=100,turn=100):
        client=self.base_client(name,limit,turn)
        client.reading_efficiency=True;client.reading_content_identity=True
        client.reading_page_navigation=True
        client.reading_plan_root=self.root/'plans'
        return client

    def read(self,packet,client):
        return read_all(packet,client.root/'reading',client=client,limit=None)

    def test_new_contract_keeps_every_fragment_and_actual_request_within_original_limits(self):
        packet=self.packet(73)
        for index,event in enumerate(packet['events']):event['text']=('失败\\\n😀"'+str(index))*100
        old=plan(packet,efficiency=True,content_identity=True)
        new=plan(packet,efficiency=True,content_identity=True,page_navigation=True)
        self.assertNotIn('page_navigation_mode',old);self.assertEqual(MODE,new['page_navigation_mode'])
        self.assertEqual([f for p in old['pages'] for f in p['fragments']],
                         [f for p in new['pages'] for f in p['fragments']])
        for page in new['pages']:
            self.assertLessEqual(len(page['fragments']),24)
            self.assertLessEqual(raw_size(page),90_000)
            self.assertLessEqual(request_size(page,page_navigation=True),180_000)
        a,b=new['pages'][0]['fragments'][:2]
        size=lambda fs:request_size(dict(fragments=fs),page_navigation=True)
        self.assertEqual(7,size([a,b])-size([a])-size([b])+size([]))

    def test_valid_page_summary_cannot_mask_missing_notes_or_bad_facts(self):
        page=plan(self.packet(2),efficiency=True,page_navigation=True)['pages'][0]
        original=output(page);checked(page,original,page_navigation=True)
        variants=[]
        for kind in ('missing','anchor','limitation','coverage','counterevidence','huge'):
            value=copy.deepcopy(original);fid=page['fragments'][0]['fragment_id']
            if kind=='missing':value['notes'].pop(fid)
            elif kind=='anchor':value['notes'][fid]['facts'][0]['anchor_id']='unprovided'
            elif kind=='limitation':value['notes'][fid]['limitations']=['']
            elif kind=='coverage':value['page_navigation']['covered_fragment_ids']*=2
            elif kind=='counterevidence':value['page_navigation']['counterevidence']=''
            else:value['page_navigation']['summary']='反证不得删'*3000
            variants.append(value)
        for value in variants:
            with self.assertRaises(ValueError):checked(page,value,page_navigation=True)

    def test_leaf_hash_mapping_capacity_is_checked_before_accepting_p_page(self):
        page=plan(self.packet(24),efficiency=True,page_navigation=True)['pages'][0]
        value=output(page)
        value['page_navigation']['summary']='x'*(11_900-raw_size(value['page_navigation']))
        self.assertLessEqual(raw_size(value['page_navigation']),12_000)
        with self.assertRaisesRegex(ValueError,'真实叶引用'):checked(page,value,page_navigation=True)

    def test_page_branches_keep_all_facts_and_program_occurrences_and_save_navigation_calls(self):
        packet=self.packet(49)
        for event in packet['events']:event.update(protocol_role='tool',source_user_id='1',session_id='s')
        packet['events'] += [dict(packet['events'][0],event='repeat'),dict(packet['events'][1],event='empty',text='')]
        client=self.client();status=self.read(packet,client);p_calls=self.calls
        notes,groups=inputs(packet,status)
        self.assertEqual((3,2),(len(groups),sum('procedural' in n for n in notes)))
        self.assertTrue(all(n['facts'][0]['quote'] for n in notes if 'procedural' not in n))
        new=Navigation(self.root/'new-tree',packet,page_navigation=True)
        tree=new.build(notes,client,received,page_groups=groups)
        new_n=self.calls-p_calls
        old=Navigation(self.root/'old-tree',packet);before=self.calls
        old.build(notes,client,received);old_n=self.calls-before
        self.assertLess(new_n,old_n)
        self.assertEqual((1,3),(new_n,old_n))
        verify_tree(new,status)
        leaves=json.loads((new.root/'leaves.json').read_text())['node_ids']
        self.assertEqual(len(notes),len(leaves))
        repeat=[k for k in leaves if new.load(k)['note']['event_id']=='repeat']
        self.assertEqual(packet['events'][-2]['text'],new.read(repeat,raw=True)['nodes'][0]['text'])
        unit=dict(user_id='1',since=packet['since'],until=packet['until'],events=[])
        access=Access(Navigation(new.root,packet),self.root/'access',initial_directory=True)
        initial=access.start(unit)
        self.assertEqual([],initial['context_access']['raw_node_ids'])
        self.assertEqual(MODE,tree['scope']['page_navigation_mode'])

    def test_shared_external_p_receipt_change_is_detected_even_with_intact_tree(self):
        packet=self.packet(25);client=self.client();status=self.read(packet,client)
        notes,groups=inputs(packet,status);nav=Navigation(self.root/'tree',packet,page_navigation=True)
        nav.build(notes,client,received,page_groups=groups);verify_tree(nav,status)
        tree_before=(nav.root/'tree.json').read_bytes()
        receipt=Path(groups[0]['proof']['receipt_path']);record=json.loads(receipt.read_text())
        record['response']['changed']='原回执变化';atomic(receipt,record)
        with self.assertRaisesRegex(ValueError,'签名'):verify_tree(nav,status)
        self.assertEqual(tree_before,(nav.root/'tree.json').read_bytes())

    def test_valid_notes_bad_page_navigation_stops_without_rewriting_or_resending(self):
        packet=self.packet(2);client=self.client();original=client._transmit
        def broken(*args):
            record=original(*args);value=received(record)
            value['page_navigation']['counterevidence']=''
            record['response']['output'][0]['content'][0]['text']=json.dumps(value)
            atomic(args[0],record)
            return record
        with patch.object(client,'_transmit',side_effect=broken):status=self.read(packet,client)
        self.assertEqual('reading_blocked',status['state']);self.assertEqual(1,self.calls)
        self.assertEqual(1,client.budget.used())
        error=next(Path(status['evidence_directory']).glob('*.page-navigation-error.json'))
        before=error.read_bytes()
        self.assertEqual('reading_blocked',self.read(packet,client)['state'])
        self.assertEqual((1,before),(self.calls,error.read_bytes()))
        self.assertFalse(list(Path(status['evidence_directory']).glob('*.fix*.receipt.json')))

    def test_old_unknown_blocks_new_strategy_without_consuming_a_new_request(self):
        packet=self.packet(25);old=self.client('old');old.reading_page_navigation=False
        prior=self.read(packet,old)
        receipt=Path(prior['evidence_directory'])/'0.receipt.json'
        reference=json.loads(receipt.read_text());reference['status']='unknown';atomic(receipt,reference)
        before=receipt.read_bytes();calls=self.calls
        client=self.client('new');status=self.read(packet,client)
        self.assertEqual('reading_blocked',status['state']);self.assertEqual('unknown',status['blocked'][0]['state'])
        self.assertEqual((calls,0,before),(self.calls,client.budget.used(),receipt.read_bytes()))

    def test_same_page_new_output_contract_is_not_an_old_cache_hit(self):
        packet=self.packet(25);old=self.client('old');old.reading_page_navigation=False
        before=self.read(packet,old);self.assertEqual(2,self.calls)
        client=self.client('new');after=self.read(packet,client)
        self.assertEqual((4,2),(self.calls,client.budget.used()))
        self.assertNotEqual(before['evidence_directory'],after['evidence_directory'])
        self.assertEqual(4,len(list((self.root/'shared').rglob('*.receipt.json'))))
        inputs(packet,after);check_evidence(packet,Path(after['evidence_directory']))
        saved=self.calls;self.read(packet,self.client('new'));self.assertEqual(saved,self.calls)

    def test_partial_or_legacy_reading_cannot_be_reinterpreted_as_complete_page_navigation(self):
        packet=self.packet(25);client=self.client()
        status=read_all(packet,client.root/'reading',client=client,limit=1)
        self.assertEqual('reading_in_progress',status['state'])
        with self.assertRaises(ValueError):inputs(packet,status)
        old=self.client('legacy');old.reading_page_navigation=False
        status=self.read(packet,old)
        with self.assertRaises(ValueError):inputs(packet,status)

    def test_empty_source_text_all_program_leaves_need_no_p_or_n_model(self):
        packet=self.packet(40)
        for event in packet['events']:event['text']=''
        client=self.client();status=self.read(packet,client)
        notes,groups=inputs(packet,status);self.assertEqual([],groups)
        nav=Navigation(self.root/'tree',packet,page_navigation=True)
        nav.build(notes,client,received,page_groups=groups);verify_tree(nav,status)
        self.assertEqual(0,self.calls);self.assertEqual(40,len(notes))

    def test_fused_tree_refuses_old_leaf_prefix_algorithm_and_daily_claims_only_page_cache(self):
        packet=self.packet(96);client=self.client();status=self.read(packet,client)
        notes,groups=inputs(packet,status)
        nav=Navigation(self.root/'tree',packet,page_navigation=True)
        with self.assertRaisesRegex(ValueError,'旧片段下标'):nav.build_prefix(notes,client,received)
        calls=self.calls;prefix=deferred_prefix(packet,status)
        self.assertEqual(calls,self.calls);self.assertFalse(prefix['complete']);self.assertTrue(prefix['high_level_deferred'])
        self.assertEqual(4,prefix['pages']);self.assertFalse((nav.root/'tree.json').exists())

    def test_existing_navigation_root_cannot_be_upgraded_by_flag(self):
        packet=self.packet(2);Navigation(self.root/'tree',packet)
        with self.assertRaisesRegex(ValueError,'已有导航'):Navigation(self.root/'tree',packet,page_navigation=True)

    def test_complete_week_keeps_identification_scoring_audit_and_rechecks_shared_p(self):
        from pipeline import evaluate_week
        from shared_weeks import efficient_reading_files,check_efficient_reading,reference
        packet=source();before=digest(packet)
        client=FusionClient();client.readback=True;client.reading_plan_root=self.root/'whole-plans'
        root=self.root/'week';shared=self.root/'weeks'
        result=evaluate_week(packet,root,client,shared_root=shared)
        self.assertEqual('80.0',result['reports'][0]['score'])
        self.assertTrue((root/'identified.json').exists());self.assertTrue((root/'audit.json').exists())
        self.assertEqual(before,digest(packet))
        provenance=json.loads((root/'reading-view.json').read_text())['reading_provenance']
        self.assertEqual(MODE,provenance['page_navigation_mode'])
        calls=client.calls
        self.assertEqual(result,evaluate_week(packet,self.root/'second',client,shared_root=shared))
        self.assertEqual(calls,client.calls)
        entry=dict(directory=str(root),efficient_reading_files=[reference(p) for p in efficient_reading_files(root)])
        check_efficient_reading(entry,packet)
        evidence=Path(json.loads((root/'reading-efficiency.json').read_text())['evidence_directory'])
        proof=json.loads((evidence/'0.checked.json').read_text())
        receipt=Path(proof.get('receipt_path',evidence/'0.receipt.json'))
        value=json.loads(receipt.read_text());value['status']='unknown';atomic(receipt,value)
        with self.assertRaises(ValueError):check_efficient_reading(entry,packet)

    def test_shared_accepted_results_are_separate_by_mode_in_both_directions(self):
        from pipeline import evaluate_week
        from shared_weeks import source_identity
        packet=source();shared=self.root/'mode-weeks'
        old=Client();old.reading_efficiency=True;old.reading_content_identity=True
        a=evaluate_week(packet,self.root/'legacy',old,shared_root=shared)
        new=FusionClient();b=evaluate_week(packet,self.root/'fusion',new,shared_root=shared)
        self.assertGreater(new.calls,0)
        self.assertEqual(MODE,json.loads((self.root/'fusion/input.json').read_text())['page_navigation_mode'])
        self.assertTrue((self.root/'fusion/reading-view.json').exists())
        entries=[json.loads(p.read_text()) for p in (shared/source_identity(packet)).glob('*.json')]
        self.assertEqual({None,MODE},{e.get('page_navigation_mode') for e in entries})
        old2=Client();old2.reading_efficiency=True
        self.assertEqual(a,evaluate_week(packet,self.root/'legacy2',old2,shared_root=shared))
        new2=FusionClient()
        self.assertEqual(b,evaluate_week(packet,self.root/'fusion2',new2,shared_root=shared))
        self.assertEqual((0,0),(old2.calls,new2.calls))

    def test_existing_execution_and_shared_pointer_refuse_mode_switch_before_any_model_call(self):
        from pipeline import evaluate_week
        packet=source();shared=self.root/'mode-weeks'
        old=Client();old.reading_efficiency=True
        evaluate_week(packet,self.root/'legacy',old,shared_root=shared)
        evaluate_week(packet,self.root/'pointer',old,shared_root=shared)
        pointer=self.root/'pointer/shared-week.json';before=pointer.read_bytes()
        self.assertFalse((self.root/'pointer/input.json').exists())
        for directory in ('legacy','pointer'):
            for selected_shared in (None,shared):
                with self.subTest(directory=directory,shared=selected_shared):
                    client=FusionClient()
                    with self.assertRaises(Paused) as error:
                        evaluate_week(packet,self.root/directory,client,shared_root=selected_shared)
                    self.assertEqual('needs_review',error.exception.state);self.assertEqual(0,client.calls)
        self.assertEqual(before,pointer.read_bytes())
        self.assertFalse((self.root/'pointer/input.json').exists())

    def test_cross_mode_shared_unknown_cannot_be_bypassed_by_a_new_index(self):
        from shared_weeks import slot
        packet=self.packet(1);root=self.root/'pending';root.mkdir()
        receipt=root/'identify.receipt.json'
        with self.assertRaises(Paused):
            with slot(self.root/'shared-weeks',packet,root):
                atomic(receipt,dict(status='unknown',request_sha256='synthetic-unknown'))
                raise Paused('unknown','合成未定请求')
        before=receipt.read_bytes()
        with self.assertRaises(Paused) as error:
            with slot(self.root/'shared-weeks',packet,self.root/'fusion',page_navigation=True):
                self.fail('不得创建融合执行')
        self.assertEqual('unknown',error.exception.state);self.assertEqual(before,receipt.read_bytes())
        self.assertEqual(0,self.calls)

    def test_existing_execution_and_pointer_freeze_source_before_shared_lookup(self):
        from pipeline import evaluate_week
        packet=source();shared=self.root/'source-weeks';client=FusionClient()
        evaluate_week(packet,self.root/'original',client,shared_root=shared)
        evaluate_week(packet,self.root/'pointer',client,shared_root=shared)
        changed=copy.deepcopy(packet);changed['events'][0]['text']+='新增合成反证'
        # 另一目录的合法已核结果也不能绕过本目录的冻结来源。
        evaluate_week(changed,self.root/'changed',FusionClient(),shared_root=shared)
        for name in ('original','pointer'):
            root=self.root/name;before={p:p.read_bytes() for p in root.rglob('*.json')}
            for selected_shared in (None,shared):
                with self.subTest(directory=name,shared=selected_shared):
                    test_client=FusionClient()
                    with self.assertRaises(Paused) as error:
                        evaluate_week(changed,root,test_client,shared_root=selected_shared)
                    self.assertEqual('needs_review',error.exception.state)
                    self.assertEqual(0,test_client.calls)
                    self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*.json')})

    def test_same_source_pointer_revalidates_and_resumes_without_local_duplicate_execution(self):
        from pipeline import evaluate_week
        packet=source();shared=self.root/'resume-weeks';original=self.root/'accepted'
        result=evaluate_week(packet,original,FusionClient(),shared_root=shared)
        pointer=self.root/'alias'
        self.assertEqual(result,evaluate_week(packet,pointer,FusionClient(),shared_root=shared))
        before={p:p.read_bytes() for p in pointer.rglob('*') if p.is_file()}
        for selected_shared in (None,shared):
            client=FusionClient()
            self.assertEqual(result,evaluate_week(packet,pointer,client,shared_root=selected_shared))
            self.assertEqual(0,client.calls)
            self.assertEqual(before,{p:p.read_bytes() for p in pointer.rglob('*') if p.is_file()})
        (original/'review.json').write_text('{}')
        for selected_shared in (None,shared):
            client=FusionClient()
            with self.assertRaisesRegex(ValueError,'签名变化'):
                evaluate_week(packet,pointer,client,shared_root=selected_shared)
            self.assertEqual(0,client.calls)
            self.assertEqual(before,{p:p.read_bytes() for p in pointer.rglob('*') if p.is_file()})


if __name__=='__main__':unittest.main()
