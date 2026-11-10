"""合成全链合同回归：真实装配/算分/缓存，模型以夹具替代，不证明语义效度。"""
import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pipeline import evaluate_month,evaluate_week,Paused
from runtime import VERSION,RUBRIC,atomic,moment
from shared_weeks import slot,resolve
from review_control import parent_proof,resolution,revise,selected
from shared_weeks import reference
from scoring_pipeline.weekly_performance.review_policy import REVIEW_POLICY


def packet(start,end):
    active=start[:10] in {'2026-09-07','2026-09-14','2026-09-21'}
    text='\n'.join('合成合同夹具'+i for i in RUBRIC['items'])
    return dict(user_id='test',user_email='test@example.test',since=start,until=end,issues=[],source_snapshot={'sha256':'a'*64},
        source_scope='full_person_window',events=[dict(event='test-'+start[:10],text=text,actor_class='candidate_user',
        at=(moment(start)+timedelta(hours=10)).isoformat(),session_id='test',request_id='test',timestamp_unknown=False,truncated=False)] if active else [])


def record(value,receipt=None):
    result=dict(status='received',response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(value))])]))
    if receipt:atomic(receipt,result)
    return result


class FixtureClient:
    def __init__(self):self.calls=0
    def identify(self,p,receipt):
        self.calls+=1;e=p['events'][0]
        return record(dict(materials={e['event']:dict(classification='human',human_quotes=[e['text']],reason='仅合成合同用例',
            duplicate_of=None,goal_id='test',step='合成流程',assignment_reason='夹具归属')},
            goals=[dict(goal_id='test',title='合成合同用例',purpose='验证程序串联',boundary='confirmed',boundary_reason='仅测试',context_event_ids=[])]),receipt)
    def call(self,u,receipt):
        self.calls+=1;event=next(e for e in u['events'] if e['actor_class']=='human');cells=[];evidence=[]
        for i in RUBRIC['items']:
            rid='person-'+i;evidence.append(dict(id=rid,event=event['event'],quote='合成合同夹具'+i,origin='direct_expression',origin_reason='合成用例'))
            c=dict(item=i,state='observed',reason='合成合同用例',opportunity=dict(description='合成机会',refs=[rid]),
                action=dict(fact='合成事实'+i,ai_role='夹具',attribution_explanation='夹具归属',refs=[rid]),
                quality=dict(label='sufficient',reason='合成质量事实',refs=[rid]),
                performance=dict(verdict='good',scope='scored_week_scope',reason='只用于程序检查'),difference='各项不同合成事实')
            if i.startswith('M'):c['challenge_handling']=dict(status='not_demonstrated',reason='合成常规场景',refs=[])
            cells.append(c)
        return record(dict(version=VERSION,review_policy=REVIEW_POLICY,key=u['key'],needs_events=[],evidence=evidence,
            period_reviews=[dict(period_ids=[u['periods'][0]['period_id']],cells=cells,context=dict(scope='scored_week_scope',
                effective_request='合成请求',necessary_scope='合成范围',observed_execution='合成反馈',boundary='consistent',boundary_reason='仅测试',refs=['person-P1']))]),receipt)
    def audit_review(self,u,r,receipt):
        self.calls+=1
        return record(dict(items={i:dict(verdict='supported',reason='合成复查夹具',source_quotes=[dict(event=r['evidence'][0]['event'],quote=r['evidence'][0]['quote'])],
            problem_codes=[]) for i in RUBRIC['items']},global_issues=[]),receipt)


class ExecutionTests(unittest.TestCase):
    def test_mixed_empty_month_three_actual_week_contracts_and_shared_reuse(self):
        with tempfile.TemporaryDirectory() as tmp,patch('pipeline.prepare',side_effect=lambda _,__,a,b:packet(a,b)):
            root=Path(tmp);client=FixtureClient()
            result=evaluate_month('test','test','2026-09-01T00:00:00+08:00','2026-10-01T00:00:00+08:00',root/'month',client,shared_root=root/'shared')
            report=result['reports'][0]
            self.assertEqual('1.6',report['policy_versions']['rules'])
            self.assertEqual('80.0',report['score']);self.assertEqual(3,report['eligibility']['valid_weeks'])
            self.assertEqual(5,len(report['coverage']));self.assertEqual(9,client.calls)
            before=client.calls
            p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00');p['source_snapshot']={'sha256':'b'*64}
            reused=evaluate_week(p,root/'later-week-job',client,shared_root=root/'shared')
            self.assertEqual('80.0',reused['reports'][0]['score']);self.assertEqual(before,client.calls)
            actual=resolve(root/'later-week-job')
            (actual/'review.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'签名变化'):evaluate_week(p,root/'another',client,shared_root=root/'shared')

    def test_unknown_in_other_job_prevents_duplicate_week_and_policy_bypass(self):
        p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);first=root/'first';first.mkdir()
            with self.assertRaises(Paused):
                with slot(root/'shared',p,first):
                    atomic(first/'call.receipt.json',dict(status='unknown'))
                    raise Paused('unknown','测试终态未知')
            for changed in (False,True):
                with patch('shared_weeks.policy',return_value='changed') if changed else patch('shared_weeks.policy',wraps=__import__('runtime').policy):
                    with self.assertRaises(Paused) as ex:
                        with slot(root/'shared',p,root/'second'):self.fail('unknown不允许换目录重发')
                    self.assertEqual('unknown',ex.exception.state)

    def test_explicit_resolution_preserves_disagreement_and_never_repeats_model(self):
        class Disputed(FixtureClient):
            def identify(self,p,path):return json.loads(path.read_text()) if path.exists() else super().identify(p,path)
            def call(self,u,path):return json.loads(path.read_text()) if path.exists() else super().call(u,path)
            def audit_review(self,u,r,path):
                result=super().audit_review(u,r,path)
                v=json.loads(result['response']['output'][0]['content'][0]['text']);v['items']['P2'].update(verdict='revise',problem_codes=['synthetic-dispute'])
                return record(v,path)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);client=Disputed();p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
            with self.assertRaises(Paused):evaluate_week(p,root/'week',client)
            week=root/'week';u=json.loads((week/'unit.json').read_text());review=json.loads((week/'review.json').read_text())['value']
            original=(week/'audit.receipt.json').read_bytes();calls=client.calls
            fixture=FixtureClient();audit=json.loads(fixture.audit_review(u,review,root/'synthetic-audit.receipt.json')['response']['output'][0]['content'][0]['text'])
            proof=dict(parent=parent_proof(week,u),reviewer='合成测试核对者',reviewer_kind='codex',reason='仅验证逐项处置合同，非真实员工评分',
                review=review,assessment=audit)
            atomic(week/'resolution-proof.json',proof);atomic(week/'review-resolution.json',reference(week/'resolution-proof.json'))
            result=evaluate_week(p,week,client)
            self.assertEqual('80.0',result['reports'][0]['score']);self.assertEqual(calls,client.calls)
            self.assertEqual(original,(week/'audit.receipt.json').read_bytes())
            proof['assessment']['items']['P2']['verdict']='unresolved';atomic(week/'resolution-proof.json',proof)
            with self.assertRaises(ValueError):resolution(week,u)
            proof['parent']['model_receipt']=reference(week/'review.receipt.json')
            atomic(week/'review.receipt.json',dict(status='unknown'))
            with self.assertRaises(ValueError):resolution(week,u)

    def test_reasoned_revision_preserves_original_and_unknown_cannot_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
            failed=root/'failed';failed.mkdir();atomic(failed/'packet.json',p)
            atomic(failed/'review.receipt.json',dict(status='rejected'))
            revised=revise(failed,'合成核对者','明确失败，测试新修订与原结果并存')
            self.assertEqual(revised,selected(failed));self.assertEqual('rejected',json.loads((failed/'review.receipt.json').read_text())['status'])
            self.assertTrue((revised/'revision.json').exists())
            atomic(failed/'review.receipt.json',dict(status='unknown'))
            with self.assertRaises(ValueError):revise(failed,'合成核对者','不能绕过unknown')
            with self.assertRaises(ValueError):selected(failed)

    def test_code_upgrade_can_revision_known_failure_without_accepting_historical_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            failed=Path(tmp)/'failed';failed.mkdir()
            p=packet('2026-09-07T00:00:00+08:00','2026-09-14T00:00:00+08:00')
            atomic(failed/'packet.json',p)
            with patch('review_control.policy',return_value='a'*64):
                first=revise(failed,'合成核对者','首个明确修订')
            atomic(first/'packet.json',p)
            atomic(first/'input.json',dict(policy_sha256='a'*64))
            atomic(first/'review.receipt.json',dict(status='received',response=dict(status='completed')))
            before={str(q):q.read_bytes() for q in first.iterdir() if q.is_file()}
            with patch('review_control.policy',return_value='b'*64):
                with self.assertRaises(ValueError):selected(failed)
                second=revise(failed,'合成核对者','代码修复后新合同重新评审，旧候选保留')
                self.assertEqual(second,selected(failed))
            self.assertNotEqual(first,second)
            self.assertEqual(before,{str(q):q.read_bytes() for q in first.iterdir() if q.is_file()})
            # 历史输入与声明不符仍阻断，不能凭有理由修订绕过签名。
            atomic(first/'input.json',dict(policy_sha256='c'*64))
            with self.assertRaises(ValueError):revise(failed,'合成核对者','不允许篡改历史后修订')


if __name__=='__main__':unittest.main()
