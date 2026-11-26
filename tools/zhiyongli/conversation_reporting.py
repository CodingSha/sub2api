"""把已完成事实检查的连续对话评审装配到现有日周合同；不自行导入或发布。"""
import copy
import json
import re
from pathlib import Path

from bounded_read import checkpoint
from conversation_review import assess, VERSION as ENGINE
from conversation_provenance import verify
from conversation_attribution import annotate
from daily_assessment import checked_call, check_assessment
from daily_export import bundle as export_day, text_sha
from daily_pilot import report
from runtime import atomic, digest
from weekly_daily import receipt_digest, prefix
from weekly_scores import apply as weekly_score, VERSION as WEEK_AGGREGATION
from workday_scope import daily_window, validate, VERSION as SCOPE_VERSION
from task_review_policy import RUBRIC as REVIEW_RUBRIC

WEEK_VERSION='weekly-conversation-20261125.1'


def canonical_daily_review(review):
    """对齐Go DailyTask的tool_index,omitempty；其他字段及非空目录必须逐字相同。"""
    result=copy.deepcopy(review)
    for task in result['conversations'].values():
        if task.get('tool_index')==[]:task.pop('tool_index')
    return result


def display_catalog(root, source=None):
    """只从已验签的最终请求解析片段原文，展示替换不改模型判断。"""
    from conversation_provenance import candidate
    quality=json.loads((Path(root)/'quality.json').read_text())
    if quality.get('quality_status')=='source_checked':return {}
    _,intent,_=candidate(root,quality['candidate_stage'],source=source)
    return {key:text for row in intent['data']['records'] for key,text in row.get('text_parts',[]) if key}


def explain_references(text, references):
    if references is None:return text
    def replace(match):
        key=match.group()
        if key not in references:raise ValueError('评语引用未交付的原文片段')
        return '“'+references[key]+'”'
    return re.sub(r'(?<![A-Za-z0-9_])q[1-9][0-9]*(?![A-Za-z0-9_])',replace,text)


def presentation(value, tasks, originals, reads, references=None):
    """原话与行为编号来自已核结果，任务说明和正文分别绑定来源。"""
    final=[]
    for code,cell in value['items'].items():
        final.append(dict(code=code,state='observed' if cell['band'] else 'unobservable',
            verdict=cell['band'] or 'unresolved',basis=explain_references(cell['basis'],references),
            higher_band=explain_references(cell['next_band'],references),
            quotes=[dict(record_id=e['record_id'],quote=e['quote'],origin=e['origin']) for e in cell['evidence']]))
    pilot=dict(quality_status='source_checked' if value.get('source_kind')=='background_only' else 'model_checked',behaviors=value['behaviors'],
        source_labels={},source_sessions={},conversations={})
    for task in tasks:
        summary=value['task_summaries'][task['id']]
        rows=task['records']+[r for r in reads if originals[r['id']].get('task_id')==task['id']]
        pilot['conversations'][task['id']]=dict(title=summary['title'],goal=summary['goal'],outcome=summary['outcome'],
            workflow=[dict(name=w['name'],purpose=w['purpose'],selection=w['selection'],
                user_refs=w['user_refs'],tool_refs=[w['tool_ref']]) for w in summary['workflow']],
            tool_index=task.get('workflow_index',[]),
            records=[dict(id=r['id'],role='context' if r['role']=='user' and r.get('boundary_context') else r['role'],text=r['text'],excerpt=bool(r.get('excerpt')),
                source_order=i,source_hash=text_sha(r['text']),names=r.get('names',[])) for i,r in enumerate(rows)])
        for row in task['records']:
            if not row.get('boundary_context'):
                pilot['source_labels'][row['id']]=summary['title']
                pilot['source_sessions'][row['id']]=task['id']
    return dict(items=final,summary=explain_references(value['summary'],references),strengths='',improvements=''),pilot


def scored(packet, day, value, tasks, originals, reads, references=None):
    if any(cell['band'] is not None for cell in value['items'].values()):
        check_assessment(value,{r['id']:r for task in tasks for r in task['records']},tasks,allow_unknown_next=True)
    elif value['behaviors'] or any(cell['evidence'] or cell['negative'] for cell in value['items'].values()):
        raise ValueError('未观察项不能携带得分行为或负向归责')
    final,pilot=presentation(value,tasks,originals,reads,references)
    result=report(final,packet,day,originals,normalize_observed=True)
    if value.get('source_kind')=='background_only':result['limitations'].append(value['summary'])
    result['pilot'].update(pilot)
    for item in result['items']:
        cell=value['items'][item['item_code']]
        if cell['band'] in REVIEW_RUBRIC['performance_anchors'][item['item_code']]:
            item['criterion']=REVIEW_RUBRIC['performance_anchors'][item['item_code']][cell['band']]
        for evidence,source in zip(item['evidence'],value['items'][item['item_code']]['evidence']):
            evidence.update(id=source['behavior_id'],source_hash=text_sha(originals[source['record_id']]['text']),
                task_title=pilot['source_labels'][source['record_id']])
    return result


def evaluate_day(packet, day, root, client, material):
    """只处理已绑定的最多五个冻结任务；日总分的页面规则由现有合同维持。"""
    root=Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    if validate(packet['workday_scope'])!=daily_window(day):raise ValueError('日评窗口与冻结来源不符')
    tasks=material['tasks'];originals=material['originals']
    if not tasks or len(tasks)>5:raise ValueError('连续日评须提供一至五个已绑定任务')
    checkpoint(root/'production-source.json',dict(engine=ENGINE,day=day,user_id=packet['user_id'],
        packet_sha256=digest(packet),material_sha256=digest(material)))
    client.dimension_coverage=True
    value,reads=assess(client,root,tasks,originals,checked_call)
    checkpoint(root/'provenance-check.json',verify(root,tasks,originals))
    tasks,originals,_=annotate(tasks,originals)
    checkpoint(root/'selected-tasks.json',tasks)
    checkpoint(root/'source/records.json',originals)
    result=scored(packet,day,value,tasks,originals,reads,display_catalog(root,(tasks,originals)))
    result.update(period_type='day',period_end=packet['workday_scope']['period_end'],publication_status='draft',published_at=None)
    atomic(root/'behavior-catalog.json',value['behaviors'])
    atomic(root/'report.json',result)
    return export_day(packet,day,root,client.config.model,conversation=True)


def empty_day(packet, day, root, model):
    root=Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    checkpoint(root/'empty.json',dict(day=day,user_id=packet['user_id'],status='no_activity',score=None,
        reason='已核冻结来源，本日采集窗口内没有可评的本人使用记录。'))
    return export_day(packet,day,root,model,conversation=True)


def weekly_material(days, working_dates):
    """五日原文重新综合，旧日分及行为归项不进入模型输入。"""
    if [day['day'] for day in days]!=working_dates:raise ValueError('周评缺少五日冻结材料或顺序不符')
    tasks=[];originals={};seen=set()
    for day in days:
        material=day['material'];p=prefix(day['day'])
        for task in material['tasks']:
            current=copy.deepcopy(task);current['id']=p+task['id']
            for row in current['records']:
                old=row['id']
                if row['role']=='user':
                    if old in seen:raise ValueError('五日日评重复包含同一条本人原文')
                    seen.add(old)
                row['id']=p+old
            for entry in current.get('workflow_index',[]):entry['record_id']=p+entry['record_id']
            tasks.append(current)
        for key,row in material['originals'].items():
            originals[p+key]=dict(row,id=p+key,task_id=p+row['task_id'])
    return dict(tasks=tasks,originals=originals)


def published_view(days):
    """周评重新判断，展示的五日任务与已发布日评保持逐字一致。"""
    labels={};sessions={};conversations={}
    for day in days:
        review=day['review'];p=prefix(day['day'])
        if set(review['conversations'])!={task['id'] for task in day['material']['tasks']}:
            raise ValueError('周评原文任务与已发布日评不符')
        for key,task in review['conversations'].items():
            task=copy.deepcopy(task)
            for row in task['records']:row['id']=p+row['id']
            for entry in task.get('tool_index',[]):entry['record_id']=p+entry['record_id']
            for usage in task['workflow']:
                usage['user_refs']=[p+ref for ref in usage['user_refs']]
                usage['tool_refs']=[p+ref for ref in usage['tool_refs']]
            conversations[p+key]=task
        labels.update({p+key:value for key,value in review['source_labels'].items()})
        sessions.update({p+key:p+value for key,value in review['source_sessions'].items()})
    return dict(conversations=conversations,source_labels=labels,source_sessions=sessions)


def evaluate_week(job, root, client, days, sources):
    """沿用正式周评版本和固定权重，全维依据五日原文综合评判。"""
    scope=validate(job['workday_scope'])
    if any(day['user_id']!=job['user_id'] or day['user_email']!=job['user_email'] for day in days):
        raise ValueError('五日原文身份与周评用户不符')
    material=weekly_material(days,scope['working_dates'])
    shown=published_view(days)
    if [r['period_start'] for r in sources]!=scope['working_dates']:raise ValueError('周评日评引用不齐全')
    root=Path(root);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    checkpoint(root/'weekly-source.json',dict(engine=ENGINE,user_id=job['user_id'],
        scope=scope,sources_sha256=digest(sources),material_sha256=digest(material)))
    tasks=material['tasks'];originals=material['originals'];client.dimension_coverage=True
    if tasks:
        value,reads=assess(client,root,tasks,originals,checked_call)
        checkpoint(root/'provenance-check.json',verify(root,tasks,originals))
        tasks,originals,_=annotate(tasks,originals)
        packed=scored(dict(user_id=job['user_id'],user_email=job['user_email']),str(job['period_start']),
            value,tasks,originals,reads,display_catalog(root,(tasks,originals)))
        pilot=packed.pop('pilot')
    else:
        packed=dict(score=None,exact=None,rating=None,items=[],dimensions=[],
            score_status='no_activity' if all(day['score_status']=='no_activity' for day in days) else 'observation_only',
            score_reason='',limitations=[])
        pilot=dict(source_labels={},source_sessions={},conversations={},behaviors={})
        atomic(root/'quality.json',dict(quality_status='not_required',reason='五日均无可评本人任务'))
    from daily_pilot import RUBRIC
    result={key:packed[key] for key in ('score','exact','rating','items','dimensions','score_status','score_reason','limitations')}
    result.update(user_email=job['user_email'],period_type='week',period_start=str(job['period_start']),
        period_end=str(job['period_end']),workday_scope=scope,source_digest=digest(sources),
        policy_versions=dict(rules='1.6',scale=RUBRIC['version'],review=WEEK_VERSION,
            aggregation=WEEK_AGGREGATION,scope=SCOPE_VERSION,rating='G2-proposal28'),
        weekly_review=dict(version=WEEK_VERSION,model=client.config.model,request_digest=receipt_digest(root),
            window_start=scope['working_dates'][0]+'T08:00:00+08:00',
            window_end=scope['working_dates'][-1]+'T18:00:00+08:00',
            sources=sources,behaviors=pilot['behaviors'],**shown))
    weekly_score(result)
    for item in result['items']:
        for evidence in item['evidence']:
            evidence['task_title']=shown['source_labels'][evidence['source_ref']]
    atomic(root/'report.json',result)
    return dict(schema_version=1,source_name='conversation-week-'+str(job['period_start'])+'-'+str(job['user_id'])+'-'+digest(sources)[:12],reports=[result])
