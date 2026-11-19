"""按既有任务组织本人请求及同次反馈；不选优、不评分、不调用模型。"""
import copy
from ..common import moment, require

WORKFLOW_VERSION = 'task-whole-context-20261105.01'


class TaskContextIndex:
    """单次只读装配内复用事件索引及时间排序，不缓存任务判断或输出。"""
    def __init__(self, unit):
        self._unit = unit
        self._source = None
        self._mapping = self._ordered = None

    def mapping(self, events):
        if self._source is None:
            self._source = self._unit['events']
        if events is not self._source or self._unit['events'] is not self._source:
            raise ValueError('任务过程索引不属于当前来源事件')
        if self._mapping is None:
            self._mapping = {e['event']: e for e in events}
        return self._mapping

    def ordered(self):
        # 到原排序位置才解析时间，保留本期本人及成员验证的异常优先级。
        if self._ordered is None:
            dated = [(e, moment(e['at'])) for e in self.mapping(self._unit['events']).values()]
            self._ordered = tuple(sorted(dated, key=lambda row: (row[1], row[0]['event'])))
        return self._ordered


def task_context(unit, *, task_person_events=None, context_index=None):
    """保留截至评价时点的任务过程；其他会话上下文仍可按索引补读。"""
    require(len(unit['periods']) == 1, '任务过程阅读须明确单一评价时窗')
    period = unit['periods'][0]
    start, end = moment(period['start']), moment(period['end'])
    require(start < end, '评价时窗错误')
    events = ({e['event']: e for e in unit['events']} if context_index is None
              else context_index.mapping(unit['events']))
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
    ordered = (((e, moment(e['at'])) for e in sorted(events.values(), key=lambda e: (moment(e['at']), e['event'])))
               if context_index is None else context_index.ordered())
    for event, occurred_at in ordered:
        if occurred_at >= end:
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
