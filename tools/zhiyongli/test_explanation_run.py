"""受管共享来源说明执行归属；只用合成accepted及本地锁。"""
import json
from contextlib import contextmanager
import multiprocessing
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from explanation_run import claim
from explanation_stage import enrich,verify
from pipeline import Paused
from runtime import atomic,digest
from shared_weeks import reference
import test_explanation_stage as fixtures


def competing(entry,root,queue):
    try:
        with claim(entry,root):queue.put('entered')
    except Exception as ex:queue.put(type(ex).__name__)


class ExplanationRunTests(unittest.TestCase):
    setUp=fixtures.ExplanationStageTests.setUp

    def owner(self):return self.entry_path.parent/'explanation-run/owner.json'

    def test_completed_owner_blocks_another_root_and_same_root_replays_without_send(self):
        client=fixtures.BasisClient();expected=enrich(self.entry,self.output,client)
        replay=fixtures.BasisClient();self.assertEqual(expected,enrich(self.entry,self.output,replay));self.assertEqual(0,replay.calls)
        alternate=fixtures.BasisClient()
        with self.assertRaises(Paused) as error:enrich(self.entry,self.root/'other',alternate)
        self.assertEqual('needs_review',error.exception.state);self.assertEqual(0,alternate.calls)

    def test_unknown_owner_cannot_be_bypassed_with_empty_root(self):
        with claim(self.entry,self.output):pass
        atomic(self.output/'generation.receipt.json',dict(status='unknown',request_sha256='0'*64))
        client=fixtures.BasisClient()
        with self.assertRaises(Paused) as error:enrich(self.entry,self.root/'other',client)
        self.assertEqual('unknown',error.exception.state);self.assertEqual(0,client.calls)

    def test_same_root_unknown_receipt_still_blocks_without_new_send(self):
        enrich(self.entry,self.output,fixtures.BasisClient())
        path=self.output/'basis/generation.receipt.json'
        record=json.loads(path.read_text());record['status']='unknown';atomic(path,record)
        before=path.read_bytes();client=fixtures.BasisClient()
        with self.assertRaises(Paused) as error:enrich(self.entry,self.output,client)
        self.assertEqual('unknown',error.exception.state)
        self.assertEqual((0,before),(client.calls,path.read_bytes()))

    def test_readonly_missing_registration_never_creates_state(self):
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaises(ValueError):verify(self.entry,self.output,fixtures.BasisClient().config)
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertFalse(self.owner().parent.exists())

    def test_readonly_existing_owner_preserves_bytes_and_mtime(self):
        client=fixtures.BasisClient();enrich(self.entry,self.output,client)
        paths=[self.owner(),self.owner().parent/'run.lock',self.owner().parent/'requests.json']
        before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths}
        verify(self.entry,self.output,client.config)
        self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in paths})

    def test_corrupted_or_missing_owner_is_not_recreated(self):
        with claim(self.entry,self.output):pass
        path=self.owner();before=path.read_bytes();path.write_text('{"broken":true}')
        with self.assertRaises(ValueError):
            with claim(self.entry,self.output):pass
        self.assertEqual('{"broken":true}',path.read_text());path.write_bytes(before);path.unlink()
        with self.assertRaises(ValueError):
            with claim(self.entry,self.output):pass
        self.assertFalse(path.exists())

    def test_missing_lock_or_corrupted_owner_root_is_not_rebuilt(self):
        with claim(self.entry,self.output):pass
        owner=self.owner();value=json.loads(owner.read_text());value['root']=str(self.root/'changed')
        atomic(owner,value)
        with self.assertRaises(ValueError):
            with claim(self.entry,self.root/'changed'):pass
        value['root']=str(self.output.resolve());atomic(owner,value)
        lock=owner.parent/'run.lock';lock.unlink()
        with self.assertRaises(ValueError):
            with claim(self.entry,self.output):pass
        self.assertFalse(lock.exists())

    def test_noncanonical_entry_path_is_rejected_without_registration(self):
        copied=self.root/'copied.json';copied.write_bytes(self.entry_path.read_bytes())
        with self.assertRaises(ValueError):
            with claim(reference(copied),self.output):pass
        self.assertFalse((self.root/'explanation-run').exists())

    def test_fusion_index_uses_the_same_canonical_key_as_shared_slot(self):
        entry=json.loads(self.entry_path.read_text());entry['page_navigation_mode']='synthetic-mode'
        key=digest(dict(policy_sha256=entry['policy_sha256'],page_navigation_mode=entry['page_navigation_mode']))
        path=self.entry_path.parent/(key+'.json');atomic(path,entry)
        with claim(reference(path),self.output):pass
        wrong=self.entry_path.parent/('f'*64+'.json');atomic(wrong,entry)
        with self.assertRaises(ValueError):
            with claim(reference(wrong),self.output):pass

    def test_deleted_generation_receipt_or_whole_output_never_resends(self):
        enrich(self.entry,self.output,fixtures.BasisClient())
        receipt=self.output/'basis/generation.receipt.json';receipt.unlink()
        for whole_root in (False,True):
            if whole_root:shutil.rmtree(self.output)
            client=fixtures.BasisClient()
            with self.assertRaises(Paused) as error:enrich(self.entry,self.output,client)
            self.assertEqual('unknown',error.exception.state);self.assertEqual(0,client.calls)

    def test_missing_requests_ledger_cannot_be_reinitialized(self):
        with claim(self.entry,self.output):pass
        ledger=self.owner().parent/'requests.json';ledger.unlink()
        client=fixtures.BasisClient()
        with self.assertRaises(ValueError):enrich(self.entry,self.output,client)
        self.assertEqual(0,client.calls);self.assertFalse(ledger.exists())

    def test_corrupted_requests_ledger_is_preserved_without_send(self):
        enrich(self.entry,self.output,fixtures.BasisClient())
        ledger=self.owner().parent/'requests.json';value=json.loads(ledger.read_text())
        value['entries']={};atomic(ledger,value);before=ledger.read_bytes();client=fixtures.BasisClient()
        with self.assertRaises(ValueError):enrich(self.entry,self.output,client)
        self.assertEqual((0,before),(client.calls,ledger.read_bytes()))

    def test_missing_whole_output_keeps_unknown_for_alternate_root(self):
        enrich(self.entry,self.output,fixtures.BasisClient());shutil.rmtree(self.output)
        client=fixtures.BasisClient()
        with self.assertRaises(Paused) as error:enrich(self.entry,self.root/'alternate',client)
        self.assertEqual('unknown',error.exception.state);self.assertEqual(0,client.calls)

    def test_persisted_intent_before_delegate_failure_blocks_missing_receipt(self):
        class Interrupted(fixtures.BasisClient):
            def _call(self,payload,receipt):raise RuntimeError('合成意图落盘后进程中断')
        with self.assertRaises(RuntimeError):enrich(self.entry,self.output,Interrupted())
        ledger=json.loads((self.owner().parent/'requests.json').read_text())
        self.assertEqual(1,len(ledger['entries']))
        client=fixtures.BasisClient()
        with self.assertRaises(Paused) as error:enrich(self.entry,self.output,client)
        self.assertEqual('unknown',error.exception.state);self.assertEqual(0,client.calls)

    def test_existing_receipt_without_intent_is_not_registered(self):
        with claim(self.entry,self.output) as owner:
            client=fixtures.BasisClient();receipt=self.output/'foreign.receipt.json'
            atomic(receipt,dict(status='received'))
            before=owner.path.read_bytes()
            with self.assertRaises(ValueError):owner.call(client,{},receipt)
            self.assertEqual(0,client.calls);self.assertEqual(before,owner.path.read_bytes())

    def test_readonly_missing_intent_cannot_write_or_delegate(self):
        with claim(self.entry,self.output):pass
        with claim(self.entry,self.output,read_only=True) as owner:
            before=owner.path.read_bytes();client=fixtures.BasisClient()
            with self.assertRaises(ValueError):owner.call(client,{},self.output/'missing.receipt.json')
            self.assertEqual(0,client.calls);self.assertEqual(before,owner.path.read_bytes())

    def test_empty_ledger_allows_absent_output_before_first_authorization(self):
        with claim(self.entry,self.output):pass
        self.assertFalse(self.output.exists())
        client=fixtures.BasisClient();result=enrich(self.entry,self.output,client)
        self.assertEqual('basis_checked',result['status']);self.assertEqual(2,client.calls)

    def test_registered_payload_drift_never_delegates(self):
        enrich(self.entry,self.output,fixtures.BasisClient())
        with claim(self.entry,self.output) as owner:
            client=fixtures.BasisClient()
            with self.assertRaises(ValueError):owner.call(client,{'changed':True},self.output/'basis/generation.receipt.json')
            self.assertEqual(0,client.calls)

    def test_concurrent_process_cannot_claim_second_root(self):
        context=multiprocessing.get_context('spawn');queue=context.Queue()
        with claim(self.entry,self.output):
            process=context.Process(target=competing,args=(self.entry,str(self.root/'other'),queue))
            process.start();process.join(10)
            if process.is_alive():process.kill();process.join();self.fail('合成子进程锁测试超时')
            self.assertEqual(0,process.exitcode);self.assertEqual('Paused',queue.get(timeout=2))
        self.assertEqual(str(self.output.resolve()),json.loads(self.owner().read_text())['root'])

    def test_job_budget_rotation_before_send_keeps_intent_empty_and_resumes(self):
        from worker import JobClient
        from runtime import CompanyClient
        from model_config import ModelConfig
        key=self.root/'synthetic.key';key.write_text('synthetic-unused-key');key.chmod(0o600)
        config=ModelConfig('https://ai.apsatcom.com',key);jobroot=self.root/'job-budget'
        receipt=self.output/'basis/generation.receipt.json';payload={'synthetic':True}
        blocked=JobClient(config,jobroot,10,0)
        with claim(self.entry,self.output) as owner:
            before=owner.path.read_bytes()
            with self.assertRaises(Paused) as error:owner.call(blocked,payload,receipt)
            self.assertEqual('queued',error.exception.state)
            self.assertEqual(before,owner.path.read_bytes())
        self.assertEqual(0,blocked.budget.used());self.assertFalse(receipt.exists())
        resumed=JobClient(config,jobroot,10,1);sent=[]
        def send(client,path,request_hash,body,key):
            sent.append(True)
            result=dict(status='received',request_sha256=request_hash,response={'status':'completed'})
            atomic(path,result);return result
        with claim(self.entry,self.output) as owner,patch.object(CompanyClient,'_transmit',send):
            owner.call(resumed,payload,receipt)
        self.assertEqual(1,len(sent));self.assertEqual(1,resumed.budget.used())
        with claim(self.entry,self.output) as owner:
            self.assertEqual(1,len(owner.load()['entries']))

    def test_job_budget_reserved_without_receipt_is_never_reauthorized(self):
        from worker import JobClient
        from model_config import ModelConfig
        client=JobClient(ModelConfig('https://ai.apsatcom.com',self.root/'synthetic-unused.key'),self.root/'job-budget',10,1)
        receipt=self.output/'basis/generation.receipt.json';payload={'synthetic':True}
        client.budget.reserve(receipt,digest(dict(endpoint=client.config.responses_url,payload=payload)))
        with claim(self.entry,self.output) as owner:
            with self.assertRaises(Paused) as error:owner.call(client,payload,receipt)
            self.assertEqual('unknown',error.exception.state)
        self.assertEqual(1,client.budget.used());self.assertFalse(receipt.exists())

    def test_pause_and_flow_refusal_before_reserve_leave_no_intent_and_resume(self):
        from worker import JobClient
        from runtime import CompanyClient
        from model_config import ModelConfig
        key=self.root/'synthetic.key';key.write_text('synthetic-unused-key');key.chmod(0o600)
        config=ModelConfig('https://ai.apsatcom.com',key)
        class Flow:
            config={}
            blocked=True
            @contextmanager
            def slot(self,*args):
                if self.blocked:raise Paused('queued','合成全局槽等待')
                yield
        flow=Flow();client=JobClient(config,self.root/'job-budget',10,5,flow)
        receipt=self.output/'basis/generation.receipt.json';payload={'synthetic':True}
        pause=self.root/'pause-new-requests';pause.touch()
        with claim(self.entry,self.output) as owner:
            before=owner.path.read_bytes()
            with self.assertRaises(Paused):owner.call(client,payload,receipt)
            self.assertEqual(before,owner.path.read_bytes());self.assertEqual(0,client.budget.used())
        pause.unlink()
        with claim(self.entry,self.output) as owner:
            before=owner.path.read_bytes()
            with self.assertRaises(Paused):owner.call(client,payload,receipt)
            self.assertEqual(before,owner.path.read_bytes());self.assertEqual(0,client.budget.used())
        flow.blocked=False
        def send(client,path,request_hash,body,key):
            result=dict(status='received',request_sha256=request_hash,response={'status':'completed'})
            atomic(path,result);return result
        with claim(self.entry,self.output) as owner,patch.object(CompanyClient,'_transmit',send):
            owner.call(client,payload,receipt)
        self.assertEqual(1,client.budget.used());self.assertTrue(receipt.exists())

    def test_intent_callback_failure_after_reserve_keeps_missing_receipt_unknown(self):
        from worker import JobClient
        from runtime import CompanyClient
        from model_config import ModelConfig
        key=self.root/'synthetic.key';key.write_text('synthetic-unused-key');key.chmod(0o600)
        config=ModelConfig('https://ai.apsatcom.com',key)
        client=JobClient(config,self.root/'job-budget',10,5)
        receipt=self.output/'basis/generation.receipt.json';payload={'synthetic':True}
        with claim(self.entry,self.output) as owner:
            def fail(path,value):raise OSError('合成意图持久化失败，预算已预留')
            with patch('explanation_run.atomic',side_effect=fail),patch.object(CompanyClient,'_transmit') as transmit:
                with self.assertRaises(OSError):owner.call(client,payload,receipt)
                transmit.assert_not_called()
        self.assertEqual(1,client.budget.used());self.assertFalse(receipt.exists())
        resumed=JobClient(config,self.root/'job-budget',10,5)
        with claim(self.entry,self.output) as owner,patch.object(CompanyClient,'_transmit') as transmit:
            with self.assertRaises(Paused) as error:owner.call(resumed,payload,receipt)
            self.assertEqual('unknown',error.exception.state);transmit.assert_not_called()
        self.assertEqual(1,resumed.budget.used())

    def test_disk_refusal_before_send_does_not_create_an_intent(self):
        from worker import JobClient
        from model_config import ModelConfig
        client=JobClient(ModelConfig('https://ai.apsatcom.com',self.root/'unused.key'),self.root/'job-budget',10,5)
        with claim(self.entry,self.output) as owner:
            before=owner.path.read_bytes()
            with patch('disk_guard.require_space',side_effect=Paused('needs_review','合成磁盘容量不足')):
                with self.assertRaises(Paused):owner.call(client,{'synthetic':True},self.output/'new.receipt.json')
            self.assertEqual(before,owner.path.read_bytes());self.assertEqual(0,client.budget.used())

    def test_transmit_failure_after_intent_is_preserved_and_blocks_restart(self):
        from worker import JobClient
        from runtime import CompanyClient
        from model_config import ModelConfig
        key=self.root/'synthetic.key';key.write_text('synthetic-unused-key');key.chmod(0o600)
        client=JobClient(ModelConfig('https://ai.apsatcom.com',key),self.root/'job-budget',10,5)
        receipt=self.output/'new.receipt.json'
        with claim(self.entry,self.output) as owner:
            with patch.object(CompanyClient,'_transmit',side_effect=RuntimeError('合成发送准备后异常')):
                with self.assertRaises(RuntimeError):owner.call(client,{'synthetic':True},receipt)
            before=owner.path.read_bytes();self.assertEqual(1,len(owner.load()['entries']))
        with self.assertRaises(Paused) as error:
            with claim(self.entry,self.output):self.fail('已有意图缺回执不能再次进入')
        self.assertEqual('unknown',error.exception.state)
        self.assertEqual(before,(self.owner().parent/'requests.json').read_bytes())
        self.assertEqual(1,client.budget.used());self.assertFalse(receipt.exists())


if __name__=='__main__':unittest.main()
