"""会话原文保真、身份隔离、工具配对及有界取证，不能替代模型评分验收。"""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import digest
from session_evidence import SessionEvidence, captured_events, MAX_BYTES, model_tools


def event(eid, text, *, role='user', request='r1', session='s1', key=1):
    return dict(event=eid,text=text,protocol_role=role,source_user_id='2',
        actor_class='candidate_user' if role=='user' else 'context',request_id=request,
        session_id=session,api_key_id=key,at='2026-11-16T09:00:00+08:00',
        timestamp_unknown=False,timestamp_basis='audit_capture',truncated=False,
        source=dict(frame='audit:1:request:'+eid,pointer='captured_user'))


def packet(events):
    return dict(user_id='2',since='2026-11-16T00:00:00+08:00',until='2026-11-17T00:00:00+08:00',events=events)


class SessionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def build(self, events):
        store = SessionEvidence.build(packet(events), self.root/'store')
        self.addCleanup(store.close)
        return store

    def test_tool_kinds_and_response_context_survive_real_capture_contract(self):
        def frame(field, items):
            text=json.dumps(dict(audit_events=items),ensure_ascii=False)
            return dict(frame='audit:1:'+field+':0',field=field,text=text,
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),user_id='2',session_id='s',request_id='r',
                at='2026-11-16T09:00:00+08:00',timestamp_unknown=False,truncated=False,api_key_id=1,
                source=dict(path='objects/1.json',sha256='a'*64,field=field,index=0),group_id=3)
        source=dict(snapshot_at='2026-11-17T00:00:00+08:00',issues=[],frames=[
            frame('request',[dict(kind='tool_call',text='Tool: exec\nCall ID: call1\nType: function_call\n\n运行测试'),
                dict(kind='tool_result',text='Call ID: call1\nType: function_call_output\nStatus: error\n\n测试失败')]),
            frame('response',[dict(kind='assistant',text='尚未通过测试'),dict(kind='user',text='这是返回材料里的引文')])])
        actual=captured_events(source)
        self.assertTrue(all(e['source']['capture_source']['path']=='objects/1.json' for e in actual['events']))
        self.assertTrue(all(e['group_id']==3 for e in actual['events']))
        self.assertEqual(['tool_call','tool_result','assistant','user'],[e['audit_kind'] for e in actual['events']])
        store=self.build(actual['events']); sid=store.list_sessions()['items'][0]['session_key']
        call=store.list_calls(sid)['items'][0]
        self.assertEqual(['exec'],call['tools']);self.assertEqual(1,call['kinds']['tool_result'])
        rows=store.list_events(call['request_key'])['items']
        self.assertEqual(['call1','call1'],[r['tool']['call_id'] for r in rows[:2]])
        self.assertEqual(2,store.tool_trace(sid,'call1')['total'])
        self.assertTrue(all(e['actor_class']=='context' for e in actual['events']))

    def test_unknown_captured_event_keeps_complete_opaque_source(self):
        text=json.dumps(dict(audit_events=[dict(kind='future_type',text='不可静默丢掉的来源')]))
        frame=dict(frame='audit:1:request:0',field='request',text=text,text_sha256='x',
            user_id='2',session_id='s',request_id='r',at='2026-11-16T09:00:00+08:00',timestamp_unknown=False,truncated=False)
        result=captured_events(dict(snapshot_at=frame['at'],issues=[],frames=[frame]))
        self.assertIn(text,[e['text'] for e in result['events']])
        self.assertTrue(result['issues'])
        response=copy.deepcopy(frame)
        response.update(frame='audit:1:response:0',field='response')
        result=captured_events(dict(snapshot_at=frame['at'],issues=[],frames=[response]))
        self.assertEqual([text],[e['text'] for e in result['events']])
        self.assertEqual('protocol_payload',result['events'][0]['audit_kind'])
        self.assertEqual('context',result['events'][0]['actor_class'])
        self.assertEqual('opaque_response_audit_events',result['issues'][0]['code'])

    def test_same_session_id_under_distinct_keys_and_unknown_sessions_do_not_merge(self):
        store=self.build([event('a','同文',key=1),event('b','同文',key=2),
            event('c','同文',session=None,request=None),event('d','同文',session=None,request=None)])
        self.assertEqual(4,len(store.manifest['sessions']))
        self.assertEqual(4,len(store.manifest['requests']))
        self.assertEqual(1,store.db.execute('SELECT count(*) FROM bodies').fetchone()[0])
        self.assertEqual(4,store.manifest['events'])
        self.assertEqual(4,store.list_inputs()['total'])
        self.assertTrue(all(item['actor_class']=='candidate_user' for item in store.list_inputs()['items']))
        with self.assertRaises(ValueError):store.list_calls('unknown-session')

    def test_cross_person_rejected_and_repeat_real_submissions_retained(self):
        wrong=event('a','真实输入');wrong['source_user_id']='3'
        with self.assertRaises(ValueError):SessionEvidence.build(packet([wrong]),self.root/'wrong')
        store=self.build([event('a','继续',request='r1'),event('b','继续',request='r2')])
        sid=store.list_sessions()['items'][0]['session_key']
        self.assertEqual(2,store.list_calls(sid)['total'])

    def test_group_scope_and_complete_page_wrapping_are_preserved(self):
        a, b = event('a','相同会话'), event('b','相同会话')
        a['group_id'], b['group_id'] = 1, 2
        store = self.build([a, b])
        self.assertEqual(2,len(store.manifest['sessions']))
        # 容量包含标记和限制说明；临界情况拆页，不能在分页后追加而超限。
        rows = [dict(text='中文'*30) for _ in range(3)]
        with patch('session_evidence.MAX_BYTES', 500):
            first = store._page(rows,0,50,limitation='定位用途'*15)
            self.assertLessEqual(len(json.dumps(first,ensure_ascii=False).encode()),500)
            self.assertEqual(1,len(first['items']))
            last = store._page(rows,2,50,limitation='定位用途'*15)
            self.assertLessEqual(len(json.dumps(last,ensure_ascii=False).encode()),500)
            self.assertIsNone(last['next_cursor'])

    def test_unicode_long_body_and_wrapper_ranges_are_lossless_and_bounded(self):
        text='配置\n'*1000+'<user_query>检查这次失败</user_query>'+'日志😀\x00\n'*7000
        store=self.build([event('a',text)])
        sid=store.list_sessions()['items'][0]['session_key']
        rid=store.list_calls(sid)['items'][0]['request_key']
        item=store.list_events(rid)['items'][0]
        self.assertEqual(text,''.join(text[s['start']:s['end']] for s in item['sections']))
        cursor=0;parts=[]
        while True:
            result=store.read_event(item['event_key'],cursor,6000)
            self.assertLessEqual(len(json.dumps(result,ensure_ascii=False).encode()),MAX_BYTES)
            parts.append(result['text'])
            if result['next_start'] is None:break
            self.assertGreater(result['next_start'],cursor);cursor=result['next_start']
        self.assertEqual(text,''.join(parts))
        self.assertEqual('candidate_user',result['actor_class'])

    def test_directory_is_not_evidence_and_unread_text_cannot_be_cited(self):
        store=self.build([event('a','本人要求检查；后面包含实际失败')])
        sid=store.list_sessions()['items'][0]['session_key']
        rid=store.list_calls(sid)['items'][0]['request_key']
        row=store.list_events(rid)['items'][0]
        directory=self.root/'directory.json';read=self.root/'read.json'
        store.tool_call('list_events',dict(request_key=rid),directory)
        with self.assertRaises(ValueError):store.cite(directory,'本人要求')
        result=store.tool_call('read_event',dict(event_key=row['event_key'],start=0,max_characters=7),read)
        cite=store.cite(read,'本人要求')
        self.assertEqual([0,4],[cite['start'],cite['end']]);self.assertFalse(cite['attribution_confirmed'])
        with self.assertRaises(ValueError):store.cite(read,'实际失败')
        self.assertEqual(result,store.tool_call('read_event',dict(event_key=row['event_key'],start=0,max_characters=7),read))
        with self.assertRaises(ValueError):store.tool_call('read_event',dict(event_key=row['event_key'],start=1),read)

    def test_late_counterevidence_search_and_full_pagination_preserve_every_call(self):
        events=[event(str(i),'输入'+str(i),request='r'+str(i)) for i in range(75)]
        events.append(event('failure','先前宣称成功，之后测试失败',role='tool',request='r74'))
        store=self.build(events);sid=store.list_sessions()['items'][0]['session_key']
        cursor=0;seen=[]
        while True:
            page=store.list_calls(sid,cursor,7);seen.extend(r['request_key'] for r in page['items'])
            self.assertLessEqual(len(json.dumps(page,ensure_ascii=False).encode()),MAX_BYTES)
            if page['next_cursor'] is None:break
            cursor=page['next_cursor']
        self.assertEqual(75,len(set(seen)))
        found=store.search(sid,'测试失败')['items'][0]
        result=store.read_event(found['event_key']);self.assertIn('之后测试失败',result['text'])

    def test_reopen_and_tampering_never_change_original_source(self):
        events=[event('a','原始输入')];before=digest(events);store=self.build(events)
        other=SessionEvidence.build(packet(events),self.root/'store');other.close()
        changed=copy.deepcopy(events);changed[0]['text']='改写'
        with self.assertRaises(ValueError):SessionEvidence.build(packet(changed),self.root/'store')
        key=store.db.execute('SELECT key FROM events').fetchone()[0]
        with sqlite3.connect(self.root/'store/evidence.sqlite') as db:db.execute('UPDATE bodies SET text=?',('改为成功',))
        with self.assertRaisesRegex(ValueError,'签名'):store.read_event(key)
        self.assertEqual(before,digest(events))

    def test_manifest_cannot_relabel_session_identity(self):
        store=self.build([event('a','来源身份')])
        path=store.root/'manifest.json'
        manifest=json.loads(path.read_text())
        next(iter(manifest['sessions'].values()))['api_key_id']=999
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'范围签名'):
            SessionEvidence(store.root)

    def test_batch_read_preserves_all_characters_and_pending_ranges(self):
        events=[event(str(i),'原句'+str(i)+'😀\x00日志'*1000,request='r'+str(i)) for i in range(12)]
        store=self.build(events)
        keys={r['origin']:r['key'] for r in store.db.execute('SELECT origin,key FROM events')}
        requests=[dict(event_key=keys[e['event']],start=0,max_characters=6000) for e in events]
        before=copy.deepcopy(requests);actual={k:'' for k in keys.values()};rounds=0
        while requests:
            result=store.read_events(requests);rounds+=1
            self.assertLessEqual(len(json.dumps(result,ensure_ascii=False).encode()),MAX_BYTES)
            self.assertTrue(result['items'])
            for piece in result['items']:
                self.assertEqual(len(actual[piece['event_key']]),piece['start'])
                actual[piece['event_key']]+=piece['text']
            self.assertEqual(not result['next_requests'],result['complete'])
            requests=result['next_requests']
            self.assertLess(rounds,100)
        self.assertEqual({keys[e['event']]:e['text'] for e in events},actual)
        self.assertEqual(0,before[0]['start'])

    def test_batch_citation_requires_delivered_and_unambiguous_source(self):
        store=self.build([event('a','请继续'),event('b','请继续',request='r2'),event('c','尚未交付',request='r3')])
        keys={r['origin']:r['key'] for r in store.db.execute('SELECT origin,key FROM events')}
        path=self.root/'batch.json'
        result=store.tool_call('read_events',dict(requests=[dict(event_key=keys[k],start=0,max_characters=6000) for k in ('a','b')]),path)
        self.assertEqual(2,len(result['items']))
        with self.assertRaises(ValueError):store.cite(path,'请继续')
        self.assertEqual('a',store.cite(path,'请继续',event_key=keys['a'])['origin_event'])
        with self.assertRaises(ValueError):store.cite(path,'尚未交付',event_key=keys['c'])

    def test_tool_contract_has_no_write_or_publish_operations(self):
        self.assertEqual({'list_sessions','list_inputs','list_calls','list_events','read_event','read_events','search','tool_trace'}, {t['name'] for t in model_tools()})
        self.assertTrue(all(t['strict'] and t['parameters']['additionalProperties'] is False for t in model_tools()))


if __name__=='__main__':unittest.main()
