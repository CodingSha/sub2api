"""复查的逐字依据须含实际内容，不能用来源空白冒充支持。"""
import copy
from pathlib import Path
import tempfile
import unittest

from runtime import RUBRIC
from semantic_audit import QuoteError, validate


class QualityEvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self.unit = dict(events=[dict(event='source', text='原文\n  已核依据\\实体&amp;')])
        self.value = dict(items={key: dict(verdict='supported', reason='来源实际支持本项判断',
            person_act='按本项要求处理', own_state='observed', problem_codes=[],
            source_quotes=[dict(event='source', quote='已核依据\\实体&amp;')])
            for key in RUBRIC['items']}, global_issues=[], remarks=[], needs_events=[])

    def test_blank_quotes_never_count_as_actual_support(self):
        for quote in (' ', '\n', '\n  '):
            with self.subTest(quote=repr(quote)):
                value = copy.deepcopy(self.value)
                value['items']['P1']['source_quotes'][0]['quote'] = quote
                with self.assertRaises(QuoteError) as error:
                    validate(self.unit, {}, value, RUBRIC['items'])
                self.assertEqual(dict(item='P1', index=0, event='source'), error.exception.slot)

    def test_whitespace_in_meaningful_quote_is_preserved_exactly(self):
        value = copy.deepcopy(self.value)
        value['items']['P1']['source_quotes'][0]['quote'] = '\n  已核依据\\实体&amp;'
        before = copy.deepcopy(value)
        self.assertTrue(validate(self.unit, {}, value, RUBRIC['items'])['supported'])
        self.assertEqual(before, value)
        value['items']['P1']['source_quotes'][0]['quote'] = '\n 已核依据\\实体&amp;'
        with self.assertRaises(QuoteError):
            validate(self.unit, {}, value, RUBRIC['items'])

    def test_blank_reason_is_not_a_supported_judgment(self):
        for reason in (' ', '\n\t', None, True):
            with self.subTest(reason=repr(reason)):
                value = copy.deepcopy(self.value)
                value['items']['P1']['reason'] = reason
                with self.assertRaisesRegex(ValueError, '缺判断或依据'):
                    validate(self.unit, {}, value, RUBRIC['items'])

    def test_blank_quote_repair_preserves_all_judgments_and_global_issues(self):
        from audit_readback import checked
        from pipeline import received
        from test_execution_paths import record
        value = copy.deepcopy(self.value)
        value['items']['P1']['source_quotes'][0]['quote'] = '\n  '
        value['items']['M3'].update(verdict='revise', problem_codes=['independent_claim_needed'])
        value['global_issues'] = ['M3/E2存在待核主归争议']
        before = copy.deepcopy(value)
        class Client:
            accepts_revision_feedback = True
            def __init__(self):
                self.feedback = []
            def audit_review(self, unit, review, path, feedback=None):
                self.feedback.append(copy.deepcopy(feedback))
                return record(value if feedback is None else
                    dict(event='source', quote='已核依据\\实体&amp;'), path)
        client = Client()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, corrected, result, _ = checked(self.unit, self.unit, {}, root/'audit.receipt.json', client, received)
            self.assertFalse(result['supported'])
            self.assertEqual(value['global_issues'], corrected['global_issues'])
            self.assertEqual(value['items']['M3'], corrected['items']['M3'])
            self.assertEqual(dict(item='P1', index=0, event='source'), client.feedback[1]['quote_slot'])
            self.assertEqual(2, len(list(root.glob('*.receipt.json'))))
        self.assertEqual(before, value)

    def test_blank_reason_can_be_repaired_without_rewriting_valid_judgments(self):
        from audit_readback import checked
        from pipeline import received
        from test_execution_paths import record
        value = copy.deepcopy(self.value)
        value['items']['P1']['reason'] = ' \n '
        corrected = copy.deepcopy(value)
        corrected['items']['P1']['reason'] = '原文实际说明本项行为'
        class Client:
            accepts_revision_feedback = True
            def audit_review(self, unit, review, path, feedback=None):
                return record(value if feedback is None else corrected, path)
        with tempfile.TemporaryDirectory() as tmp:
            _, actual, result, _ = checked(self.unit, self.unit, {}, Path(tmp)/'audit.receipt.json', Client(), received)
            self.assertTrue(result['supported'])
            self.assertEqual(corrected, actual)

    def test_model_supported_without_independent_facts_is_rejected_but_explicit_legacy_validation_remains(self):
        for field in ('person_act','own_state'):
            with self.subTest(field=field):
                value=copy.deepcopy(self.value);value['items']['P1'].pop(field)
                with self.assertRaisesRegex(ValueError,'缺本人行为事实或独立状态'):
                    validate(self.unit,{},value,RUBRIC['items'],require_facts=True)
                # 显式人工处置的旧合同保留，不能把该兼容入口用于模型终态。
                self.assertTrue(validate(self.unit,{},value,RUBRIC['items'])['supported'])
        for act in (' ',None,True):
            value=copy.deepcopy(self.value);value['items']['P1']['person_act']=act
            with self.assertRaises(ValueError):validate(self.unit,{},value,RUBRIC['items'],require_facts=True)

    def test_model_fact_repair_cannot_erase_existing_dispute_or_invoke_new_score(self):
        from audit_readback import checked
        from pipeline import received
        from test_execution_paths import record
        value=copy.deepcopy(self.value);value['items']['P1'].pop('own_state')
        value['items']['M3'].update(verdict='revise',problem_codes=['synthetic'])
        corrected=copy.deepcopy(value);corrected['items']['P1']['own_state']='observed'
        class Client:
            accepts_revision_feedback=True
            calls=0
            def audit_review(self,unit,review,path,feedback=None):
                self.calls+=1;return record(value if feedback is None else corrected,path)
        with tempfile.TemporaryDirectory() as tmp:
            client=Client();_,actual,result,_=checked(self.unit,self.unit,{},Path(tmp)/'audit.receipt.json',client,received)
            self.assertFalse(result['supported']);self.assertEqual(value['items']['M3'],actual['items']['M3'])
            self.assertEqual(2,client.calls)

    def test_model_missing_facts_never_passes_by_claiming_all_items_supported(self):
        from audit_readback import checked
        from semantic_audit import ContractError
        from pipeline import received
        from test_execution_paths import record
        value=copy.deepcopy(self.value)
        for item in value['items'].values():item.pop('person_act');item.pop('own_state')
        class Client:
            def audit_review(self,unit,review,path):return record(value,path)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ContractError):
                checked(self.unit,self.unit,{},Path(tmp)/'audit.receipt.json',Client(),received)


if __name__ == '__main__':
    unittest.main()
