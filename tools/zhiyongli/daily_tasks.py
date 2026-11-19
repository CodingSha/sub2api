"""会话内连续任务选材；目录只定位，评分只使用实际交付的原文。"""
import json
import re
from itertools import zip_longest

from runtime import digest

DIRECTORY_BYTES = 28000
TASK_BYTES = 18000
MAIN_BYTES = 48000
WINDOW_OPTIONS = 250


def tool_directory(originals, task_ids, maximum=12000, query=None):
    """目录仅定位；提取实际命令或输出开头，去掉重复调用元数据，不当作正文证据。"""
    candidates=[]
    for row in originals.values():
        if row['role']!='tool' or row.get('task_id') not in task_ids:continue
        match=re.search(re.escape(query),row['text'],re.I) if query else None
        location=match.start() if match else -1
        if query and location<0:continue
        body=row['text'].split('\n\n',1)[-1].strip()
        if '\nOutput:\n' in body:body=body.split('\nOutput:\n',1)[1].strip()
        try:
            decoded=json.loads(body)
            if isinstance(decoded,dict) and isinstance(decoded.get('output'),str):body=decoded['output']
        except (ValueError,TypeError):pass
        command=re.search(r'\bcmd\s*:\s*("(?:[^"\\]|\\.)*")',body)
        if command:
            try:body=json.loads(command[1])
            except ValueError:pass
        # 命中可能在长输出中段；检索结果必须展示命中处，不能又给无关的正文开头。
        if query:
            body=row['text'][max(0,location-60):location+160]
        preview=' '.join(body.split())[:160]
        entry=dict(record_id=row['id'],task_id=row['task_id'],chars=len(row['text']),
            kind='result' if 'Type: custom_tool_call_output' in row['text'] or 'Type: function_call_output' in row['text'] else 'call',preview=preview)
        if query:entry['match_start']=location
        candidates.append(entry)
    selected=[]
    # 均匀保留目录定位点，不按预期评分或成功状态选择。
    for row in sorted(candidates,key=lambda r:digest(r['record_id'])):
        if size(selected+[row])<=maximum:selected.append(row)
    return dict(records=selected,total=len(candidates),listed=len(selected),preview_is_evidence=False)


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())


def workflow_rows(rows):
    result=[]
    for row in rows:
        if row['role']!='tool' or re.search(r'^Type:\s*(?:custom_tool_call_output|function_call_output)\s*$',row['text'],re.M):
            continue
        # 工具返回的文件目录/技能目录不是调用；仅从调用本身识别加载与MCP使用。
        names=re.findall(r'[/\\]([A-Za-z0-9_.-]+)[/\\]SKILL\.md',row['text'])
        names+=re.findall(r'tools\.(mcp__[\w]+)\s*\(',row['text'])
        result.extend(dict(name=name,record_id=row['id'],chars=len(row['text'])) for name in names)
    return result


def turns(groups):
    """每轮保留完整用户消息、最后一条AI反馈和原记录区间，不推断审计时间是发送时间。"""
    result = {}
    for group in groups:
        rows = group['records']
        # 图片输入、后台标题等虽不计本人表现，仍结束上一轮，不能把其反馈配给前一个任务。
        starts = [i for i, row in enumerate(rows) if row['role'] == 'user' or row.get('turn_boundary')]
        for number, start in enumerate(starts):
            if rows[start]['role'] != 'user':
                continue
            end = starts[number + 1] if number + 1 < len(starts) else len(rows)
            replies = [r for r in rows[start + 1:end] if r['role'] == 'assistant']
            # 没有反馈的末条安排仍可由同任务的前轮关联，不能推定失败或采用。
            result[rows[start]['id']] = dict(session=group['session'], order=number,
                user=rows[start], reply=replies[-1] if replies else None,
                rows=rows[start:end], clarification=rows[start-1] if start and rows[start-1].get('kind') in {'clarification_question','provided_attachment'} else None,
                prior=next((r for r in reversed(rows[:start]) if r['role'] == 'assistant'), None),workflow=workflow_rows(rows[start:end]))
    return result


def directory(all_turns):
    """目录按中性身份选择定位点；预览不作为评分依据，不拆用户原话生成成千上万的q编号。"""
    rows = []
    for key, turn in all_turns.items():
        rows.append(dict(id=key, session=turn['session'], order=turn['order'],
            user_preview=turn['user']['text'][:400], user_chars=len(turn['user']['text']),
            reply_preview=turn['reply']['text'][-240:] if turn['reply'] else '',
            reply_chars=len(turn['reply']['text']) if turn['reply'] else 0))
    selected = []
    for row in sorted(rows, key=lambda r:digest([r['session'], r['id']])):
        if size(selected + [row]) <= DIRECTORY_BYTES:
            selected.append(row)
    return sorted(selected, key=lambda r:(r['session'], r['order']))


def materialize(plan, all_turns, *, with_records=True):
    """规划只决定连续区间；完整用户消息及AI反馈必须实际交付，不能拿目录预览代替。"""
    tasks = []
    occupied = set()
    records = {}
    for index, spec in enumerate(plan['tasks']):
        if spec['start'] not in all_turns or spec['end'] not in all_turns:
            raise ValueError('任务边界不存在于原会话')
        start, end = all_turns[spec['start']], all_turns[spec['end']]
        if start['session'] != end['session'] or start['order'] > end['order']:
            raise ValueError('任务边界跨会话或顺序倒置')
        selected = [t for t in all_turns.values() if t['session'] == start['session']
            and start['order'] <= t['order'] <= end['order']]
        selected.sort(key=lambda t:t['order'])
        user_ids = {t['user']['id'] for t in selected}
        if occupied & user_ids:
            raise ValueError('入选任务重叠，可能重复计分')
        occupied.update(user_ids)
        spine = []
        if start['prior']:
            spine.append(dict(start['prior'], role='assistant', boundary_context=True))
        for turn in selected:
            # 澄清问题属于背景；原回答保持用户身份及完整文本。
            if turn['clarification']:
                spine.append(turn['clarification'])
            spine.append(turn['user'])
            if turn['reply']:
                spine.append(turn['reply'])
        if not any(r['role'] == 'assistant' and not r.get('boundary_context') for r in spine):
            raise ValueError('任务缺少AI实际反馈，不能作为完整任务选材')
        tools = [r for t in selected for r in t['rows'] if r['role'] == 'tool']
        # 名称只证明出现了调用/加载，不把目录认成执行成功；必要正文可按编号读取。
        workflow = []
        seen = set()
        for usage in (w for t in selected for w in t['workflow']):
            if usage['name'] not in seen:
                seen.add(usage['name'])
                workflow.append(usage)
        task = dict(id='task-'+str(index+1), session=start['session'], title=spec['title'],
            goal=spec['goal'], records=[dict(id=r['id'],role=r['role'],text=r['text'],
                excerpt=bool(r.get('truncated')), boundary_context=bool(r.get('boundary_context'))) for r in spine],
            workflow_index=workflow)
        if size(task) > TASK_BYTES:
            raise ValueError('任务完整主线超过选材预算：'+spec['start'])
        tasks.append(task)
        if with_records:
            for row in spine + tools:
                records[row['id']] = dict(row, task_id=task['id'])
    if not tasks or len(tasks) > 5 or size(tasks) > MAIN_BYTES:
        raise ValueError('每日完整任务材料超过预算或为空：实际'+str(size(tasks))+'字节，上限'+str(MAIN_BYTES)+'字节；应减少完整任务数量或选择其他完整窗口，不截断原文')
    return tasks, records


def windows(all_turns, listing):
    """完整会话能交付就不拆成片段；仅超限会话提供连续任务窗口。"""
    allowed={r['id'] for r in listing}
    options=[]
    sessions={}
    for key,turn in all_turns.items():
        sessions.setdefault(turn['session'],[]).append(key)
    whole=set()
    for session,keys in sessions.items():
        keys.sort(key=lambda key:all_turns[key]['order'])
        plan=dict(tasks=[dict(start=keys[0],end=keys[-1],title='完整会话',goal='核对当日完整任务来回')])
        try:
            tasks,_=materialize(plan,all_turns,with_records=False)
        except ValueError:
            continue
        options.append(dict(start=keys[0],end=keys[-1],bytes=size(tasks[0]),scope='complete_session',
            user_turns=len(keys),feedback_turns=sum(all_turns[key]['reply'] is not None for key in keys)))
        whole.add(session)
    for start,turn in all_turns.items():
        if start not in allowed or turn['session'] in whole:
            continue
        for end,last in all_turns.items():
            if end not in allowed or turn['session']!=last['session'] or last['order']<turn['order']:
                continue
            plan=dict(tasks=[dict(start=start,end=end,title='选材窗口',goal='核对完整任务目标')])
            try:
                tasks,_=materialize(plan,all_turns,with_records=False)
            except ValueError:
                continue
            records=tasks[0]['records']
            options.append(dict(start=start,end=end,bytes=size(tasks[0]),scope='task_window',
                user_turns=sum(r['role']=='user' for r in records),
                feedback_turns=sum(r['role']=='assistant' and not r['boundary_context'] for r in records)))
    # 优先保留来回更充分、正文更完整的定位范围；跨会话轮取，避免单个长会话占满目录。
    # 这些计数只辅助选材，不作为评分项或成功判定。
    by_session={}
    for option in options:
        by_session.setdefault(all_turns[option['start']]['session'],[]).append(option)
    for choices in by_session.values():
        choices.sort(key=lambda r:(r['scope']!='complete_session',-r['user_turns'],-r['feedback_turns'],
            -r['bytes'],digest([r['start'],r['end']])))
    rounds=zip_longest(*(by_session[key] for key in sorted(by_session,key=digest)))
    options=[option for group in rounds for option in group if option is not None][:WINDOW_OPTIONS]
    return [dict(id='w'+str(i+1),**row) for i,row in enumerate(options)]
