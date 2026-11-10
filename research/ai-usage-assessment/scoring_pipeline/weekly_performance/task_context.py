"""按既有任务组织本人请求及同次反馈；不选优、不评分、不调用模型。"""
import copy
from ..common import moment, require

WORKFLOW_VERSION = 'task-whole-context-20261105.01'


def task_context(unit, *, task_person_events=None):
    """保留截至评价时点的任务过程；其他会话上下文仍可按索引补读。"""
    require(len(unit['periods']) == 1, '任务过程阅读须明确单一评价时窗')
    period = unit['periods'][0]
    start, end = moment(period['start']), moment(period['end'])
    require(start < end, '评价时窗错误')
    events = {e['event']: e for e in unit['events']}
    current = set(unit['person_events'])
    require(current and current <= set(events), '本人事件缺失')
    require(all(start <= moment(events[e]['at']) < end for e in current), '本人动作跨评价时窗')
    members = set(task_person_events if task_person_events is not None else current)
    require(current <= members, '任务成员漏掉当前本人事件')
    members = {e for e in members if e in events and moment(events[e]['at']) < end}

    def request_key(event):
        # request_id只在来源会话内关联，不能跨会话串联同名请求。
        if event.get('session_id') and event.get('request_id'):
            return event['session_id'], event['request_id']
        return None

    requests = {request_key(events[e]) for e in members} - {None}
    chosen, other, withheld = [], [], []
    for event in sorted(events.values(), key=lambda e: (moment(e['at']), e['event'])):
        if moment(event['at']) >= end:
            continue
        linked = event['event'] in members or request_key(event) in requests
        if event.get('actor_class') != 'human' and event.get('response_timestamp_unknown'):
            withheld.append(event['event'])
        elif linked:
            chosen.append(event)
        else:
            other.append(event['event'])
    return dict(workflow_version=WORKFLOW_VERSION, key=unit['key'],
        period=copy.deepcopy(period), person_events=sorted(current),
        background_person_events=sorted(members-current), events=chosen,
        other_context_events=other, withheld_time_unknown=withheld,
        unpaired_person_events=sorted(e for e in members if request_key(events[e]) is None),
        source_gaps=copy.deepcopy(unit.get('source_gaps', [])),
        limits='同请求关联只组织阅读，不证明反馈属实、方案有效或任务全部上下文已读；其他上下文按需补读。')
