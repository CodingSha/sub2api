"""固定执行升级证明：旧任务可续接，规则、文件和未知回执保护不放宽。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime
from runtime import atomic,digest,compatible_policy
from pipeline import _evaluate_week,Paused
from shared_weeks import slot,source_identity


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.previous={'tools/zhiyongli/runtime.py':'a'*64,
                       'research/ai-usage-assessment/score-levels-g2.json':'b'*64}
        self.current=dict(self.previous,**{'tools/zhiyongli/runtime.py':'c'*64})
        self.files=patch.object(runtime,'policy_files',return_value=self.current)
        self.files.start();self.addCleanup(self.files.stop)
        path=patch.object(runtime,'__file__',str(self.root/'runtime.py'))
        path.start();self.addCleanup(path.stop)
        self.manifest=self.root/'execution-upgrade-20261113.json'
        self.write_manifest()

    def write_manifest(self):
        atomic(self.manifest,dict(version=1,reason='合成等价执行升级',
            previous_files=self.previous,current_policy_sha256=digest(self.current)))

    def packet(self):
        return dict(user_id='1',user_email='synthetic@example.test',since='2026-11-02T00:00:00+08:00',
                    until='2026-11-09T00:00:00+08:00',events=[],issues=[])

    def test_accepts_exact_transition_and_rejects_unlisted_drift(self):
        self.assertTrue(compatible_policy(digest(self.previous)))
        self.assertTrue(compatible_policy(digest(self.current)))
        self.assertFalse(compatible_policy('0'*64))
        self.current['tools/zhiyongli/runtime.py']='d'*64
        self.assertFalse(compatible_policy(digest(self.previous)))
        self.manifest.unlink()
        self.assertFalse(compatible_policy(digest(self.previous)))

    def test_even_explicit_manifest_cannot_accept_scoring_rule_changes(self):
        self.current['research/ai-usage-assessment/score-levels-g2.json']='e'*64
        self.write_manifest()
        self.assertFalse(compatible_policy(digest(self.previous)))

    def test_each_prior_execution_requires_its_own_exact_manifest(self):
        intermediate=dict(self.previous,**{'tools/zhiyongli/runtime.py':'e'*64})
        proof=json.loads(self.manifest.read_text());proof['previous_versions']=[intermediate]
        atomic(self.manifest,proof)
        self.assertTrue(compatible_policy(digest(self.previous)))
        self.assertTrue(compatible_policy(digest(intermediate)))
        proof['previous_versions'][0]['research/ai-usage-assessment/score-levels-g2.json']='f'*64
        atomic(self.manifest,proof)
        self.assertFalse(compatible_policy(digest(proof['previous_versions'][0])))
        self.assertFalse(compatible_policy(digest(intermediate)))

    def test_old_input_is_preserved_and_changed_source_or_frozen_file_is_rejected(self):
        packet=self.packet();directory=self.root/'job'
        stamp=dict(packet_sha256=digest(packet),policy_sha256=digest(self.previous))
        atomic(directory/'input.json',stamp);atomic(directory/'packet.json',packet)
        before=(directory/'input.json').read_bytes()
        with self.assertRaises(Paused) as error:_evaluate_week(packet,directory,None,lambda *a:None)
        self.assertEqual('no_activity',error.exception.state)
        self.assertEqual(before,(directory/'input.json').read_bytes())
        atomic(directory/'packet.json',dict(packet,issues=['篡改']))
        with self.assertRaisesRegex(ValueError,'冻结来源文件'):_evaluate_week(packet,directory,None,lambda *a:None)
        with self.assertRaises(Paused) as error:_evaluate_week(dict(packet,issues=['变化']),directory,None,lambda *a:None)
        self.assertEqual('needs_review',error.exception.state)

    def test_pending_shared_week_uses_old_index_and_blocks_unknown(self):
        packet=self.packet();directory=self.root/'original';directory.mkdir()
        base=self.root/'shared'/source_identity(packet)
        old=base/(digest(self.previous)+'.json')
        atomic(old,dict(state='pending',source_identity=source_identity(packet),
            policy_sha256=digest(self.previous),directory=str(directory.resolve())))
        with self.assertRaises(Paused):
            with slot(self.root/'shared',packet,directory):raise Paused('queued','合成轮转')
        self.assertEqual([old],list(base.glob('*.json')))
        atomic(directory/'model.receipt.json',dict(status='unknown',error_code='worker_restart_unresolved_send_intent'))
        with self.assertRaises(Paused) as error:
            with slot(self.root/'shared',packet,self.root/'another'):self.fail('不得另起请求')
        self.assertEqual('unknown',error.exception.state)
