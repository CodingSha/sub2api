"""已核说明直接导出，原分、证据及无分状态不因排版改变。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from export_results import reviewed_reason
from pipeline import evaluate_week
from test_execution_paths import FixtureClient,packet


class ReviewedReasonTests(unittest.TestCase):
    def test_pipeline_exports_reviewed_facts_without_additional_model_calls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=FixtureClient()
            result=evaluate_week(packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00'),root,client)
            report=result['reports'][0];review=json.loads((root/'review.json').read_text())
            self.assertEqual(3,client.calls);self.assertEqual('80.0',report['score'])
            for cell,item in zip(review['rows'][0]['cells'],report['items']):
                for value in (cell['reason'],cell['opportunity']['description'],cell['action']['fact'],
                    cell['action']['ai_role'],cell['action']['attribution_explanation'],
                    cell['quality']['reason'],cell['performance']['reason'],cell['difference']):
                    self.assertIn(value,item['reason'])
                self.assertTrue(item['evidence'])
                self.assertNotIn('未达更高档',item['reason'])

    def test_nonobserved_preserves_missing_state_and_does_not_invent_actions(self):
        cell=dict(state='unobservable',reason='来源缺失，无法判断')
        before=copy.deepcopy(cell)
        self.assertEqual(cell['reason'],reviewed_reason(cell));self.assertEqual(before,cell)

    def test_reviewed_multiline_text_stays_one_period_line_and_credentials_are_redacted(self):
        cell=dict(state='observed',reason='已核\n限制',opportunity=dict(description='机会'),
            action=dict(fact='行为',ai_role='AI\r\n草稿',attribution_explanation='本人明确采纳'),
            quality=dict(reason='api_key: synthetic-secret'),performance=dict(reason='判档'))
        before=copy.deepcopy(cell);text=reviewed_reason(cell)
        self.assertNotIn('\n',text);self.assertNotIn('\r',text)
        self.assertNotIn('synthetic-secret',text);self.assertIn('[已脱敏]',text)
        self.assertEqual(before,cell)


if __name__=='__main__':unittest.main()
