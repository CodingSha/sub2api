"""一次生成评分和读者说明；程序核引用及计算，不伪造另次模型复查。"""
import copy
import json
import re
from pathlib import Path

MODE = 'single-pass-20261116.1'
INSTRUCTIONS = '''
本次采用single-pass：在同一次回答完成十二项评判、评分原因、邻档原因及代表任务解读，不等待另一个模型替你作判断。
先根据原文核本人行为、实际机会、归属、支持与反证、相邻档及重复计分，再输出最终判断。AI自主执行不得记为本人行为，材料缺失不得推定表现差。固定评分规则及未知状态保持不变。
person_week_fragment只生成月内片段评判，presentation=null；其余presentation.items逐项解释为什么有这个评分，或本期为什么没有评分；higher_band解释紧邻更高档缺少的具体表现依据，最高档不编更高档，未评分项只解释未评分原因。禁止编造事实或数值。
面向读者直接说明具体行为、事实和计分影响，不写“已核查、待核查、待复核、待证明、仍需核实、暂不判断”等流程状态，也不让用户自行解决判断问题。有证据不足时明确“记录未包含什么，本项不计分/不足以支持更高档”，不得把未确定事实写成确定事实。
presentation_case如非null，给出代表任务的全部record_ids；正文来自当前events及导航。必要正文尚未读取时先提出needs_events或needs_nodes，presentation=null，不同时提交结论。最终presentation.case必须解读该任务，claims分别说明目标、过程和结果，各自绑定真实record_ids和对应item_codes。没有评分作用的背景段可用unresolved，其reason仍须说明具体事实和不计分原因。
案例只解释相关行为，不能以单个案例替代完整评审范围。任务结果只说记录实际支持的结果；没有外部验收记录时直接说明记录中没有验收结果，不把AI自述冒充成功。没有presentation_case时case=null，不编造任务。
'''


def active(client):
    return getattr(client, '_single_pass', False) is True


def select_case(unit, *, visible_events=None):
    """仅选完整保存且有界的正式任务；不裁长任务、也不影响全周评分范围。"""
    from task_interpretation import records_for, size
    from scoring_pipeline.weekly_performance.task_context import TaskContextIndex
    index = TaskContextIndex(unit)
    visible=set(visible_events) if visible_events is not None else None
    for source in unit['source_units']:
        if source['source_kind'] != 'task':
            continue
        task, records, limits, status = records_for(unit, source['key'], context_index=index)
        if visible is not None and not {record['id'] for record in records}<=visible:
            continue
        if (records and len(records) <= 1000 and size(records) <= 120_000
                and len(task['title'].encode()) <= 600 and len((task.get('purpose') or task['title']).encode()) <= 6000):
            return dict(task_key=task['key'], title=task['title'], goal=task.get('purpose') or task['title'],
                record_ids=[r['id'] for r in records], records=records, limitations=limits, context_status=status)
    return None


def descriptor(case):
    return {k: v for k, v in case.items() if k in ('task_key', 'title', 'goal', 'record_ids')} if case else None


def extend_schema(schema, case, *, fragment=False):
    from review_schema import obj, array, enum
    schema = copy.deepcopy(schema)
    if fragment:
        schema['properties']['presentation'] = dict(type='null')
        schema['required'].append('presentation')
        return schema
    text = dict(type='string', minLength=1, maxLength=2400)
    codes = tuple(__import__('runtime').RUBRIC['items'])
    item = obj(dict(item_code=enum(*codes), basis=text, higher_band=text))
    case_schema = dict(type='null')
    if case:
        # 正负评价必须绑定分项；不让模型生成结构合法、到汇合才失败的空归属。
        claim = dict(anyOf=[obj(dict(text=text,
            item_codes=dict(array(enum(*codes)),uniqueItems=True,**({'minItems':1} if kind!='unresolved' else {})),
            record_ids=dict(array(enum(*case['record_ids'])),minItems=1,uniqueItems=True),
            assessment=obj(dict(kind=enum(kind),reason=text)))) for kind in ('support','shortfall','unresolved')])
        case_schema = obj(dict(task_key=enum(case['task_key']), outcome=text,
            claims=dict(array(claim), minItems=3, maxItems=12)))
    presentation = obj(dict(items=dict(array(item), minItems=12, maxItems=12), case=case_schema))
    schema['properties']['presentation'] = dict(anyOf=[dict(type='null'), presentation])
    schema['required'].append('presentation')
    return schema


def validate_case(spec,case,*,visible_events):
    """在当前模型回合内核案例，展示合同错误只纠正该回合。"""
    from runtime import RUBRIC
    from task_interpretation import clean,delivery_text
    if case is None:
        if spec is not None:raise ValueError('没有正式案例来源，不能补造案例')
        return []
    if not spec or spec['task_key']!=case['task_key']:raise ValueError('同次任务解读未绑定选定来源')
    ids=set(case['record_ids'])
    if not ids<=set(visible_events):raise ValueError('任务解读所需正文未全部提供给本次模型，禁止声称已读')
    if not isinstance(spec['outcome'],str) or not spec['outcome'].strip() or len(spec['outcome'])>2400:
        raise ValueError('任务结果说明为空或过长')
    delivery_text(spec['outcome'],6000,'任务结果')
    if not 3<=len(spec['claims'])<=12:raise ValueError('任务解读须包含目标、过程和结果的具体说明')
    claims=clean(copy.deepcopy(spec['claims']))
    for claim in claims:
        refs=claim['record_ids'];codes=claim['item_codes'];assessment=claim['assessment']
        if (not refs or len(refs)!=len(set(refs)) or not set(refs)<=ids
                or not set(codes)<=set(RUBRIC['items']) or len(codes)!=len(set(codes))
                or assessment['kind'] not in ('support','shortfall','unresolved')
                or assessment['kind']!='unresolved' and not codes
                or any(not isinstance(t,str) or not t.strip() or len(t)>2400 for t in (claim['text'],assessment['reason']))):
            raise ValueError('任务解读的评价或来源引用无效：正负评判须有分项，背景须用unresolved，引用须唯一且来自案例')
        delivery_text(claim['text'],6000,'任务分析');delivery_text(assessment['reason'],2400,'评价原因')
        claim['limitations']=[]
    return claims


def build(packet, unit, review, presentation, case, *, visible_events):
    from runtime import RUBRIC, validate_review
    from export_results import convert, safe
    from qualify_results import qualify_bundle
    from task_evidence import VERSION, sha
    from task_interpretation import clean, delivery_text
    validate_review(unit, review)
    if not isinstance(presentation, dict) or set(presentation) != {'items', 'case'}:
        raise ValueError('一次评分缺少同次生成的说明')
    items = presentation['items']
    if len(items) != 12 or {r['item_code'] for r in items} != set(RUBRIC['items']):
        raise ValueError('一次评分说明须对应十二个不同分项')
    entry = dict(user_email=packet['user_email'], source_user_id=packet['user_id'], period_type='week',
        period_start=packet['since'][:10], period_end=packet['until'][:10], evidence_checked=True,
        periods=[dict(unit={'name': 'unit'}, review={'name': 'review'})])
    report, _ = convert(Path('.'), entry, lambda ref: unit if ref['name'] == 'unit' else review)
    explanations = {r['item_code']: r for r in items}
    for item in report['items']:
        authored = explanations[item['item_code']]
        if any(not isinstance(authored[k], str) or not authored[k].strip() or len(authored[k]) > 2400
               for k in ('basis', 'higher_band')):
            raise ValueError('一次评分说明为空或过长')
        if any(re.search('待观察|待支持|待审查|待审核|待复核|待核查|仍需核实|暂不判断',authored[k])
               for k in ('basis','higher_band')):
            raise ValueError('分项说明须给出本次终判，不得把判断留给后续审核')
        reason = safe(clean(authored['basis'])) + '邻档判断：' + safe(clean(authored['higher_band']))
        delivery_text(reason, 6000, '分项原因')
        item['reason'] = reason
    # 此标识只记录生成方式，不宣称独立审核，也不改变固定评分规则。
    report['limitations'].append('评分与说明由同次AI评判生成，程序校验引用、归属字段和计算；未进行另次模型或独立人工复核。')
    result = qualify_bundle(dict(schema_version=1, source_name='自动评分:'+MODE+':'+unit['key'], reports=[report]))
    report = result['reports'][0]
    spec = presentation['case']
    claims=validate_case(spec,case,visible_events=visible_events)
    if case is None:
        return report, result
    formal = list(dict.fromkeys(k for row in report['eligibility']['observations'] for k in row['task_keys']))
    task = dict(id=sha(case['task_key']), task_key=case['task_key'], title=safe(clean(case['title'])),
        goal=safe(clean(case['goal'])), outcome=safe(clean(spec['outcome'])), claims=claims,
        records=case['records'], record_count=len(case['records']), context_status=case['context_status'],
        limitations=case['limitations'])
    report['task_review'] = dict(version=VERSION, status='generated', task_keys=formal, checked_task_keys=[],
        scope_note='评价范围以报告周期及工作日为准；案例解释具体行为，分数依据本期全部评审材料。',
        cases=[task], week_judgments=[])
    return report, result


def evaluate(packet, unit, root, client, progress):
    from runtime import atomic, normalize_review_locations, digest, validate_review
    from pipeline import review_materials, received
    from bounded_read import checkpoint
    from source_index import enabled
    if enabled(client):
        # 展示案例是可选项；只选择首轮实际可见的完整案例，不能让展示拖垮评分。
        # 原生完整任务及计分范围仍保留，模型后续仍可补读；不放宽build的可见性校验。
        from bounded_read import raw_size
        from pipeline import DIRECT_BYTES
        from model_view import create
        initial=create(unit) if raw_size(unit)>DIRECT_BYTES else unit
        visible=[e['event'] for e in initial['events']]
        case=None if unit['evaluation_scope']=='person_week_fragment' else select_case(unit,visible_events=visible)
        checkpoint(root/'single-pass-case-selection.json',dict(version='visible-initial-input-20261117.1',
            unit_sha256=digest(unit),visible_event_ids=visible,case_sha256=digest(case)))
    else:
        case = None if unit['evaluation_scope'] == 'person_week_fragment' else select_case(unit)
    checkpoint(root/'single-pass-case.json', case)
    client.presentation_case = descriptor(case)
    def validate_final(native,view,value):
        candidate=copy.deepcopy(value);presentation=candidate.pop('presentation',None)
        review,_=normalize_review_locations(native,candidate)
        validate_review(native,review)
        from model_view import check_review
        check_review(view,review)
        if native['evaluation_scope']!='person_week_fragment':
            build(packet,native,review,presentation,case,visible_events=[e['event'] for e in view['events']])
    unit, view, record, projected, _ = review_materials(packet, unit, root, client, progress,validate_final=validate_final)
    value = received(record)
    presentation = value.pop('presentation', None)
    review, adaptation = normalize_review_locations(unit, value)
    validate_review(unit, review)
    from model_view import check_review
    check_review(view, review)
    atomic(root/'model-review.json', dict(value=review, adaptation=adaptation))
    atomic(root/'review.json', dict(value=review, adaptation=adaptation))
    checkpoint(root/'single-pass-presentation.json', presentation)
    if unit['evaluation_scope'] == 'person_week_fragment':
        return None
    report, bundle = build(packet, unit, review, presentation, case, visible_events=[e['event'] for e in view['events']])
    atomic(root/'native-report.json', report)
    atomic(root/'bundle.json', bundle)
    checkpoint(root/'single-pass.json', dict(version=MODE, unit_sha256=digest(unit),
        review_sha256=digest(review), presentation_sha256=digest(presentation),
        case_sha256=digest(case), bundle_sha256=digest(bundle), separate_model_audit=False))
    progress('checking', 1, 1, '评分及原因已生成，引用与计算校验通过')
    return bundle


def verify(entry, data):
    """共享复用重验同次回执并确定性重建，不用supported=true冒充模型复查。"""
    from runtime import normalize_review_locations, digest, response_text
    from shared_weeks import read_ref
    from review_control import model_receipt
    from request_retry import read_receipt
    root = Path(entry['directory']); unit = data['unit.json']; review = data['review.json']['value']
    receipt = read_receipt(model_receipt(root, unit))
    if receipt.get('status') != 'received':
        raise ValueError('一次评分缺少成功回执，禁止重发或放行')
    from review_protocol import decode
    value = decode(json.loads(response_text(receipt['response']))); presentation = value.pop('presentation', None)
    if normalize_review_locations(unit, value)[0] != review:
        raise ValueError('一次评分候选与原回执不一致')
    if presentation != read_ref(entry['files']['single-pass-presentation.json']):
        raise ValueError('一次评分说明与原回执不一致')
    case = read_ref(entry['files']['single-pass-case.json'])
    # 案例仍由冻结输入确定，不允许换成另一任务。
    if case != select_case(unit):
        raise ValueError('一次评分案例与冻结来源不符')
    pointer = json.loads((root/'current-model-review.json').read_text())
    view = read_ref(pointer['model_context']) if pointer.get('model_context') else unit
    report, bundle = build(data['packet.json'], unit, review, presentation, case,
        visible_events=[e['event'] for e in view['events']])
    expected = dict(version=MODE, unit_sha256=digest(unit), review_sha256=digest(review),
        presentation_sha256=digest(presentation), case_sha256=digest(case), bundle_sha256=digest(bundle), separate_model_audit=False)
    if (read_ref(entry['files']['single-pass.json']) != expected or bundle != data['bundle.json']
            or report != read_ref(entry['files']['native-report.json'])):
        raise ValueError('一次评分共享结果与回执导出不一致')
    return bundle
