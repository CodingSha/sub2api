"""现有共享材料的单元分组；只组织输入，不推断事实、不写重复原文缓存。"""
from ..common import require

def scope_units(units,period_id,*,evaluation_scope):
    require(evaluation_scope in {'local','person_week'},'明确局部或当前已评分周范围')
    require(units and len({u['key'] for u in units})==len(units),'来源任务空或重复')
    require(len({u['user_id'] for u in units})==1,'不能混入不同人员')
    if evaluation_scope=='local':require(len(units)==1,'局部验证限一个原任务期间')
    periods=[];events={};persons=set();sources=[]
    for unit in units:
        selected=[p for p in unit['periods'] if p['period_id']==period_id]
        require(len(selected)==1,'来源期间缺失/重复')
        periods.append(selected[0])
        for e in unit['events']:
            if e['event'] in events:
                old=events[e['event']]
                require(all(old.get(k)==e.get(k) for k in ('text','at','actor_class','human_spans')),'相同事件来源冲突，不能静默合并')
            else:events[e['event']]=e
        persons.update(unit['person_events'])
        sources.append({k:unit[k] for k in ('key','title','purpose','boundary_reason','person_events','source_gaps') if k in unit})
    require(len({(p['start'],p['end']) for p in periods})==1,'跨时窗不能合并')
    first=units[0]
    return dict(key=first['key'] if evaluation_scope=='local' else first['user_id']+':'+period_id,
        user_id=first['user_id'],title=first['title'] if evaluation_scope=='local' else '当前已识别已评分范围周观察',
        periods=[periods[0]],events=list(events.values()),person_events=sorted(persons),
        source_keys=[u['key'] for u in units],source_units=sources,evaluation_scope=evaluation_scope)
