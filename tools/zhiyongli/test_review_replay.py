"""回放范围与服务Key来源合同；不以模型分数当作质量真值。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from protocol_events import extract
from review_replay import offline, select, model_run, bind_contract
from source_frames import _row_frames
from test_source_index import source


class ReplayTests(unittest.TestCase):
    def test_identification_reuse_rejects_unknown_and_another_person(self):
        from review_replay import seed_identification
        from runtime import atomic
        with tempfile.TemporaryDirectory() as directory:
            parent=Path(directory)/'parent';parent.mkdir();root=Path(directory)/'new';root.mkdir()
            packet=source();atomic(parent/'packet.json',packet);atomic(parent/'identified.json',{})
            receipt=parent/'identify.receipt.json';atomic(receipt,dict(status='unknown'))
            with self.assertRaisesRegex(ValueError,'终态'):seed_identification(packet,root,parent)
            atomic(receipt,dict(status='received'))
            other=dict(packet,user_id='other')
            with self.assertRaisesRegex(ValueError,'另一冻结包'):seed_identification(other,root,parent)
            seed_identification(packet,root,parent)
            self.assertEqual(receipt.read_bytes(),(root/'run/identify.receipt.json').read_bytes())
            self.assertFalse((root/'run/review.receipt.json').exists())

    def test_scoring_phase_keeps_original_deadline_and_cannot_reset_on_resume(self):
        from review_replay import phase_deadline
        from runtime import atomic
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory);root=state/'new';root.mkdir()
            atomic(state/'deadline.json',dict(started_at=1,max_seconds=10))
            original=(state/'deadline.json').read_bytes()
            first=phase_deadline(state,root,dict(policy_sha256='fixed'),'scoring-contract',900)
            self.assertEqual(first,phase_deadline(state,root,dict(policy_sha256='fixed'),'scoring-contract',900))
            self.assertEqual(original,(state/'deadline.json').read_bytes())
            with self.assertRaisesRegex(ValueError,'不能改变'):
                phase_deadline(state,root,dict(policy_sha256='fixed'),'scoring-contract',901)

    def test_explicit_code_revision_retains_original_contract_and_budget(self):
        from runtime import digest
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'original';state.mkdir()
            revised=Path(directory)/'revised';revised.mkdir()
            old=dict(settings=dict(max_requests=96),model='fixed',endpoint='fixed',max_seconds=3600,policy_sha256='old')
            bind_contract(state,state,old)
            original=(state/'model-contract.json').read_bytes()
            new=dict(old,policy_sha256='fixed')
            with self.assertRaisesRegex(ValueError,'显式指定'):
                bind_contract(state,revised,new)
            with self.assertRaisesRegex(ValueError,'新的执行目录'):
                bind_contract(state,state,new,digest(old))
            with self.assertRaisesRegex(ValueError,'不能改变'):
                bind_contract(state,revised,dict(new,settings=dict(max_requests=97)),digest(old))
            bind_contract(state,revised,new,digest(old))
            bind_contract(state,revised,new)
            self.assertEqual(original,(state/'model-contract.json').read_bytes())
            self.assertEqual(new,json.loads((revised/'model-contract.json').read_text()))

    def test_expired_deadline_survives_resume_without_reserving_or_sending(self):
        from runtime import atomic
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            deadline=dict(started_at=10,max_seconds=5)
            atomic(root/'deadline.json',deadline)
            config=SimpleNamespace(responses_url='https://ai.apsatcom.com/v1/responses',
                model='deepseek-v4.1-flash-local',timeout=180)
            def attempt(packet,where,client,progress):
                with client.send_slot({},where/'never.receipt.json','a'*64,b'{}'):
                    raise AssertionError('过期后不能发送')
            with patch('model_config.ModelConfig.load',return_value=config),patch('pipeline.evaluate_week',side_effect=attempt):
                for _ in range(2):
                    result=model_run(source(),root,'unused.json',8,max_seconds=5)
                    self.assertEqual('acceptance_deadline',result['reason_code'])
                    self.assertEqual(0,result['flow']['request_budget']['used'])
                self.assertEqual(deadline,json.loads((root/'deadline.json').read_text()))

    def test_new_sample_cannot_raise_shared_request_limit(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'state'
            config=SimpleNamespace(responses_url='https://ai.apsatcom.com/v1/responses',model='deepseek-v4.1-flash-local')
            with patch('model_config.ModelConfig.load',return_value=config), \
                    patch('model_flow.ModelFlow',side_effect=RuntimeError('禁止初始化发送器')) as flow:
                for name,maximum,error in [('first',8,RuntimeError),('second',9,ValueError)]:
                    root=Path(directory)/name;root.mkdir()
                    with self.assertRaises(error):
                        model_run(source(),root,'unused.json',maximum,state)
                self.assertEqual(1,flow.call_count)

    def test_engineering_budget_revision_preserves_original_ledger_and_contract(self):
        from runtime import digest
        from model_flow import ModelFlow
        with tempfile.TemporaryDirectory() as directory:
            state=Path(directory)/'state';state.mkdir();root=Path(directory)/'new';root.mkdir()
            settings=dict(version=1,budget_key='synthetic-budget',approved_by='合成确认人',reason='合成测试',
                max_concurrency=1,requests_per_minute=10,reserved_tokens_per_minute=1000,max_requests=96,
                transient_failure_limit=1,cooldown_seconds=60)
            old=dict(settings=settings,policy_sha256='old',endpoint='fixed',model='fixed')
            bind_contract(state,state,old)
            flow=ModelFlow(state/'flow',settings)
            with flow.connect() as db:
                db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    ('historical',settings['budget_key'],'sha','receipt',1,1,'unknown',2,None,'historical',None))
            original=(state/'model-contract.json').read_bytes()
            new=copy.deepcopy(old);new['settings']['max_requests']=160;new['policy_sha256']='new'
            bind_contract(state,root,new,digest(old),'继续已授权链路验收，修订本地工程上限，累计消耗保留')
            bind_contract(state,root,new)
            self.assertEqual(original,(state/'model-contract.json').read_bytes())
            revised=ModelFlow(state/'flow',new['settings'])
            self.assertEqual(1,revised.remaining_requests()['used'])
            audit=json.loads(next((state/'budget-revisions').glob('*.json')).read_text())
            self.assertEqual((96,160,1),(audit['previous_limit'],audit['new_limit'],audit['used_at_revision']))

    def test_existing_run_cannot_switch_shared_ledger_to_reset_budget(self):
        from runtime import atomic
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'run';root.mkdir()
            atomic(root/'model-state-ref.json',dict(state_root=str(Path(directory)/'original-state')))
            config=SimpleNamespace(responses_url='https://ai.apsatcom.com/v1/responses',model='deepseek-v4.1-flash-local')
            with patch('model_config.ModelConfig.load',return_value=config),patch('model_flow.ModelFlow') as flow:
                with self.assertRaises(ValueError):
                    model_run(source(),root,'unused.json',8,Path(directory)/'replacement-state')
                flow.assert_not_called()

    def test_untrusted_calendar_blocks_before_model_or_budget_initialization(self):
        packet=source();packet['workday_scope']={'registry_sha256':'not-trusted'}
        with tempfile.TemporaryDirectory() as root:
            with patch('workday_scope.validate',side_effect=ValueError('未登记日历')), \
                    patch('model_config.ModelConfig.load') as load:
                with self.assertRaisesRegex(ValueError,'未登记'):
                    model_run(packet,Path(root),'unused.json',2)
                load.assert_not_called()
                self.assertFalse((Path(root)/'flow').exists())

    def test_transport_key_identity_survives_all_protocol_roles_and_response(self):
        row=dict(id=1,user_id=2,api_key_id=9,session_id='same-user',request_id='req',
            created_at='2026-11-04T10:00:00+08:00',request_truncated=False,response_truncated=False,
            request_body=json.dumps(dict(messages=[dict(role='user',content='本人要求'),
                dict(role='tool',content='工具结果')])),response_body='响应背景')
        frames=[f for f,_ in _row_frames(row,dict(path='row.json',sha256='test'),None,None,None,False)]
        result=extract(dict(snapshot_at=row['created_at'],frames=frames,issues=[]))
        self.assertEqual(3,len(result['events']))
        self.assertTrue(all(e['api_key_id']==9 for e in result['events']))
        row.pop('api_key_id')
        frames=[f for f,_ in _row_frames(row,dict(path='row.json',sha256='test'),None,None,None,False)]
        unknown=extract(dict(snapshot_at=row['created_at'],frames=frames,issues=[]))
        self.assertTrue(all('api_key_id' not in e for e in unknown['events']))

    def test_missing_key_identity_refuses_filter_and_other_keys_of_same_user_remain(self):
        packet=source()
        with self.assertRaisesRegex(ValueError,'Key ID'):
            select(packet,excluded_keys=[9])
        original=copy.deepcopy(packet)
        for event,key in zip(packet['events'],[9,10,10]):
            event['api_key_id']=key
        selected=select(packet,excluded_keys=[9])
        self.assertEqual(['long','failure'],[e['event'] for e in selected['events']])
        self.assertEqual(3,len(packet['events']))
        self.assertEqual(original['user_id'],selected['user_id'])
        self.assertTrue(selected['replay_scope']['service_key_exclusion_verified'])
        packet['events'][0]['api_key_id']=None
        with self.assertRaises(ValueError):
            select(packet,excluded_keys=[9])

    def test_one_request_sample_is_explicit_and_ambiguous_identity_refused(self):
        packet=source();selected=select(packet,request_id='test')
        self.assertEqual(2,len(selected['events']))
        self.assertTrue(selected['replay_scope']['experiment_only'])
        self.assertFalse(selected['replay_scope']['service_key_exclusion_verified'])
        packet['events'][-1]['session_id']='different'
        with self.assertRaises(ValueError):
            select(packet,request_id='test')

    def test_source_key_id_survives_plain_and_protocol_requests_responses_and_metadata(self):
        row=dict(id=1,user_id=7,api_key_id=42,session_id='s',request_id='r',created_at='2026-11-16T10:00:00+08:00',
            request_body=json.dumps(dict(messages=[dict(role='user',content='实际输入')])),
            response_body='实际响应',request_truncated=False,response_truncated=False)
        ref=dict(path='row.json',sha256='test')
        frames=[f for f,_ in _row_frames(row,ref,None,None,None,False)]
        events=extract(dict(frames=frames,issues=[],snapshot_at=row['created_at']))['events']
        self.assertEqual([42,42],[e['api_key_id'] for e in events])
        self.assertTrue(all(f['api_key_id']==42 for f,_ in _row_frames(row,ref,None,None,None,True)))
        row['request_body']='非JSON的本次输入'
        frames=[f for f,_ in _row_frames(row,ref,None,None,None,False)]
        self.assertTrue(all(e['api_key_id']==42 for e in extract(dict(frames=frames,issues=[],snapshot_at=row['created_at']))['events']))

    def test_offline_reconstruction_and_branch_locations(self):
        packet=source()
        with tempfile.TemporaryDirectory() as directory:
            result=offline(packet,Path(directory))
            self.assertTrue(result['all_characters_verified'])
            self.assertEqual(0,result['source_index_model_calls'])
            self.assertFalse(result['semantic_acceptance'])
            self.assertGreater(result['source_index_fragments'],3)


if __name__=='__main__':
    unittest.main()
