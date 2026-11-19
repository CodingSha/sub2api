"""正式周与月汇总复用同源结果；原回执或来源变化不可静默复用。"""
import copy
import json
import unittest
from unittest.mock import patch

from session_weeks import evaluate,resolve
import test_session_review as fixtures
from test_session_review import ScriptedClient


class SessionWeeksTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.SessionReviewTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.root,self.packet,self.actions=fixture.root,fixture.packet,fixture.actions

    def test_shared_week_replays_original_receipts_without_new_request(self):
        first=ScriptedClient(self.actions())
        result=evaluate(self.packet,self.root/'first',first,lambda *a:None,self.root/'shared')
        self.assertEqual(16,first.sent)
        packet=copy.deepcopy(self.packet);packet['source_snapshot']={'sha256':'b'*64}
        other=ScriptedClient([])
        with patch.object(other,'_call',side_effect=AssertionError('复用不得新增请求')):
            reused=evaluate(packet,self.root/'month-week',other,lambda *a:None,self.root/'shared')
        self.assertEqual(result,reused)
        self.assertEqual((self.root/'first').resolve(),resolve(self.root/'month-week'))
        target=self.root/'first/review.json';original=target.read_text();target.write_text('{}')
        with self.assertRaisesRegex(ValueError,'签名变化'):resolve(self.root/'month-week')
        target.write_text(original)

    def test_unknown_week_remains_owned_by_original_job_without_duplicate_request(self):
        first=ScriptedClient(self.actions(),interrupt=1)
        with self.assertRaises(KeyboardInterrupt):
            evaluate(self.packet,self.root/'first',first,lambda *a:None,self.root/'shared')
        other=ScriptedClient([])
        from pipeline import Paused
        with self.assertRaises(Paused) as caught:
            evaluate(self.packet,self.root/'month-week',other,lambda *a:None,self.root/'shared')
        self.assertEqual('queued',caught.exception.state);self.assertEqual(0,other.sent)
        resumed=ScriptedClient(self.actions())
        result=evaluate(self.packet,self.root/'first',resumed,lambda *a:None,self.root/'shared')
        self.assertEqual('81.0',result['reports'][0]['score'])
        self.assertEqual(15,resumed.sent)
