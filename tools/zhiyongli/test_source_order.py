"""审计同刻配对与协议顺序回归；不把展示顺序或程序通过当作语义校准。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import test_reading_cache as reading_fixtures

from bounded_read import plan,transport
from pipeline import prepare
from runtime import atomic
from source_snapshot import save
from task_identification import model_packet


class SourceOrderTests(unittest.TestCase):
    stamp='2026-09-07T10:00:00+08:00'

    def row(self,ident=1,entries=None,replies=None):
        if entries is None:
            entries=[dict(content='合成输入'+str(i),request_id=str(i),created_at=self.stamp,truncated=False) for i in range(16)]
        if replies is None:
            replies=[dict(e,content='合成返回'+e['request_id']) for e in entries]
        return dict(id=ident,user_id=2,session_id='s',request_id='r',request_count=len(entries),created_at=self.stamp,
            request_body=json.dumps(entries),response_body=json.dumps(replies),request_truncated=False,response_truncated=False)

    def prepare(self,*rows):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);refs=[]
            for row in rows:
                name=str(row['id'])+'.json';refs.append(dict(path=name,sha256=save(root/name,row)))
            sha=save(root/'snapshot.json',dict(users=[dict(id=2,email='synthetic@example.test')],
                snapshot_at='2026-10-01T00:00:00+08:00',rows=refs))
            atomic(root/'latest.json',dict(path='snapshot.json',sha256=sha))
            return prepare(root,2,'2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')

    def test_tied_capture_times_pair_all_requests_without_rewriting_source(self):
        row=self.row();original=copy.deepcopy(row);packet=self.prepare(row)
        self.assertEqual(32,len(packet['events']))
        for a,b in zip(packet['events'][::2],packet['events'][1::2]):
            self.assertEqual(('request','response'),(a['field'],b['field']))
            self.assertEqual(a['request_id'],b['request_id'])
            self.assertEqual(a['at'],b['at'])
            self.assertEqual('audit_capture',b['timestamp_basis'])
        self.assertEqual(row,original)
        self.assertEqual(16,len({e['request_id'] for e in packet['events']}))

    def test_embedded_history_keeps_protocol_order_including_two_digit_indices(self):
        messages=[dict(role='user' if i%2==0 else 'assistant',content='协议顺序'+str(i)) for i in range(13)]
        entries=[dict(content=json.dumps(dict(messages=messages)),request_id='one',created_at=self.stamp)]
        events=self.prepare(self.row(entries=entries,replies=[]))['events']
        self.assertEqual([m['content'] for m in messages],[e['text'] for e in events])
        self.assertTrue(all(e['timestamp_unknown'] for e in events[:-1]))
        self.assertFalse(events[-1]['timestamp_unknown'])

    def test_same_request_identifier_in_different_rows_is_not_interleaved(self):
        entries=[dict(content='合成输入',request_id='same',created_at=self.stamp)]
        events=self.prepare(self.row(10,entries),self.row(2,entries))['events']
        self.assertEqual(['2','2','10','10'],[e['audit_row_id'] for e in events])
        self.assertEqual(['request','response']*2,[e['field'] for e in events])

    def test_empty_identifiers_and_different_timestamps_do_not_gain_pairing_or_timing(self):
        entries=[dict(content='第一条',request_id='',created_at=self.stamp)]
        replies=[dict(content='未知对应',request_id='',created_at='2026-09-07T09:00:00+08:00')]
        events=self.prepare(self.row(entries=entries,replies=replies))['events']
        self.assertEqual(['response','request'],[e['field'] for e in events])
        self.assertTrue(all(e['request_id']=='' for e in events))
        self.assertTrue(all(e['timestamp_basis']=='audit_capture' for e in events))

    def test_reading_and_identification_receive_time_basis_and_pairing_fields(self):
        packet=self.prepare(self.row());pages=plan(packet)['pages']
        for page in pages:
            actual=transport(page)['fragments']
            self.assertTrue(all(f['timestamp_basis']=='audit_capture' and f['audit_row_id']=='1' for f in actual))
            self.assertTrue(all(f['field'] in {'request','response'} for f in actual))
            for f in actual:
                source=next(e for e in packet['events'] if e['event']==f['event_id'])
                self.assertEqual(source['text'][f['start']:f['end']],''.join(s['text'] for s in f['segments']))
        self.assertEqual([e['timestamp_basis'] for e in packet['events']],
            [e['timestamp_basis'] for e in model_packet(packet)['events']])

    def test_identification_batches_preserve_tied_pairs_and_embedded_protocol_order(self):
        from goal_batches import plan as batches
        for packet in (self.prepare(self.row()), self.prepare(self.row(entries=[dict(
                content=json.dumps(dict(messages=[dict(role='user' if i%2==0 else 'assistant',content='协议'+str(i))
                    for i in range(13)])),request_id='one',created_at=self.stamp)],replies=[]))):
            actual=[e for page in batches(packet,8000) for e in page['events']]
            self.assertEqual(packet['events'],actual)


class SourceOrderCacheTests(unittest.TestCase):
    setUp=reading_fixtures.ReadingCacheTests.setUp
    client=reading_fixtures.ReadingCacheTests.client
    packet=reading_fixtures.ReadingCacheTests.packet
    read=reading_fixtures.ReadingCacheTests.read

    def test_order_and_time_metadata_changes_do_not_bypass_unknown_old_plan(self):
        packet=self.packet(26);packet['since']='2026-11-02T00:00:00+08:00'
        first=self.client('before');first.reading_plan_root=self.root/'plans'
        self.read(packet,first)
        receipt=next(first.reading_plan_root.rglob('*.receipt.json'))
        record=json.loads(receipt.read_text());record['status']='unknown';atomic(receipt,record)
        before=receipt.read_bytes();sent=self.calls
        packet['events'].reverse()
        for event in packet['events']:event['timestamp_basis']='audit_capture'
        second=self.client('after');second.reading_plan_root=first.reading_plan_root
        result=self.read(packet,second)
        self.assertEqual('reading_blocked',result['state'])
        self.assertEqual((sent,0),(self.calls,second.budget.used()))
        self.assertEqual(before,receipt.read_bytes())


if __name__=='__main__':unittest.main()
