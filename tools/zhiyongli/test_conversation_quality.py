"""验证分批复核保留引用原文、相邻上下文和全部待核判断。"""
import json
import unittest
import jsonschema

from conversation_quality import packets, contract
from task_review_policy import RUBRIC


class QualityPacketTests(unittest.TestCase):
    def test_disjoint_clauses_from_one_message_are_compared_as_a_whole_behavior(self):
        data,candidate=self.fixture()
        data['records'][0]['text_parts'].append(['q-object','针对这五人的任务，'])
        candidate['items']['P1']['evidence']=[dict(fragment_id='q-object',claim='指定检查对象',origin='personal')]
        candidate['items']['J1']['evidence']=[dict(fragment_id='q0',claim='检查是否有漏洞',origin='personal')]
        before=json.dumps(candidate,ensure_ascii=False)
        groups=packets(data,candidate,include_coverage=False)
        self.assertEqual(len(groups[0]['shared_source_claims']),1)
        shared=groups[0]['shared_source_claims'][0]
        self.assertEqual(shared['record_id'],'r0')
        self.assertEqual({row['item'] for row in shared['claims']},{'P1','J1'})
        self.assertEqual(json.dumps(candidate,ensure_ascii=False),before)
        self.assertIn(data['records'][0],groups[0]['records'])

    def test_next_anchor_uses_nearest_original_band_and_not_exceptional_requirements(self):
        data,candidate=self.fixture();packet=packets(data,candidate,include_coverage=False)[0]
        anchor=packet['adjacent_anchors']['J1']
        self.assertEqual(anchor,dict(band='outstanding',criterion='J1.outstanding',anchor=RUBRIC['performance_anchors']['J1']['outstanding']))
        candidate['items']['J1']['band']='exceptional'
        packet=packets(data,candidate,include_coverage=False)[0]
        self.assertIsNone(packet['adjacent_anchors']['J1'])
        candidate['items']['J1']['band']='critical_breach'
        packet=packets(data,candidate,include_coverage=False)[0]
        self.assertNotIn('J1',packet['adjacent_anchors'])

    def test_coverage_requires_current_personal_evidence_instead_of_history_summary(self):
        data,candidate=self.fixture();packet=packets(data,candidate)[-1]
        self.assertTrue(packet['coverage_only'])
        packet['records'].append(dict(id='history',role='context',boundary_context=True,text_parts=[['history-q','摘要说用户纠过错']]))
        packet['records'].append(dict(id='old-user',role='user',boundary_context=True,text_parts=[['old-q','范围外原话']]))
        schema=contract(packet,dict(items={}))
        issue=dict(item='E2',claim_pointer='/summary',criterion='E2.reasonable',problem='遗漏了本人的独立判断',source_refs=['history'],related_items=[])
        for refs in [['history'],['history-q'],['old-user'],['old-q']]:
            with self.subTest(refs=refs),self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(issues=[dict(issue,source_refs=refs)]),schema)
        personal=next(r for r in packet['records'] if r.get('role')=='user' and not r.get('boundary_context'))
        jsonschema.validate(dict(issues=[dict(issue,source_refs=[personal['id'],'history'])]),schema)
        jsonschema.validate(dict(issues=[]),schema)

    def test_no_personal_records_cannot_manufacture_coverage_omissions(self):
        data,candidate=self.fixture();packet=packets(data,candidate)[-1]
        for r in packet['records']:r['role']='context';r['boundary_context']=True
        schema=contract(packet,dict(items={}));jsonschema.validate(dict(issues=[]),schema)
        issue=dict(item='E2',claim_pointer='/summary',criterion='E2.reasonable',problem='历史摘要不能补造本人证据',source_refs=[packet['records'][0]['id']],related_items=[])
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(issues=[issue]),schema)

    def fixture(self):
        records = [dict(id='r'+str(i), role='user', start=0,end=1200,text_parts=[['q'+str(i), '中😀'*600]])
                   for i in range(9)]
        items = {code: dict(band='good', reason='具体依据', next_band='下一档依据',
                            evidence=[dict(fragment_id='q'+str(i*4), claim='本人行为', origin='personal')],
                            context_refs=['r'+str(i*4+1)], negative=None)
                 for i, code in enumerate(['P1', 'J1'])}
        return dict(records=records, rubric=RUBRIC, directory=['不得重复交付'],
                    correction=['历史纠正']), dict(summary='整体判断', items=items)

    def test_split_preserves_all_judgments_and_verbatim_context(self):
        data, candidate = self.fixture()
        combined = packets(data, candidate,include_coverage=False)[0]
        limit = len(json.dumps(combined, ensure_ascii=False).encode())-1
        groups = packets(data, candidate, limit,include_coverage=False)
        self.assertEqual([p['items_in_scope'] for p in groups], [['P1'], ['J1']])
        self.assertEqual([p['records'] for p in groups],
                         [data['records'][:3], data['records'][3:7]])
        for group in groups:
            self.assertLessEqual(len(json.dumps(group, ensure_ascii=False).encode()), limit)
            self.assertNotIn('directory', group)
            self.assertNotIn('correction', group)
            self.assertEqual(set(group['candidate']['items']), set(group['items_in_scope']))
            self.assertEqual(set(group['rubric']['items']), set(group['items_in_scope']))
            for code,other in group['other_judgments'].items():
                self.assertEqual(other['reason'],candidate['items'][code]['reason'])
                self.assertEqual(other['evidence'],candidate['items'][code]['evidence'])
            schema = contract(group, group['candidate'])
            pointers = schema['properties']['issues']['items']['properties']['claim_pointer']['enum']
            self.assertNotIn('/summary', pointers)

    def test_oversize_single_judgment_is_blocked_without_truncation(self):
        data, candidate = self.fixture()
        before = json.dumps(data, ensure_ascii=False)
        with self.assertRaisesRegex(ValueError, '不截断原文'):
            packets(data, candidate, 100)
        self.assertEqual(json.dumps(data, ensure_ascii=False), before)

    def test_large_indivisible_context_has_bounded_singleton_capacity(self):
        data,candidate=self.fixture()
        data['records'][1]=dict(id='r1',role='assistant',start=0,end=14000,text='中'*14000)
        before=json.dumps(data,ensure_ascii=False)
        with self.assertRaisesRegex(ValueError,'不截断原文'):packets(data,candidate,include_coverage=False)
        groups=packets(data,candidate,include_coverage=False,singleton_maximum=60000)
        large=next(p for p in groups if p['items_in_scope']==['P1'])
        self.assertIn(data['records'][1],large['records'])
        self.assertLessEqual(len(json.dumps(large,ensure_ascii=False).encode()),60000)
        self.assertEqual(json.dumps(data,ensure_ascii=False),before)
        with self.assertRaisesRegex(ValueError,'不截断原文'):
            packets(data,candidate,include_coverage=False,singleton_maximum=40000)

    def test_unscored_items_receive_actual_source_coverage_without_regrading_present_items(self):
        data,candidate=self.fixture();groups=[p for p in packets(data,candidate) if p.get('coverage_only')]
        self.assertTrue(groups)
        recovered={}
        for group in groups:
            self.assertIn('M3',group['items_in_scope']);self.assertNotIn('P1',group['items_in_scope'])
            self.assertEqual(group['candidate']['items'],{})
            for row in group['records']:
                recovered.setdefault(row['id'],[]).extend(row['text_parts'])
            schema=contract(group,group['candidate'])
            issue=dict(item='M3',claim_pointer='/summary',criterion='M3.reasonable',
                problem='原文中的独立改选未进入分项。',source_refs=[group['records'][0]['id']],related_items=['P1'])
            jsonschema.validate(dict(issues=[issue]),schema)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(dict(issues=[dict(issue,item='P1')]),schema)
            with self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(dict(issues=[dict(issue,related_items=['M1'])]),schema)
        self.assertEqual(recovered,{r['id']:r['text_parts'] for r in data['records']})

    def test_empty_judgment_checks_summary_against_actual_records(self):
        data, _ = self.fixture()
        groups = packets(data, dict(summary='没有本人行为', items={}))
        self.assertGreater(len(groups), 1)
        self.assertEqual([r for group in groups for r in group['records']], data['records'])
        for group in groups:
            self.assertTrue(group['summary_in_scope'])
            self.assertEqual(set(group['rubric']['items']), set(RUBRIC['items']))
            self.assertLessEqual(len(json.dumps(group, ensure_ascii=False).encode()), 36000)


if __name__ == '__main__': unittest.main()
