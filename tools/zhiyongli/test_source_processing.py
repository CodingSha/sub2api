"""评分前流式装配须保留原文、角色、问题顺序与冻结签名合同。"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline import prepare
from runtime import atomic, digest
from source_snapshot import save


class SourceProcessingTests(unittest.TestCase):
    start = '2026-09-07T00:00:00+08:00'
    end = '2026-09-14T00:00:00+08:00'
    stamp = '2026-09-08T10:00:00+08:00'

    def entry(self, text, *, at=None, request='one', truncated=False):
        return dict(content=text, request_id=request, created_at=at or self.stamp, truncated=truncated)

    def row(self, ident, requests, responses=(), *, session='shared', user=2,
            request_count=1, request_truncated=False, response_truncated=False):
        return dict(id=ident, user_id=user, session_id=session, request_id='row-'+str(ident),
            request_count=request_count, created_at=self.stamp,
            request_body=requests if isinstance(requests, str) else json.dumps(list(requests), ensure_ascii=False),
            response_body=responses if isinstance(responses, str) else json.dumps(list(responses), ensure_ascii=False),
            request_truncated=request_truncated, response_truncated=response_truncated)

    def write(self, root, rows):
        refs = []
        for row in rows:
            name = 'row-'+str(row['id'])+'.json'
            refs.append(dict(path=name, sha256=save(root/name, row)))
        snapshot = dict(users=[dict(id=2, email='synthetic@example.test'), dict(id=3, email='other@example.test')],
            snapshot_at='2026-10-01T00:00:00+08:00', rows=refs)
        signature = save(root/'snapshot.json', snapshot)
        atomic(root/'latest.json', dict(path='snapshot.json', sha256=signature))

    def run_prepare(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.write(root, rows)
            return prepare(root, 2, self.start, self.end)

    def mixed_rows(self):
        body = json.dumps(dict(messages=[dict(role='alien', content='协议未知角色'),
            dict(role='user', content=[dict(type='image_url', image_url=dict(url='local-synthetic')),
                dict(type='text', text='本次真实输入')])]), ensure_ascii=False)
        return [self.row(1, [self.entry(body)], session='first'),
            self.row(2, '旧聚合字符串', request_count=2, request_truncated=True, session='second'),
            self.row(3, [self.entry('第三帧截断', truncated=True)],
                [self.entry('原始返回data: [DONE]', truncated=True)], session='third')]

    def test_mixed_source_and_protocol_issues_keep_legacy_phase_order_and_digest(self):
        packet = self.run_prepare(self.mixed_rows())
        self.assertEqual(['source_truncated', 'source_truncated', 'source_truncated',
            'unknown_protocol_role', 'non_text_material_not_read'], [i['code'] for i in packet['issues']])
        self.assertEqual(['audit:2:request:0', 'audit:3:request:0', 'audit:3:response:0',
            'audit:1:request:0', 'audit:1:request:0'], [i['frame'] for i in packet['issues']])
        # 由本轮优化前冻结的prepare/frames/extract生成，约束完整材料而非只比较计数。
        self.assertEqual('4d898326ab73c1e90b02170e50e54cf3a602062f2c01efcf16afb291de6c6267', digest(packet))

    def test_history_before_later_active_frame_is_kept_as_context(self):
        rows = [self.row(1, [self.entry('历史前文', at='2026-09-06T10:00:00+08:00')]),
            self.row(2, [self.entry('本期输入')])]
        packet = self.run_prepare(rows)
        self.assertEqual(['历史前文', '本期输入'], [e['text'] for e in packet['events']])
        self.assertEqual(['context', 'candidate_user'], [e['actor_class'] for e in packet['events']])

    def test_response_alone_can_activate_same_session_history_without_becoming_person(self):
        rows = [self.row(1, [self.entry('旧请求背景', at='2026-09-06T10:00:00+08:00')]),
            self.row(2, [], [self.entry('data: {"type":"error"}\n\ndata: [DONE]')])]
        packet = self.run_prepare(rows)
        self.assertEqual(2, len(packet['events']))
        response = packet['events'][1]
        self.assertEqual('context', response['actor_class'])
        self.assertEqual('raw_response', response['content_kind'])
        self.assertEqual('data: {"type":"error"}\n\ndata: [DONE]', response['text'])
        self.assertEqual(hashlib.sha256(response['text'].encode()).hexdigest(), response['text_sha256'])

    def test_known_identical_real_submissions_are_not_deduplicated(self):
        rows = [self.row(1, [self.entry('真实同文', request='a'), self.entry('真实同文', request='b')])]
        events = self.run_prepare(rows)['events']
        self.assertEqual(2, len(events))
        self.assertTrue(all(not e['timestamp_unknown'] and e['actor_class']=='candidate_user' for e in events))
        self.assertEqual(2, len({e['event'] for e in events}))

    def test_unknown_history_is_demoted_after_later_known_match_and_aliases_are_kept(self):
        history = json.dumps(dict(messages=[dict(role='user', content='后来真实提交的同文'),
            dict(role='assistant', content='历史返回')]), ensure_ascii=False)
        rows = [self.row(1, [self.entry(history, request='history-a'), self.entry(history, request='history-b'),
            self.entry('后来真实提交的同文', request='real')])]
        events = self.run_prepare(rows)['events']
        histories = [e for e in events if e['text']=='后来真实提交的同文' and e['timestamp_unknown']]
        actual = [e for e in events if e['text']=='后来真实提交的同文' and not e['timestamp_unknown']]
        self.assertEqual((1, 1), (len(histories), len(actual)))
        self.assertEqual('context', histories[0]['actor_class'])
        self.assertEqual('candidate_user', actual[0]['actor_class'])
        expected = 'audit-'+digest('audit:1:request:1:messages/0:0')[:32]
        self.assertEqual([expected], histories[0]['duplicate_source_events'])

    def test_deduplicated_history_keeps_later_truncation_issue(self):
        history = json.dumps(dict(messages=[dict(role='assistant', content='历史同文背景')]), ensure_ascii=False)
        packet = self.run_prepare([self.row(1, [self.entry(history, truncated=False),
            self.entry(history, request='two', truncated=True)])])
        self.assertEqual(1, len(packet['events']))
        self.assertFalse(packet['events'][0]['truncated'])
        self.assertEqual(1, len(packet['events'][0]['duplicate_source_events']))
        self.assertEqual([dict(frame='audit:1:request:1', code='source_truncated')], packet['issues'])

    def test_foreign_user_and_future_only_sessions_do_not_activate_current_history(self):
        rows = [self.row(1, [self.entry('本人的不活跃前文', at='2026-09-06T10:00:00+08:00')]),
            self.row(2, [self.entry('另一个人的同名会话')], user=3),
            self.row(3, [self.entry('截止后才活跃', at=self.end)]),
            self.row(4, [self.entry('真实本期独立会话')], session='valid')]
        events = self.run_prepare(rows)['events']
        self.assertEqual(['真实本期独立会话'], [e['text'] for e in events])
        self.assertTrue(all(e['source_user_id']==2 for e in events))

    def test_corrupted_source_signature_is_rejected_without_rewriting_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.write(root, [self.row(1, [self.entry('签名前正文')])])
            (root/'row-1.json').write_text('{"tampered":true}')
            before = {p.name:p.read_bytes() for p in root.iterdir()}
            with self.assertRaisesRegex(ValueError, '来源签名变化'):
                prepare(root, 2, self.start, self.end)
            self.assertEqual(before, {p.name:p.read_bytes() for p in root.iterdir()})


    def cached_prepare(self, root, cache, *, user=2, start=None, end=None, scope=None):
        # 故障合同显式强制缓存路径，避免小行优化使故障注入失效。
        with patch('source_frame_cache.MIN_CACHE_BYTES',0):
            return prepare(root, user, start or self.start, end or self.end, scope, metadata_cache=cache)

    def test_default_small_source_bypasses_disk_index_without_changing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            self.write(root,self.mixed_rows())
            self.assertTrue(all(p.stat().st_size<64*1024 for p in root.glob('row-*.json')))
            expected=prepare(root,2,self.start,self.end)
            for _ in range(2):
                self.assertEqual(expected,prepare(root,2,self.start,self.end,metadata_cache=cache))
            self.assertFalse(list(cache.rglob('index.json')))
            self.assertFalse(list(cache.rglob('frames.jsonl')))

    def test_default_large_source_cold_and_warm_match_uncached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            self.write(root,[self.row(1,[self.entry('完整大行正文'*12000)])])
            self.assertGreaterEqual((root/'row-1.json').stat().st_size,64*1024)
            expected=prepare(root,2,self.start,self.end)
            self.assertEqual(expected,prepare(root,2,self.start,self.end,metadata_cache=cache))
            before={p:p.read_bytes() for p in cache.rglob('*') if p.is_file()}
            self.assertEqual(1,len(list(cache.rglob('index.json'))))
            with patch('source_frames._row_frames',side_effect=AssertionError('暖缓存不得重新解析审计原件')):
                self.assertEqual(expected,prepare(root,2,self.start,self.end,metadata_cache=cache))
            self.assertEqual(before,{p:p.read_bytes() for p in cache.rglob('*') if p.is_file()})

    def test_default_large_source_revision_only_reparses_changed_row(self):
        import source_frames
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            first=self.row(1,[self.entry('第一行完整正文'*12000)])
            second=self.row(2,[self.entry('第二行完整正文'*12000)])
            self.write(root,[first,second])
            prepare(root,2,self.start,self.end,metadata_cache=cache)
            self.write(root,[first,self.row(2,[self.entry('第二行有效修订'*12000)])])
            expected=prepare(root,2,self.start,self.end)
            original=source_frames._row_frames;parsed=[]
            def recording(row,*args,**kwargs):
                parsed.append(row['id'])
                return original(row,*args,**kwargs)
            with patch('source_frames._row_frames',side_effect=recording):
                self.assertEqual(expected,prepare(root,2,self.start,self.end,metadata_cache=cache))
            self.assertEqual([2],parsed)
            self.assertEqual(3,len(list(cache.rglob('index.json'))))

    def test_metadata_cache_cold_warm_match_uncached_across_people_windows_and_selected_days(self):
        from workday_scope import DEFAULT, window
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            rows=self.mixed_rows()+[self.row(4,[self.entry('另一人真实输入')],user=3),
                self.row(5,[self.entry('下一期间输入',at='2026-09-15T10:00:00+08:00')])]
            self.write(root,rows)
            for user,start,end in ((2,self.start,self.end),(3,self.start,self.end),
                    (2,'2026-09-14T00:00:00+08:00','2026-09-21T00:00:00+08:00')):
                expected=prepare(root,user,start,end)
                for _ in range(2):
                    self.assertEqual(digest(expected),digest(self.cached_prepare(root,cache,user=user,start=start,end=end)))
            registered=json.loads(DEFAULT.read_text())
            registered['weeks']['2026-09-07']['evaluation_dates']=['2026-09-08']
            registry=Path(tmp)/'registry.json';atomic(registry,registered)
            with patch.dict(os.environ,{'ZHIYONGLI_WORKDAY_REGISTRY':str(registry)}):
                scope=window('2026-09-07','2026-09-14')
                expected=prepare(root,2,self.start,self.end,scope)
                for _ in range(2):self.assertEqual(expected,self.cached_prepare(root,cache,scope=scope))
            self.assertEqual(prepare(root,2,self.start,self.end),self.cached_prepare(root,cache))

    def test_metadata_cache_warm_hit_still_rejects_source_sha_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            self.write(root,[self.row(1,[self.entry('原件正文')])]);self.cached_prepare(root,cache)
            before={str(p.relative_to(cache)):p.read_bytes() for p in cache.rglob('*') if p.is_file()}
            (root/'row-1.json').write_text('{"changed":true}')
            with self.assertRaisesRegex(ValueError,'来源签名变化'):self.cached_prepare(root,cache)
            self.assertEqual(before,{str(p.relative_to(cache)):p.read_bytes() for p in cache.rglob('*') if p.is_file()})

    def test_metadata_cache_corruption_never_rebuilds_or_clears_unknown(self):
        for name in ('frames.jsonl','index.json'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
                self.write(root,[self.row(1,[self.entry('原件正文')])]);self.cached_prepare(root,cache)
                unknown=Path(tmp)/'reading/0.receipt.json';atomic(unknown,dict(status='unknown',request_sha256='original'))
                before_unknown=unknown.read_bytes();target=next(cache.rglob(name))
                if name=='frames.jsonl':target.write_text('tampered\n')
                else:
                    value=json.loads(target.read_text());value['metadata'][0][0]['user_id']=999;atomic(target,value)
                before=target.read_bytes()
                with self.assertRaisesRegex(ValueError,'签名变化'):
                    self.cached_prepare(root,cache)
                self.assertEqual(before,target.read_bytes());self.assertEqual(before_unknown,unknown.read_bytes())

    def test_metadata_cache_valid_source_revision_creates_new_entry_and_keeps_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            row=self.row(1,[self.entry('原始正文')]);self.write(root,[row]);first=self.cached_prepare(root,cache)
            old={p:p.read_bytes() for p in cache.rglob('*') if p.is_file()}
            revised=self.row(1,[self.entry('有效修订的新正文')]);self.write(root,[revised])
            second=self.cached_prepare(root,cache)
            self.assertNotEqual(digest(first),digest(second))
            self.assertEqual(prepare(root,2,self.start,self.end),second)
            self.assertEqual(2,len(list(cache.rglob('index.json'))))
            self.assertTrue(all(p.read_bytes()==raw for p,raw in old.items()))

    def test_metadata_cache_parser_signature_change_does_not_reuse_old_entry(self):
        import source_frame_cache
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            self.write(root,[self.row(1,[self.entry('同一原件')])]);expected=self.cached_prepare(root,cache)
            old={p:p.read_bytes() for p in cache.rglob('*') if p.is_file()}
            original=source_frame_cache.file_sha256
            def parser_changed(path):
                return 'f'*64 if path.name=='source_frames.py' else original(path)
            with patch('source_frame_cache.file_sha256',side_effect=parser_changed):
                self.assertEqual(expected,self.cached_prepare(root,cache))
            self.assertEqual(2,len(list(cache.rglob('index.json'))))
            self.assertTrue(all(p.read_bytes()==raw for p,raw in old.items()))

    def test_metadata_cache_failed_write_never_seals_index_or_leaves_temporary_file(self):
        import source_frame_cache
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            self.write(root,[self.row(1,[self.entry('第一帧'),self.entry('第二帧',request='two')])])
            original=source_frame_cache.json_bytes;calls=[]
            def interrupted(*args,**kwargs):
                calls.append(True)
                if len(calls)==2:raise OSError('合成磁盘写入中断')
                return original(*args,**kwargs)
            with patch('source_frame_cache.json_bytes',side_effect=interrupted):
                with self.assertRaisesRegex(OSError,'磁盘写入中断'):self.cached_prepare(root,cache)
            self.assertFalse(list(cache.rglob('index.json')));self.assertFalse(list(cache.rglob('*.tmp')))
            self.assertEqual(prepare(root,2,self.start,self.end),self.cached_prepare(root,cache))

    def test_metadata_cache_disk_pause_during_write_propagates_without_fallback(self):
        from pipeline import Paused
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            self.write(root,[self.row(1,[self.entry('写入时必须暂停的原件正文')])])
            unknown=Path(tmp)/'reading/0.receipt.json'
            atomic(unknown,dict(status='unknown',request_sha256='original'))
            before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
            unknown_before=unknown.read_bytes();writes=[]
            def insufficient(directory,pending_bytes=0):
                if Path(directory).is_relative_to(cache) and pending_bytes>0:
                    writes.append(pending_bytes)
                    raise Paused('needs_review','合成写入中磁盘不足')
            with patch('disk_guard.require_space',side_effect=insufficient):
                with self.assertRaisesRegex(Paused,'磁盘不足'):
                    self.cached_prepare(root,cache)
            self.assertTrue(writes)
            self.assertFalse(list(cache.rglob('index.json')))
            self.assertFalse(list(cache.rglob('*.tmp')))
            self.assertEqual(before,{p:p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertEqual(unknown_before,unknown.read_bytes())
            self.assertEqual(prepare(root,2,self.start,self.end),self.cached_prepare(root,cache))


    def test_metadata_cache_index_seal_failure_is_not_a_successful_cache_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache'
            self.write(root,[self.row(1,[self.entry('已落盘但未封口正文')])])
            with patch('source_frame_cache.atomic',side_effect=OSError('合成索引封口失败')):
                with self.assertRaisesRegex(OSError,'索引封口失败'):self.cached_prepare(root,cache)
            self.assertTrue(list(cache.rglob('frames.jsonl')))
            self.assertFalse(list(cache.rglob('index.json')));self.assertFalse(list(cache.rglob('*.tmp')))
            self.assertEqual(prepare(root,2,self.start,self.end),self.cached_prepare(root,cache))
            self.assertEqual(1,len(list(cache.rglob('index.json'))))

    def test_metadata_cache_excluded_malformed_entry_falls_back_without_changing_output(self):
        malformed=dict(content=dict(not_text=True),request_id='future',created_at=self.end,truncated=False)
        row=self.row(1,[self.entry('保留本期正文'),malformed])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();cache=Path(tmp)/'cache';self.write(root,[row])
            expected=prepare(root,2,self.start,self.end)
            for _ in range(2):self.assertEqual(expected,self.cached_prepare(root,cache))
            self.assertEqual(['保留本期正文'],[e['text'] for e in expected['events']])
            self.assertFalse(list(cache.rglob('index.json')));self.assertFalse(list(cache.rglob('*.tmp')))


if __name__ == '__main__':
    unittest.main()
