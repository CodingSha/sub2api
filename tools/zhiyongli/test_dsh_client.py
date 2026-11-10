"""DSH终态与原文覆盖的边界回归；进程夹具不代表模型语义验收。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dsh_client import prepare_assets,terminal,budget_terminal,artifacts,DSHClient,DSHConfig
from model_config import ModelConfig
from runtime import atomic

class DSHTests(unittest.TestCase):
    def test_unicode_long_material_reconstructs_without_any_character_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload=dict(model='test',instructions='固定规则',input='原文😀\\n"'*20000,text=dict(format=dict(schema={'type':'object'})))
            m=prepare_assets(payload,Path(tmp),3)
            restored=''.join((Path(tmp)/a['file']).read_text() for a in m['assets'] if a['section']=='input')
            self.assertEqual(payload['input'],restored)
            self.assertGreater(len(m['assets']),3)
            self.assertTrue(all((Path(tmp)/a['file']).stat().st_mode&0o777==0o600 for a in m['assets']))

    def test_only_lossless_final_is_accepted(self):
        good=[dict(type='session',sessionId='s'),dict(type='text',text='被截断的中间文字',truncated=True),
              dict(type='final',sessionId='s',reason={'kind':'completed'},text='{"ok":true}')]
        self.assertEqual('{"ok":true}',terminal(good,{})['text'])
        for final in (dict(good[-1],truncated=True),dict(good[-1],sessionId='other'),
                      dict(good[-1],reason={'kind':'failed'}),dict(good[-1],text='不是JSON')):
            with self.assertRaises(ValueError):terminal(good[:-1]+[final],{})
        with self.assertRaises(ValueError):terminal(good[:-1],{})

    def test_unknown_never_launches_another_process_and_drift_is_not_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key=root/'key';key.write_text('dedicated-test-only');key.chmod(0o600)
            with patch.object(DSHConfig,'validate'):
                client=DSHClient(ModelConfig('https://ai.apsatcom.com',key),DSHConfig(root/'cli',root/'runtime',root/'home'))
            payload=dict(model=client.config.model,input='测试',instructions='规则',text=dict(format=dict(schema={'type':'object'})))
            with patch('dsh_client.configure'),patch('dsh_client.subprocess.Popen',side_effect=OSError('test-launch-failed')) as launch:
                first=client._call(payload,root/'call.receipt.json')
                second=client._call(payload,root/'call.receipt.json')
                self.assertEqual('unknown',first['status']);self.assertEqual(first,second)
                self.assertEqual(1,launch.call_count)
                with self.assertRaisesRegex(ValueError,'漂移'):client._call(dict(payload,input='更换材料'),root/'call.receipt.json')

    def test_known_oversized_stage_pauses_without_launch(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(DSHConfig,'validate'):
            root=Path(tmp);client=DSHClient(ModelConfig('https://ai.apsatcom.com',root/'unused'),DSHConfig(root/'cli',root/'runtime',root/'home'))
            with patch('dsh_client.subprocess.Popen') as launch:
                with self.assertRaisesRegex(ValueError,'模型窗口'):
                    client._call(dict(model=client.config.model,instructions='规则',input='x'*600001,text={}),root/'call.receipt.json')
                launch.assert_not_called();self.assertFalse((root/'call.receipt.json').exists())

    def test_only_proven_pre_send_budget_terminal_can_resume_same_session(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(DSHConfig,'validate'):
            root=Path(tmp);client=DSHClient(ModelConfig('https://ai.apsatcom.com',root/'unused'),DSHConfig(root/'cli',root/'runtime',root/'home'))
            receipt=root/'call.receipt.json';directory=receipt.with_suffix('.dsh');directory.mkdir()
            events=[dict(type='session',sessionId='fixed-session'),dict(type='final',sessionId='fixed-session',
                reason=dict(kind='error',error=dict(message='ZHIYONGLI_REQUEST_BUDGET')))]
            (directory/'events.ndjson').write_text('\n'.join(json.dumps(e) for e in events))
            self.assertFalse(budget_terminal(events,directory))
            (directory/'denials.ndjson').write_text(json.dumps(dict(code='ZHIYONGLI_REQUEST_BUDGET',used=3,limit=3))+'\n')
            (directory/'requests.ndjson').write_text('{}\n'*3)
            atomic(directory/'assets.json',dict(assets=[]))
            self.assertTrue(budget_terminal(events,directory))
            old=dict(status='budget_paused',session_id='fixed-session',model_requests=3,
                execution_contract=client.contract(),artifacts=artifacts(directory))
            atomic(receipt,old);before=receipt.read_bytes()
            with patch.object(client,'_execute',return_value=dict(status='test_no_model')) as execute:
                client.resume(receipt,reviewer='测试核对人',reason='明确本地预算终态',max_requests=6)
                self.assertEqual('fixed-session',execute.call_args.kwargs['session_id'])
            saved=list((directory/'attempts').glob('*/receipt.json'))
            self.assertEqual(1,len(saved));self.assertEqual(before,saved[0].read_bytes())
            for status in ('unknown','requesting','rejected'):
                atomic(receipt,dict(old,status=status))
                with patch.object(client,'_execute') as execute:
                    with self.assertRaisesRegex(ValueError,'unknown'):client.resume(receipt,reviewer='测试',reason='测试',max_requests=6)
                    execute.assert_not_called()
            atomic(receipt,old);(directory/'events.ndjson').write_text('changed')
            with self.assertRaisesRegex(ValueError,'变化'):client.resume(receipt,reviewer='测试',reason='测试',max_requests=6)

if __name__=='__main__':unittest.main()
