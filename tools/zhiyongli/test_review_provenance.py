"""合成回执只验证防伪装合同，绝不作为真实模型质量成绩。"""
import contextlib
import io
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from daily_pilot import call
from review_provenance import candidate_from_replay
from review_quality import compare
from runtime import atomic,digest
from task_review_policy import RUBRIC
from task_evidence_flow import run
from test_task_card import task
import test_task_evidence_flow as flow_tests


class ProvenanceTests(unittest.TestCase):
    def replay(self,root):
        client=flow_tests.FlowTests().client();sender=flow_tests.FlowTests().sender()
        client.reasoning_effort=None;client.temperature=0
        def transmit(payload,target):
            name=target.name.removesuffix('.receipt.json')
            schema=payload['text']['format']['schema'];data=json.loads(payload['input'])
            value=sender(client,target.parent,name,data,schema,payload['instructions'])
            receipt=dict(status='received',model=client.config.model,endpoint=client.config.responses_url,
                request_sha256=digest(dict(endpoint=client.config.responses_url,payload=payload)),
                wire_body_sha256=hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest(),
                response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
            atomic(target,receipt);return receipt
        client._call=transmit
        t=task();source=dict(tasks=[t],originals={r['id']:r for r in t['records']})
        with contextlib.redirect_stdout(io.StringIO()):
            result=run(client,root/'candidate',source['tasks'],source['originals'],call)
        ref=dict(source_sha256='synthetic-test-source',rubric_sha256=digest(RUBRIC),
            reviewer='codex_direct_review',review_notes='合成测试夹具，不是真实语义校准',candidate_seen_before_reference=False,
            cases=[dict(case_id=t['id'],items=copy.deepcopy(result['results'][0]['items']))])
        atomic(root/'frozen-source.json',source)
        atomic(root/'replay-contract.json',dict(reference_sha256=digest(ref),source_sha256=ref['source_sha256'],
            source_payload_sha256=digest(source),model=client.config.model,endpoint=client.config.responses_url))
        return ref
    def test_round_trip_reconstructs_all_stages_and_same_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);ref=self.replay(root)
            candidate=candidate_from_replay(root,ref)
            self.assertEqual(len(candidate['receipt_hashes']),15)
            report=compare(ref,root)
            self.assertTrue(report['provenance_verified']);self.assertEqual(report['state_and_band_agreement'],1)
    def test_handwritten_candidate_without_actual_receipts_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):candidate_from_replay(Path(directory),{})
    def test_altered_result_or_response_or_reference_cannot_pass(self):
        for mutation in ('result','response','reference'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);ref=self.replay(root)
                if mutation=='reference':ref['review_notes']='回放后修改参考'
                else:
                    path=root/'candidate/task-0/E3/judgment-0.json'
                    if mutation=='response':path=path.with_name('judgment-0.receipt.json')
                    value=json.loads(path.read_text())
                    if mutation=='result':value['result']['reason']='被手工修改'
                    else:value['response']['status']='incomplete'
                    atomic(path,value)
                with self.assertRaises((ValueError,RuntimeError)):candidate_from_replay(root,ref)
