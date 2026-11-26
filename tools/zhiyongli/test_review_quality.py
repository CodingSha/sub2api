import copy
import unittest
from runtime import digest
from review_quality import compare_verified as compare,release_gate
from task_evidence_flow import no_observation


class QualityTests(unittest.TestCase):
    def pair(self):
        case=dict(case_id='real-url-case',items={'E3':no_observation('E3','只是文档读取，没有用户选用工作流证据')})
        reference=dict(source_sha256='source',rubric_sha256='rules',reviewer='codex_direct_review',
            review_notes='实际阅读了该任务7条原文',candidate_seen_before_reference=False,cases=[case])
        candidate=dict(source_sha256='source',rubric_sha256='rules',origin='verified_model_receipts',receipt_hashes=['receipt'],
            cases=[dict(copy.deepcopy(case),source_records={'u1':dict(role='user',text='读取这个文档')})])
        return reference,candidate
    def test_predefined_or_copied_candidate_does_not_pass(self):
        ref,cand=self.pair();cand['origin']='hardcoded'
        with self.assertRaisesRegex(ValueError,'回执'):compare(ref,cand)
    def test_matching_one_negative_case_is_never_release_ready(self):
        ref,cand=self.pair();report=compare(ref,cand)
        gate=release_gate(report,{},{});self.assertEqual(gate['status'],'blocked')
        self.assertIn('缺少真实E3正例或反例',gate['failures'])
    def test_missing_observed_evidence_is_a_conflict_not_perfect_citation_rate(self):
        ref,cand=self.pair();cand['cases'][0]['items']['E3'].update(state='observed',band='good')
        report=compare(ref,cand)
        self.assertEqual(report['e3_false_positive_rate'],1)
        self.assertIsNone(report['e3_false_negative_rate'])
        self.assertTrue(any(r.get('kind')=='missing_evidence' for r in report['conflicts']))
