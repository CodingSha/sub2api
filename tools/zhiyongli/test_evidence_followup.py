"""区分未提供原文与来源真缺失；停止重评不能关闭补证工作。"""
import copy
import unittest

from revision_convergence import evidence_followup


class EvidenceFollowupTests(unittest.TestCase):
    def setUp(self):
        self.packet=dict(events=[dict(event='request',text='核对全部条件'),dict(event='reply',text='条件未满足；后续修复')],issues=[])
        self.issues=dict(items={'J2':dict(verdict='unresolved')})

    def test_unprovided_reply_remains_locatable_and_never_called_missing_source(self):
        context=dict(events=self.packet['events'][:1],context_access=dict(
            available_node_ids=['branch','leaf'],leaf_node_ids=['leaf'],raw_node_ids=[],inspected_node_ids=[]))
        before=copy.deepcopy(context);value=evidence_followup(self.packet,context,self.issues)
        self.assertEqual('locate_frozen_evidence',value['next_action'])
        self.assertEqual(1,value['not_fully_provided_events'])
        self.assertEqual((1,1),(value['available_raw_nodes'],value['available_branches']))
        self.assertFalse(value['source_missing_established']);self.assertTrue(value['required'])
        self.assertEqual(before,context)

    def test_partial_text_is_not_treated_as_full_and_out_of_order_spans_cover_without_gaps(self):
        def span(start,end):return dict(event='reply-span-'+str(start)+'-'+str(end),source_event='reply',
            source_span=[start,end],text=self.packet['events'][1]['text'][start:end])
        context=dict(events=[self.packet['events'][0],span(0,3),span(4,len(self.packet['events'][1]['text']))])
        value=evidence_followup(self.packet,context,self.issues)
        self.assertEqual((1,1),(value['not_fully_provided_events'],value['partially_provided_events']))
        context['events'].append(span(3,4));value=evidence_followup(self.packet,context,self.issues)
        self.assertEqual(0,value['not_fully_provided_events'])
        self.assertEqual('adjudicate_existing_evidence',value['next_action']);self.assertTrue(value['required'])
        context['events'].append(self.packet['events'][1])
        self.assertEqual(0,evidence_followup(self.packet,context,self.issues)['not_fully_provided_events'])

    def test_bad_span_and_absent_source_issue_do_not_prove_source_missing(self):
        self.packet['issues']=[dict(code='saved_source_truncated')]
        context=dict(events=[dict(event='reply-fake',source_event='reply',source_span=[0,9],text='伪造原文')])
        value=evidence_followup(self.packet,context,self.issues)
        self.assertEqual(2,value['not_fully_provided_events']);self.assertEqual(1,value['source_issue_count'])
        self.assertFalse(value['source_missing_established']);self.assertEqual(['J2'],value['unresolved_items'])

    def test_full_raw_with_dispute_never_marks_followup_complete(self):
        value=evidence_followup(self.packet,dict(events=self.packet['events']),self.issues)
        self.assertEqual(0,value['not_fully_provided_events']);self.assertTrue(value['required'])
        self.assertFalse(value['source_missing_established'])


if __name__=='__main__':unittest.main()
