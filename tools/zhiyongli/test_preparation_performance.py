"""同截止日重复轮转复用签名完整材料，原文、unknown和预算不放宽。"""
import gzip,json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import preparation
from pipeline import prepare
from pipeline import Paused
from model_config import ModelConfig
from runtime import digest
from bounded_read import raw_size
from source_snapshot import file_sha256
import test_workday_scope as source_fixtures
from workday_scope import window


class PreparationPerformanceTests(unittest.TestCase):
    def test_reading_preflight_does_not_reencode_whole_packet_for_each_branch(self):
        from pipeline import reading_preflight
        from test_execution_paths import packet
        class Client:
            reading_efficiency=True
            def navigate_context(self,*args):raise AssertionError('预检不得请求模型')
            def merge_goals(self,*args):raise AssertionError('预检不得请求模型')
        for background in ('', '中文背景、失败反证及未决\\\"\n'*10_000):
            with self.subTest(long=bool(background)):
                value=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
                if background:value['events'].append(dict(event='context',actor_class='context',text=background,
                    at='2026-09-08T12:00:00+08:00',timestamp_unknown=False))
                before=digest(value)
                with patch('bounded_read.raw_size',wraps=raw_size) as measured:
                    reading_preflight(value,Client())
                self.assertEqual(1,sum(call.args[0] is value for call in measured.call_args_list))
                self.assertEqual(before,digest(value))

    def test_combined_packet_signature_matches_original_unicode_escape_and_nested_contracts(self):
        cases=[{},dict(events=[],issues=[]),dict(events=[{'text':'中文完整材料及反证'}]),
            {'events':[{'text':'e\u0301é\u200d👩‍💻😀\ufe0f'}]},
            {'events':[{'text':''.join(chr(i) for i in range(32))+'"\\/'}]},
            {'metadata':{'z':[None,True,False,0,-1,1.25,{'乙':'\t','a':'\n'}],'a':{}},
                'events':[{'z':'😀','a':'原文'},{'text':''}]}]
        for value in cases:
            with self.subTest(value=value):
                self.assertEqual((digest(value),raw_size(value)),preparation._packet_signature(value))

    def test_cached_packet_size_tampering_is_rejected_without_rewrite_or_reassembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root);self.packet(root)
            manifest=next((root/'prepared-packets').glob('*.manifest.json'))
            proof=json.loads(manifest.read_text());proof['packet_bytes']+=1
            manifest.write_text(json.dumps(proof))
            before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in (root/'prepared-packets').iterdir() if p.is_file()}
            with patch('pipeline.prepare',side_effect=AssertionError('容量异常不允许重新装配覆盖')):
                with self.assertRaisesRegex(ValueError,'容量签名变化'):self.packet(root)
            self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in before})

    def source(self,root):
        source_fixtures.WorkdayScopeTests().source(root,['2026-09-08T10:00:00+08:00'],'2026-09-09T02:00:00+08:00')

    def packet(self,root,until='2026-09-09T00:00:00+08:00'):
        return preparation.prepared_packet(root,2,window('2026-09-07','2026-09-14'),until,root/'metadata-cache')

    def test_repeated_same_day_assembles_once_and_preserves_all_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root)
            with patch('pipeline.prepare',wraps=prepare) as build:
                first,sha,size=self.packet(root);second,again,other_size=self.packet(root)
                self.assertEqual(1,build.call_count)
            self.assertEqual((sha,size),(again,other_size));self.assertEqual(first,second);self.assertEqual(sha,digest(first))
            self.assertEqual(1,len(list((root/'prepared-packets').glob('*.jsonl.gz'))))
            self.assertFalse(list(root.rglob('*.receipt.json')))

    def test_modified_original_or_compressed_cache_stops_without_reassembly(self):
        for target in ('source','cache'):
            with self.subTest(target=target),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);self.source(root);self.packet(root)
                path=root/'row.json' if target=='source' else next((root/'prepared-packets').glob('*.jsonl.gz'))
                with path.open('ab') as stream:stream.write(b'changed')
                with patch('pipeline.prepare',side_effect=AssertionError('签名损坏不能重建掩盖')):
                    with self.assertRaisesRegex(ValueError,'签名'):self.packet(root)

    def test_changed_cutoff_has_distinct_cache_and_tampered_manifest_is_not_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.source(root)
            with patch('pipeline.prepare',wraps=prepare) as build:
                old=self.packet(root,'2026-09-08T00:00:00+08:00')
                new=self.packet(root)
                self.assertEqual(2,build.call_count)
            self.assertNotEqual(old[1],new[1]);self.assertEqual([],old[0]['events']);self.assertTrue(new[0]['events'])
            for path in (root/'prepared-packets').glob('*.manifest.json'):
                value=json.loads(path.read_text());value['packet_sha256']='changed';path.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError,'签名'):self.packet(root)

    def test_repeated_queued_turns_reuse_packet_but_unknown_still_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);queue=preparation.Preparations(root)
            queue.enqueue(dict(through_date='2026-09-08',mode='reading_only',workday_scope=window('2026-09-07','2026-09-14'),
                users=[dict(user_id=2,user_email='synthetic@example.test')]))
            def collect(_,directory,*args,**kwargs):directory.mkdir(parents=True,exist_ok=True);self.source(directory)
            base=SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',root/'unused'))
            blocked=dict(state='reading_blocked',pages=1,received_pages=[],blocked=[dict(state='unknown')])
            with patch('source_snapshot.collect',side_effect=collect) as collected,patch('pipeline.prepare',wraps=prepare) as built,\
                    patch('pipeline.DIRECT_BYTES',1),patch('bounded_read.read_all',side_effect=[Paused('queued','轮转'),blocked]) as read:
                self.assertTrue(queue.tick(None,base,[999]));self.assertEqual('queued',queue.snapshot()[0]['state'])
                self.assertTrue(queue.tick(None,base,[999]));self.assertEqual('unknown',queue.snapshot()[0]['state'])
                self.assertFalse(queue.tick(None,base,[999]))
                self.assertEqual((1,1,2),(collected.call_count,built.call_count,read.call_count))
            self.assertEqual({},json.loads(next(root.rglob('request-budget.json')).read_text())['entries'])

    def test_cached_packet_retains_preparation_person_and_scope_semantics(self):
        for changes in (dict(preparation_only=False),dict(user_id='other'),dict(until='2026-10-01T00:00:00+08:00')):
            with self.subTest(changes=changes),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);self.source(root);packet,_,_=self.packet(root);packet.update(changes)
                cache=next((root/'prepared-packets').glob('*.jsonl.gz'))
                with gzip.open(cache,'wt',encoding='utf-8') as stream:
                    stream.write(json.dumps({k:v for k,v in packet.items() if k!='events'})+'\n')
                    for event in packet['events']:stream.write(json.dumps(event)+'\n')
                manifest=next((root/'prepared-packets').glob('*.manifest.json'));proof=json.loads(manifest.read_text())
                proof.update(compressed_sha256=file_sha256(cache),packet_sha256=digest(packet),packet_bytes=raw_size(packet))
                manifest.write_text(json.dumps(proof))
                with self.assertRaisesRegex(ValueError,'身份变化'):self.packet(root)


if __name__=='__main__':unittest.main()
