"""把日评终态装配为正式产品合同；不改模型分数，不把摘要或工具目录冒充原文。"""
import copy
import hashlib
import json
from pathlib import Path

from runtime import digest
from workday_scope import DAILY_VERSION, daily_window, validate
from daily_scores import AGGREGATION_VERSION, observed_total


def text_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def bundle(packet, day, root, model):
    scope=validate(packet['workday_scope'])
    if scope!=daily_window(day) or model!='deepseek-v4.1-flash-local':
        raise ValueError('日评来源窗口或固定模型不符')
    quality=root/'quality.json'
    if quality.exists() and json.loads(quality.read_text()).get('quality_status')=='failed':
        raise ValueError('日评质量验收已失败，禁止导出人员评分')
    # 请求回执绑定正式结果；原终态保留，不以本地文件存在替代模型完成。
    from request_retry import read_receipt, ATTEMPT
    receipts=[]
    paths=list(root.glob('*.receipt.json'))
    for path in sorted(root.glob('*.reused.json')):
        reference=json.loads(path.read_text())
        source=Path(reference['path'])
        if digest(read_receipt(source))!=reference['receipt_sha256']:
            raise ValueError('复用模型回执签名变化，禁止导出人员评分')
        paths.append(source)
    for path in sorted(set(paths)):
        if ATTEMPT.match(path.name):
            continue
        receipt=read_receipt(path)
        if receipt.get('status')!='received' or receipt.get('response',{}).get('status')!='completed':
            raise ValueError('日评含未完成模型请求，禁止导出人员评分')
        receipts.append(dict(request_sha256=receipt['request_sha256'],response_sha256=digest(receipt['response'])))
    review=dict(version=DAILY_VERSION,model=model,request_digest=digest(receipts),
        window_start=day+'T08:00:00+08:00',window_end=day+'T21:00:00+08:00',
        source_labels={},source_sessions={},conversations={},behaviors={})
    report=dict(user_email=packet['user_email'],period_type='day',period_start=day,period_end=scope['period_end'],
        source_digest=digest(packet),workday_scope=scope,policy_versions=dict(scale='pmje-performance-20261105.023-proposal1.control28',
        rules='1.6',aggregation=AGGREGATION_VERSION,review=DAILY_VERSION,scope=DAILY_VERSION,rating='G2-proposal28'),
        daily_review=review,dimensions=[],items=[],limitations=[],score=None,exact=None,rating=None)
    if (root/'empty.json').exists():
        empty=json.loads((root/'empty.json').read_text())
        if str(empty['user_id'])!=str(packet['user_id']) or empty['day']!=day or empty['status'] not in {'no_activity','observation_only'}:
            raise ValueError('空日终态与来源身份不符')
        report.update(score_status=empty['status'],score_reason='当日采集窗口内没有可评的本人使用记录。' if empty['status']=='no_activity' else '当日只有简短查询，没有形成可评分的完整任务。')
    else:
        saved=json.loads((root/'report.json').read_text())
        if saved.get('pilot',{}).get('quality_status')=='failed':
            raise ValueError('日评质量验收已失败，禁止导出人员评分')
        if not quality.exists() or json.loads(quality.read_text()).get('quality_status')!='model_checked' or saved.get('pilot',{}).get('quality_status')!='model_checked':
            raise ValueError('日评尚未完成来源、归属和评语检查，禁止导出人员评分')
        if saved['period_type']!='day' or saved['period_start']!=day or str(saved['user_id'])!=str(packet['user_id']) or saved['user_email']!=packet['user_email'] or not receipts:
            raise ValueError('日评结果与冻结人员、日期或请求不符')
        tasks=json.loads((root/'selected-tasks.json').read_text())
        originals={r['id']:r for task in tasks for r in task['records']}
        from daily_behaviors import validate as validate_behaviors, check_evidence
        review['behaviors']=copy.deepcopy(saved['pilot'].get('behaviors',{}))
        validate_behaviors(review['behaviors'],originals)
        report.update(score_status=saved['score_status'],score_reason=saved['score_reason'],items=copy.deepcopy(saved['items']),
            dimensions=copy.deepcopy(saved['dimensions']),score=saved['score'],exact=saved['exact'])
        from scoring_pipeline.weekly_performance.scores022 import packed
        total=observed_total(report['dimensions'])
        if total is None:
            raise ValueError('已选任务没有已观察维度，不能保存为有分日评')
        report.update(packed(total),score_status='scored')
        for task in tasks:
            task_id=task['id']
            shown=copy.deepcopy(saved['pilot']['conversations'][task_id])
            shown['tool_index']=task['workflow_index']
            seen=set()
            for i,row in enumerate(shown['records']):
                # 补读是工具原文节选；多段不能覆盖成完整正文，保留各段独立显示。
                if row['id'] in seen:
                    row['id']=row['id']+'-excerpt-'+str(i)
                seen.add(row['id'])
                row.update(source_order=i,source_hash=text_sha(row['text']))
                if row['role']=='user':
                    original=originals.get(row['id'])
                    if not original or original['role']!='user' or original['text']!=row['text']:
                        raise ValueError('正式日评本人正文与实际交付材料不符')
                    review['source_labels'][row['id']]=shown['title']
                    review['source_sessions'][row['id']]=task_id
            review['conversations'][task_id]=shown
        for item in report['items']:
            for evidence in item['evidence']:
                ref=evidence['source_ref']
                source=originals.get(ref)
                if not source or source['role']!='user' or evidence['quote'] not in source['text']:
                    raise ValueError('正式日评分项引文不属于实际用户材料')
                check_evidence(review['behaviors'],item['item_code'],evidence['id'],ref,evidence['quote'])
                evidence.update(source_hash=text_sha(source['text']),context=source['text'],
                    task_title=review['source_labels'][ref])
    return dict(schema_version=1,source_name='daily-'+day+'-'+str(packet['user_id'])+'-'+digest(packet)[:12],reports=[report])
