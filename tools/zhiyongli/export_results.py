#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把原生、已复核的023周评/024月汇总导出为AI Hub结果包；不调用模型。"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'research/ai-usage-assessment'))
from scoring_pipeline.weekly_performance.review022 import check, VERSION, ITEMS, PERFORMANCE, RUBRIC
from scoring_pipeline.weekly_performance.scores022 import summarize, NECESSARY
from scoring_pipeline.weekly_performance.month_rollup import bound_period, summarize_month, VERSION as MONTH_VERSION
from eligibility import attach
from scoring_pipeline.weekly_performance.review_policy import attach_review_policy

TZ = ZoneInfo('Asia/Shanghai')
NAMES = dict(P='提示词质量', M='任务驾驭度', J='判断核验', E='使用效率')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def load_ref(base, ref):
    path = (base / ref['path']).resolve()
    raw = path.read_bytes()
    if digest(raw) != ref['sha256']:
        raise ValueError('输入哈希不一致：' + str(path))
    return json.loads(raw)


def safe(text):
    """仅输出判定引用；凭证脱敏，不输出原始审计请求体或磁盘位置。"""
    text = re.sub(r'(?i)((?:password|authorization|api[_-]?key|secret|token|密码|密钥)[\"\s:=：]+)([^\s,\"}\n]+)', r'\1[已脱敏]', text)
    return re.sub(r'\bsk-[A-Za-z0-9_-]{16,}', '[已脱敏]', text)


def evidence_for(unit, review, cell):
    refs = list(dict.fromkeys(ref for key in ('opportunity', 'action', 'quality') for ref in cell.get(key, {}).get('refs', [])))
    source = {e['id']: e for e in review['evidence']}
    events = {e['event']: e for e in unit['events']}
    result = []
    for ref in refs:
        ev = source[ref]
        event = events[ev['event']]
        if ev['quote'] not in event['text']:
            raise ValueError('引用不在原始事件中')
        quote = safe(ev['quote'])
        # 原始通道与本人归属分开；用户消息也可能是转发材料。
        actor = event['actor_class']
        role = 'user' if actor == 'human' else 'assistant' if actor == 'assistant' else 'tool' if actor.startswith('tool') else 'context'
        tasks = [s for s in unit.get('source_units', []) if ev['event'] in s.get('person_events', [])]
        titles = list(dict.fromkeys(s['title'] for s in tasks))
        offset = event['text'].index(ev['quote'])
        excerpt = safe(event['text'][max(0, offset-300):min(len(event['text']), offset+len(ev['quote'])+500)])
        background = '\n'.join(safe(s.get('purpose', s['title'])) for s in tasks)
        context = ('原任务背景（来源索引）：'+(background or safe(unit['title']))+
                   '\n本项已核判断及限制：'+safe(cell['reason'])+'\n来源原文片段：\n'+excerpt)
        limits = []
        if event.get('truncated'):
            limits.append('来源事件存在截断，仅使用已保存的可核部分。')
        if ev.get('limitation'):
            limits.append(safe(ev['limitation']))
        if quote != ev['quote']:
            limits.append('凭证已脱敏。')
        result.append(dict(id=unit['key'] + ':' + ref, source_ref=ev['event'],
                           source_hash=digest(event['text'].encode()), task_title=safe(' / '.join(titles) if titles else unit['title']),
                           occurred_at=event['at'], role=role, origin=ev['origin'], quote=quote,
                           supports=safe(cell.get('action', {}).get('fact', cell['reason'])),
                           context=context, limitations=limits))
    return result


def base_report(entry, sources):
    return dict(user_email=entry['user_email'], period_type=entry['period_type'],
                period_start=entry['period_start'], period_end=entry['period_end'],
                score_status='scored', score_reason='', exact=None, score=None, rating=None,
                policy_versions=dict(scale=VERSION, aggregation=MONTH_VERSION if entry['period_type'] == 'month' else VERSION,
                                     rating='G2-proposal28', rules='1.2'),
                dimensions=[], items=[], limitations=[], gap_dispositions=[], coverage=[],
                source_digest=digest(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode()))


def convert(base, entry, loader=None):
    if entry.get('evidence_checked') is not True:
        raise ValueError('只接入已完成人工或当前评审会话证据核对的结果，结构检查不能替代语义核对')
    sources = []
    identity = set()
    for ref in entry['periods']:
        if 'empty' in ref:
            if entry['period_type']!='month':raise ValueError('空期间不能伪造正式周评分')
            from month_evidence import audited_empty
            unit,review,row=audited_empty(loader(ref['empty']) if loader else load_ref(base,ref['empty']))
        else:
            unit = loader(ref['unit']) if loader else load_ref(base, ref['unit'])
            review = loader(ref['review']) if loader else load_ref(base, ref['review'])
            if 'value' in review:
                review = review['value']
            row = bound_period(unit, review) if entry['period_type'] == 'month' else check(unit, review)[0]
        identity.add(unit['user_id'])
        row['evidence_checked'] = True
        sources.append((unit, review, row))
    if identity != {entry['source_user_id']}:
        raise ValueError('来源与显式身份映射不一致')
    result = base_report(entry, entry['periods'])
    work_scope=entry.get('workday_scope')
    if entry['period_type']=='week' and sources:work_scope=sources[0][0].get('workday_scope')
    if work_scope is not None:
        from workday_scope import validate,VERSION as SCOPE_VERSION
        validate(work_scope)
        result['workday_scope']=work_scope
        result['policy_versions']['scope']=SCOPE_VERSION
    attach_review_policy(result, (s[1] for s in sources if s[2]['source_keys']))
    if entry['period_type'] == 'week':
        if len(sources) != 1:
            raise ValueError('周评只能引用一次原生完整周判定')
        unit, review, row = sources[0]
        computed = summarize([row], expected_keys=unit['source_keys'])
        if unit['evaluation_scope'] != 'person_week':
            raise ValueError('局部或月评不能作为周评分')
        window = unit['periods'][0]
        if window['start'][:10] != entry['period_start'] or window['end'][:10] != entry['period_end']:
            raise ValueError('结果窗口与来源不一致')
        for cell in row['cells']:
            observed = cell['state'] == 'observed' and cell['performance']['verdict'] != 'unresolved'
            result['items'].append(dict(item_code=cell['item'], name=ITEMS[cell['item']],
                state=cell['state'] if not (cell['state'] == 'observed' and not observed) else 'pending',
                exact=str(PERFORMANCE[cell['performance']['verdict']]) if observed else None, score=None,
                reason=safe(cell['performance']['reason'] if observed else cell['reason']),
                criterion=RUBRIC['performance_anchors'].get(cell['item'], {}).get(cell.get('performance', {}).get('verdict'),
                    ITEMS[cell['item']] + '：按023对应表现档与已证必要缺口判定'),
                necessary_gap=observed and cell['quality']['label'] in NECESSARY, components=[], period_states={},
                evidence=evidence_for(unit, review, cell) if observed else [], limitations=[]))
        result['limitations'] = [safe(g if isinstance(g,str) else
            g['needed']+'；'+g['why_changes_current_judgment']) for g in computed.get('source_gaps', [])]
    elif entry['period_type'] == 'month':
        dispositions = entry.get('gap_dispositions', [])
        computed = summarize_month([s[2] for s in sources],
            start=entry['period_start'] + 'T00:00:00+08:00', end=entry['period_end'] + 'T00:00:00+08:00',
            gap_dispositions=dispositions)
        if not computed['publishable']:
            from release_scope import assess
            assessment=assess(computed,entry.get('scope_decisions',[]),entry.get('scope_eligibility'))
            if not assessment['gradeable']:
                raise ValueError('月来源或证据尚未核完，不导出可发布数字：' + str(computed['source_gaps']))
            result['assessment']=dict(version=assessment['version'],source_gap_digest=assessment['source_gap_digest'],
                scope=assessment['scope'],issues=[dict(id=d['id'],status=d['status'],
                    description=safe(d['source'] if isinstance(d['source'],str) else json.dumps(d['source'],ensure_ascii=False)),
                    reason=safe(d['reason'])) for d in assessment['issues']])
            result['limitations'].extend(safe(d['reason']) for d in assessment['issues'])
        result['coverage'] = [dict(period_start=row['window']['start'][:10], period_end=row['window']['end'][:10], evidence_checked=True)
                              for _, _, row in sorted(sources, key=lambda s: s[2]['window']['start'])]
        for code in ITEMS:
            item = computed['items'][code]
            states = set(item['states'].values())
            periods, evidence, reasons = [], [], []
            for unit, review, row in sources:
                cell = next(c for c in row['cells'] if c['item'] == code)
                if cell['state'] == 'observed' and cell['performance']['verdict'] != 'unresolved':
                    periods.append(dict(period_start=row['window']['start'][:10], period_end=row['window']['end'][:10],
                                        exact=str(PERFORMANCE[cell['performance']['verdict']]),
                                        necessary_gap=cell['quality']['label'] in NECESSARY))
                    evidence.extend(evidence_for(unit, review, cell))
                    reasons.append(row['window']['start'][:10] + '：' + safe(cell['performance']['reason']))
            result['items'].append(dict(item_code=code, name=ITEMS[code],
                state='observed' if item['exact'] is not None else next(iter(states)) if len(states)==1 else 'mixed_unscored', exact=item['exact'], score=None,
                reason='\n'.join(reasons) if reasons else '本月各评价期间未形成该分项的数值，逐期间状态保留在计算依据中。',
                criterion='024：按可评期间覆盖的日历天数汇总本分项',
                necessary_gap=False, components=sorted(periods,key=lambda p:p['period_start']), evidence=evidence, limitations=[],
                period_states={row['window']['start'][:10]: next(c['state'] for c in row['cells'] if c['item']==code) for _,_,row in sources}))
        by_period = {row['period_id']: row for _, _, row in sources}
        for g in dispositions:
            recovery = by_period.get(g.get('recovery_period'))
            converted_refs = []
            if recovery:
                unit = next(u for u, _, row in sources if row['period_id'] == g['recovery_period'])
                converted_refs = [unit['key'] + ':' + ref for ref in g['recovery_refs']]
            result['gap_dispositions'].append(dict(item_code=g['item'], period_start=by_period[g['period_id']]['window']['start'][:10],
                status=g['status'], reason=safe(g['reason']), recovery_period=recovery['window']['start'][:10] if recovery else '',
                recovery_evidence_ids=converted_refs))
    else:
        raise ValueError('未知周期类型')
    if computed['exact'] is None:
        result['score_reason'] = '部分维度本期没有可评数值，未形成总分。'
    elif any(g['status']=='open' for g in result['gap_dispositions']):
        result['score_reason'] = '本月仍有未处理必要缺口，相关维度按相应最低缺口分计分。'
    # 已核判断不等于来源完整；受限或缺失来源必须继续阻断完整周期出分。
    attach(result, sources, complete=not any(row.get('source_gaps') for _, _, row in sources))
    return result, computed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    reports = [convert(args.manifest.parent, entry)[0] for entry in manifest['reports']]
    value = dict(schema_version=1, source_name=manifest['source_name'], reports=reports)
    # 不覆盖既有导出文件，便于来源追溯。
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write('\n')
    print(json.dumps(dict(report_count=len(reports), sha256=digest(args.output.read_bytes()))))


if __name__ == '__main__':
    main()
