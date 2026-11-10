"""日期只作为阅读分页边界，不改变原文、先后顺序、任务或评分；不发送真实API。"""
import copy
import json
import unittest

from bounded_read import fragments,plan
from runtime import atomic
import test_reading_cache as fixtures


class ReadingDateTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    packet=fixtures.ReadingCacheTests.packet
    client=fixtures.ReadingCacheTests.client
    read=fixtures.ReadingCacheTests.read

    def dates(self):
        packet=self.packet(26)
        for i,event in enumerate(packet['events']):
            event['at']='2026-11-'+('03' if i<13 else '04')+'T12:00:00+08:00'
        return packet

    def test_date_pages_keep_every_fragment_character_and_original_order(self):
        packet=self.dates();packet['events'][0]['text']='完整原文\n引号"与\\转义🙂'*2000
        packet['events'][1]['text']=''
        self.assertEqual(fragments(packet),[f for p in plan(packet)['pages'] for f in p['fragments']])
        self.assertTrue(all(len({f['at'][:10] for f in p['fragments']})==1 for p in plan(packet)['pages']))

    def test_next_date_does_not_repack_previous_partially_filled_page(self):
        full=self.dates();first=copy.deepcopy(full);first['events']=first['events'][:13]
        a=self.client('a');self.read(first,a);self.assertEqual(1,self.calls)
        b=self.client('b',limit=1);result=self.read(full,b)
        self.assertEqual('reading_complete_pending_reconciliation',result['state'])
        self.assertEqual((2,1),(self.calls,b.budget.used()))

    def test_inserted_historical_date_keeps_old_pages_and_reading_order(self):
        packet=self.dates();self.read(packet,self.client('a'));self.assertEqual(2,self.calls)
        historical=copy.deepcopy(packet['events'][0]);historical.update(event='earlier',at='2026-11-02T12:00:00+08:00')
        newer=copy.deepcopy(packet);newer['events'].insert(0,historical)
        b=self.client('b',limit=1);result=self.read(newer,b)
        self.assertEqual('reading_complete_pending_reconciliation',result['state'])
        self.assertEqual((3,1),(self.calls,b.budget.used()))
        self.assertEqual(fragments(newer),[f for p in plan(newer)['pages'] for f in p['fragments']])

    def test_changed_source_in_one_day_is_not_reused_as_unchanged(self):
        packet=self.dates();self.read(packet,self.client('a'));self.assertEqual(2,self.calls)
        packet['events'][0]['text']='修改后的失败证据'
        b=self.client('b',limit=1);self.read(packet,b)
        self.assertEqual((3,1),(self.calls,b.budget.used()))

    def test_timezone_is_normalized_and_unknown_time_remains_unknown(self):
        packet=self.packet(3)
        packet['events'][0]['at']='2026-11-03T16:00:00Z'
        packet['events'][1]['at']='2026-11-04T00:01:00+08:00'
        packet['events'][2]['at']='2026-11-04T00:02:00+08:00'
        packet['events'][2]['timestamp_unknown']=True
        self.assertEqual(1,len(plan(packet)['pages']))
        self.assertTrue(plan(packet)['pages'][0]['fragments'][2]['timestamp_unknown'])
        del packet['events'][2]['at']
        self.assertEqual(2,len(plan(packet)['pages']))
        self.assertNotIn('at',plan(packet)['pages'][-1]['fragments'][0])

    def test_unknown_previous_plan_blocks_changed_boundaries_without_resending(self):
        packet=self.packet(26);packet['since']='2026-11-02T00:00:00+08:00'
        a=self.client('a');a.reading_plan_root=self.root/'plans'
        self.read(packet,a);sent=self.calls
        receipts=list((self.root/'plans').rglob('*.receipt.json'));self.assertTrue(receipts)
        value=json.loads(receipts[0].read_text());value['status']='unknown';atomic(receipts[0],value)
        changed=self.dates();changed['since']=packet['since']
        b=self.client('b');b.reading_plan_root=a.reading_plan_root
        result=self.read(changed,b)
        self.assertEqual('reading_blocked',result['state'])
        self.assertEqual((sent,0),(self.calls,b.budget.used()))


if __name__=='__main__':unittest.main()
