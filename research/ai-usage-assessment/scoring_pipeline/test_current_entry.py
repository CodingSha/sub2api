"""当前入口和语义版本边界；不调用网络，不验证模型准确率。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .weekly_performance.review_policy import REVIEW_POLICY, attach_review_policy

ENTRY = Path(__file__).resolve().parents[1]/'run_scoring.py'


class CurrentEntryTests(unittest.TestCase):
    def test_default_and_explicit_week_are_offline(self):
        for command in ([], ['week']):
            result = subprocess.run([sys.executable, str(ENTRY), *command, '--help'],
                                    capture_output=True, text=True, check=True)
            self.assertIn('模型API请求0', result.stdout)
            self.assertIn('--review', result.stdout)

    def test_old_review_cannot_be_relabelled_current(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, value in [('input', {'evaluation_scope': 'person_week'}),
                                ('review', {}), ('sources', ['source'])]:
                (root/(name+'.json')).write_text(json.dumps(value))
            result = subprocess.run([sys.executable, str(ENTRY), '--input', str(root/'input.json'),
                '--review', str(root/'review.json'), '--expected-sources', str(root/'sources.json'),
                '--output', str(root/'output.json')], capture_output=True, text=True)
            self.assertEqual(2, result.returncode)
            self.assertIn('旧评审不能仅改规则版本升级', result.stderr)
            self.assertFalse((root/'output.json').exists())

    def test_only_all_current_reviews_propagate_semantic_policy(self):
        for reviews, expected in [([], False), ([{}], False),
                ([{'review_policy': REVIEW_POLICY}, {}], False),
                ([{'review_policy': REVIEW_POLICY}], True)]:
            report = {'policy_versions': {}}
            attach_review_policy(report, reviews)
            self.assertEqual(expected, 'review' in report['policy_versions'])
