"""午夜日常首读与稳定导航前缀实际接续；仅本机合成来源和模型回执。"""
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from preparation import Preparations
from pipeline import prepare,reading_view
from runtime import atomic,digest
from source_snapshot import save
from worker import JobClient
import test_preparation as preparation_tests
import test_reading_cache as fixtures
import test_workday_scope as sources


class PreparationPrefixTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    daily=preparation_tests.PreparationTests.daily

    def collect(self,_,directory,*args,**kwargs):
        directory.mkdir(parents=True,exist_ok=True)
        initial='2026-09-08T10:00:00+08:00'
        sources.WorkdayScopeTests().source(directory,[initial],'2026-09-13T02:00:00+08:00')
        row=json.loads((directory/'row.json').read_text())
        size=32 if directory.name=='2026-09-08' else 64
        entries=[dict(content='合成独立完整结果'+str(i)+'：保留失败反证。'*500,
            request_id='response-'+str(i),created_at=initial if i<32 else '2026-09-09T10:00:00+08:00') for i in range(size)]
        entries.extend(dict(content='',request_id='empty-'+str(i),created_at=initial) for i in range(32))
        row['response_body']=json.dumps(entries)
        sha=save(directory/'row.json',row)
        snapshot=json.loads((directory/'snapshot.json').read_text());snapshot['rows'][0]['sha256']=sha
        sha=save(directory/'snapshot.json',snapshot);atomic(directory/'latest.json',dict(path='snapshot.json',sha256=sha))

    def queue(self):
        queue=Preparations(self.root/'state');daily=self.daily();daily['mode']='reading_prefix';queue.enqueue(daily)
        return queue

    def test_daily_cross_day_and_weekend_share_reading_prefix_and_cumulative_budget(self):
        queue=self.queue();base=SimpleNamespace(config=self.config)
        with patch('source_snapshot.collect',side_effect=self.collect):
            self.assertTrue(queue.tick(None,base,[999],max_requests=200,new_request_limit=100))
            self.assertEqual('prepared',queue.snapshot()[0]['state'])
            first=list((self.root/'state/navigation-branches').rglob('*.receipt.json'))
            self.assertTrue(first)
            daily=self.daily('2026-09-09');daily['mode']='reading_prefix';queue.enqueue(daily)
            self.assertTrue(queue.tick(None,base,[999],max_requests=200,new_request_limit=100))
        self.assertEqual('prepared',queue.snapshot()[0]['state'])
        root=next((self.root/'state/preparations').iterdir())
        proof=json.loads((root/'prepared-prefix.json').read_text())
        self.assertFalse(proof['complete']);self.assertFalse(proof['prefix']['complete'])
        self.assertGreater(proof['prefix']['deferred_leaves'],0)
        self.assertFalse(list((root/'prepared-prefixes').rglob('tree.json')))
        self.assertFalse(list((root/'prepared-prefixes').rglob('leaves.json')))
        budget=json.loads((root/'request-budget.json').read_text())
        self.assertEqual(self.calls,len(budget['entries']))
        self.assertEqual(1,len(list(root.rglob('request-budget.json'))))
        packet=prepare(root/'sources/2026-09-09',2,'2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00',daily['workday_scope'])
        final=JobClient(self.config,self.root/'weekend',200-len(budget['entries']),100)
        final.reading_cache_root=self.root/'state/read-pages';final.reading_plan_root=self.root/'state/reading-plans'
        final.navigation_cache_root=self.root/'state/navigation-branches'
        final.reading_content_identity=True;final.reading_efficiency=True
        old={p:p.read_bytes() for p in first};sent=self.calls
        view=reading_view(packet,self.root/'weekend',final,lambda *args:None)
        self.assertIn('context_navigation',view)
        used_receipts={json.loads(p.read_text())['receipt'] for p in (self.root/'weekend/context-navigation').glob('*.cache.json')}
        self.assertTrue({str(p.resolve()) for p in first}<=used_receipts)
        self.assertEqual(old,{p:p.read_bytes() for p in old})
        self.assertGreater(self.calls-sent,0) # 仍须完成开放尾组/完整根，前缀不冒充整周树。
        self.assertEqual(self.calls-sent,final.budget.used())
        self.assertFalse(list((self.root/'state').rglob('bundle.json')))

    def test_incomplete_reading_does_not_start_prefix_and_unknown_is_not_prepared(self):
        queue=self.queue();base=SimpleNamespace(config=self.config)
        with patch('source_snapshot.collect',side_effect=self.collect):
            queue.tick(None,base,[999],max_requests=200,new_request_limit=1)
        self.assertEqual('queued',queue.snapshot()[0]['state'])
        self.assertFalse(list((self.root/'state/preparations').rglob('prefix.json')))
        self.assertFalse(list((self.root/'state/navigation-branches').rglob('*.receipt.json')))
        receipt=next((self.root/'state/read-pages').rglob('*.receipt.json'))
        value=json.loads(receipt.read_text());value['status']='unknown';atomic(receipt,value)
        # 模拟原执行落下unknown引用，而非仅篡改成功原件造成签名损坏。
        for reference in (self.root/'state/reading-plans').rglob('*.receipt.json'):
            saved=json.loads(reference.read_text())
            if saved.get('shared_reading_receipt')==str(receipt.resolve()):
                saved.update(status='unknown',shared_receipt_sha256=digest(value));atomic(reference,saved)
        sent=self.calls
        with patch('source_snapshot.collect',side_effect=AssertionError('续跑不重新采集')):
            queue.tick(None,base,[999],max_requests=200,new_request_limit=100)
        self.assertEqual('unknown',queue.snapshot()[0]['state']);self.assertEqual(sent,self.calls)
        self.assertFalse(list((self.root/'state/preparations').rglob('prefix.json')))

    def test_existing_mode_requires_explicit_migration(self):
        queue=Preparations(self.root/'state');daily=self.daily();daily['mode']='reading_only';queue.enqueue(daily)
        daily['mode']='reading_prefix'
        with self.assertRaisesRegex(ValueError,'模式发生变化'):queue.enqueue(daily)
        with queue.connect() as db:self.assertEqual('reading_only',db.execute('SELECT mode FROM preparations').fetchone()[0])


if __name__=='__main__':unittest.main()
