"""验证正式日评来源绑定、跨语言正文哈希及空日边界；不代替真实模型质量验收。"""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from daily_assessment import evaluate
from daily_export import bundle, text_sha
from daily_pilot import report, RUBRIC
from runtime import atomic, digest
from workday_scope import daily_window


def fixture(root):
    day='2026-11-18'
    text='请比较两份报价，说明差异原因。'
    user=dict(id='u',role='user',text=text,at=day+'T10:00:00+08:00')
    packet=dict(user_id=2,user_email='person@example.test',workday_scope=daily_window(day),events=[])
    items=[dict(code=code,state='unobservable',verdict='unresolved',basis='本次没有独立行为。',higher_band='',quotes=[]) for code in RUBRIC['items']]
    items[0].update(state='observed',verdict='outstanding',basis='你明确了比较对象和解释差异的目标。',higher_band='尚未展现特别突出的表达质量。',quotes=[dict(record_id='u',quote=text,origin='personal')])
    saved=report(dict(items=items,summary='目标清楚。',strengths='',improvements=''),packet,day,{'u':user})
    saved.update(period_type='day',period_end='2026-11-19')
    saved['pilot']['quality_status']='model_checked'
    atomic(root/'quality.json',dict(quality_status='model_checked'))
    records=[dict(id='u',role='user',text=text,excerpt=False,source_order=0,source_hash=digest(text),names=[]),
        dict(id='a',role='assistant',text='已列出差异及其原因。',excerpt=False,source_order=1,source_hash='old',names=[])]
    saved['pilot']['conversations']={'task-1':dict(title='比较报价',goal='解释报价差异',outcome='',workflow=[],records=records)}
    saved['pilot']['behaviors']={saved['items'][0]['evidence'][0]['id']:dict(primary_item='P1',source_ref='u',claim='要求比较两份报价并解释差异')}
    task=dict(id='task-1',records=records,workflow_index=[])
    atomic(root/'selected-tasks.json',[task])
    atomic(root/'report.json',saved)
    atomic(root/'score.receipt.json',dict(status='received',request_sha256='a'*64,response=dict(status='completed',output=[])))
    return packet


class DailyExportTests(unittest.TestCase):
    def test_unchecked_or_changed_reused_receipt_cannot_be_exported(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);packet=fixture(root)
            (root/'quality.json').unlink()
            with self.assertRaisesRegex(ValueError,'尚未完成'):
                bundle(packet,'2026-11-18',root,'deepseek-v4.1-flash-local')
            fixture(root)
            source=root/'original.json'
            atomic(source,json.loads((root/'score.receipt.json').read_text()))
            atomic(root/'facts.reused.json',dict(path=str(source),receipt_sha256='f'*64))
            with self.assertRaisesRegex(ValueError,'回执签名变化'):
                bundle(packet,'2026-11-18',root,'deepseek-v4.1-flash-local')

    def test_known_quality_failure_cannot_be_exported_even_with_completed_response(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);packet=fixture(root)
            saved=json.loads((root/'report.json').read_text())
            saved['pilot']['quality_status']='failed'
            atomic(root/'report.json',saved)
            with self.assertRaisesRegex(ValueError,'质量验收已失败'):
                bundle(packet,'2026-11-18',root,'deepseek-v4.1-flash-local')
            fixture(root)
            atomic(root/'quality.json',dict(quality_status='failed'))
            with self.assertRaisesRegex(ValueError,'质量验收已失败'):
                bundle(packet,'2026-11-18',root,'deepseek-v4.1-flash-local')

    def test_export_preserves_model_judgment_and_uses_utf8_body_hash(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);packet=fixture(root)
            before=(root/'report.json').read_bytes()
            result=bundle(packet,'2026-11-18',root,'deepseek-v4.1-flash-local')['reports'][0]
            self.assertEqual((result['period_type'],result['period_end']),('day','2026-11-19'))
            evidence=result['items'][0]['evidence'][0]
            self.assertEqual(evidence['source_hash'],text_sha(evidence['context']))
            self.assertNotEqual(evidence['source_hash'],digest(evidence['context']))
            self.assertEqual(evidence['task_title'],'比较报价')
            self.assertEqual(result['items'][0]['exact'],'90')
            self.assertEqual(result['items'][0]['criterion'],RUBRIC['performance_anchors']['P1']['outstanding'])
            self.assertEqual(result['score_status'],'scored')
            self.assertEqual(result['score'],'90.0')
            self.assertEqual(result['policy_versions']['aggregation'],'daily-observed-weighted-20261119.2')
            self.assertNotIn('pilot',result)
            self.assertEqual((root/'report.json').read_bytes(),before)
            bad=copy.deepcopy(packet);bad['workday_scope']['freeze_at']='2026-11-18T22:00:00+08:00'
            with self.assertRaises(ValueError):bundle(bad,'2026-11-18',root,'deepseek-v4.1-flash-local')

    def test_unknown_response_or_changed_user_body_cannot_be_exported(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);packet=fixture(root)
            atomic(root/'score.receipt.json',dict(status='unknown'))
            with self.assertRaisesRegex(ValueError,'未完成'):
                bundle(packet,'2026-11-18',root,'deepseek-v4.1-flash-local')
            fixture(root)
            saved=json.loads((root/'report.json').read_text())
            saved['pilot']['conversations']['task-1']['records'][0]['text']='未经模型阅读的替换正文'
            atomic(root/'report.json',saved)
            with self.assertRaisesRegex(ValueError,'实际交付'):
                bundle(packet,'2026-11-18',root,'deepseek-v4.1-flash-local')

    def test_missing_feedback_is_failure_while_real_empty_day_needs_no_model(self):
        event=dict(event='u',at='2026-11-18T10:00:00+08:00',session_id='s',api_key_id=1,group_id=1,
            actor_class='candidate_user',protocol_role='user',text='请整理需求。',truncated=False,source=dict(frame='f'))
        for body,expected in [(None,'no_activity'),('你是什么模型','observation_only')]:
            with TemporaryDirectory() as tmp:
                root=Path(tmp)
                packet=dict(user_id=2,user_email='person@example.test',workday_scope=daily_window('2026-11-18'),events=[] if body is None else [dict(event,text=body)])
                client=Mock()
                self.assertIsNone(evaluate(packet,'2026-11-18',root,client))
                result=bundle(packet,'2026-11-18',root,'deepseek-v4.1-flash-local')['reports'][0]
                self.assertEqual(result['score_status'],expected)
                self.assertIsNone(result['score'])
                client._call.assert_not_called()
        with TemporaryDirectory() as tmp,self.assertRaisesRegex(ValueError,'来源不完整'):
            evaluate(dict(user_id=2,events=[event]),'2026-11-18',Path(tmp),Mock(dimension_coverage=False))


if __name__=='__main__':unittest.main()
