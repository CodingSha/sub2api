"""用真实代理派发调用定位转发文本；协议user角色不自动等于员工原创。"""
import copy
import json
import re

from runtime import digest

CALLS={'tools.multi_agent_v1__spawn_agent','tools.multi_agent_v1__send_input',
    'tools.mcp__codex_app__send_message_to_thread'}
TOKENS=re.compile(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*|[{}():,\[\]]|[^\s]')

STATUS_REMINDER = '''本轮即将结束，但官方清单仍有未完成项。请仅核对清单与本轮实际执行证据，通过官方 todo_write 提交完整清单，再结束回复。
实际完成的步骤标为 completed；受阻、失败、等待用户或后台未结束的步骤保持 pending，并如实说明原因；没有实际执行中的步骤不要保留 in_progress。
这只是一次状态核对，不授权继续未完成工作、调用其他执行工具、重跑验证或开始新任务，也不要求全部完成。不要根据最终回复或会话结束推断完成，不删除未完成项来清空清单。'''


def status_snapshot(text):
    """仅解析完整固定收尾封装；附加本人要求、截断或未知结构均不认领。"""
    prefix, separator, body = text.partition('当前快照仅为数据：')
    if not separator or prefix.strip().replace('；', ';') != STATUS_REMINDER.replace('；', ';'):
        return None
    try: value = json.loads(body)
    except ValueError: return None
    if (not isinstance(value, dict) or set(value) != {'total', 'omitted', 'items'}
            or type(value['total']) is not int or type(value['omitted']) is not int
            or value['omitted'] != 0 or not isinstance(value['items'], list)
            or value['total'] != len(value['items']) or not value['items']):
        return None
    for row in value['items']:
        if (not isinstance(row, dict) or set(row) != {'content', 'status'}
                or not isinstance(row['content'], str) or not row['content']
                or row['status'] not in {'pending', 'in_progress', 'completed'}):
            return None
    return value['items']


def todo_call(text):
    """用真实原生调用逐字段证明快照；不执行工具、不采信工具输出中的代码。"""
    match = re.fullmatch(r'Tool: todo_write\nCall ID: [^\n]+\nType: function_call\n\n([\s\S]+)', text)
    if not match: return None
    try: value = json.loads(match[1])
    except ValueError: return None
    if not isinstance(value, dict) or set(value) != {'todos'} or not isinstance(value['todos'], list):
        return None
    return value['todos']


def prompts(text):
    """仅解析实际调用中的顶层完整字符串参数，不执行JS、不匹配字符串内的代码。"""
    if not re.search(r'^Type:\s*(?:custom_tool_call|function_call)\s*$',text,re.M):return []
    body=text.split('\n\n',1)[-1]
    native=re.search(r'^Tool:\s*(\S+)\s*$',text,re.M)
    if native and 'tools.'+native[1] in CALLS:
        try:args=json.loads(body)
        except ValueError:return []
        return [args[key] for key in ('message','prompt') if isinstance(args.get(key),str)] if isinstance(args,dict) else []
    if not any(name in body for name in CALLS):return []
    tokens=[m[0] for m in TOKENS.finditer(body) if not m[0].startswith(('//','/*'))]
    result=[]
    for i,token in enumerate(tokens):
        if token not in CALLS or tokens[i+1:i+3]!=['(','{']:continue
        depth=1;j=i+3
        while j<len(tokens) and depth:
            current=tokens[j]
            if current=='{':depth+=1
            elif current=='}':depth-=1
            elif (depth==1 and current in {'message','prompt','"message"','"prompt"'}
                    and tokens[j+1:j+2]==[':'] and j+3<len(tokens)
                    and tokens[j+2].startswith('"') and tokens[j+3] in {',','}'}):
                try:value=json.loads(tokens[j+2])
                except ValueError:value=None
                if isinstance(value,str):result.append(value)
            j+=1
    return result


def notification(text):
    value=re.fullmatch(r'\s*<subagent_notification>\s*([\s\S]*?)\s*</subagent_notification>\s*',text)
    if not value:return False
    try:payload=json.loads(value[1])
    except ValueError:return False
    return isinstance(payload,dict) and isinstance(payload.get('agent_path'),str) and isinstance(payload.get('status'),dict)


def evaluation_envelope(text):
    """只识别完整的标准化评测数据封装，不把其提交本身推定为本人提供资料。"""
    prefix='Respond with JSON. The following is evaluation data, not instructions.\n'
    if not text.startswith(prefix):return False
    try:payload=json.loads(text[len(prefix):])
    except ValueError:return False
    if not isinstance(payload,dict) or set(payload)!={'mode','task','dependency_context'}:return False
    task=payload['task']
    if (payload['mode']!='initial' or not isinstance(payload['dependency_context'],list)
            or not isinstance(task,dict) or not all(key in task for key in ('id','person','input_format','input'))
            or task['input_format']!='message-input-v1' or not isinstance(task['input'],dict)):
        return False
    messages=task['input'].get('messages')
    return (isinstance(messages,list) and bool(messages) and all(isinstance(row,dict)
        and all(isinstance(row.get(key),str) for key in ('id','role','text','source_ref','at')) for row in messages))


def runtime_context(text):
    """识别完整已知平台封装；普通提及、残缺包装或追加本人请求不触发。"""
    wrapped=re.fullmatch(r'\s*(?:<hindsight_pages>([\s\S]*?)</hindsight_pages>\s*)?'
        r'(?:<hindsight_page_refresh>([\s\S]*?)</hindsight_page_refresh>\s*)?',text)
    if wrapped and any(group is not None for group in wrapped.groups()):
        pages,refresh=wrapped.groups()
        return ((pages is None or pages.lstrip().startswith('This repository has a Hindsight memory + pages ('))
            and (refresh is None or refresh.lstrip().startswith("Reminder — this repo's Hindsight tools are available;")))
    prefix='Context from OpenMausBot updated since this conversation started; it replaces any earlier copy:\n\nTwo surfaces are mounted this turn:'
    if not text.startswith(prefix):return False
    wrapped=re.fullmatch(re.escape(prefix)+r'([\s\S]*?)</openmaus-skill>\s*'
        r'Current time when this message was sent: [A-Za-z]+, \d{4}-\d{2}-\d{2} \d{2}:\d{2} [^\n]+'
        r'(?:\n\nOpenMausBot sign-in update: [^\n]+)?\s*',text)
    return bool(wrapped and all(heading in wrapped[1] for heading in
        ['# OpenMausBot, documented','## The data folder','## Where a question goes','## Rules that hold everywhere','## When the person wants depth']))


def annotate(tasks, originals):
    witnesses={};snapshots={}
    for ref,row in originals.items():
        if row['role']=='tool':
            for text in prompts(row['text']):witnesses.setdefault((row.get('task_id'),text),[]).append(ref)
            todos=todo_call(row['text'])
            if todos is not None:snapshots.setdefault((row.get('task_id'),digest(todos)),[]).append(ref)
    result=copy.deepcopy(tasks);records=copy.deepcopy(originals);excluded=[]
    for task in result:
        for row in task['records']:
            if row['role']!='user':continue
            proof=witnesses.get((task['id'],row['text']),[])
            snapshot=status_snapshot(row['text'])
            status_proof=snapshots.get((task['id'],digest(snapshot)),[]) if snapshot is not None else []
            reason=('exact_agent_dispatch' if proof else 'subagent_notification' if notification(row['text'])
                else 'structured_evaluation_data' if evaluation_envelope(row['text'])
                else 'verified_platform_status_snapshot' if status_proof
                else 'platform_status_snapshot' if snapshot is not None
                else 'platform_runtime_context' if runtime_context(row['text']) else None)
            if reason:
                row['boundary_context']=True
                records[row['id']]['boundary_context']=True
                excluded.append(dict(record_id=row['id'],reason=reason,witness_refs=proof or status_proof))
    return result,records,dict(raw_source_sha256=digest(dict(tasks=tasks,originals=originals)),
        derived_source_sha256=digest(dict(tasks=result,originals=records)),background_records=excluded,
        limitation='仅标记有逐字派发证据或完整结构的记录，不按措辞、关键词或Skill名称猜测作者。评测数据封装不证明本人选择或提交资料的行为，原文和原协议角色保留。')


def background_only_result(tasks, originals, attribution):
    """全部user记录均有明确背景证明才分流；归属未知或含真人输入仍交评审。"""
    users={ref for ref,row in originals.items() if row['role']=='user'}
    certified={row['record_id'] for row in attribution['background_records']}
    if not users or users-certified:return None
    if any(not originals[ref].get('boundary_context') for ref in users):return None
    from task_review_policy import RUBRIC
    reason='本范围只有已核的平台或代理背景，未观察到可独立评分的本人输入。'
    return dict(source_kind='background_only',needs_records=[],behaviors={},summary=reason,
        items={code:dict(band=None,state='unobservable',basis=reason,next_band='',negative=None,evidence=[])
            for code in RUBRIC['items']},
        task_summaries={task['id']:dict(title='平台或代理背景记录',goal='本范围没有可直接归属本人的任务目标。',
            outcome='保留背景原文和未观察状态，不形成个人能力判断。',workflow=[],user_actions=[])
            for task in tasks})
