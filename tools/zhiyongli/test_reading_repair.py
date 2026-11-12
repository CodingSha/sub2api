"""阅读已知无效输出的有界修正、共享复用和中断续跑；不请求真实模型。"""
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from bounded_read import read_all
from model_config import ModelConfig
from pipeline import Paused
from runtime import atomic
from test_reading_cache import ReadingCacheTests


class ReadingRepairTests(unittest.TestCase):
    setUp=ReadingCacheTests.setUp
    client=ReadingCacheTests.client
    packet=ReadingCacheTests.packet
    read=ReadingCacheTests.read

    def reply(self,request,**kwargs):
        self.requests.append(json.loads(request.data))
        page=json.loads(self.requests[-1]['input'])
        repair=page.get('contract_feedback') is not None
        notes={f['fragment_id']:dict(summary='合成失败和恢复过程',
            facts=[dict(anchor_id=f['segments'][0]['anchor_id'],fact='合成原文依据')] if repair else [],
            limitations=['合成来源限制'],open_questions=[]) for f in page['fragments']}
        response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(dict(notes=notes)))])])
        return io.BytesIO(('data: '+json.dumps(dict(type='response.completed',response=response))+'\n\n').encode())

    def test_repair_is_signed_budgeted_reusable_and_offline_readable(self):
        self.requests=[];packet=self.packet(1);first=self.client('a')
        with patch.object(ModelConfig,'opener') as opened:
            opened.return_value.open.side_effect=self.reply
            result=self.read(packet,first)
            self.assertEqual('reading_complete_pending_reconciliation',result['state'])
            self.assertEqual((2,2),(len(self.requests),first.budget.used()))
            originals={str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')}
            self.assertEqual(result,self.read(packet,first))
            second=self.client('b')
            self.assertEqual(result['received_pages'],self.read(packet,second)['received_pages'])
            self.assertEqual((2,0),(len(self.requests),second.budget.used()))
            offline=read_all(packet,first.root/'reading',limit=100)
            self.assertEqual([0],offline['received_pages'])
            self.assertEqual(originals,{str(p):p.read_bytes() for p in self.root.rglob('*.receipt.json')
                if str(p) in originals})
        feedback=json.loads(self.requests[1]['input'])['contract_feedback']
        self.assertIn('非空来源片段缺逐字阅读依据',feedback['error'])
        self.assertEqual(64,len(feedback['previous_response_sha256']))

    def test_turn_budget_pause_continues_same_correction_without_resending_original(self):
        self.requests=[];packet=self.packet(1);first=self.client('a',turn=1)
        with patch.object(ModelConfig,'opener') as opened:
            opened.return_value.open.side_effect=self.reply
            with self.assertRaises(Paused) as caught:self.read(packet,first)
            self.assertEqual('queued',caught.exception.state)
            self.assertEqual(1,len(self.requests))
            original=next(first.root.rglob('0.receipt.json'));before=original.read_bytes()
            restarted=self.client('a',turn=1)
            self.assertEqual([0],self.read(packet,restarted)['received_pages'])
            self.assertEqual(2,len(self.requests));self.assertEqual(2,restarted.budget.used())
            self.assertEqual(before,original.read_bytes())

    def test_unknown_correction_blocks_other_jobs_and_does_not_restart_chain(self):
        self.requests=[];packet=self.packet(1);first=self.client('a')
        with patch.object(ModelConfig,'opener') as opened:
            opened.return_value.open.side_effect=self.reply
            self.read(packet,first)
        correction=next(first.root.rglob('0.fix1.receipt.json'))
        pointer=json.loads(correction.read_text());shared=Path(pointer['shared_reading_receipt'])
        record=json.loads(shared.read_text());record['status']='unknown';atomic(shared,record)
        other=self.client('b')
        with self.assertRaises(Paused) as caught:self.read(packet,other)
        self.assertEqual('unknown',caught.exception.state)
        self.assertEqual(0,other.budget.used())


if __name__=='__main__':unittest.main()
