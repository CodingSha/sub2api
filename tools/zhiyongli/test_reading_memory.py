"""执行优化必须保留原字节、分页、请求和旧检查点；使用合成来源。"""
import hashlib
import json
from pathlib import Path
import unittest

from bounded_read import plan, source_page, reading_identity, schema, payload, INSTRUCTIONS
from runtime import digest, json_bytes
import test_reading_cache as fixtures


class ReadingMemoryTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    packet=fixtures.ReadingCacheTests.packet
    client=fixtures.ReadingCacheTests.client
    read=fixtures.ReadingCacheTests.read

    def test_canonical_bytes_keep_unicode_escaping_numbers_and_key_order(self):
        for value in [None, {}, [], {'中😀':'换行\n\\"\t\x00','a':[True,False,None,-0.0,1e-20,123]},
                      {'long':'中😀\\\n"'*70000}]:
            for sort in (True,False):
                old=json.dumps(value,ensure_ascii=False,sort_keys=sort,separators=(',',':')).encode()
                self.assertEqual(old,b''.join(json_bytes(value,sort_keys=sort)))
            self.assertEqual(hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
                separators=(',',':')).encode()).hexdigest(),digest(value))

    def test_index_keeps_original_full_plan_identity_and_actual_request_bytes(self):
        packet=self.packet(49)
        packet['events'][0]['text']='中😀\n\\"\t'*6000
        packet['events'][25]['at']='2026-11-04T12:00:00+08:00'
        full=plan(packet,8192); index=plan(packet,8192,include_text=False)
        events={e['event']:e for e in packet['events']}
        original=digest(dict(plan=full,contract=digest(dict(instructions=INSTRUCTIONS,
            schemas=[schema(p) for p in full['pages']]))))
        self.assertEqual(original,reading_identity(index,events))
        self.assertTrue(all('text' not in f for p in index['pages'] for f in p['fragments']))
        for old,slot in zip(full['pages'],index['pages']):
            current=source_page(slot,events)
            self.assertEqual(old,current)
            self.assertEqual(json.dumps(payload(old,'合成模型'),ensure_ascii=False),
                             json.dumps(payload(current,'合成模型'),ensure_ascii=False))
        self.assertEqual(full,dict(index,pages=[source_page(p,events) for p in index['pages']]))

    def test_legacy_plan_and_checked_files_resume_without_model_or_budget(self):
        from bounded_read import read_all
        packet=self.packet(49); client=self.client('resume')
        self.read(packet,client)
        before={str(p):p.read_bytes() for p in client.root.rglob('*') if p.is_file()
                and (p.name=='plan.json' or p.name.endswith('.checked.json'))}
        calls=self.calls; budget=client.budget.used()
        current=read_all(packet,client.root/'reading',client=client,limit=None)
        self.assertEqual('reading_complete_pending_reconciliation',current['state'])
        self.assertEqual((calls,budget),(self.calls,client.budget.used()))
        self.assertEqual(before,{p:Path(p).read_bytes() for p in before})
