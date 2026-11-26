"""模型提出分项证据需求；程序从冻结来源取有界原文，不用目录代替证据。"""
import json
import jsonschema
from review_schema import obj, array, enum

ITEMS = tuple(f'{d}{i}' for d in 'PMJE' for i in range(1,4))
MAX_RECORD_CHARS = 4000
MAX_PACKAGE_BYTES = 40000
MAX_DIRECTORY_BYTES = 16000
INSTRUCTIONS = '''根据任务卡和固定十二项规则，为每项提出回读需求，不评分、不沿用旧分或旧归项。
directory只有定位预览，不是已阅读的完整证据。record_id只选本任务目录；requests指定要读的原文字坐标，最多4000字符。必要的前后用户和AI消息由程序附加，但工具要显式请求。
每项均要处置：没有线索可空requests，理由说明当前线索及待查范围。疑似Skill或个人工作流不能只看名字：寻找本人选择/明确采纳、真实执行及对本任务的免重复作用；queries可给具体关键词，程序只搜索本任务原文。
禁止把提供文件/URL、读取文档、一般CLI/MCP工具存在、AI自主调用或角色分工作为E3的充分线索；不能因为没出现关键词就认为没有工作流。本人用自然语言沿用既有多步骤流程也要检索。'''


def read_schema(ids, maximum=6):
    return dict(array(obj(dict(record_id=enum(*ids), start=dict(type='integer',minimum=0),
        chars=dict(type='integer',minimum=1,maximum=MAX_RECORD_CHARS)))),maxItems=maximum)


def schema(ids):
    plan=obj(dict(reason=dict(type='string',minLength=1,maxLength=300),
        requests=read_schema(ids), queries=dict(array(dict(type='string',minLength=2,maxLength=80)),maxItems=2,uniqueItems=True)))
    return obj({code:plan for code in ITEMS})


def directory(originals):
    """预览可缩短，记录目录不能静默丢项；坐标回读仍取完整冻结原件。"""
    rows=[dict(id=row['id'],role=row['role'],chars=len(row['text']),
        preview=' '.join(row['text'].split())[:180],preview_is_evidence=False) for row in originals.values()]
    for width in (180,80,0):
        compact=[dict(row,preview=row['preview'][:width]) for row in rows]
        if len(json.dumps(compact,ensure_ascii=False).encode())<=MAX_DIRECTORY_BYTES:return compact
    raise ValueError('来源目录超出容量，须分段导航；未删除记录或生成评分')


def validate_requests(requests, originals):
    for request in requests:
        row=originals.get(request['record_id'])
        if (not row or type(request['start']) is not int or type(request['chars']) is not int
                or request['start']<0 or request['chars']<1 or request['chars']>MAX_RECORD_CHARS
                or request['start']>=len(row['text'])):
            raise ValueError('证据回读来源或坐标非法')


def validate_plan(plan, originals):
    jsonschema.validate(plan,schema(list(originals)))
    for row in plan.values(): validate_requests(row['requests'],originals)
    return plan


def search(originals, queries):
    result=[]
    for query in queries:
        hits=[]
        for row in originals.values():
            start=0
            query_text = query.lower()
            while True:
                at = row['text'].lower().find(query_text,start)
                if at < 0:
                    break
                hits.append(dict(record_id=row['id'],role=row['role'],start=max(0,at-200),
                    chars=min(1200,len(row['text'])-max(0,at-200))))
                start=at+len(query)
        result.append(dict(query=query,total=len(hits),hits=hits[:8],omitted=max(0,len(hits)-8),preview_is_evidence=False))
    return result


def read_package(task, originals, requests, previous=()):
    """每段有实际起止坐标；超容量报错，不裁掉已请求的原文。"""
    validate_requests(requests,originals)
    spans={(r['id'],r['start'],r['end']):dict(r) for r in previous}
    main=task['records']
    for request in requests:
        row=originals[request['record_id']];start=request['start'];end=min(start+request['chars'],len(row['text']))
        spans[(row['id'],start,end)]=dict(id=row['id'],role=row['role'],start=start,end=end,
            text=row['text'][start:end],excerpt=start>0 or end<len(row['text']) or bool(row.get('excerpt') or row.get('truncated')))
        for index,source in enumerate(main):
            if source['id']!=row['id']:continue
            for neighbor in main[max(0,index-1):index+2]:
                stop=min(len(neighbor['text']),MAX_RECORD_CHARS)
                spans[(neighbor['id'],0,stop)]=dict(id=neighbor['id'],role=neighbor['role'],start=0,end=stop,
                    text=neighbor['text'][:stop],excerpt=stop<len(neighbor['text']) or bool(neighbor.get('excerpt') or neighbor.get('truncated')))
    result=list(spans.values())
    if len(json.dumps(result,ensure_ascii=False).encode())>MAX_PACKAGE_BYTES:
        raise ValueError('证据包超过容量，须缩小单项请求；未截断原文或提交评分')
    return result
