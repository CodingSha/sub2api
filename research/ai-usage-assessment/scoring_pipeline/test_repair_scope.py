"""检查实际发送内容、签名和差异，不访问模型。"""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import repair_scope as scope, stable_client, stable_batch, candidate_review_coverage as candidate
from .batch_client import digest, ModelError
from .test_stable_review import fixture


class ScopeTests(unittest.TestCase):
    def test_initial_and_material_read_are_not_repair(self):
        for correction in (None, '所需事件全文已补齐，请重新返回完整判断。'):
            data = dict(correction=correction, full_context=['新增材料'])
            self.assertIs(scope.adapt_payload(data), data)

    def test_adaptation_keeps_error_output_and_original(self):
        data = dict(task={'key':'test'}, correction=dict(contract_error='真实错误',
                    previous_output='原输出',instruction='旧指令'))
        before = copy.deepcopy(data)
        actual = scope.adapt_payload(data)
        self.assertEqual(data,before)
        self.assertEqual(actual['correction'],dict(before['correction'],instruction=scope.INSTRUCTION))
        self.assertIs(actual['task'],data['task'])

    def test_wire_signature_and_audit_match_adapted_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.codex').mkdir()
            (root/'.codex/auth.json').write_text(json.dumps({'OPENAI_API_KEY':'unit-test-only'}))
            unit,value=fixture(); value['version']=candidate.VERSION
            response=dict(id='offline',status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])])
            data=dict(task={'key':'test'},correction=dict(contract_error='引文错误',previous_output=json.dumps(value),instruction='旧指令'))
            before=copy.deepcopy(data); sent=[]
            def opener(request,timeout):
                sent.append(json.loads(request.data)); return io.BytesIO(json.dumps(response).encode())
            with patch.object(stable_client,'configured_model',return_value='test'), patch.object(stable_client.Path,'home',return_value=root):
                result=scope.ScopedClient('test',audit_dir=root/'audits',opener=opener).call('固定标准',data,root/'cache','offline')
            self.assertEqual(data,before)
            self.assertEqual(result['request_sha256'],digest(sent[0]))
            self.assertEqual(json.loads(sent[0]['input']),scope.adapt_payload(data))
            note=json.loads(next((root/'audits').glob('*.json')).read_text())
            self.assertEqual(note['request_sha256'],result['request_sha256'])
            self.assertEqual(note['actual_input_sha256'],digest(scope.adapt_payload(data)))
            self.assertFalse(note['diff']['changed_cells'])

    def test_quote_id_rename_not_a_content_change(self):
        _,a=fixture();b=copy.deepcopy(a);b['evidence'][0]['id']='renamed'
        def rename(x):
            if isinstance(x,list):return [rename(v) for v in x]
            if not isinstance(x,dict):return x
            return {k:['renamed' if r=='q' else r for r in v] if k=='refs' else rename(v) for k,v in x.items()}
        b['period_reviews']=rename(b['period_reviews'])
        self.assertFalse(scope.output_diff(a,b)['changed_cells'])

    def test_quote_origin_and_actual_band_changes_are_retained(self):
        _,a=fixture();b=copy.deepcopy(a)
        b['evidence'][0]['origin']='context'
        b['period_reviews'][0]['cells'][0]['performance']['verdict']='tailored'
        diff=scope.output_diff(a,b); first=next(c for c in diff['changed_cells'] if c['item']=='P1')
        self.assertIn('action',first['changed_fields'])
        self.assertNotEqual(first['before_verdict'],first['after_verdict'])
        self.assertFalse(diff['automatic_semantic_verdict'])

    def test_current_validator_and_score_rows_preserved(self):
        unit,value=fixture(); value['period_reviews'][0]['fact_coverage']=[dict(fact='目标事实',refs=['q'],primary_item='P1',reason='只评目标')]
        old=dict(value,version=candidate.previous.VERSION);new=dict(value,version=candidate.VERSION)
        before=copy.deepcopy(new)
        self.assertEqual(candidate.previous.check(unit,old),candidate.check(unit,new))
        self.assertEqual(new,before)
        new['period_reviews'][0]['fact_coverage'][0]['primary_item']='P2'
        with self.assertRaisesRegex(ValueError,'事实主归与该项状态矛盾'):candidate.check(unit,new)

    def test_two_repairs_remain_the_only_contract_retry_budget(self):
        unit,value=fixture();event=unit['events'][0];seen=[]
        packet=dict(raw_material=dict(person_events=[dict(event=event['event'],text=event['text'])],
                    prior_person_context=[],linked_context=[]))
        class InvalidClient:
            def call(self,instructions,data,*args):
                seen.append(scope.adapt_payload(data));return dict(text='{}')
        with tempfile.TemporaryDirectory() as tmp, patch.object(stable_batch,'packet',return_value=(unit,packet,{event['event']:event})):
            with self.assertRaisesRegex(ModelError,'contract_failed'):
                stable_batch.evaluate_one({},unit['key'],Path(tmp),dict(run_id='offline',replicate='test'),
                    InvalidClient(),unit['periods'][0],contract=candidate)
        self.assertEqual(len(seen),3)
        self.assertIsNone(seen[0]['correction'])
        self.assertTrue(all(x['correction']['instruction']==scope.INSTRUCTION for x in seen[1:]))

    def test_adapted_input_size_is_checked_before_transport(self):
        data=dict(correction=dict(contract_error='错误',previous_output='x'*1_200_000,instruction='旧'))
        with tempfile.TemporaryDirectory() as tmp, patch.object(stable_client.JSONClient,'call') as send:
            with self.assertRaisesRegex(ValueError,'input_budget_exceeded'):
                scope.ScopedClient('test',audit_dir=Path(tmp)).call('固定标准',data,Path(tmp),'offline')
            send.assert_not_called()


if __name__=='__main__':unittest.main()
