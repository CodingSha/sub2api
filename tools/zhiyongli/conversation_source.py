"""从冻结选材区间恢复任务与工具归属，不把整日原文索引当成入选任务。"""
from daily_tasks import materialize, turns
from daily_reasoning import record_source_contract


def bind(tasks, originals, selection):
    groups=[dict(session=group['session'],records=[originals[key] for key in group['record_ids']])
        for group in selection['selected']]
    indexed=turns(groups)
    specs=[]
    for task in tasks:
        people=[row['id'] for row in task['records'] if row['role']=='user']
        if not people or any(ref not in indexed for ref in people):
            raise ValueError('冻结选材缺少本人记录或原会话定位')
        if any(indexed[ref]['session']!=task['session'] for ref in people):
            raise ValueError('冻结任务的本人记录跨会话，不能拼接')
        specs.append(dict(start=people[0],end=people[-1],title=task['title'],goal=task['goal']))
    restored,records=materialize(dict(tasks=specs),indexed,allow_input_only=True)
    # 历史版本可能没有列出通用CLI目录；正文与边界必须相同，旧目录必须仍有原调用。
    def spine(task):return {key:value for key,value in task.items() if key!='workflow_index'}
    if ([spine(task) for task in restored]!=[spine(task) for task in tasks]
            or any(entry not in new['workflow_index'] for old,new in zip(tasks,restored) for entry in old.get('workflow_index',[]))):
        raise ValueError('冻结任务无法由原选材区间逐字恢复，须核对来源版本')
    record_source_contract(restored,records)
    return dict(tasks=restored,originals=records)
