"""导航字段归位必须保留真实回执，且不能把未知响应或手改分数变成通过。"""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import jsonschema

from conversation_review import ask_conversation
from conversation_provenance import stage
from review_schema import obj,array
from runtime import digest


class TerminalNormalizationTests(unittest.TestCase):
    def test_repair_null_summary_moves_only_when_unambiguous(self):
        from conversation_transport import normalize_terminal
        cell=dict(band='good',reason='原判断',evidence=[dict(fragment_id='q1')])
        raw=dict(items=dict(E2=cell,J3=None,summary=None))
        before=json.loads(json.dumps(raw));result,moves=normalize_terminal(raw)
        self.assertEqual(result,dict(items=dict(E2=cell,J3=None),summary=None))
        self.assertEqual(raw,before)
        self.assertEqual(moves,[dict(op='move',source='/items/summary',target='/summary')])
        for invalid in (dict(items=dict(E2=cell)),dict(items=dict(E2=cell,summary='新解释')),
                dict(items=dict(E2=cell,summary=None),summary='冲突'),
                dict(items=dict(E2=cell,summary=None,other=None))):
            self.assertEqual(normalize_terminal(invalid),(invalid,[]))

    def test_repair_summary_requires_completed_receipt_and_rebuilds(self):
        schema=obj(dict(items=obj(dict(E2=obj(dict(band=dict(type='string'))))),summary=dict(type='null')))
        raw=dict(items=dict(E2=dict(band='good'),summary=None))
        for status in ('received','unknown'):
            with self.subTest(status=status),TemporaryDirectory() as d:
                root=Path(d);client=SimpleNamespace(config=SimpleNamespace(model='fixture',responses_url='fixture'),output_token_limit=6000)
                def send(client,folder,name,*unused):
                    payload=json.loads((folder/(name+'.intent.json')).read_text())['payload']
                    (folder/(name+'.input.json')).write_text(json.dumps(payload))
                    receipt=dict(status=status,model='fixture',endpoint='fixture',request_sha256=digest(dict(endpoint='fixture',payload=payload)),
                        wire_body_sha256=hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest(),
                        response=dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(raw))])]))
                    (folder/(name+'.receipt.json')).write_text(json.dumps(receipt));return raw
                name='repair-fixture-0'
                if status=='unknown':
                    with self.assertRaises(jsonschema.ValidationError):ask_conversation(client,root,name,{},schema,'修正',send,300)
                    self.assertFalse((root/(name+'.normalization.json')).exists());continue
                result=ask_conversation(client,root,name,{},schema,'修正',send,300)
                self.assertEqual(stage(root,name)[0],result)
                result['items']['E2']['band']='outstanding';(root/(name+'.json')).write_text(json.dumps(result))
                with self.assertRaisesRegex(ValueError,'真实模型正文不符'):stage(root,name)

    def test_workflow_personal_aliases_resolve_losslessly_and_unknown_refs_stay_invalid(self):
        from conversation_transport import normalize_terminal
        rows=[dict(id='r1',role='user',text_parts=[['q1','请按流程'],['q2','处理这次任务']]),
            dict(id='r2',role='context',text_parts=[['q3','平台指南']])]
        raw=dict(items=dict(E3=dict(band='good')),task_summaries=dict(t=dict(workflow=[dict(name='流程',user_refs=['q1','q2'],tool_ref='r9')])))
        before=json.loads(json.dumps(raw));value,moves=normalize_terminal(raw,rows)
        self.assertEqual(value['task_summaries']['t']['workflow'][0]['user_refs'],['r1'])
        self.assertEqual(value['items'],raw['items']);self.assertEqual(raw,before)
        self.assertEqual(moves[0]['original'],['q1','q2'])
        raw['task_summaries']['t']['workflow'][0]['user_refs']=['q999','q3']
        value,moves=normalize_terminal(raw,rows)
        self.assertEqual(value,raw);self.assertFalse(moves)

    def test_audit_reference_aliases_keep_all_original_records_and_received_judgment(self):
        refs=['q'+str(i) for i in range(10)]
        records=[dict(id='r'+str(i),text_parts=[['q'+str(i*5+j),'原文'] for j in range(5)]) for i in range(2)]
        schema=obj(dict(issues=array(obj(dict(problem=dict(type='string'),source_refs=dict(array(dict(type='string',enum=['r0','r1',*refs])),maxItems=6,uniqueItems=True))))))
        raw=dict(issues=[dict(problem='待纠正的事实不改变。',source_refs=refs)])
        for status in ['received','unknown']:
            with self.subTest(status=status),TemporaryDirectory() as d:
                root=Path(d);client=SimpleNamespace(config=SimpleNamespace(model='fixture',responses_url='fixture'),output_token_limit=6000)
                def send(client,folder,name,*unused):
                    payload=json.loads((folder/(name+'.intent.json')).read_text())['payload'];(folder/(name+'.input.json')).write_text(json.dumps(payload))
                    receipt=dict(status=status,model='fixture',endpoint='fixture',request_sha256=digest(dict(endpoint='fixture',payload=payload)),
                        wire_body_sha256=hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest(),
                        response=dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(raw))])]))
                    (folder/(name+'.receipt.json')).write_text(json.dumps(receipt));return raw
                name='conversation-quality-fixture'
                if status=='unknown':
                    with self.assertRaises(jsonschema.ValidationError):ask_conversation(client,root,name,dict(records=records),schema,'核查',send,300)
                    self.assertFalse((root/(name+'.normalization.json')).exists());continue
                result=ask_conversation(client,root,name,dict(records=records),schema,'核查',send,300)
                self.assertEqual(result,dict(issues=[dict(problem=raw['issues'][0]['problem'],source_refs=['r0','r1'])]))
                self.assertEqual(stage(root,name)[0],result)
                proof=json.loads((root/(name+'.normalization.json')).read_text());self.assertEqual(proof['moves'][0]['original'],refs)

    def test_audit_more_than_six_distinct_records_is_not_truncated_or_accepted(self):
        from conversation_transport import normalize_terminal
        rows=[dict(id='r'+str(i),text_parts=[['q'+str(i),'原文']]) for i in range(8)]
        raw=dict(issues=[dict(problem='原问题',source_refs=['q'+str(i) for i in range(8)])])
        value,moves=normalize_terminal(raw,rows);self.assertEqual(value,raw);self.assertEqual(moves,[])

    def test_many_background_fragments_collapse_to_the_same_records_without_losing_coverage(self):
        from conversation_transport import normalize_terminal
        rows=[dict(id='r'+str(i),text_parts=[['q'+str(i*5+j),'原文'] for j in range(5)]) for i in range(3)]
        refs=[key for row in rows for key,_ in row['text_parts']]
        raw=dict(items=dict(P1=dict(band='good',context_refs=refs)))
        normalized,steps=normalize_terminal(raw,rows)
        self.assertEqual(raw['items']['P1']['context_refs'],refs)
        self.assertEqual(normalized['items']['P1'],dict(band='good',context_refs=['r0','r1','r2']))
        self.assertEqual(steps[0]['original'],refs)

    def test_complete_receipt_rebuilds_canonical_result_and_rejects_tampering(self):
        schema=obj(dict(summary=dict(type='string'),task_summaries=obj({}),
            items=obj(dict(P1=obj(dict(band=dict(type='string',enum=['good','outstanding']))))),
            requests=array(dict(type='object')),queries=array(dict(type='string'))))
        raw=dict(summary='真实判断',task_summaries={},items=dict(P1=dict(band='good'),requests=[],queries=[]))
        for status in ['received','unknown']:
            with self.subTest(status=status),TemporaryDirectory() as d:
                root=Path(d);client=SimpleNamespace(config=SimpleNamespace(model='fixture',responses_url='fixture'),output_token_limit=6000)
                def send(client,folder,name,*unused):
                    payload=json.loads((folder/(name+'.intent.json')).read_text())['payload']
                    (folder/(name+'.input.json')).write_text(json.dumps(payload))
                    receipt=dict(status=status,model='fixture',endpoint='fixture',
                        request_sha256=digest(dict(endpoint='fixture',payload=payload)),
                        wire_body_sha256=hashlib.sha256(json.dumps(payload,ensure_ascii=False).encode()).hexdigest(),
                        response=dict(status='completed',output=[dict(type='message',content=[dict(type='output_text',text=json.dumps(raw))])]))
                    (folder/(name+'.receipt.json')).write_text(json.dumps(receipt))
                    return raw
                name='synthesis-fixture-0'
                if status=='unknown':
                    with self.assertRaises(jsonschema.ValidationError):
                        ask_conversation(client,root,name,{},schema,'只返回JSON',send,300)
                    self.assertFalse((root/(name+'.normalization.json')).exists())
                    continue
                result=ask_conversation(client,root,name,{},schema,'只返回JSON',send,300)
                self.assertEqual(result['items'],dict(P1=dict(band='good')))
                value,_,_=stage(root,name);self.assertEqual(value,result)
                altered=json.loads(json.dumps(result));altered['items']['P1']['band']='outstanding'
                (root/(name+'.json')).write_text(json.dumps(altered))
                with self.assertRaisesRegex(ValueError,'真实模型正文不符'):stage(root,name)


if __name__=='__main__':unittest.main()
