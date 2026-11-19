"""只读恢复诊断核有效回执链，不等于恢复授权，不发送模型或改队列。"""
import json
import io
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import operations
from request_retry import VERSION,attempt_path
from runtime import atomic,digest


class ReadingRecoveryDiagnosisTests(unittest.TestCase):
    def failure(self):
        return dict(status='received',request_sha256='synthetic-request',response=dict(status='failed',output=[],
            error=dict(code='gateway_concurrency_limit',message='Concurrency limit exceeded for account, please retry later')))

    def chain(self,root,records=None,exhausted=True):
        entry=root/'page.receipt.json';records=records or [self.failure(),self.failure()]
        for i,record in enumerate(records):atomic(attempt_path(entry,i),record)
        atomic(entry,dict(transport_version=VERSION,request_sha256='synthetic-request',
            status=records[-1]['status'] if exhausted else 'retry_wait',exhausted=exhausted,
            attempts=[dict(sha256=digest(record)) for record in records]))
        return entry

    def diagnosis(self,path):return operations.reading_recovery_diagnosis(path)

    def test_gateway_exhaustion_is_not_automatic_recovery_and_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.chain(root)
            before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            result=self.diagnosis(entry)
            self.assertEqual('gateway_retry_exhausted',result['classification'])
            self.assertTrue(result['receipt_chain_verified']);self.assertTrue(result['exhausted'])
            self.assertEqual(2,result['attempts']);self.assertFalse(result['automatic_resume_allowed'])
            self.assertEqual('not_implemented',result['recovery_mechanism'])
            self.assertEqual(before,{str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertNotIn('Concurrency limit',json.dumps(result));self.assertNotIn(str(root),json.dumps(result))

    def test_stale_shared_reference_only_accepts_verified_original_gateway_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.chain(root);ref=root/'local.receipt.json'
            atomic(ref,dict(shared_reading_receipt=str(entry),request_sha256='synthetic-request',shared_receipt_sha256=digest(self.failure())))
            result=self.diagnosis(ref)
            self.assertEqual('gateway_retry_exhausted',result['classification'])
            self.assertTrue(result['shared_reference_requires_refresh'])
            changed=json.loads(ref.read_text());changed['shared_receipt_sha256']='tampered';atomic(ref,changed)
            self.assertEqual('invalid_receipt_chain',self.diagnosis(ref)['classification'])

    def test_unknown_requesting_and_pending_missing_receipt_never_allow_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for status in ('unknown','requesting'):
                path=root/(status+'.receipt.json');atomic(path,dict(status=status,request_sha256='synthetic-request'))
                result=self.diagnosis(path)
                self.assertEqual('unknown_requires_reconciliation',result['classification'])
                self.assertFalse(result['automatic_resume_allowed'])
            entry=self.chain(root,exhausted=False);value=json.loads(entry.read_text())
            budget=root/'request-budget.json';target=attempt_path(entry,2)
            atomic(budget,dict(version=1,entries={str(target.resolve()):dict(request_sha256='synthetic-request')}))
            value['pending']=dict(index=2,budget=str(budget));atomic(entry,value)
            self.assertEqual('unknown_requires_reconciliation',self.diagnosis(entry)['classification'])

    def test_receipt_success_does_not_claim_page_checked_and_corruption_is_not_recoverable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);success=dict(status='received',request_sha256='synthetic-request',response=dict(status='completed',output=[]))
            entry=self.chain(root,[self.failure(),success]);result=self.diagnosis(entry)
            self.assertEqual('completed_response_requires_page_validation',result['classification'])
            self.assertFalse(result['page_validation_performed'])
            corrupt=attempt_path(entry,0);raw=json.loads(corrupt.read_text());raw['changed']=True;atomic(corrupt,raw)
            self.assertEqual('invalid_receipt_chain',self.diagnosis(entry)['classification'])
            self.assertEqual('unavailable',self.diagnosis(root/'missing.receipt.json')['classification'])

    def test_waiting_and_non_gateway_failed_output_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);waiting=self.chain(root,exhausted=False)
            self.assertEqual('owned_executor_retry_wait',self.diagnosis(waiting)['classification'])
            path=root/'has-output.receipt.json';bad=self.failure();bad['response']['output']=[dict(text='非空输出不可安全重发')];atomic(path,bad)
            self.assertEqual('failed_not_eligible_for_gateway_recovery',self.diagnosis(path)['classification'])
            mixed=self.chain(root,[dict(status='rejected',error='http_429',request_sha256='synthetic-request'),self.failure()])
            self.assertEqual('mixed_retry_history_requires_review',self.diagnosis(mixed)['classification'])

    def test_standalone_cli_needs_no_database_flow_or_model_and_default_still_requires_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);entry=self.chain(root);output=io.StringIO()
            with patch('sys.argv',['operations','--reading-receipt',str(entry)]),redirect_stdout(output):
                operations.main()
            self.assertEqual('gateway_retry_exhausted',json.loads(output.getvalue())['classification'])
            with patch('sys.argv',['operations']),patch('sys.stderr',new=io.StringIO()),self.assertRaises(SystemExit) as caught:
                operations.main()
            self.assertEqual(2,caught.exception.code)


if __name__=='__main__':unittest.main()
