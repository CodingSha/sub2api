"""原文候选只修复抄写；事实、异议、来源及未决结果不能被自动替换。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from audit_readback import checked
from pipeline import received, Paused
from quote_candidates import propose, resolve
from runtime import CompanyClient, RUBRIC, atomic
from semantic_audit import ContractError
from test_execution_paths import record


class QuoteCandidateTests(unittest.TestCase):
    def fixture(self):
        unit=dict(events=[dict(event='source',text='前文说明。已核，依据；保留\\符号&amp;，本轮结束。')])
        value=dict(items={key:dict(verdict='supported',reason='合成依据',person_act='合成本人事实',
            own_state='observed',problem_codes=[],source_quotes=[dict(event='source',quote='前文说明')])
            for key in RUBRIC['items']},global_issues=['合成未决异议，不因格式修复而删除'])
        value['items']['P1']['source_quotes'][0]['quote']='已核 依据'
        slot=dict(item='P1',index=0,event='source')
        feedback=dict(quote_slot=slot,previous_audit=value,quote_candidates=propose(unit,slot,value))
        return unit,value,feedback

    def test_model_choice_expands_exact_source_without_cleaning_it(self):
        unit,value,feedback=self.fixture();before=copy.deepcopy((unit,value))
        candidate=feedback['quote_candidates'][0]
        self.assertEqual('已核，依据',candidate['quote'])
        self.assertEqual(dict(event='source',quote='已核，依据'),
            resolve(unit,feedback,dict(candidate_id=candidate['candidate_id'])))
        self.assertEqual(before,(unit,value))

    def test_repetitions_stay_separate_and_no_lexical_or_cross_event_substitution(self):
        unit,value,feedback=self.fixture()
        unit['events'][0]['text']='甲已核，依据。乙已核；依据。'
        unit['events'].append(dict(event='other',text='已核，依据'))
        candidates=propose(unit,feedback['quote_slot'],value)
        self.assertEqual(2,len(candidates));self.assertNotEqual(candidates[0]['candidate_id'],candidates[1]['candidate_id'])
        self.assertEqual({'source'},{c['event'] for c in candidates})
        for invalid in ('已核新依据','已核依据&amp;',' \n '):
            changed=copy.deepcopy(value);changed['items']['P1']['source_quotes'][0]['quote']=invalid
            self.assertEqual([],propose(unit,feedback['quote_slot'],changed))

    def test_changed_source_forged_choice_or_abstention_never_expands(self):
        unit,_,feedback=self.fixture();candidate=feedback['quote_candidates'][0]
        for value in (dict(candidate_id='not_found'),dict(candidate_id='forged'),
                      dict(candidate_id=candidate['candidate_id'],quote='补造')):
            with self.assertRaises(ValueError):resolve(unit,feedback,value)
        unit['events'][0]['text']+='新事实'
        with self.assertRaisesRegex(ValueError,'冻结原文不符'):
            resolve(unit,feedback,dict(candidate_id=candidate['candidate_id']))

    def test_numbers_code_and_urls_do_not_lose_meaningful_punctuation(self):
        unit,value,feedback=self.fixture()
        for source,quote in (('-1','1。'),('1.2','12。'),('1.2','1。'),('1%','1。'),
                             ('a-b','ab。'),('https://a/b','httpsab。')):
            with self.subTest(source=source,quote=quote):
                unit['events'][0]['text']=source;value['items']['P1']['source_quotes'][0]['quote']=quote
                self.assertEqual([],propose(unit,feedback['quote_slot'],value))

    def test_all_repeated_candidates_must_fit_without_silent_sampling(self):
        unit,value,feedback=self.fixture();unit['events'][0]['text']='已核，依据。'*100
        with patch('bounded_read.REQUEST_BYTES',4000),self.assertRaisesRegex(ValueError,'全部原文候选超出'):
            propose(unit,feedback['quote_slot'],value)

    def client(self,value,decision=None):
        class Client(CompanyClient):
            accepts_revision_feedback=True
            def __init__(self):
                self.config=type('Config',(),dict(model='synthetic'))();self.payloads=[]
            def _call(self,payload,path):
                if path.exists():return json.loads(path.read_text())
                self.payloads.append(copy.deepcopy(payload))
                if payload['metadata']['purpose']=='zhiyongli_audit_quote_selection':
                    candidates=json.loads(payload['input'])['candidates']
                    return record(dict(candidate_id=decision or candidates[0]['candidate_id']),path)
                return record(value,path)
        return Client()

    def test_actual_client_path_preserves_every_judgment_and_resumes_same_receipts(self):
        unit,value,_=self.fixture();before=copy.deepcopy(value);client=self.client(value)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'audit.receipt.json'
            _,actual,result,final=checked(unit,unit,{},path,client,received)
            self.assertFalse(result['supported']);self.assertEqual(value['global_issues'],actual['global_issues'])
            expected=copy.deepcopy(value);expected['items']['P1']['source_quotes'][0]['quote']='已核，依据'
            self.assertEqual(expected,actual);self.assertEqual('audit.quote1.receipt.json',final.name)
            self.assertEqual(2,len(client.payloads))
            transport=json.loads(client.payloads[-1]['input'])
            self.assertNotIn('review',transport);self.assertNotIn('previous_audit',transport)
            self.assertEqual(actual,checked(unit,unit,{},path,client,received)[1])
            self.assertEqual(2,len(client.payloads))
            atomic(final,dict(status='unknown'))
            with self.assertRaises(Paused):checked(unit,unit,{},path,client,received)
            self.assertEqual(2,len(client.payloads))
        self.assertEqual(before,value)

    def test_model_abstention_stops_without_free_quote_retry_or_rescoring(self):
        unit,value,_=self.fixture();client=self.client(value,'not_found')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'audit.receipt.json'
            with self.assertRaisesRegex(ContractError,'仍未核实'):checked(unit,unit,{},path,client,received)
            self.assertEqual(2,len(client.payloads))
            self.assertEqual(2,len(list(Path(tmp).glob('*.receipt.json'))))
            self.assertEqual(value,received(json.loads(path.read_text())))

    def test_malformed_selection_never_falls_back_to_a_full_audit(self):
        unit,value,_=self.fixture()
        for invalid in ([],None,dict(event='source',quote='已核，依据')):
            with self.subTest(invalid=invalid),tempfile.TemporaryDirectory() as tmp:
                client=self.client(value)
                original=client._call
                def malformed(payload,path):
                    if payload['metadata']['purpose']=='zhiyongli_audit_quote_selection':
                        client.payloads.append(payload);return record(invalid,path)
                    return original(payload,path)
                client._call=malformed
                with self.assertRaises(ContractError):checked(unit,unit,{},Path(tmp)/'audit.receipt.json',client,received)
                self.assertEqual(2,len(client.payloads))

    def test_narrow_selection_does_not_resend_large_candidate_and_original_audit(self):
        from bounded_read import raw_size
        unit,value,_=self.fixture();unit['events'].append(dict(event='unused',text='x'*75000))
        review=dict(padding='r'*33000)
        for item in value['items'].values():item['reason']='合成理由'+'r'*1000
        client=self.client(value)
        with tempfile.TemporaryDirectory() as tmp:
            checked(unit,unit,review,Path(tmp)/'audit.receipt.json',client,received)
        self.assertGreater(raw_size(dict(unit=unit,review=review,contract_feedback=dict(previous_audit=value))),120000)
        self.assertLess(raw_size(client.payloads[-1]),12000)


if __name__=='__main__':unittest.main()
