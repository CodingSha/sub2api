"""对外简报与内部分析分离；验证展示不会改写已存评分。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from .examples import run_demo
from .reader_report import render_report, public_cases


class ReaderReportTest(unittest.TestCase):
    def test_brief_preserves_scores_and_keeps_internal_audit_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            run_demo(Path(temp) / 'demo')
            source = Path(temp) / 'demo/run/scored/report-data.json'
            data = json.loads(source.read_text())
            data['limits'] = '内部诊断专用内容'
            data['summary'][0]['reader_items']['P1']['limitations'].append('内部逐项检查标记')
            before = deepcopy(data)
            output = Path(temp) / 'brief'; output.mkdir()
            render_report(data, output)
            self.assertEqual(data, before)
            public = (output / 'report.html').read_text()
            private = (output / 'analysis/report.html').read_text()
            self.assertNotIn('内部逐项检查标记', public)
            self.assertIn('内部逐项检查标记', private)
            self.assertNotIn('已评分子项', public)
            self.assertIn('74.0', public)
            self.assertIn('L3 良好', public)
            self.assertLess(public.count('class="case"'), 4)

    def test_case_budget_retains_proven_gaps_and_avoids_repeated_goal(self):
        cases = [{'goal_id': str(n), 'packet_id': str(n), 'band': 40 if n == 4 else 80} for n in range(5)]
        row = {'period': {'kind': 'monthly'}, 'reader_items': {'P1': {'cases': cases}, 'P2': {'cases': cases}}}
        chosen = public_cases([row])
        self.assertEqual(len(chosen), 3)
        self.assertEqual(chosen[0][2]['band'], 40)
        self.assertEqual(len({c['goal_id'] for _, _, c in chosen}), 3)


if __name__ == '__main__':
    unittest.main()
