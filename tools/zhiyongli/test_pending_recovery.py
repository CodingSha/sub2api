"""待核局部恢复：冻结签名跨升级稳定，明确截断有界修正，未知发送仍阻断。"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from runtime import atomic,digest
from pipeline import reading_view
from model_config import ModelConfig
import test_reading_cache as fixtures


class FrozenReadingTests(unittest.TestCase):
    def test_same_source_after_upgrade_keeps_all_identification_page_hashes(self):
        from candidate_segments import split
        from goal_batches import plan
        packet=dict(user_id='1',user_email='synthetic@example.test',events=[dict(event='e1',text='本人任务',
            actor_class='candidate_user',at='2026-11-03T00:00:00+08:00')],issues=[])
        from bounded_read import plan as reading_plan
        slots=reading_plan(packet)['pages']
        class Client:
            def navigate_context(self):pass
            def merge_goals(self):pass
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);evidence=root/'reading';evidence.mkdir()
            atomic(root/'input.json',dict(packet_sha256=digest(packet),policy_sha256='old'))
            atomic(evidence/'plan.json',dict(pages=slots))
            fragment=slots[0]['fragments'][0]
            atomic(evidence/'0.checked.json',dict(value=dict(notes={fragment['fragment_id']:
                dict(summary='原阅读结论',limitations=[],open_questions=[])})))
            status=dict(pages=1,received_pages=[0],blocked=[],state='reading_complete_pending_reconciliation',
                evidence_directory=str(evidence),policy_sha256='old')
            descriptor=dict(node_id='0'*64,summary='完整来源导航')
            from hierarchical_reading import compact
            def build(packet,notes,status,*args):
                return compact(packet,descriptor,dict(reading_status_sha256=digest(status)))
            with patch('pipeline.reading_preflight'),patch('bounded_read.raw_size',return_value=200000),\
                 patch('bounded_read.read_all',return_value=status),patch('hierarchical_reading.build',side_effect=build),\
                 patch('pipeline.compatible_policy',side_effect=lambda stamp:stamp in {'old','new'}):
                before=reading_view(packet,root,Client(),lambda *a:None)
                status['policy_sha256']='new'
                after=reading_view(packet,root,Client(),lambda *a:None)
                self.assertEqual([digest(p) for p in plan(split(before))],[digest(p) for p in plan(split(after))])
                # 自动修订复用父轮识别回执，不能因修订本身更新执行合同而漂移。
                revision=root/'revisions'/'second';revision.mkdir(parents=True)
                atomic(revision/'input.json',dict(packet_sha256=digest(packet),policy_sha256='new'))
                atomic(revision/'revision.json',dict(automatic_round=1,parent_directory=str(root)))
                again=reading_view(packet,revision,Client(),lambda *a:None)
                self.assertEqual(before,again)
                # 来源变化和不兼容合同仍拒绝，不能借此绕过冻结保护。
                atomic(root/'input.json',dict(packet_sha256='changed',policy_sha256='old'))
                with self.assertRaisesRegex(ValueError,'冻结来源'):reading_view(packet,root,Client(),lambda *a:None)
                atomic(root/'input.json',dict(packet_sha256=digest(packet),policy_sha256='unlisted'))
                with self.assertRaisesRegex(ValueError,'合同'):reading_view(packet,root,Client(),lambda *a:None)


class TruncatedReadingTests(unittest.TestCase):
    setUp=fixtures.ReadingCacheTests.setUp
    client=fixtures.ReadingCacheTests.client
    packet=fixtures.ReadingCacheTests.packet
    read=fixtures.ReadingCacheTests.read

    def truncated_opener(self,reason='max_output_tokens',always=False):
        original=self.config.opener();test=self
        class Opener:
            def open(self,request,**kwargs):
                if always or test.calls==0:
                    test.calls+=1
                    response=dict(status='incomplete',incomplete_details=dict(reason=reason),
                        output=[dict(content=[dict(type='output_text',text='{"notes":')])])
                    return io.BytesIO(('data: '+json.dumps(dict(type='response.incomplete',response=response))+'\n\n').encode())
                return original.open(request,**kwargs)
        return Opener()

    def test_terminal_truncation_repairs_only_failed_page_and_preserves_receipt(self):
        with patch.object(ModelConfig,'opener',return_value=self.truncated_opener()):
            client=self.client('truncated');result=self.read(self.packet(),client)
            self.assertEqual('reading_complete_pending_reconciliation',result['state'])
            self.assertEqual((2,2),(self.calls,client.budget.used()))
            originals={str(p):p.read_bytes() for p in (self.root/'shared').rglob('*.receipt.json')}
            self.assertTrue(any(json.loads(raw).get('response',{}).get('status')=='incomplete' for raw in originals.values()))
            self.read(self.packet(),client)
            self.assertEqual((2,2),(self.calls,client.budget.used()))
            self.assertEqual(originals,{p:Path(p).read_bytes() for p in originals})

    def test_truncation_is_bounded_and_other_incomplete_reasons_still_stop(self):
        from bounded_read import CONTRACT_RETRIES
        with patch.object(ModelConfig,'opener',return_value=self.truncated_opener(always=True)):
            client=self.client('bounded');result=self.read(self.packet(),client)
            self.assertEqual('reading_blocked',result['state'])
            self.assertEqual(1+CONTRACT_RETRIES,self.calls)
            self.read(self.packet(),client)
            self.assertEqual(1+CONTRACT_RETRIES,self.calls)
        self.calls=0
        with patch.object(ModelConfig,'opener',return_value=self.truncated_opener(reason='content_filter')):
            packet=self.packet(25);packet['user_id']='other'
            result=self.read(packet,self.client('other'))
            self.assertEqual('reading_blocked',result['state']);self.assertEqual(1,self.calls)


if __name__=='__main__':unittest.main()
