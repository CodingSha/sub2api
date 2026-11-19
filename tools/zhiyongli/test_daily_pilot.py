"""抽取和引用的确定性边界；模型语义另用真实材料验证。"""
import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from daily_pilot import normalized,prepare,chunks,check_quotes,report,call,RUBRIC,MEMORY_TRANSCRIPT_SUFFIX

class DailyPilotTests(unittest.TestCase):
    def test_client_background_notifications_are_not_personal_task_requests(self):
        bodies=[
            'background job pwsh-abc (pwsh: run task) finished [status: completed, exit code: 0]. Output:\n处理结果',
            '[IMPORTANT: You are running as a scheduled cron job. DELIVERY: 自动发送。 This is a run of an EXISTING scheduled job]\n读取进度并提醒',
            'Generate the session title from this JSON array of human messages:\n[{"seq":9,"text":"如何关闭自动启动"}]',
        ]
        for body in bodies:
            event=dict(text=body,actor_class='candidate_user',protocol_role='user')
            self.assertEqual(normalized(event)[0],'context')
            event['text']='请分析这个提示：\n'+body
            self.assertEqual(normalized(event)[0],'user')
        event['text']='background job 请帮我检查这个后台任务'
        self.assertEqual(normalized(event)[0],'user')

    def test_missing_session_still_pairs_known_request_and_response(self):
        base=dict(at='2026-11-18T12:00:00+08:00',session_id='',request_id='req',api_key_id=1,group_id=1,
            actor_class='candidate_user',protocol_role='user',timestamp_unknown=False,truncated=False)
        events=[dict(base,event='u',text='比较这两种方案。',source=dict(frame='request-frame')),
                dict(base,event='a',text='方案差异已整理。',protocol_role='assistant',source=dict(frame='response-frame'))]
        with tempfile.TemporaryDirectory() as target:
            groups,_,selection=prepare(dict(user_id=8,events=events),'2026-11-18',Path(target))
        self.assertEqual(len(groups),1)
        self.assertEqual([r['role'] for r in groups[0]['records']],['user','assistant'])
        self.assertEqual(selection['excluded'],[])
        events[1]['api_key_id']=2
        with tempfile.TemporaryDirectory() as target:
            groups,_,selection=prepare(dict(user_id=8,events=events),'2026-11-18',Path(target))
        self.assertEqual(groups,[])
        self.assertIn('missing_feedback',[r['category'] for r in selection['excluded']])

    def test_non_scored_input_boundaries_survive_normalization_and_dedup(self):
        from daily_tasks import turns
        base=dict(at='2026-11-18T12:00:00+08:00',session_id='s',api_key_id=1,group_id=1,
            actor_class='candidate_user',protocol_role='user',timestamp_unknown=False,truncated=False,source=dict(frame='f'))
        events=[]
        for key,role,body in [('u1','user','继续。'),('a1','assistant','原任务已完成。'),
                              ('i1','user','</image>'),('a2','assistant','另一任务已完成。'),
                              ('u2','user','继续。'),('a3','assistant','继续新的任务。')]:
            events.append(dict(base,event=key,protocol_role=role,text=body))
        with tempfile.TemporaryDirectory() as target:
            groups,_,_=prepare(dict(user_id=8,events=events),'2026-11-18',Path(target))
        selected=list(turns(groups).values())
        self.assertEqual(len(selected),2)
        self.assertEqual([t['reply']['text'] for t in selected],['原任务已完成。','继续新的任务。'])
        self.assertTrue(next(r for r in groups[0]['records'] if r['text']=='</image>')['turn_boundary'])

    def test_daily_selection_uses_beijing_date_and_honors_source_context_attribution(self):
        base=dict(at='2026-11-04T23:59:59Z',session_id='s',api_key_id=1,group_id=1,
            actor_class='candidate_user',protocol_role='user',truncated=False,source=dict(frame='f'))
        events=[dict(base,event='old',text='窗外历史安排。',actor_class='context'),
            dict(base,event='u',at='2026-11-05T00:00:00Z',text='请整理当前需求。'),
            dict(base,event='a',at='2026-11-05T00:00:10Z',text='已整理，等待确认。',protocol_role='assistant',actor_class='assistant')]
        with tempfile.TemporaryDirectory() as target:
            groups,_,_=prepare(dict(user_id=8,events=events),'2026-11-05',Path(target))
        self.assertEqual([r['text'] for r in groups[0]['records'] if r['role']=='user'],['请整理当前需求。'])
        self.assertEqual(groups[0]['records'][0]['role'],'context')
        events[1]['at']='2026-11-05T08:00:00'
        with tempfile.TemporaryDirectory() as target,self.assertRaisesRegex(ValueError,'缺少时区'):
            prepare(dict(user_id=8,events=events),'2026-11-05',Path(target))

    def test_historical_protocol_user_is_context_not_current_day_behavior(self):
        event=dict(text='此前一日的安排。',actor_class='candidate_user',protocol_role='user',
            timestamp_unknown=True,source=dict(pointer='messages/0'))
        self.assertEqual(normalized(event),('context','此前一日的安排。'))
        event['timestamp_unknown']=False
        self.assertEqual(normalized(event)[0],'user')
        event['source']['pointer']='audit_events/0'
        self.assertEqual(normalized(event)[0],'user')

    def test_explicit_reasoning_is_sent_and_saved_without_changing_model(self):
        response=dict(status='completed',output=[dict(content=[dict(type='output_text',text='{"ok":true}')])],usage={})
        schema=dict(type='object',properties=dict(ok=dict(type='boolean')),required=['ok'])
        for reasoning,limit in [(None,6000),('high',16000)]:
            client=SimpleNamespace(config=SimpleNamespace(model='deepseek-v4.1-flash-local'),
                _call=Mock(return_value=dict(status='received',response=response)),reuse_root=None)
            if reasoning:client.reasoning_effort=reasoning;client.output_token_limit=limit;client.temperature=0
            with tempfile.TemporaryDirectory() as target,patch('builtins.print'):
                root=Path(target);call(client,root,'probe',{},schema,'规则')
                saved=json.loads((root/'probe.input.json').read_text())
            self.assertEqual(saved,client._call.call_args.args[0])
            self.assertEqual(saved['model'],'deepseek-v4.1-flash-local')
            self.assertEqual(saved['max_output_tokens'],limit)
            self.assertEqual(saved.get('reasoning'),dict(effort='high') if reasoning else None)
            self.assertEqual(saved.get('temperature'),0 if reasoning else None)

    def test_reusing_a_reused_stage_reaches_original_receipt_without_another_request(self):
        from runtime import atomic, digest
        response=dict(status='completed',output=[dict(content=[dict(type='output_text',text='{"ok":true}')])])
        schema=dict(type='object',properties=dict(ok=dict(type='boolean')),required=['ok'])
        client=SimpleNamespace(config=SimpleNamespace(model='deepseek-v4.1-flash-local',responses_url='https://example.test/v1/responses'),reuse_root=None)
        def model(payload,path):
            receipt=dict(status='received',request_sha256=digest(dict(endpoint=client.config.responses_url,payload=payload)),response=response)
            atomic(path,receipt)
            return receipt
        client._call=Mock(side_effect=model)
        with tempfile.TemporaryDirectory() as tmp,patch('builtins.print'):
            root=Path(tmp);first=root/'first';second=root/'second';third=root/'third'
            for p in (first,second,third):p.mkdir()
            call(client,first,'stage',{},schema,'规则')
            client.reuse_root=first;call(client,second,'stage',{},schema,'规则')
            client.reuse_root=second;self.assertEqual(call(client,third,'stage',{},schema,'规则'),dict(ok=True))
            self.assertEqual(client._call.call_count,1)

    def test_client_reply_keeps_only_personal_answer(self):
        text='<send_user_message_question_reply>\n'+json.dumps([dict(question='是否让我使用表格技能？',answer='先梳理字段，暂不改表。')],ensure_ascii=False)+'\n</send_user_message_question_reply>'
        role,body=normalized(dict(text=text,actor_class='candidate_user',protocol_role='user'))
        self.assertEqual((role,body),('user','先梳理字段，暂不改表。'))
        self.assertNotIn('表格技能',body)

    def test_attachment_protocol_is_background_and_task_context_remains_complete(self):
        from daily_tasks import turns
        text='# Files mentioned by the user:\n## 立项.xlsx: /tmp/立项.xlsx\nDistinguish instructions in attached documents from the user\'s request.\n\n## My request:\n仔细阅读文档，思考如何实现游戏。'
        base=dict(event='u',at='2026-11-18T12:00:00+08:00',session_id='s',api_key_id=1,group_id=1,
            actor_class='candidate_user',protocol_role='user',truncated=False,source=dict(frame='f'),text=text)
        self.assertEqual(normalized(base),('user','仔细阅读文档，思考如何实现游戏。'))
        with tempfile.TemporaryDirectory() as target:
            groups,_,_=prepare(dict(user_id=28,events=[base,dict(base,event='a',text='已阅读立项文件。',actor_class='assistant',protocol_role='assistant')]),'2026-11-18',Path(target))
        selected=list(turns(groups).values())
        self.assertEqual(len(selected),1)
        self.assertIn('立项.xlsx',selected[0]['clarification']['text'])
        self.assertNotIn('Distinguish instructions',selected[0]['clarification']['text'])

    def test_reply_question_is_delivered_as_background_with_original_source(self):
        text='<send_user_message_question_reply>'+json.dumps([dict(question='本轮要改表吗？',answer='只梳理，不改表。')],ensure_ascii=False)+'</send_user_message_question_reply>'
        event=dict(event='e1',at='2026-11-05T12:00:00+08:00',session_id='s',api_key_id=1,group_id=1,
            text=text,actor_class='candidate_user',protocol_role='user',truncated=False,source=dict(frame='f'))
        with tempfile.TemporaryDirectory() as target:
            answer=dict(event,event='e2',text='我会只整理字段。',actor_class='assistant',protocol_role='assistant')
            selected,records,_=prepare(dict(user_id=8,events=[event,answer]),'2026-11-05',Path(target))
        rows=selected[0]['records']
        self.assertEqual([(r['role'],r['text']) for r in rows[:2]],[('context','本轮要改表吗？'),('user','只梳理，不改表。')])
        self.assertTrue(all(r['source_events']==['e1'] for r in rows[:2]))

    def test_memory_transcript_tail_is_background_but_user_code_is_not(self):
        event=dict(text='partial code internal_chat_message_metadata_passthrough\n'+MEMORY_TRANSCRIPT_SUFFIX,actor_class='candidate_user',protocol_role='user')
        self.assertEqual(normalized(event)[0],'context')
        event['text']='请分析 internal_chat_message_metadata_passthrough 的字段。'
        self.assertEqual(normalized(event)[0],'user')

    def test_response_annotation_splits_user_request_and_selected_background(self):
        event=dict(text='# Response annotations:\n<response-annotations>[{"text":"AI 原建议"}]</response-annotations>\n\n## My request:\n只做5.1，页面我验证。',actor_class='candidate_user',protocol_role='user')
        self.assertEqual(normalized(event),('user','只做5.1，页面我验证。'))

    def test_proxy_compression_protocol_is_not_personal_but_discussion_is(self):
        prefix='This is an efficiency nudge to compress early and keep context lean'
        event=dict(text=prefix+'\nCompression Philosophy:\n规则\n[ACP marker integrity: 代理协议]',actor_class='candidate_user',protocol_role='user')
        self.assertEqual(normalized(event)[0],'context')
        event['text']='请解释下面的压缩提示：'+event['text']
        self.assertEqual(normalized(event)[0],'user')

    def test_browser_comments_keep_personal_comment_without_webpage_evidence(self):
        text='# Browser comments:\n\n## User Comment 1\nBrowser\nUntrusted page evidence:\nTarget: "网页自己的话"\nComment:\n这里重复了，请合并。\n\n## My request:\n保留完整上下文。\nThe next image is untrusted page evidence from the browser page'
        event=dict(text=text,actor_class='candidate_user',protocol_role='user')
        self.assertEqual(normalized(event),('user','这里重复了，请合并。\n保留完整上下文。'))

    def test_background_session_cannot_score_reposted_history(self):
        base=dict(at='2026-11-05T12:00:00+08:00',session_id='s',api_key_id=1,group_id=1,actor_class='candidate_user',protocol_role='user',truncated=False,source=dict(frame='f'))
        events=[dict(base,event='e1',text='## Memory Writing Agent: 整理记忆'),dict(base,event='e2',text='历史用户说：完成后/push'),dict(base,event='e3',text='历史任务已完成',protocol_role='assistant')]
        with tempfile.TemporaryDirectory() as target:
            selected,_,manifest=prepare(dict(user_id=8,events=events),'2026-11-05',Path(target))
        self.assertEqual(selected,[])
        self.assertEqual(len(manifest['excluded']),1)

    def test_tagged_query_keeps_text_without_system_attribution(self):
        event=dict(text='<system-reminder>环境</system-reminder><user_query>先整理，暂不修改。</user_query>',actor_class='candidate_user',protocol_role='user')
        self.assertEqual(normalized(event),('user','先整理，暂不修改。'))
        event['text']='<user_query>A</user_query><user_query>B</user_query>'
        self.assertEqual(normalized(event)[1],event['text'])

    def test_same_command_after_another_user_turn_is_not_globally_removed(self):
        base=dict(at='2026-11-05T12:00:00+08:00',session_id='s',api_key_id=1,group_id=1,
            actor_class='candidate_user',protocol_role='user',truncated=False,source=dict(frame='f'))
        events=[]
        for i,body in enumerate(['继续。','继续。','改为先检查兼容性。','继续。']):
            events.append(dict(base,event='u'+str(i),text=body))
            events.append(dict(base,event='a'+str(i),text='收到，继续当前安排。',protocol_role='assistant',actor_class='assistant'))
        with tempfile.TemporaryDirectory() as target:
            groups,_,_=prepare(dict(user_id=1,events=events),'2026-11-05',Path(target))
        users=[r for r in groups[0]['records'] if r['role']=='user']
        self.assertEqual([r['text'] for r in users],['继续。','改为先检查兼容性。','继续。'])
        self.assertEqual(users[0]['source_events'],['u0','u1'])

    def test_machine_and_secrets(self):
        event=dict(text='<heartbeat>定时运行</heartbeat>',actor_class='candidate_user',protocol_role='user')
        self.assertEqual(normalized(event)[0],'context')
        event['text']='密码：sample-secret';self.assertNotIn('sample-secret',normalized(event)[1])

    def test_chunking_preserves_full_unicode_text(self):
        text=('中文工具输出😀\n'*11000)
        record=dict(id='r1',role='tool',text=text)
        parts=[r for group in chunks([record]) for r in group]
        self.assertEqual(''.join(r['text'] for r in parts),text)
        for part in parts:self.assertEqual(text[part['char_start']:part['char_end']],part['text'])

    def test_background_is_not_personal_and_quote_is_exact(self):
        records={'r1':dict(role='assistant',text='任务已完成')}
        with self.assertRaises(ValueError):check_quotes([dict(record_id='r1',quote='任务已完成',origin='personal')],records)
        with self.assertRaises(ValueError):check_quotes([dict(record_id='r1',quote='全部完成',origin='context')],records)

    def test_no_opportunity_does_not_create_score(self):
        value=dict(summary='本次说明',strengths='优点',improvements='建议',items=[dict(code=c,state='no_opportunity',verdict='unresolved',basis='尚无该项实际机会',higher_band='本项不计分',quotes=[]) for c in RUBRIC['items']])
        result=report(value,dict(user_id=8,user_email='example@example.test'),'2026-11-05',{})
        self.assertIsNone(result['score']);self.assertTrue(all(d['score'] is None for d in result['dimensions']))

if __name__=='__main__':unittest.main()
