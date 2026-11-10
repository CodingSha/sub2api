"""支持的审计协议转为候选阅读事件；角色、历史时间和本人语义分开。"""
import hashlib
import json


def extract(packet):
    events, issues = [], list(packet['issues'])
    def captured(frame, role, text, pointer):
        eid=frame['frame']+':'+pointer
        events.append(dict(event=eid,text=text,field=frame['field'],
            actor_class='candidate_user' if role=='user' else 'context',protocol_role=role,
            content_kind='text',at=frame['at'],timestamp_unknown=frame['timestamp_unknown'],
            truncated=frame['truncated'],session_id=frame['session_id'],request_id=frame['request_id'],
            source_user_id=frame['user_id'],human_spans=[],source=dict(frame=frame['frame'],pointer=pointer,
                frame_text_sha256=frame['text_sha256']),text_sha256=hashlib.sha256(text.encode()).hexdigest()))
    for frame in packet['frames']:
        if frame['field'] != 'request':
            # 原始响应可能为SSE，先保持原始字符来源，不假称已还原自然语言反馈。
            events.append(dict(event=frame['frame'], text=frame['text'], field='response',
                actor_class='context', protocol_role='assistant', content_kind='raw_response',
                at=frame['at'], timestamp_unknown=frame['timestamp_unknown'],
                response_timestamp_unknown=frame['timestamp_unknown'], truncated=frame['truncated'],
                session_id=frame['session_id'], request_id=frame['request_id'], source_user_id=frame['user_id'],
                human_spans=[], source=dict(frame=frame['frame'], frame_text_sha256=frame['text_sha256']),
                text_sha256=frame['text_sha256']))
            continue
        try:
            body = json.loads(frame['text'])
        except (json.JSONDecodeError, TypeError):
            # AI Hub当前采集器优先提取本次用户文字；纯文字并不是协议解析失败。
            captured(frame,'user',frame['text'],'captured_user')
            continue
        if not isinstance(body, dict):
            captured(frame,'user',frame['text'],'captured_user')
            continue
        if isinstance(body.get('audit_events'),list):
            for index,item in enumerate(body['audit_events']):
                if not isinstance(item,dict) or item.get('kind') not in {'user','assistant','tool_call','tool_result'} or not isinstance(item.get('text'),str):
                    issues.append(dict(frame=frame['frame'],code='unsupported_audit_event'));continue
                role='tool' if item['kind'].startswith('tool_') else item['kind']
                captured(frame,role,item['text'],'audit_events/'+str(index))
            continue
        if 'messages' in body and isinstance(body['messages'], list):
            messages, field = body['messages'], 'messages'
        elif isinstance(body.get('input'), str):
            messages, field = [dict(role='user', content=body['input'])], 'input'
        elif isinstance(body.get('input'), list):
            messages, field = body['input'], 'input'
        else:
            captured(frame,'user',frame['text'],'captured_user_json')
            continue
        for index, message in enumerate(messages):
            pointer = field + '/' + str(index)
            if not isinstance(message, dict):
                issues.append(dict(frame=frame['frame'], pointer=pointer, code='unsupported_message'))
                continue
            role = message.get('role')
            if role not in ('user', 'assistant', 'system', 'developer', 'tool'):
                # function_call/function_call_output 等仍保留完整来源，不伪装为本人输入。
                role = 'tool' if message.get('type') in ('function_call', 'function_call_output') else 'unknown'
                content = json.dumps(message, ensure_ascii=False, sort_keys=True)
                blocks = [('protocol_object', content)]
            else:
                content = message.get('content')
                if isinstance(content, str):
                    blocks = [('text', content)]
                elif isinstance(content, list):
                    blocks = []
                    for block in content:
                        if isinstance(block, dict) and block.get('type') in ('text', 'input_text', 'output_text') and isinstance(block.get('text'), str):
                            blocks.append(('text', block['text']))
                        else:
                            blocks.append(('non_text', json.dumps(block, ensure_ascii=False, sort_keys=True)))
                elif content is None and message.get('tool_calls'):
                    blocks = [('protocol_object', json.dumps(message, ensure_ascii=False, sort_keys=True))]
                else:
                    issues.append(dict(frame=frame['frame'], pointer=pointer, code='unsupported_message_content'))
                    continue
            for block_index, (kind, text) in enumerate(blocks):
                eid = frame['frame'] + ':' + pointer + ':' + str(block_index)
                # 只有请求尾部user候选可定位到本次提交时刻；历史正文不补造发送时间。
                current = role == 'user' and index == len(messages) - 1
                uncertain = frame['timestamp_unknown'] or not current
                events.append(dict(event=eid, text=text, protocol_role=role, content_kind=kind,
                    actor_class='candidate_user' if role == 'user' and kind == 'text' else 'context',
                    at=frame['at'], timestamp_unknown=uncertain, truncated=frame['truncated'],
                    session_id=frame['session_id'], request_id=frame['request_id'],
                    source_user_id=frame['user_id'], human_spans=[],
                    source=dict(frame=frame['frame'], pointer=pointer, block=block_index,
                                frame_text_sha256=frame['text_sha256']),
                    text_sha256=hashlib.sha256(text.encode()).hexdigest()))
                if kind == 'non_text':
                    issues.append(dict(frame=frame['frame'], pointer=pointer, code='non_text_material_not_read'))
                if role == 'unknown':
                    issues.append(dict(frame=frame['frame'], pointer=pointer, code='unknown_protocol_role'))
    for event in events:
        # 采集定位时间不是逐条历史消息的发送时刻，响应也可能沿用请求采集时间。
        event['timestamp_basis']='audit_capture'
        event['field']=event['source']['frame'].split(':')[2]
        event['audit_row_id']=event['source']['frame'].split(':')[1]
    return dict(version=1, snapshot_at=packet['snapshot_at'], events=events, issues=issues,
                state='needs_semantic_task_and_author_attribution',
                limitation='候选user正文未认定为本人；历史时间未知仅作上下文；协议帧完整保留，未消费的附件不冒称已读。')
