"""午夜准备共用暂停入口，已有执行模式不得被隐式迁移；不发外部请求。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from model_config import ModelConfig
from preparation import Preparations
from runtime import atomic
import test_preparation as preparation_fixtures
import test_workday_scope as source_fixtures


class PreparationGuardTests(unittest.TestCase):
    daily = preparation_fixtures.PreparationTests.daily

    def test_global_pause_stops_daily_preparation_before_any_send_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);queue=Preparations(root);daily=self.daily();daily['mode']='reading_only';queue.enqueue(daily)
            atomic(root/'pause-new-requests',dict(reason='合成运维暂停'))
            sent=[]
            def collect(_,directory,*args,**kwargs):
                directory.mkdir(parents=True,exist_ok=True)
                source_fixtures.WorkdayScopeTests().source(directory,['2026-09-08T10:00:00+08:00'],'2026-09-09T02:00:00+08:00')
                packet['source_snapshot']=json.loads((directory/'latest.json').read_text())
            packet=dict(events=[dict(event='synthetic',text='完整背景'*30000)],issues=[],source_snapshot={})
            def reading(packet,directory,*,client,limit):
                receipt=client.root/'reading'/'synthetic.receipt.json'
                with client.send_slot(dict(max_output_tokens=1),receipt,'synthetic-request',b'{}'):
                    sent.append(True)
                return dict(state='reading_complete_pending_reconciliation',received_pages=[0],pages=1)
            base=SimpleNamespace(config=ModelConfig('https://ai.apsatcom.com',root/'unused'))
            with patch('source_snapshot.collect',side_effect=collect),patch('pipeline.prepare',return_value=packet),patch('bounded_read.read_all',side_effect=reading):
                self.assertTrue(queue.tick(None,base,[999]))
            self.assertEqual([],sent,'全局暂停必须覆盖午夜准备的嵌套执行目录')
            self.assertEqual('queued',queue.snapshot()[0]['state'])
            ledgers=list((root/'preparations').rglob('request-budget.json'))
            self.assertEqual(1,len(ledgers))
            self.assertEqual({},json.loads(ledgers[0].read_text())['entries'])
            self.assertFalse(list(root.rglob('*.receipt.json')))

    def test_preparation_mode_conflict_preserves_existing_state_dates_and_budget(self):
        for state in ('prepared','unknown'):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);queue=Preparations(root);daily=self.daily();queue.enqueue(daily)
                with queue.connect() as db:key=db.execute('SELECT key FROM preparations').fetchone()[0]
                if state=='prepared':queue.update(key,state,'原模式完成','2026-09-08')
                else:queue.update(key,state,'原请求待核')
                ledger=root/'preparations'/key/'request-budget.json';atomic(ledger,dict(version=1,entries={'old-send-intent':{}}))
                with queue.connect() as db:before=dict(db.execute('SELECT * FROM preparations').fetchone())
                old_budget=ledger.read_bytes()
                changed=self.daily('2026-09-09');changed['mode']='reading_only'
                with self.assertRaisesRegex(ValueError,'准备模式'):
                    queue.enqueue(changed)
                with queue.connect() as db:after=dict(db.execute('SELECT * FROM preparations').fetchone())
                self.assertEqual(before,after)
                self.assertEqual(old_budget,ledger.read_bytes())
                self.assertEqual('navigation',after['mode'])


if __name__=='__main__':unittest.main()
