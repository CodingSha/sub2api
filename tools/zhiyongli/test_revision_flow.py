"""收敛门禁的实际自动流程对照；模型全部为合成夹具。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline import evaluate_week,Paused
from review_control import selected
from test_execution_paths import FixtureClient,packet,record


class FlowClient(FixtureClient):
    accepts_revision_feedback=True
    verdict='revise'
    changing=False

    def __init__(self):
        super().__init__();self.scores=0;self.audits=0

    def identify(self,source,path):
        if path.exists():return json.loads(path.read_text())
        return super().identify(source,path)

    def call(self,unit,path,feedback=None):
        if path.exists():return json.loads(path.read_text())
        self.scores+=1
        return super().call(unit,path)

    def audit_review(self,unit,review,path):
        if path.exists():return json.loads(path.read_text())
        self.audits+=1
        result=super().audit_review(unit,review,path)
        value=json.loads(result['response']['output'][0]['content'][0]['text'])
        value['items']['P2'].update(verdict=self.verdict,
            reason='合成异议'+(str(self.audits) if self.changing else ''),problem_codes=['synthetic'])
        return record(value,path)


class RevisionFlowTests(unittest.TestCase):
    def run_case(self,root,client):
        source=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        with self.assertRaises(Paused) as caught:evaluate_week(source,root,client)
        self.assertEqual('needs_review',caught.exception.state)
        return source,str(caught.exception)

    def test_unresolved_stops_after_one_score_and_audit_and_resume_sends_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=FlowClient();client.verdict='unresolved'
            source,message=self.run_case(root,client)
            self.assertIn('未决缺口',message)
            self.assertEqual((1,1),(client.scores,client.audits))
            self.assertFalse((root/'revisions').exists());self.assertFalse((root/'bundle.json').exists())
            original={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            with self.assertRaises(Paused):evaluate_week(source,root,client)
            self.assertEqual((1,1),(client.scores,client.audits))
            self.assertEqual(original,{str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()})

    def test_exact_repeat_stops_after_one_revision_and_keeps_disagreement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=FlowClient();_,message=self.run_case(root,client)
            self.assertIn('相同材料',message);self.assertEqual((2,2),(client.scores,client.audits))
            current=selected(root)
            self.assertFalse(json.loads((current/'audit.json').read_text())['supported'])
            self.assertFalse((current/'bundle.json').exists())
            self.assertTrue((current/'revision-stop.json').exists())

    def test_new_objection_keeps_two_revision_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            client=FlowClient();client.changing=True;self.run_case(Path(tmp),client)
            self.assertEqual((3,3),(client.scores,client.audits))

    def test_before_control_repeats_three_score_audit_pairs(self):
        for verdict in ('revise','unresolved'):
            with self.subTest(verdict=verdict),tempfile.TemporaryDirectory() as tmp:
                client=FlowClient();client.verdict=verdict
                with patch('revision_convergence.decision',return_value=dict(allow=True)):
                    self.run_case(Path(tmp),client)
                self.assertEqual((3,3),(client.scores,client.audits))


if __name__=='__main__':unittest.main()
