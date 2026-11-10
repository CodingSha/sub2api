"""空期间与月末必要缺口处置：有冻结来源才可确认空，有后续引文才可确认恢复。"""
import copy
from datetime import timedelta
from pathlib import Path
import json

from runtime import atomic, digest, moment, VERSION, RUBRIC
from review_schema import obj, array, enum
from scoring_pipeline.weekly_performance.review022 import ITEMS
from scoring_pipeline.weekly_performance.scores022 import NECESSARY

INSTRUCTIONS = '''本次只判断已证必要缺口截至月末的状态，不重新评分、不改变原期间判断。
逐个slots输出status(open/recovered/unresolved)、reason、source_quotes[{period_id,event,quote}]、recovery_period和recovery_refs。
只能以冻结资料中的实际后续处理解除缺口。AI的计划、用户沉默、没有后续记录均不证明已恢复。
open指根据本月全部已核资料未发现完成恢复，说明观察范围；不能声称现实工作必然未处理。
恢复必须在原期间结束之后的月内后续期间，引用其review.evidence里真实且能支持恢复的引文id。
缺资料而改变恢复判断时记unresolved，不自动open或recovered。只输出JSON。'''


def empty_period(packet):
    """仅确认真正无事件且无来源问题的封口期间，不把未识别本人当作空。"""
    nonwork=packet.get('workday_scope') is not None and not packet['workday_scope']['working_dates']
    if nonwork:
        from workday_scope import validate
        validate(packet['workday_scope'])
    if (not nonwork and (packet.get('events') or packet.get('issues'))) or not packet.get('source_snapshot'):
        raise ValueError('空期间必须由封口的无事件、无问题来源证明')
    lo,hi=moment(packet['since']),moment(packet['until'])
    if lo>=hi:raise ValueError('空期间窗口无效')
    pid=('week-' if hi-lo==timedelta(days=7) and lo.weekday()==0 and lo.time().isoformat()=='00:00:00' else 'fragment-')+lo.date().isoformat()
    unit=dict(version=VERSION,key=packet['user_id']+':'+pid,user_id=packet['user_id'],
        title='已核无记录期间',evaluation_scope='person_week' if pid.startswith('week-') else 'person_week_fragment',
        periods=[dict(period_id=pid,start=packet['since'],end=packet['until'])],events=[],person_events=[],source_units=[],source_keys=[],source_gaps=[])
    if packet.get('workday_scope') is not None:unit['workday_scope']=copy.deepcopy(packet['workday_scope'])
    row=dict(user_id=packet['user_id'],period_id=pid,window=unit['periods'][0],scale_version=VERSION,
        source_keys=[],source_gaps=[],evidence_checked=True,evidence_ids=[],
        cells=[dict(item=i,state='no_opportunity',reason='封口来源核查无事件，不补分') for i in ITEMS])
    return unit,dict(evidence=[]),row


def audited_empty(record):
    packet=record['packet'];unit,review,row=empty_period(packet)
    audit=record['audit']
    if audit.get('packet_sha256')!=digest(packet) or audit.get('expected_person_events')!=0 or audit.get('exact_match') is not True:
        raise ValueError('空期间来源核对签名或计数不符')
    proof=record['audit_ref'];raw=Path(proof['path']).read_bytes()
    import hashlib
    if hashlib.sha256(raw).hexdigest()!=proof['sha256'] or json.loads(raw)!=audit:
        raise ValueError('空期间实际核查回执变化')
    row['empty_source_check']=dict(expected_person_events=0,exact_match=True,audit_path=proof['path'],audit_sha256=proof['sha256'])
    return unit,review,row


def freeze_empty(packet,directory):
    empty_period(packet)
    root=Path(directory);root.mkdir(parents=True,exist_ok=True,mode=0o700)
    audit=dict(packet_sha256=digest(packet),source_snapshot=packet['source_snapshot'],
        expected_person_events=0,exact_match=True,start=packet['since'],end=packet['until'])
    atomic(root/'empty-audit.json',audit)
    import hashlib
    record=dict(packet=packet,audit=audit,audit_ref=dict(path=str((root/'empty-audit.json').resolve()),
        sha256=hashlib.sha256((root/'empty-audit.json').read_bytes()).hexdigest()))
    atomic(root/'empty.json',record);return record


def slots(periods):
    result={}
    for p in periods:
        for c in p['row']['cells']:
            if c['state']=='observed' and c['quality']['label'] in NECESSARY:
                result[p['row']['period_id']+'/'+c['item']]=dict(period_id=p['row']['period_id'],item=c['item'])
    return result


def schema(periods):
    text=dict(type='string');quote=obj(dict(period_id=text,event=text,quote=text))
    return obj(dict(slots=obj({key:obj(dict(status=enum('open','recovered','unresolved'),reason=text,
        source_quotes=array(quote),recovery_period=dict(type=['string','null']),recovery_refs=array(text))) for key in slots(periods)})))


def validate(periods,value,only_keys=None):
    expected=slots(periods);decisions=value.get('slots')
    if only_keys is not None:
        if not set(only_keys)<=set(expected):raise ValueError('逐项核对不能引入未知月末缺口')
        expected={key:expected[key] for key in only_keys}
    if not isinstance(decisions,dict) or decisions.keys()!=expected.keys():raise ValueError('月末必要缺口未逐条处置')
    by_id={p['row']['period_id']:p for p in periods};result=[]
    for key,d in decisions.items():
        if d.get('status') not in {'open','recovered','unresolved'} or not d.get('reason') or not d.get('source_quotes'):
            raise ValueError('月末缺口处置缺状态、理由或实际来源')
        for q in d['source_quotes']:
            p=by_id.get(q.get('period_id'));events={e['event']:e for e in p['unit']['events']} if p else {}
            if q.get('event') not in events or not q.get('quote') or q['quote'] not in events[q['event']]['text']:
                raise ValueError('月末处置引文不在实际期间来源')
        origin=expected[key];source=by_id[origin['period_id']]
        if d['status']=='unresolved':raise ValueError('必要缺口月末状态未决，保留回执待核，不补造处置')
        if d['status']=='recovered':
            recovery=by_id.get(d.get('recovery_period'))
            if not recovery or moment(recovery['row']['window']['start'])<moment(source['row']['window']['end']):
                raise ValueError('恢复必须引用月内后续期间')
            evidence={e['id']:e for e in recovery['review']['evidence']}
            if not d.get('recovery_refs') or not set(d['recovery_refs'])<=evidence.keys():raise ValueError('恢复缺后续已核引文')
            quotes={(q['period_id'],q['event'],q['quote']) for q in d['source_quotes']}
            if not any((d['recovery_period'],evidence[r]['event'],evidence[r]['quote']) in quotes for r in d['recovery_refs']):
                raise ValueError('恢复理由未引用其声明的恢复证据')
        elif d.get('recovery_period') is not None or d.get('recovery_refs'):
            raise ValueError('开放缺口不能混入恢复声明')
        result.append(dict(origin,status=d['status'],reason=d['reason'],recovery_period=d.get('recovery_period'),recovery_refs=d.get('recovery_refs',[])))
    return result
