"""候选导入恢复的意图、回执及幂等；服务端为显式合成器，不代替Go/PG验收。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import atomic,digest
from worker import import_candidate


class SyntheticImporter:
    base_url='http://127.0.0.1:1'
    def __init__(self):self.calls=0;self.runs={};self.cut=False;self.created_at='2026-11-15T00:00:00Z'

    def __call__(self,job,bundle):
        self.calls+=1
        # 仅模拟“相同完整包返回同Run”，不把此SHA冒充Go类型化JSON摘要。
        key=digest(bundle)
        if key not in self.runs:
            self.runs[key]=dict(id=len(self.runs)+1,digest=key,source_name=bundle['source_name'],
                report_count=len(bundle['reports']),created_at=self.created_at)
        if self.cut:self.cut=False;raise ConnectionResetError('合成服务端提交后连接中断')
        return copy.deepcopy(self.runs[key])


class CandidateImportTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.job=dict(id=21);self.bundle=dict(source_name='synthetic-only',reports=[dict(synthetic=True)])
        self.send=SyntheticImporter()

    def run_import(self):return import_candidate(self.root,self.job,self.bundle,self.send)

    def evidence(self):
        return {p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.glob('import*.json')}

    def test_completed_import_reentry_rechecks_server_and_preserves_evidence(self):
        run=self.run_import();before=self.evidence()
        self.assertEqual(run,self.run_import());self.assertEqual(2,self.send.calls)
        self.assertEqual(before,self.evidence())

    def test_full_bundle_is_frozen_before_any_import_send(self):
        def unavailable(job,bundle):
            self.assertEqual(json.loads((self.root/'import-bundle.json').read_text()),bundle)
            self.assertEqual(json.loads((self.root/'import-input.json').read_text())['bundle_sha256'],digest(bundle))
            raise ConnectionResetError('发送后没有收到回执')
        unavailable.base_url=self.send.base_url
        with self.assertRaises(ConnectionResetError):import_candidate(self.root,self.job,self.bundle,unavailable)
        self.assertEqual(json.loads((self.root/'import-bundle.json').read_text()),self.bundle)
        self.assertFalse((self.root/'import.json').exists())

    def test_checked_files_cannot_claim_completion_after_server_rollback(self):
        self.run_import();before=self.evidence()
        # 产品库较旧、文件卷较新；重建同包的Run即使ID相同，创建时刻也不能被静默替换。
        self.send.runs.clear();self.send.created_at='2026-11-16T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'幂等回执'):self.run_import()
        self.assertEqual(2,self.send.calls);self.assertEqual(1,len(self.send.runs))
        self.assertEqual(before,self.evidence())

    def test_checked_files_do_not_hide_server_failure(self):
        self.run_import();before=self.evidence();calls=[]
        def unavailable(job,bundle):
            calls.append((job,bundle));raise ConnectionResetError('合成接入服务无响应')
        unavailable.base_url=self.send.base_url
        with self.assertRaises(ConnectionResetError):import_candidate(self.root,self.job,self.bundle,unavailable)
        self.assertEqual(1,len(calls));self.assertEqual(before,self.evidence())

    def test_legacy_receipt_without_intent_is_preserved_and_never_adopted(self):
        path=self.root/'import.json';path.write_text('{}');before=self.evidence()
        with self.assertRaisesRegex(ValueError,'缺少事前'):self.run_import()
        self.assertEqual(before,self.evidence());self.assertEqual(0,self.send.calls)
        self.assertFalse((self.root/'import-input.json').exists())

    def test_http_commit_before_receipt_replays_same_package_and_creates_one_run(self):
        self.send.cut=True
        with self.assertRaises(ConnectionResetError):self.run_import()
        self.assertEqual((1,1),(self.send.calls,len(self.send.runs)))
        intent=(self.root/'import-input.json').read_bytes()
        self.assertFalse((self.root/'import.json').exists())
        self.run_import()
        self.assertEqual((2,1),(self.send.calls,len(self.send.runs)))
        self.assertEqual(intent,(self.root/'import-input.json').read_bytes())

    def test_receipt_before_checked_is_verified_again_without_rewriting_receipt(self):
        def crash(path,value):
            if Path(path).name=='import-checked.json':raise KeyboardInterrupt('合成落盘中断')
            atomic(path,value)
        with patch('bounded_read.atomic',side_effect=crash),self.assertRaises(KeyboardInterrupt):self.run_import()
        prior=self.root/'import.json';before=(prior.read_bytes(),prior.stat().st_mtime_ns)
        self.run_import()
        self.assertEqual((2,1),(self.send.calls,len(self.send.runs)))
        self.assertEqual(before,(prior.read_bytes(),prior.stat().st_mtime_ns))
        self.assertTrue((self.root/'import-checked.json').exists())

    def test_checked_without_receipt_does_not_resend_or_mark_success(self):
        self.run_import();(self.root/'import.json').unlink();before=self.evidence();calls=self.send.calls
        with self.assertRaisesRegex(ValueError,'原回执缺失'):self.run_import()
        self.assertEqual(calls,self.send.calls);self.assertEqual(before,self.evidence())

    def test_malformed_or_changed_receipt_is_preserved_with_zero_new_http(self):
        run=self.run_import();path=self.root/'import.json';calls=self.send.calls
        variants=[None,{},dict(run,id=True),dict(run,digest='wrong'),dict(run,report_count=True),
            dict(run,source_name='other'),dict(run,created_at='yesterday'),
            dict(run,created_at='2026-11-15T00:00:00'),dict(run,id=456)]
        for value in variants:
            with self.subTest(value=value):
                atomic(path,value);before=self.evidence()
                with self.assertRaises(ValueError):self.run_import()
                self.assertEqual(calls,self.send.calls);self.assertEqual(before,self.evidence())

    def test_frozen_job_bundle_and_endpoint_cannot_drift(self):
        self.run_import();before=self.evidence();calls=self.send.calls
        changes=[('job',dict(id=22)),('bundle',dict(self.bundle,source_name='changed'))]
        for attribute,value in changes:
            with self.subTest(attribute=attribute):
                original=getattr(self,attribute);setattr(self,attribute,value)
                with self.assertRaises(ValueError):self.run_import()
                setattr(self,attribute,original)
        self.send.base_url='http://127.0.0.1:2'
        with self.assertRaises(ValueError):self.run_import()
        self.assertEqual(calls,self.send.calls);self.assertEqual(before,self.evidence())

    def test_unchecked_plausible_but_wrong_run_requires_server_verification_and_is_preserved(self):
        run=self.run_import();(self.root/'import-checked.json').unlink()
        path=self.root/'import.json';atomic(path,dict(run,id=999));before=self.evidence()
        with self.assertRaisesRegex(ValueError,'幂等回执'):self.run_import()
        self.assertEqual(2,self.send.calls);self.assertEqual(before,self.evidence())
        self.assertEqual(1,len(self.send.runs))

    def test_invalid_server_run_never_creates_success_receipt(self):
        for value in (None,{},dict(id=1,digest='a'*64,source_name='wrong',report_count=1,created_at='2026-11-15T00:00:00Z')):
            with self.subTest(value=value):
                def send(*args):return value
                send.base_url=SyntheticImporter.base_url
                with self.assertRaises(ValueError):import_candidate(self.root,self.job,self.bundle,send)
                self.assertFalse((self.root/'import.json').exists())
                self.assertFalse((self.root/'import-checked.json').exists())


if __name__=='__main__':unittest.main()
