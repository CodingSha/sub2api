"""共享缓存预检只读取原证据，未决和损坏不得伪装未命中。"""
import fcntl
import json
from pathlib import Path
import unittest

from reading_cache import ReadingCache
from runtime import atomic,digest
from pipeline import Paused
from bounded_read import plan
import test_reading_cache as fixtures

class CacheInspectionTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    client=fixtures.ReadingCacheTests.client
    packet=fixtures.ReadingCacheTests.packet
    read=fixtures.ReadingCacheTests.read
    def cache(self,client=None,identity=None):
        return ReadingCache(self.root/'shared',identity or dict(user_id='1',user_email='synthetic@example.test'),client or self.c)
    def prepare(self):
        self.c=self.client('a');self.read(self.packet(),self.c)
        self.page=plan(self.packet())['pages'][0];self.payload=self.c.reading_payload(self.page)
        self.instance=self.cache();self.source=self.instance.root/digest(self.page)
    def snapshot(self):
        return {str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
    def test_hit_and_payload_miss_do_not_change_any_bytes_or_mtime(self):
        self.prepare();before=self.snapshot();calls=self.calls
        path,record=self.instance.inspect(self.page,self.payload)
        self.assertEqual('received',record['status']);self.assertTrue(path.exists())
        self.assertIsNone(self.instance.inspect(self.page,dict(self.payload,model='different')))
        self.assertEqual(before,self.snapshot());self.assertEqual(calls,self.calls)
    def test_absent_source_does_not_create_source_or_lock_and_people_are_isolated(self):
        self.prepare();other=self.cache(identity=dict(user_id='2',user_email='other@example.test'))
        before=self.snapshot();dirs={str(p) for p in self.root.rglob('*') if p.is_dir()}
        self.assertIsNone(other.inspect(self.page,self.payload))
        self.assertIsNone(self.instance.inspect(dict(fragments=[]),self.payload))
        self.assertEqual(before,self.snapshot());self.assertEqual(dirs,{str(p) for p in self.root.rglob('*') if p.is_dir()})
    def test_unknown_and_requesting_block_even_different_payload(self):
        for state in ('unknown','requesting'):
            with self.subTest(state=state):
                self.prepare();path=next(self.source.glob('*.receipt.json'))
                original=path.read_bytes();value=json.loads(original);value['status']=state;atomic(path,value)
                before=self.snapshot()
                with self.assertRaises(Paused) as error:self.instance.inspect(self.page,dict(self.payload,model='new'))
                self.assertEqual('unknown',error.exception.state);self.assertEqual(before,self.snapshot())
                path.write_bytes(original)
    def test_reservation_before_receipt_crash_and_broken_ledger_are_not_misses(self):
        self.c=self.client('reserve');self.instance=self.cache();self.page=plan(self.packet())['pages'][0]
        self.payload=self.c.reading_payload(self.page)
        with self.instance.request(self.page,self.payload) as receipt:self.c.budget.reserve(receipt,'synthetic')
        self.source=receipt.parent;before=self.snapshot()
        with self.assertRaises(Paused) as error:self.instance.inspect(self.page,self.payload)
        self.assertEqual('unknown',error.exception.state);self.assertEqual(before,self.snapshot())
        self.c.budget.path.write_text('{"version":1,"entries":[]}')
        with self.assertRaises(ValueError):self.instance.inspect(self.page,self.payload)
        self.c.budget.path.unlink()
        with self.assertRaisesRegex(ValueError,'账簿丢失'):self.instance.inspect(self.page,self.payload)
    def test_bad_seal_is_error_even_for_payload_miss(self):
        self.prepare();seal=next(self.source.glob('*.seal.json'));seal.write_text('{}')
        before=self.snapshot()
        with self.assertRaises(ValueError):self.instance.inspect(self.page,dict(self.payload,model='new'))
        self.assertEqual(before,self.snapshot())
    def test_known_failed_terminal_is_returned_without_retry(self):
        from request_retry import origin_digest
        self.prepare();path=next(self.source.glob('*.receipt.json'))
        value=json.loads(path.read_text())
        value['response']=dict(status='failed',output=[],error=dict(code='synthetic_failed'))
        atomic(path,value)
        seal=path.with_name(path.name.replace('.receipt.json','.seal.json'))
        atomic(seal,dict(origin_sha256=origin_digest(path),request_sha256=value['request_sha256'],terminal_sha256=digest(value)))
        before=self.snapshot();calls=self.calls
        actual,result=self.instance.inspect(self.page,self.payload)
        self.assertEqual(path,actual);self.assertEqual('failed',result['response']['status'])
        self.assertEqual(calls,self.calls);self.assertEqual(before,self.snapshot())

    def test_orphan_seal_and_bad_receipt_are_errors_not_misses(self):
        self.prepare();path=next(self.source.glob('*.receipt.json'));original=path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError):self.instance.inspect(self.page,dict(self.payload,model='new'))
        path.write_bytes(original);value=json.loads(original);value['response']['changed']=True;atomic(path,value)
        with self.assertRaises(ValueError):self.instance.inspect(self.page,self.payload)

    def test_existing_lock_busy_is_queued_and_missing_lock_with_evidence_rejects(self):
        self.prepare();before=self.snapshot()
        with (self.source/'page.lock').open('rb') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaises(Paused) as error:self.instance.inspect(self.page,self.payload)
            self.assertEqual('queued',error.exception.state)
        self.assertEqual(before,self.snapshot())
        (self.source/'page.lock').unlink()
        with self.assertRaisesRegex(ValueError,'来源锁缺失'):self.instance.inspect(self.page,self.payload)
        self.assertFalse((self.source/'page.lock').exists())

if __name__=='__main__':unittest.main()
