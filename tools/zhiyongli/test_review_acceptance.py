"""防止局部候选、过期合同和离线成功被当成生产放行。"""
import json
from pathlib import Path
import tempfile
import unittest

from review_acceptance import inspect_run,assess
from review_replay import select
from runtime import atomic,policy
from test_source_index import source


class AcceptanceTests(unittest.TestCase):
    def test_one_completed_person_cannot_pass_four_person_scope(self):
        run=dict(state='candidate_generated',whole_person=True,current_contract=True,candidate_file_present=True)
        profile=dict(initial_batches_fit=True,key_identity_missing=0)
        result=assess([run],[profile]*4)
        self.assertEqual('unverified',result['gates'][1]['state'])
        self.assertFalse(result['deployment_eligible'])

    def test_partial_candidate_cannot_pass_full_person_gate(self):
        packet=select(source())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            atomic(root/'run/packet.json',dict(packet,events=packet['events'][:1]))
            atomic(root/'run/input.json',dict(policy_sha256=policy()))
            atomic(root/'run/bundle.json',dict(reports=[{}]))
            atomic(root/'model-result.json',dict(state='candidate_generated'))
            result=inspect_run(packet,root)
            self.assertFalse(result['whole_person'])
            assessed=assess([result],[dict(initial_batches_fit=True,key_identity_missing=0)])
            self.assertEqual('failed',assessed['gates'][1]['state'])
            self.assertFalse(assessed['deployment_eligible'])

    def test_candidate_and_batch_fit_do_not_prove_import_daily_scope_or_throughput(self):
        result=assess([dict(state='candidate_generated',whole_person=True,current_contract=True,candidate_file_present=True)],
            [dict(initial_batches_fit=True,key_identity_missing=1)])
        self.assertEqual('passed',result['gates'][1]['state'])
        self.assertEqual(1,result['full_person_candidates'])
        self.assertFalse(result['deployment_eligible'])
        self.assertEqual('failed',result['gates'][4]['state'])


if __name__=='__main__':unittest.main()
