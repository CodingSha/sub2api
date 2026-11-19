import copy
import json
import unittest
from task_evidence import task_records, attach_task_review, unit_digest, sha


def fixture():
    def ev(key,actor,text,request='r1',**extra):
        return dict(event=key,actor_class=actor,text=text,at='2026-09-08T10:00:00+08:00',
            session_id='s',request_id=request,human_spans=[[0,len(text)]] if actor=='human' else [],**extra)
    events=[ev('h1','human','请核对字段'),ev('a1','assistant',''),
            ev('h2','human','请核对字段',request='r2'),ev('a2','assistant','已核对',request='r2'),
            ev('other','assistant','其他任务',request='r3')]
    return dict(key='week',periods=[dict(start='2026-09-07T00:00:00+08:00',end='2026-09-14T00:00:00+08:00')],
        events=events,person_events=['h1','h2'],source_keys=['task'],source_units=[dict(key='task',title='核对字段',purpose='确保接口一致',person_events=['h1','h2'])])


class TaskEvidenceTests(unittest.TestCase):
    def test_nul_is_visible_only_in_display_and_has_its_own_disclosure(self):
        u=fixture();raw='AI反馈\x00保留正文'
        u['events'][1]['text']=raw;before=copy.deepcopy(u)
        _,records,_,_=task_records(u,'task')
        displayed=next(r for r in records if r['id']=='a1')
        self.assertEqual('AI反馈〔NUL控制字符〕保留正文',displayed['text'])
        self.assertEqual(sha(raw),displayed['source_hash'])
        self.assertEqual(sha(displayed['text']),displayed['text_hash'])
        self.assertNotEqual(displayed['source_hash'],displayed['text_hash'])
        self.assertTrue(any('NUL' in text for text in displayed['limitations']))
        self.assertFalse(any('凭证' in text for text in displayed['limitations']))
        self.assertEqual(before,u)

    def test_literal_nul_escape_and_other_controls_are_not_rewritten(self):
        u=fixture();raw='字面\\u0000\x01\t\n\r结尾';u['events'][1]['text']=raw
        _,records,_,_=task_records(u,'task')
        displayed=next(r for r in records if r['id']=='a1')
        self.assertEqual(raw,displayed['text'])
        self.assertEqual(displayed['source_hash'],displayed['text_hash'])
        self.assertFalse(any('NUL' in text for text in displayed['limitations']))

    def native_unit(self, response='已保存的回复'):
        from protocol_events import extract
        from pipeline import assemble
        messages = [dict(role=role, content=text) for role, text in (
            ('system', '不能公开的系统提示'), ('developer', '不能公开的开发指令'),
            ('user', '归属未确认的历史输入'), ('assistant', '此前AI提出的检查建议'),
            ('tool', '工具上下文'), ('user', '注入背景：请核对字段'))]
        frame = dict(frame='audit:1:request:0', field='request', text=json.dumps(dict(messages=messages)),
            at='2026-09-08T10:00:00+08:00', timestamp_unknown=False, truncated=False,
            session_id='s', request_id='r', user_id=2, text_sha256='a'*64)
        packet = extract(dict(frames=[frame, dict(frame, frame='audit:1:response:0',
            field='response', text=response)], issues=[], snapshot_at=frame['at']))
        packet.update(user_id='2', since='2026-09-07T00:00:00+08:00', until='2026-09-14T00:00:00+08:00')
        candidates = [e for e in packet['events'] if e['actor_class'] == 'candidate_user']
        identified = dict(materials=[dict(event_id=e['event'], classification='human' if i else 'unresolved',
            human_quotes=['请核对字段'] if i else [], reason='合成归属核查') for i,e in enumerate(candidates)],
            goals=[dict(goal_id='g', title='核对字段', purpose='确认一致', boundary='confirmed',
                boundary_reason='明确核对对象', event_ids=[e['event'] for e in packet['events']])], unassigned_events=[])
        return assemble(packet, identified)

    def test_native_protocol_and_assembly_keep_assistant_without_exposing_unconfirmed_input(self):
        u = self.native_unit()
        _, records, _, status = task_records(u, u['source_keys'][0])
        self.assertEqual(4, len(records))
        self.assertEqual(2, sum(r['role'] == 'assistant' for r in records))
        self.assertEqual(1, sum(r['role'] == 'user' for r in records))
        body = str(records)
        for hidden in ('不能公开', '归属未确认的历史输入', '注入背景'):
            self.assertNotIn(hidden, body)
        self.assertIn('此前AI提出的检查建议', body)
        self.assertIn('已保存的回复', body)
        self.assertEqual('source_incomplete', status)
        history = next(r for r in records if r['text'] == '此前AI提出的检查建议')
        self.assertIsNone(history['occurred_at'])
        tool = next(r for r in records if r['text'] == '工具上下文')
        self.assertEqual(('context', 'unattributed'), (tool['role'], tool['origin']))
        self.assertTrue(tool['limitations'])

    def test_native_empty_or_raw_sse_response_kept_with_limitations_without_parsing(self):
        for raw in ('', 'data: {"delta":"原样响应"}\n\ndata: [DONE]\n'):
            u = self.native_unit(response=raw)
            _, records, _, status = task_records(u, u['source_keys'][0])
            response = next(r for r in records if r['id'] == 'audit:1:response:0')
            self.assertEqual(raw, response['text'])
            self.assertEqual('assistant', response['role'])
            self.assertTrue(response['limitations'])
            self.assertEqual('source_incomplete', status)

    def test_native_protocol_object_is_not_published_as_dialogue(self):
        u = self.native_unit()
        e = next(e for e in u['events'] if e.get('protocol_role') == 'assistant')
        e.update(content_kind='protocol_object', text='不能冒充自然文本的协议对象')
        _, records, limits, status = task_records(u, u['source_keys'][0])
        self.assertNotIn(e['event'], {r['id'] for r in records})
        self.assertTrue(any('协议对象' in limit for limit in limits))
        self.assertEqual('source_incomplete', status)

    def test_same_request_other_task_human_is_excluded(self):
        u = fixture()
        u['events'].append(dict(u['events'][0], event='foreign-person', text='另一任务本人输入'))
        u['person_events'].append('foreign-person')
        self.assertNotIn('foreign-person', {r['id'] for r in task_records(u, 'task')[1]})

    def test_keep_real_repeats_empty_response_and_exclude_unrelated(self):
        _,records,_,status=task_records(fixture(),'task')
        self.assertEqual({r['id'] for r in records},{'h1','h2','a1','a2'})
        self.assertEqual(status,'source_incomplete')
        self.assertTrue(next(r for r in records if r['id']=='a1')['limitations'])

    def test_same_source_alias_is_not_a_second_action(self):
        u=fixture();u['events'][0]['event_id']='original'
        alias=dict(u['events'][0],event='alias',actor_class='source_context')
        u['events'].append(alias)
        self.assertEqual(len(task_records(u,'task')[1]),4)

    def test_no_system_or_injected_background_or_key_on_page(self):
        u=fixture();event=u['events'][0];event['text']='秘密系统背景:真实输入 sk-'+'a'*32
        event['human_spans']=[[7,len(event['text'])]]
        u['events'].append(dict(u['events'][1],event='sys',actor_class='system',text='系统提示词'))
        _,records,_,_=task_records(u,'task')
        body=str(records)
        self.assertNotIn('秘密系统背景',body)
        self.assertNotIn('系统提示词',body)
        self.assertNotIn('sk-'+'a'*32,body)
        self.assertIn('[已脱敏]',body)

    def test_unknown_reply_not_assigned_to_every_task(self):
        u=fixture();u['events'].append(dict(u['events'][-1],event='unknown',response_timestamp_unknown=True))
        self.assertNotIn('unknown',{r['id'] for r in task_records(u,'task')[1]})

    def test_credentials_in_chinese_server_account_sentence_are_redacted(self):
        u=fixture();event=u['events'][0]
        event['text']='服务器ssh账密是demo-user/demo-pass，你先排查。'
        event['human_spans']=[[0,len(event['text'])]]
        record=next(r for r in task_records(u,'task')[1] if r['id']=='h1')
        self.assertNotIn('demo-user/demo-pass',record['text'])
        self.assertEqual(record['text'],'服务器ssh账密是[已脱敏]，你先排查。')
        self.assertIn('记录中的凭证已脱敏。',record['limitations'])

    def test_present_record_with_unknown_timestamp_requires_incomplete_disclosure(self):
        u=fixture();u['events'][1]['text']='已收到，尚未验收'
        u['events'][1]['response_timestamp_unknown']=True
        _,records,_,status=task_records(u,'task')
        self.assertEqual(status,'source_incomplete')
        self.assertIsNone(next(r for r in records if r['id']=='a1')['occurred_at'])

    def test_analysis_never_filled_by_exporter(self):
        u=fixture();r=dict(user_email='one@test',period_type='week',source_digest='frozen')
        authored=dict(source_digest='frozen',unit_digest=unit_digest(u),user_email='one@test',reviewer='codex_direct_self_review',
            status='case_checked',checked_task_keys=['task'],scope_note='仅核案例',cases=[dict(task_key='task',outcome='结果未核',claims=[dict(text='本人提出核对要求',record_ids=['foreign'],item_codes=['J2'])])])
        with self.assertRaises(ValueError):attach_task_review(r,u,authored)
        authored['cases'][0]['claims'][0]['record_ids']=['h1']
        authored['cases'][0]['claims'][0]['assessment'] = dict(kind='support', reason='本人明确要求核对字段，支持核验安排，不代表已经完成核验。')
        attach_task_review(r,u,authored)
        self.assertEqual(r['task_review']['week_judgments'],[])
        self.assertEqual(r['task_review']['cases'][0]['claims'][0]['assessment'],authored['cases'][0]['claims'][0]['assessment'])
        altered=copy.deepcopy(u);altered['events'][0]['text']='变更来源'
        with self.assertRaises(ValueError):attach_task_review(r,altered,authored)

    def test_model_attachment_keeps_score_and_requires_distinct_review(self):
        u=fixture();r=dict(user_email='one@test',period_type='week',source_digest='frozen',score=80,items=[dict(score=80,reason='原理由')])
        before=copy.deepcopy(r)
        a=dict(source_digest='frozen',unit_digest=unit_digest(u),user_email='one@test',reviewer='model_generated_separately_reviewed',
            generation_receipt_digest='a'*64,review_receipt_digest='b'*64,status='case_checked',
            checked_task_keys=['task'],scope_note='仅核代表案例，整周依据未重核',cases=[dict(task_key='task',outcome='尚无最终验收',claims=[dict(text='本人要求核对字段',record_ids=['h1'],item_codes=['J2'],assessment=dict(kind='support',reason='有核验要求，尚无核验结果'))])])
        for change in [dict(review_receipt_digest='a'*64),dict(review_receipt_digest='invalid'),dict(status='week_checked'),dict(checked_task_keys=['task','other'])]:
            with self.assertRaises(ValueError):attach_task_review(copy.deepcopy(r),u,dict(a,**change))
        attach_task_review(r,u,a)
        self.assertEqual({k:v for k,v in r.items() if k!='task_review'},before)
        with self.assertRaises(ValueError):attach_task_review(r,u,a)


if __name__=='__main__':unittest.main()
