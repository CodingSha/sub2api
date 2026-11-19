"""合成候选验证无进展守卫；不访问模型、不放行未决异议。"""
import json
from pathlib import Path
import tempfile
import unittest

from shared_weeks import reference
from runtime import atomic
from pipeline import evaluate_week,Paused
from review_control import revise
from revision_convergence import decision
from test_execution_paths import FixtureClient,packet,record

class Client(FixtureClient):
    verdict='revise';issue='同一异议';global_issue=False
    candidate_reason=None;own_state=None
    def call(self,u,path,feedback=None):
        result=super().call(u,path)
        if self.candidate_reason:
            value=json.loads(result['response']['output'][0]['content'][0]['text'])
            value['period_reviews'][0]['cells'][0]['reason']=self.candidate_reason
            return record(value,path)
        return result
    def audit_review(self,u,r,path):
        result=super().audit_review(u,r,path)
        value=json.loads(result['response']['output'][0]['content'][0]['text'])
        value['items']['P2'].update(verdict=self.verdict,reason=self.issue,problem_codes=['synthetic'])
        if self.own_state:value['items']['P2']['own_state']=self.own_state
        if self.global_issue:value['global_issues']=['合成跨项异议']
        return record(value,path)

class RevisionConvergenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
    def run_round(self,root,client):
        with self.assertRaises(Paused):evaluate_week(self.source,root,client)
    def test_unresolved_only_stops_without_modifying_files_or_accepting(self):
        client=Client();client.verdict='unresolved';self.run_round(self.root,client)
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertFalse(decision(self.root)['allow']);self.assertFalse(decision(self.root)['allow'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertFalse((self.root/'bundle.json').exists());self.assertEqual(3,client.calls)
    def test_revise_allowed_but_identical_verified_ancestor_stops(self):
        self.run_round(self.root,Client());self.assertTrue(decision(self.root)['allow'])
        child=revise(self.root,'测试','合成修订',automatic_round=1)
        self.run_round(child,Client());self.assertFalse(decision(child)['allow'])
    def test_changed_objection_and_global_issue_allow_original_bounded_revision(self):
        self.run_round(self.root,Client());child=revise(self.root,'测试','合成修订',automatic_round=1)
        client=Client();client.issue='新的具体异议';self.run_round(child,client)
        self.assertTrue(decision(child)['allow'])
        with tempfile.TemporaryDirectory() as tmp:
            client=Client();client.verdict='unresolved';client.global_issue=True
            self.run_round(Path(tmp),client);self.assertTrue(decision(Path(tmp))['allow'])
    def test_changed_candidate_and_unresolved_state_mismatch_do_not_stop(self):
        self.run_round(self.root,Client());child=revise(self.root,'测试','合成修订',automatic_round=1)
        client=Client();client.candidate_reason='新的完整候选理由';self.run_round(child,client)
        self.assertTrue(decision(child)['allow'])
        with tempfile.TemporaryDirectory() as tmp:
            client=Client();client.verdict='unresolved';client.own_state='no_opportunity'
            self.run_round(Path(tmp),client);self.assertTrue(decision(Path(tmp))['allow'])
    def test_quote_only_final_receipt_is_conservative_and_missing_parent_fails(self):
        self.run_round(self.root,Client())
        path=self.root/'audit.receipt.json'
        record(dict(event='synthetic',quote='合成'),path)
        pointer=self.root/'current-model-audit.json';proof=json.loads(pointer.read_text())
        proof['receipt']=reference(path);atomic(pointer,proof)
        self.assertTrue(decision(self.root)['allow'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.run_round(root,Client());child=revise(root,'测试','合成修订',automatic_round=1)
            self.run_round(child,Client());(root/'audit.json').unlink()
            with self.assertRaises(OSError):decision(child)
    def test_nonadjacent_a_b_a_stops_and_ancestor_new_unknown_rejects(self):
        self.run_round(self.root,Client())
        middle=revise(self.root,'测试','第二个不同候选',automatic_round=1)
        client=Client();client.candidate_reason='B候选';self.run_round(middle,client)
        self.assertTrue(decision(middle)['allow'])
        final=revise(self.root,'测试','返回A候选',automatic_round=2)
        self.run_round(final,Client())
        result=decision(final);self.assertFalse(result['allow'])
        self.assertEqual(str(self.root.resolve()),result['proof']['ancestor'])
        atomic(self.root/'new-unknown.receipt.json',dict(status='unknown'))
        with self.assertRaisesRegex(ValueError,'未决请求'):decision(final)
    def test_derived_audit_or_candidate_tamper_and_unknown_reject(self):
        for case in ('audit','review','unknown'):
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);self.run_round(root,Client())
                if case=='unknown':atomic(root/'other.receipt.json',dict(status='unknown'))
                else:
                    path=root/('model-'+case+'.json');value=json.loads(path.read_text())
                    if case=='audit':value['supported']=True
                    else:value['value']['evidence'][0]['quote']='篡改'
                    atomic(path,value)
                with self.assertRaises(ValueError):decision(root)
    def test_contract_error_keeps_existing_behavior_and_parent_tamper_rejects(self):
        self.run_round(self.root,Client());child=revise(self.root,'测试','合成修订',automatic_round=1)
        self.run_round(child,Client())
        path=self.root/'audit.json';original=path.read_bytes();path.write_text('{}')
        with self.assertRaises(ValueError):decision(child)
        atomic(child/'contract-error.json',dict(stage='review',error='合成'))
        with self.assertRaises(ValueError):decision(child)
        path.write_bytes(original)
        self.assertTrue(decision(child)['allow'])

if __name__=='__main__':unittest.main()
