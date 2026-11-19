"""页摘要融合的日常准备与最终周接续；仅合成来源及真实mock opener。"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bounded_read import read_all
from context_navigation import Navigation
from page_navigation import MODE,verify_tree
from pipeline import prepare,reading_view
from runtime import atomic,digest
from worker import JobClient
import test_preparation_prefix as prefix_fixtures


class PageNavigationPreparationTests(unittest.TestCase):
    setUp=prefix_fixtures.PreparationPrefixTests.setUp
    daily=prefix_fixtures.PreparationPrefixTests.daily
    collect=prefix_fixtures.PreparationPrefixTests.collect
    queue=prefix_fixtures.PreparationPrefixTests.queue

    def base(self):
        return SimpleNamespace(config=self.config,reading_page_navigation=True)

    def test_two_daily_cutoffs_defer_navigation_then_final_reuses_paid_pages(self):
        queue=self.queue();base=self.base()
        with patch('source_snapshot.collect',side_effect=self.collect):
            for day in ('2026-09-08','2026-09-09'):
                if day!='2026-09-08':
                    daily=self.daily(day);daily['mode']='reading_prefix';queue.enqueue(daily)
                self.assertTrue(queue.tick(None,base,[999],max_requests=200,new_request_limit=100))
                self.assertEqual('prepared',queue.snapshot()[0]['state'])
                root=next((self.root/'state/preparations').iterdir())
                proof=json.loads((root/'prepared-prefix.json').read_text())
                self.assertFalse(proof['complete']);self.assertFalse(proof['prefix']['complete'])
                self.assertTrue(proof['prefix']['high_level_deferred'])
                self.assertEqual(MODE,proof['prefix']['page_navigation_mode'])
                self.assertFalse(list((self.root/'state/navigation-branches').rglob('*.receipt.json')))
                self.assertFalse(list((root/'prepared-prefixes').rglob('tree.json')))
        pages=list((self.root/'state/read-pages').rglob('*.receipt.json'))
        self.assertTrue(pages)
        paid={p:p.read_bytes() for p in pages};before=self.calls
        budget=json.loads((root/'request-budget.json').read_text())
        self.assertEqual(before,len(budget['entries']))
        packet=prepare(root/'sources/2026-09-09',2,'2026-09-07T00:00:00+08:00',
            '2026-09-14T00:00:00+08:00',self.daily()['workday_scope'])
        final=JobClient(self.config,self.root/'weekend',200-before,100)
        final.reading_cache_root=self.root/'state/read-pages';final.reading_plan_root=self.root/'state/reading-plans'
        final.navigation_cache_root=self.root/'state/navigation-branches'
        final.reading_content_identity=True;final.reading_efficiency=True;final.reading_page_navigation=True
        status=read_all(packet,final.root/'reading',client=final,limit=None)
        self.assertEqual(before,self.calls)
        self.assertEqual(0,final.budget.used())
        view=reading_view(packet,final.root,final,lambda *args:None)
        self.assertIn('context_navigation',view)
        self.assertGreater(self.calls,before)
        self.assertEqual(self.calls-before,final.budget.used())
        self.assertLessEqual(before+final.budget.used(),200)
        self.assertEqual(paid,{p:p.read_bytes() for p in paid})
        self.assertEqual(set(pages),set((self.root/'state/read-pages').rglob('*.receipt.json')))
        nav=Navigation(final.root/'context-navigation',packet,page_navigation=True)
        verify_tree(nav,status)
        leaves=json.loads((nav.root/'leaves.json').read_text())['node_ids']
        nodes=[nav.load(key) for key in leaves]
        procedural=[n for n in nodes if 'procedural' in n['note']]
        self.assertEqual(32,len(procedural))
        self.assertEqual({e['event'] for e in packet['events']},{n['note']['event_id'] for n in nodes})
        self.assertFalse(list((self.root/'state').rglob('bundle.json')))

    def test_prepared_queue_rejects_contract_switch_in_both_directions(self):
        original_root=self.root
        for initial in (False,True):
            with self.subTest(initial_page_navigation=initial):
                self.root=original_root/('fusion-first' if initial else 'legacy-first')
                self.root.mkdir();self.calls=0
                queue=self.queue()
                base=SimpleNamespace(config=self.config,reading_page_navigation=initial)
                with patch('source_snapshot.collect',side_effect=self.collect):
                    self.assertTrue(queue.tick(None,base,[999],max_requests=200,new_request_limit=100))
                self.assertEqual('prepared',queue.snapshot()[0]['state'])
                root=next((self.root/'state/preparations').iterdir())
                budget=root/'request-budget.json';budget_bytes=budget.read_bytes()
                self.assertGreater(len(json.loads(budget_bytes)['entries']),0)
                evidence={p:p.read_bytes() for p in root.glob('prepared-*.json')}
                self.assertTrue(evidence)
                contract=root/'reading-contract.json'
                self.assertEqual(initial,contract.exists())
                contract_bytes=contract.read_bytes() if contract.exists() else None
                daily=self.daily('2026-09-09');daily['mode']='reading_prefix';queue.enqueue(daily)
                base.reading_page_navigation=not initial;sent=self.calls
                with patch('source_snapshot.collect',side_effect=self.collect) as collect:
                    self.assertTrue(queue.tick(None,base,[999],max_requests=200,new_request_limit=100))
                self.assertEqual('needs_review',queue.snapshot()[0]['state'])
                self.assertEqual(sent,self.calls);collect.assert_not_called()
                self.assertEqual(budget_bytes,budget.read_bytes())
                self.assertEqual(evidence,{p:p.read_bytes() for p in evidence})
                self.assertEqual(contract_bytes,contract.read_bytes() if contract.exists() else None)
        self.root=original_root

    def test_incomplete_and_unknown_never_mark_daily_prepared(self):
        queue=self.queue();base=self.base()
        with patch('source_snapshot.collect',side_effect=self.collect):
            queue.tick(None,base,[999],max_requests=200,new_request_limit=1)
        self.assertEqual('queued',queue.snapshot()[0]['state'])
        self.assertFalse(list((self.root/'state/preparations').rglob('prepared-prefix.json')))
        self.assertFalse(list((self.root/'state/navigation-branches').rglob('*.receipt.json')))
        receipt=next((self.root/'state/read-pages').rglob('*.receipt.json'))
        record=json.loads(receipt.read_text());record['status']='unknown';atomic(receipt,record)
        for reference in (self.root/'state/reading-plans').rglob('*.receipt.json'):
            saved=json.loads(reference.read_text())
            if saved.get('shared_reading_receipt')==str(receipt.resolve()):
                saved.update(status='unknown',shared_receipt_sha256=digest(record));atomic(reference,saved)
        sent=self.calls
        with patch('source_snapshot.collect',side_effect=AssertionError('未决续跑不得重采')):
            queue.tick(None,base,[999],max_requests=200,new_request_limit=100)
        self.assertEqual('unknown',queue.snapshot()[0]['state']);self.assertEqual(sent,self.calls)
        self.assertFalse(list((self.root/'state/preparations').rglob('prepared-prefix.json')))
        self.assertFalse(list((self.root/'state/navigation-branches').rglob('*.receipt.json')))


if __name__=='__main__':unittest.main()
