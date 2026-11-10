"""现有共享材料的单元分组；只组织输入，不推断事实、不写重复原文缓存。"""
from ..common import require
from ..common import moment
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def check_window(scope, period):
    """日历窗口左闭右开；只核边界，不推断月度表现。"""
    start, end = moment(period['start']), moment(period['end'])
    require(start.tzinfo is not None and end.tzinfo is not None, '窗口须含时区')
    start = start.astimezone(ZoneInfo('Asia/Shanghai'))
    end = end.astimezone(ZoneInfo('Asia/Shanghai'))
    require(start < end and start.hour == start.minute == start.second == start.microsecond == 0,
            '窗口须从北京时间零点开始')
    if scope == 'person_week':
        require(start.weekday() == 0 and end == start+timedelta(days=7), '周须为周一零点起七天')
        expected = 'week-'+start.date().isoformat()
    else:
        require(scope == 'person_month' and start.day == 1, '月须从自然月首日开始')
        next_month = datetime(start.year+(start.month == 12), start.month % 12+1, 1, tzinfo=start.tzinfo)
        require(end == next_month, '月须在下月首日零点结束')
        expected = 'month-'+start.date().isoformat()
    require(period['period_id'] == expected, '期间ID与实际窗口不符')
    return start, end

def scope_units(units,period_id,*,evaluation_scope,window=None):
    require(evaluation_scope in {'local','person_week','person_month'},'明确局部、周或月范围')
    require(units and len({u['key'] for u in units})==len(units),'来源任务空或重复')
    require(len({u['user_id'] for u in units})==1,'不能混入不同人员')
    if evaluation_scope=='local':require(len(units)==1,'局部验证限一个原任务期间')
    monthly = evaluation_scope == 'person_month'
    if monthly:
        from .review022 import VERSION
        require(isinstance(window,dict), '月度须显式给自然月窗口')
        month = dict(window,period_id=period_id)
        start,end = check_window(evaluation_scope,month)
    else:
        require(window is None, '局部和周窗口沿用冻结原期间')
    periods=[];events={};persons=set();sources=[];gaps=[];week_ids=set()
    for unit in units:
        if monthly:
            require(unit.get('evaluation_scope') == 'person_week' and unit.get('version') == VERSION,
                    '月度只复用同版周输入，禁止机械混版')
            require(isinstance(unit.get('source_gaps'),list), '月度来源须显式保留来源缺口，不能默认完整')
            require(unit.get('source_keys') and len(unit['source_keys']) == len(set(unit['source_keys'])), '周来源缺失/重复')
            selected=unit['periods']
        else:
            selected=[p for p in unit['periods'] if p['period_id']==period_id]
        require(len(selected)==1,'来源期间缺失/重复')
        if monthly:
            a,b=check_window('person_week',selected[0])
            require(a < end and b > start, '月外周不能混入来源')
            require(selected[0]['period_id'] not in week_ids, '同周不能拆成多份增强支持')
            week_ids.add(selected[0]['period_id'])
        # 周装配也保留原任务缺口，不能只在月装配时汇集。
        gaps.extend(unit.get('source_gaps', []))
        periods.append(selected[0])
        for e in unit['events']:
            if monthly and moment(e['at']) >= end:continue
            if e['event'] in events:
                old=events[e['event']]
                require(all(old.get(k)==e.get(k) for k in ('text','at','actor_class','human_spans')),'相同事件来源冲突，不能静默合并')
            else:events[e['event']]=e
        persons.update(unit['person_events'])
        sources.append({k:unit[k] for k in ('key','title','purpose','boundary_reason','person_events','source_gaps','source_keys','version','evaluation_scope','periods') if k in unit})
    if monthly:
        persons={e for e in persons if e in events and start <= moment(events[e]['at']) < end}
        cursor=start-timedelta(days=start.weekday())
        while cursor < end:
            pid='week-'+cursor.date().isoformat()
            if pid not in week_ids:gaps.append('来源未完成：'+pid+'；不等同于本人无机会')
            cursor += timedelta(days=7)
        for source in sources:
            source['person_events']=[e for e in source.get('person_events',[]) if e in persons]
        periods=[month]
    else:
        require(len({(p['start'],p['end']) for p in periods})==1,'跨时窗不能合并')
    first=units[0]
    return dict(key=first['key'] if evaluation_scope=='local' else first['user_id']+':'+period_id,
        user_id=first['user_id'],title=first['title'] if evaluation_scope=='local' else '当前已识别已评分范围'+('月观察' if monthly else '周观察'),
        periods=[periods[0]],events=list(events.values()),person_events=sorted(persons),
        source_keys=[u['key'] for u in units],source_units=sources,evaluation_scope=evaluation_scope,
        source_gaps=gaps,version=VERSION if monthly else units[0].get('version'))
