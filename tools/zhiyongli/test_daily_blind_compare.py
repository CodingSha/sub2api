import copy
from pathlib import Path
import tempfile
import unittest

from daily_blind_compare import compare, load
from runtime import RUBRIC, atomic, digest


class DailyBlindCompareTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.packet = dict(user_id=1, user_email='fixture@example.test')
        self.tasks = [dict(records=[dict(id='user', role='user', text='比较两份表。核对原始总数。保留第一项。'),
                                    dict(id='assistant', role='assistant', text='已核对。')])]
        self.contract = dict(day='2026-11-19', version='fixture', packet_sha256=digest(self.packet),
                             model='deepseek-v4.1-flash-local', policy_sha256=digest(RUBRIC))
        self.cells = {code: dict(band=None, evidence=[]) for code in RUBRIC['items']}
        self.cells['P1'] = self.cell(70, '比较两份表。')
        self.cells['J2'] = self.cell(70, '核对原始总数。')
        self.reference = dict(reviewer='fixture-reviewer', method='independent_human_blind', day=self.contract['day'],
                              packet_sha256=digest(self.packet), selected_tasks_sha256=digest(self.tasks), items=self.cells)

    def cell(self, band, quote, ref='user', origin='direct_expression'):
        return dict(band=band, evidence=[dict(source_ref=ref, quote=quote, origin=origin)])

    def write_run(self, name, cells, usage=21, response_id=None):
        root = self.root/name
        report = dict(period_type='day', period_start=self.contract['day'], **self.packet,
                      pilot=dict(quality_status='model_checked'), items=[dict(item_code=code,
                          exact=str(cell['band']) if cell['band'] is not None else None,
                          state='observed' if cell['band'] is not None else 'unobservable', evidence=cell['evidence'])
                          for code, cell in cells.items()])
        response = dict(id=response_id or name, status='completed', usage=dict(total_tokens=usage))
        artifacts = {'contract': self.contract, 'selected-tasks': self.tasks, 'report': report,
                     'quality': dict(quality_status='model_checked'),
                     'judge-P-0.receipt': dict(status='received', response=response),
                     'metrics': dict(requests=1, elapsed_seconds=2.5, model_seconds=2, input_bytes={'judge': 512})}
        for filename, value in artifacts.items():
            atomic(root/(filename+'.json'), value)
        return root

    def test_distinguishes_coverage_band_evidence_and_token_gaps(self):
        other = copy.deepcopy(self.cells)
        other['P1'] = self.cell(80, '保留第一项。')
        other['J2'] = dict(band=None, evidence=[])
        other['M1'] = self.cell(70, '核对原始总数。')
        result = compare(self.reference, self.packet, [self.write_run('first', self.cells), self.write_run('second', other, usage=None)])
        first, second = result['runs']
        self.assertEqual(first['measurements']['reported_tokens'], 21)
        self.assertEqual(second['measurements']['reported_requests'], 0)
        self.assertIsNone(second['measurements']['reported_tokens'])
        self.assertEqual(second['extra_scored'], ['M1'])
        self.assertEqual(second['missed'], ['J2'])
        self.assertEqual(second['evidence_review'], ['P1'])
        self.assertEqual(second['band_disagreements'], [dict(item='P1', human=70, model=80)])
        self.assertCountEqual(result['repeat_band_changes'], ['M1', 'P1', 'J2'])

    def test_repeat_only_distinguishes_observation_bands_and_alternative_evidence(self):
        other = dict(self.cells, P1=self.cell(80, '保留第一项。'),
                     J2=dict(band=None, evidence=[]))
        result = compare(None, self.packet, [self.write_run('first', self.cells), self.write_run('second', other)])
        self.assertEqual(result['mode'], 'repeat_only')
        self.assertIsNone(result['reference_sha256'])
        self.assertNotIn('missed', result['runs'][1])
        self.assertCountEqual(result['repeat_band_details'], [dict(item='P1', first=70, second=80),
                                                              dict(item='J2', first=70, second=None)])
        self.assertEqual(result['repeat_observation_changes'], ['J2'])
        self.assertEqual(result['repeat_evidence_changes'], ['P1'])
        self.assertIn('无独立人审真值', result['limitation'])

    def test_fails_closed_for_same_receipt_or_reused_artifact(self):
        first = self.write_run('first', self.cells)
        second = self.write_run('second', self.cells, response_id='first')
        with self.assertRaisesRegex(ValueError, '相同的模型响应'):
            compare(None, self.packet, [first, second])
        atomic(second/'judge-P-0.reused.json', {})
        with self.assertRaisesRegex(ValueError, '复用上一轮'):
            compare(self.reference, self.packet, [first, second])

    def test_rejects_material_or_contract_changes(self):
        first, second = (self.write_run(name, self.cells) for name in ('first', 'second'))
        atomic(second/'selected-tasks.json', [])
        with self.assertRaisesRegex(ValueError, '同一份冻结任务'):
            compare(self.reference, self.packet, [first, second])
        atomic(second/'contract.json', dict(load(second/'contract.json'), version='other'))
        with self.assertRaisesRegex(ValueError, '评分合同不一致'):
            compare(self.reference, self.packet, [first, second])

    def test_rejects_reference_for_a_different_selection(self):
        first, second = (self.write_run(name, self.cells) for name in ('first', 'second'))
        with self.assertRaisesRegex(ValueError, '人审基准'):
            compare(dict(self.reference, selected_tasks_sha256='wrong'), self.packet, [first, second])

    def test_rejects_ai_source_and_cross_item_quote_overlap(self):
        first = self.write_run('first', self.cells)
        for cell, message in ((self.cell(70, '已核对。', 'assistant'), '缺本人原文'),
                              (self.cell(70, '比较两份表。'), '跨项重复')):
            with self.subTest(message=message):
                other = dict(self.cells, M1=cell)
                with self.assertRaisesRegex(ValueError, message):
                    compare(None, self.packet, [first, self.write_run('second', other)])


if __name__=='__main__':
    unittest.main()
