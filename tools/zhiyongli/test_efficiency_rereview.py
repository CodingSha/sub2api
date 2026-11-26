"""使用效率补评只移动工作流归属，并锁住本批镜像和规则。"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from efficiency_rereview import classify_one, merge_frozen, require_lock, revision_plan
from pipeline import Paused
from runtime import RUBRIC
from test_weekly_daily import Client, fixture
from weekly_daily import evaluate


def row(ident, role, text):
    return dict(id=ident, role=role, text=text)


class EfficiencyRereviewTests(unittest.TestCase):
    def test_command_moves_method_to_workflow_but_file_reference_and_prompt_stay(self):
        command = [row('u', 'user', '把改动推上去'), row('t', 'tool', 'git push origin HEAD')]
        self.assertEqual('E3', classify_one(dict(primary_item='M1', source_ref='u'), '把改动推上去', command))
        self.assertEqual('P1', classify_one(dict(primary_item='P1', source_ref='u'), '把改动推上去', command))
        reference = [row('u2', 'user', '参考 old_script.py'), row('t2', 'tool', 'cmd: cat old_script.py')]
        self.assertIsNone(classify_one(dict(primary_item='E3', source_ref='u2'), '参考 old_script.py', reference))
        late = [row('t', 'tool', 'git push origin HEAD'), row('u', 'user', '看看结果')]
        self.assertEqual('M1', classify_one(dict(primary_item='M1', source_ref='u'), '看看结果', late))

    def test_plan_rejudges_efficiency_and_changed_mastery_only(self):
        material = dict(
            behaviors={'b1': dict(primary_item='M1', source_ref='u'), 'b2': dict(primary_item='P1', source_ref='p')},
            quotes={'b1': '把改动推上去', 'b2': '请写清楚验收标准'})
        timelines = {
            'u': [row('u', 'user', '把改动推上去'), row('t', 'tool', 'git push origin HEAD')],
            'p': [row('p', 'user', '请写清楚验收标准')]}
        overrides, judge, frozen = revision_plan(material, timelines)
        self.assertEqual({'b1': 'E3'}, overrides)
        self.assertIn('E3', judge)
        self.assertIn('M1', judge)
        self.assertIn('P1', frozen)
        self.assertNotIn('P1', judge)
        self.assertNotIn('E1', frozen)

    def test_batch_lock_rejects_a_different_image(self):
        job = dict(scope_key='weekly-from-daily-20261123.1:week', period_start='2026-11-16', period_end='2026-11-23',
            workday_scope=dict(working_dates=['2026-11-16']))
        with TemporaryDirectory() as tmp:
            os.environ['ZHIYONGLI_IMAGE_ID'] = 'image-a'
            try:
                require_lock(tmp, job)
                os.environ['ZHIYONGLI_IMAGE_ID'] = 'image-b'
                with self.assertRaises(Paused) as caught:
                    require_lock(tmp, job)
            finally:
                os.environ.pop('ZHIYONGLI_IMAGE_ID', None)
        self.assertEqual('needs_review', caught.exception.state)

    def test_skipping_prompt_items_does_not_call_the_model(self):
        with TemporaryDirectory() as tmp:
            state = Path(tmp)
            sources, job = fixture(state)
            client = Client()
            bundle = evaluate(sources, job, state, state / 'week', client, judge_codes=set())
        self.assertEqual([], client.sent)
        self.assertEqual(1, len(bundle['reports']))

    def test_merge_keeps_published_prompt_score(self):
        def item(code, exact, basis):
            return dict(item_code=code, exact=exact, necessary_gap=False, basis=basis)
        codes = list(RUBRIC['items'])
        prior_items = []
        new_items = []
        for code in codes:
            if code.startswith('P'):
                prior_items.append(item(code, '80', '已发布提示词依据'))
            elif code.startswith('M'):
                prior_items.append(item(code, '70', '已发布驾驭依据'))
            elif code.startswith('J'):
                prior_items.append(item(code, '75', '已发布核验依据'))
            else:
                prior_items.append(item(code, None, '五日材料未体现本项可独立评价的本人行为。'))
            new_items.append(item(code, '70' if code == 'E3' else None, '这五日抽样里没有这类行为。' if code.startswith('E') and code != 'E3' else '新评语'))
        result = dict(items=new_items, dimensions=[], score_status='scored')
        frozen = {code for code in codes if not code.startswith('E')}
        merge_frozen(result, dict(items=prior_items, score_status='scored'), frozen)
        kept = {row['item_code']: row for row in result['items']}
        self.assertEqual('已发布提示词依据', kept['P1']['basis'])
        self.assertEqual('80', kept['P1']['exact'])
        self.assertEqual('70', kept['E3']['exact'])
        self.assertEqual('这五日抽样里没有这类行为。', kept['E1']['basis'])
        dimensions = {row['code']: row['exact'] for row in result['dimensions']}
        self.assertEqual('80', dimensions['P'])
        self.assertEqual('70', dimensions['E'])
        self.assertEqual('scored', result['score_status'])


if __name__ == '__main__':
    unittest.main()
