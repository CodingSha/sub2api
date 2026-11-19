"""一次评审全链回归；合成模型仅证明调用路径与合同，不证明语义公平。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_execution_paths import FixtureClient, packet, record
from pipeline import evaluate_week
from runtime import RUBRIC
from single_pass import MODE


class Client(FixtureClient):
    single_pass=True
    def identify(self, p, receipt):
        if receipt.exists():return json.loads(receipt.read_text())
        return super().identify(p,receipt)
    def call(self, unit, receipt):
        if receipt.exists():return json.loads(receipt.read_text())
        result=super().call(unit,receipt)
        value=json.loads(result['response']['output'][0]['content'][0]['text'])
        case=self.presentation_case
        value['presentation']=dict(items=[dict(item_code=code,basis='本人明确了本项要求，支持当前表现。',
            higher_band='现有记录没有展示持续运用本项方法的行为，不足以支持更高档。') for code in RUBRIC['items']],
            case=dict(task_key=case['task_key'],outcome='记录包含具体请求，没有外部验收结果。',
                claims=[dict(text=text,item_codes=['P1'],record_ids=[case['record_ids'][0]],
                    assessment=dict(kind='support',reason='本人提出了具体要求。'))
                    for text in ['本人提出明确目标。','本人要求逐项处理。','记录没有包含外部验收结果。']]) if case else None)
        if unit['evaluation_scope']=='person_week_fragment':
            value['presentation']=None
            for period in value['period_reviews']:
                period['context']['scope']='scored_fragment_scope'
                for cell in period['cells']:cell['performance']['scope']='scored_fragment_scope'
        return record(value,receipt)
    def audit_review(self,*args,**kwargs):
        raise AssertionError('正常一次评分不得调用另次模型复查')


class SinglePassTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.packet=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')

    def run_week(self,client=None,name='week'):
        client=client or Client()
        bundle=evaluate_week(self.packet,self.root/name,client,shared_root=self.root/'shared')
        return client,bundle

    def test_identification_plus_one_scoring_and_shared_reuse(self):
        client,bundle=self.run_week()
        self.assertEqual(2,client.calls)
        report=bundle['reports'][0]
        self.assertEqual('generated',report['task_review']['status'])
        self.assertEqual([],report['task_review']['checked_task_keys'])
        self.assertEqual(3,len(report['task_review']['cases'][0]['claims']))
        self.assertFalse((self.root/'week/audit.json').exists())
        self.assertFalse((self.root/'week/audit.receipt.json').exists())
        self.assertTrue(all('邻档判断：' in i['reason'] for i in report['items']))
        _,again=self.run_week(client,'reuse')
        self.assertEqual(bundle,again);self.assertEqual(2,client.calls)
        self.assertEqual(MODE,json.loads((self.root/'week/input.json').read_text())['review_mode'])

    def test_no_repeated_generation_on_resume(self):
        client,bundle=self.run_week()
        _,again=self.run_week(client)
        self.assertEqual(bundle,again);self.assertEqual(2,client.calls)

    def test_no_bounded_case_preserves_score_and_twelve_reasons(self):
        with patch('single_pass.select_case', return_value=None):
            client,bundle=self.run_week()
        report=bundle['reports'][0]
        self.assertEqual('80.0',report['score'])
        self.assertEqual(12,len(report['items']))
        self.assertTrue(all(i['reason'] for i in report['items']))
        self.assertIsNone(report.get('task_review'))
        self.assertEqual(2,client.calls)

    def test_new_mode_cannot_bypass_unfinished_legacy_execution(self):
        from shared_weeks import slot
        from pipeline import Paused
        legacy=self.root/'legacy';legacy.mkdir()
        with self.assertRaises(Paused):
            with slot(self.root/'shared',self.packet,legacy):
                raise Paused('needs_review','原评分存在未处置异议')
        client=Client()
        with self.assertRaisesRegex(Paused,'另一评分流程'):
            self.run_week(client)
        self.assertEqual(0,client.calls)

    def test_month_fragment_one_judgment_without_unused_presentation_and_resume(self):
        from pipeline import evaluate_fragment
        from runtime import moment,CompanyClient
        from types import SimpleNamespace
        client,_=self.run_week()
        unit=json.loads((self.root/'week/unit.json').read_text())
        fragment=copy.deepcopy(self.packet);fragment['until']='2026-09-08T00:00:00+08:00'
        target=self.root/'fragment'
        evaluate_fragment(fragment,unit,moment(fragment['since']),moment(fragment['until']),target,client,lambda *a:None)
        self.assertEqual(3,client.calls)
        self.assertIsNone(json.loads((target/'single-pass-presentation.json').read_text()))
        client.single_pass=False
        evaluate_fragment(fragment,unit,moment(fragment['since']),moment(fragment['until']),target,client,lambda *a:None)
        self.assertEqual(3,client.calls)
        sender=CompanyClient(SimpleNamespace(model='synthetic',timeout=1));sender._single_pass=True;sender.presentation_case=None
        sent=[];sender._call=lambda payload,receipt:sent.append(payload)
        sender.call(json.loads((target/'unit.json').read_text()),target/'not-sent')
        self.assertEqual({'type':'null'},sent[0]['text']['format']['schema']['properties']['presentation'])

    def test_tampered_presentation_is_rejected_without_new_calls(self):
        client,_=self.run_week()
        p=self.root/'week/single-pass-presentation.json';value=json.loads(p.read_text())
        value['items'][0]['basis']='篡改说明';p.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,'签名变化'):
            self.run_week(client,'tampered')
        self.assertEqual(2,client.calls)

    def test_invalid_citation_does_not_trigger_model_rescore(self):
        client=Client();original=client.call
        def invalid(unit,receipt):
            result=original(unit,receipt);value=json.loads(result['response']['output'][0]['content'][0]['text'])
            value['evidence'][0]['quote']='原文不存在的引文'
            return record(value,receipt)
        client.call=invalid
        with self.assertRaises(ValueError):self.run_week(client)
        self.assertEqual(2,client.calls)
        self.assertFalse((self.root/'week/bundle.json').exists())
        self.assertFalse((self.root/'week/active-revision.json').exists())

    def test_case_cannot_cite_another_task_or_unread_record(self):
        from single_pass import build
        client,bundle=self.run_week();root=self.root/'week'
        args=[self.packet,json.loads((root/'unit.json').read_text()),json.loads((root/'review.json').read_text())['value'],
            json.loads((root/'single-pass-presentation.json').read_text()),json.loads((root/'single-pass-case.json').read_text())]
        args[3]['case']['claims'][0]['record_ids']=['foreign']
        with self.assertRaisesRegex(ValueError,'来源引用'):build(*args,visible_events=args[4]['record_ids'])
        args[3]=json.loads((root/'single-pass-presentation.json').read_text())
        with self.assertRaisesRegex(ValueError,'未全部提供'):build(*args,visible_events=[])
        self.assertEqual(2,client.calls)

    def test_real_sender_includes_explanation_contract_in_same_request(self):
        from runtime import CompanyClient
        from types import SimpleNamespace
        self.run_week();unit=json.loads((self.root/'week/unit.json').read_text())
        client=CompanyClient(SimpleNamespace(model='synthetic',timeout=1));client._single_pass=True
        client.presentation_case=json.loads((self.root/'week/single-pass-case.json').read_text())
        from single_pass import descriptor
        client.presentation_case=descriptor(client.presentation_case)
        sent=[];client._call=lambda payload,receipt:sent.append(payload)
        client.call(unit,self.root/'unused')
        self.assertEqual(1,len(sent));payload=sent[0]
        self.assertIn('presentation',payload['text']['format']['schema']['properties'])
        self.assertIn('presentation_case',json.loads(payload['input']))
        self.assertIn('待核查',payload['instructions'])

if __name__=='__main__':unittest.main()
