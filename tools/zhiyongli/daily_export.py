"""把日评终态装配为正式产品合同；不改模型分数，不把摘要或工具目录冒充原文。"""
import copy
import hashlib
import json
from pathlib import Path

from runtime import digest
from workday_scope import DAILY_VERSION, daily_window, validate
from daily_scores import AGGREGATION_VERSION, observed_total

CONVERSATION_VERSION='daily-conversation-review-20261125.1'


def text_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def completed_capacity_continuation(path, root, receipt):
    """仅接受收到超限终态且已按原覆盖清单完成的拆批链，未知请求始终拒绝。"""
    if (receipt.get('status')!='received' or receipt.get('response',{}).get('status')!='incomplete'
            or receipt['response'].get('incomplete_details',{}).get('reason')!='max_output_tokens'):
        return False
    stage=path.name[:-len('.receipt.json')];marker=path.with_name(stage+'-output-split.json')
    continuation=root/(stage+'-output-continuation.json')
    if continuation.exists():
        proof=json.loads(continuation.read_text())
        target=stage+'-without-reasoning'
        if proof.get('parent_receipt_sha256')!=digest(receipt) or proof.get('stage')!=target:return False
        from request_retry import read_receipt
        child=root/(target+'.receipt.json')
        if not child.exists():return False
        final=read_receipt(child)
        parent_input=json.loads((root/(stage+'.input.json')).read_text())
        child_input=json.loads((root/(target+'.input.json')).read_text())
        parent_without_reasoning=dict(parent_input);parent_without_reasoning.pop('reasoning',None)
        if parent_without_reasoning!=child_input:return False
        intent=json.loads((root/(target+'.intent.json')).read_text())
        return (final.get('status')=='received' and final.get('response',{}).get('status')=='completed'
            and intent['payload']==child_input
            and final['request_sha256']==digest(dict(endpoint=intent['endpoint'],payload=child_input))
            and final['wire_body_sha256']==hashlib.sha256(json.dumps(child_input,ensure_ascii=False).encode()).hexdigest())
    coverage=root/'coverage-read.json'
    if not marker.exists() or not coverage.exists():return False
    split=json.loads(marker.read_text());read=json.loads(coverage.read_text())
    if split['receipt_sha256']!=digest(json.loads(path.read_text())) or read['status']!='complete':return False
    leaves=[b for b in read['batches'] if b['stage']==stage or b['stage'].startswith(stage+'-output-')]
    if sorted(ref for b in leaves for ref in b['focus_user_refs'])!=sorted(split['focus_user_refs']):return False
    from request_retry import read_receipt
    for leaf in leaves:
        name=leaf['stage'];target=path.parent/(name+'.receipt.json')
        if target==path or (target.exists() and read_receipt(target).get('response',{}).get('status')=='incomplete'):
            target=path.parent/(name+'-output-capacity.receipt.json')
        if not target.exists():return False
        final=read_receipt(target)
        if final.get('status')!='received' or final.get('response',{}).get('status')!='completed':return False
    return bool(leaves)


def bundle(packet, day, root, model, *, conversation=False):
    scope=validate(packet['workday_scope'])
    allowed_models={'deepseek-v4.1-flash-local','gpt-6.1-sol','gpt-6.2-sol'} if conversation else {'deepseek-v4.1-flash-local'}
    if scope!=daily_window(day) or model not in allowed_models:
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
            if not completed_capacity_continuation(path,root,receipt):
                raise ValueError('日评含未完成模型请求，禁止导出人员评分')
        receipts.append(dict(request_sha256=receipt['request_sha256'],response_sha256=digest(receipt['response'])))
    review_version=CONVERSATION_VERSION if conversation else DAILY_VERSION
    review=dict(version=review_version,model=model,request_digest=digest(receipts),
        window_start=day+'T08:00:00+08:00',window_end=scope['observed_until'],
        source_labels={},source_sessions={},conversations={},behaviors={})
    report=dict(user_email=packet['user_email'],period_type='day',period_start=day,period_end=scope['period_end'],
        source_digest=digest(packet),workday_scope=scope,policy_versions=dict(scale='pmje-performance-20261105.023-proposal1.control28',
        rules='1.6',aggregation=AGGREGATION_VERSION,review=review_version,scope=DAILY_VERSION,rating='G2-proposal28'),
        daily_review=review,dimensions=[],items=[],limitations=[],score=None,exact=None,rating=None)
    if (root/'empty.json').exists():
        empty=json.loads((root/'empty.json').read_text())
        if str(empty['user_id'])!=str(packet['user_id']) or empty['day']!=day or empty['status'] not in {'no_activity','observation_only'}:
            raise ValueError('空日终态与来源身份不符')
        report.update(score_status=empty['status'],score_reason=empty.get('reason') or ('当日采集窗口内没有可评的本人使用记录。' if empty['status']=='no_activity' else '当日只有简短查询，没有形成可评分的完整任务。'))
    else:
        saved=json.loads((root/'report.json').read_text())
        if saved.get('pilot',{}).get('quality_status')=='failed':
            raise ValueError('日评质量验收已失败，禁止导出人员评分')
        quality_state=json.loads(quality.read_text()).get('quality_status') if quality.exists() else None
        if (quality_state not in ({'model_checked','source_checked'} if conversation else {'model_checked'})
                or saved.get('pilot',{}).get('quality_status')!=quality_state):
            raise ValueError('日评尚未完成来源、归属和评语检查，禁止导出人员评分')
        if saved['period_type']!='day' or saved['period_start']!=day or str(saved['user_id'])!=str(packet['user_id']) or saved['user_email']!=packet['user_email'] or (not receipts and quality_state!='source_checked'):
            raise ValueError('日评结果与冻结人员、日期或请求不符')
        tasks=json.loads((root/'selected-tasks.json').read_text())
        originals={r['id']:r for task in tasks for r in task['records']}
        if quality_state=='source_checked':
            from conversation_attribution import annotate
            from conversation_provenance import verify_background
            source_records=json.loads((root/'source/records.json').read_text())
            checked_tasks,checked_records,attribution=annotate(tasks,source_records)
            verify_background(root,checked_tasks,checked_records,attribution)
        from daily_behaviors import validate as validate_behaviors, check_evidence
        review['behaviors']=copy.deepcopy(saved['pilot'].get('behaviors',{}))
        if review['behaviors'] or not conversation:
            validate_behaviors(review['behaviors'],originals)
        elif any(item['exact'] is not None or item['evidence'] for item in saved['items']):
            raise ValueError('无独立行为的观察日不能携带得分或计分证据')
        report.update(score_status=saved['score_status'],score_reason=saved['score_reason'],items=copy.deepcopy(saved['items']),
            dimensions=copy.deepcopy(saved['dimensions']),score=saved['score'],exact=saved['exact'],
            limitations=copy.deepcopy(saved.get('limitations',[])))
        from scoring_pipeline.weekly_performance.scores022 import packed
        total=observed_total(report['dimensions'])
        if total is None:
            if not conversation:raise ValueError('已选任务没有已观察维度，不能保存为有分日评')
            report.update(score=None,exact=None,score_status='observation_only')
        else:report.update(packed(total),score_status='scored')
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
