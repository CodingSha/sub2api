#!/usr/bin/env python3
"""接入已签名期间快照和显式资料处置；旧023/024结果保持原样。"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from export_results import base_report, convert, evidence_for, safe, ITEMS, PERFORMANCE, NECESSARY
from release_scope import assess
from eligibility import attach
from scoring_pipeline.weekly_performance.monthly_cli import calculate, load_ref
from scoring_pipeline.weekly_performance.review_policy import attach_review_policy


def month_report(entry, manifest, assessment, units):
    computed = calculate(manifest)
    if assessment['original_result']['sha256'] != hashlib.sha256(Path(assessment['original_result']['path']).read_bytes()).hexdigest():
        raise ValueError('资料审查所依赖的原结果已改变')
    original = json.loads(Path(assessment['original_result']['path']).read_text())
    if original != computed:
        raise ValueError('资料审查与当前原生计算不匹配')
    snapshots = [load_ref(ref) for ref in manifest['periods']]
    result = base_report(entry, dict(manifest=manifest, assessment=assessment))
    sources = [(units[s['row']['period_id']], s['value'], s['row']) for s in snapshots if s['row']['source_keys']]
    attach_review_policy(result, (s[1] for s in sources))
    attach(result, sources, complete=all(s['row']['evidence_checked'] for s in snapshots))
    checked = assess(computed, assessment['issues'], result['eligibility'])
    result['coverage'] = [dict(period_start=s['row']['window']['start'][:10],
        period_end=s['row']['window']['end'][:10], evidence_checked=s['row']['evidence_checked']) for s in snapshots]
    for code in ITEMS:
        item = computed['items'][code]
        states = set(item['states'].values())
        components, evidence, reasons = [], [], []
        for snap in snapshots:
            row = snap['row']
            c = next(c for c in row['cells'] if c['item'] == code)
            if c['state'] == 'observed' and c['performance']['verdict'] != 'unresolved':
                components.append(dict(period_start=row['window']['start'][:10],period_end=row['window']['end'][:10],
                    exact=str(PERFORMANCE[c['performance']['verdict']]), necessary_gap=c['quality']['label'] in NECESSARY))
                unit = units[row['period_id']]
                evidence.extend(evidence_for(unit, snap['value'], c))
                reasons.append(row['window']['start'][:10]+'：'+safe(c['performance']['reason']))
        # 同源引文若跨期间引用，应分别具有当期ID；不得静默去掉不同证据。
        if len({e['id'] for e in evidence}) != len(evidence):
            raise ValueError('期间引文ID冲突')
        result['items'].append(dict(item_code=code,name=ITEMS[code],
            state='observed' if item['exact'] is not None else next(iter(states)) if len(states)==1 else 'mixed_unscored',
            exact=item['exact'], score=None,reason='\n'.join(reasons) or '本月未形成该项可评数值，逐期间状态完整保留。',
            criterion='024：按实际可评期间覆盖的日历天数汇总本分项；未知部分不补数。',necessary_gap=False,
            components=components,evidence=evidence,limitations=[],
            period_states={s['row']['window']['start'][:10]:next(c['state'] for c in s['row']['cells'] if c['item']==code) for s in snapshots}))
    by_period = {s['row']['period_id']: s['row'] for s in snapshots}
    for gap in manifest.get('gap_dispositions', []):
        recovery = by_period.get(gap.get('recovery_period'))
        unit = units.get(gap.get('recovery_period'))
        result['gap_dispositions'].append(dict(item_code=gap['item'],
            period_start=by_period[gap['period_id']]['window']['start'][:10],status=gap['status'],
            reason=safe(gap['reason']),recovery_period=recovery['window']['start'][:10] if recovery else '',
            recovery_evidence_ids=[unit['key']+':'+rid for rid in gap.get('recovery_refs',[])] if unit else []))
    result['assessment'] = dict(version=checked['version'],source_gap_digest=checked['source_gap_digest'],scope=checked['scope'],
        issues=[dict(id=d['id'],status=d['status'],
            description=safe(d['source']['needed'] if isinstance(d['source'],dict) else d['source']), reason=safe(d['reason'])) for d in checked['issues']])
    if not checked['gradeable']:
        result['score_reason'] = '分数仅覆盖当前可评范围；资料缺失仍阻断完整月级。'
    elif any(isinstance(g, str) and g.endswith('：unobservable') for g in computed['source_gaps']):
        result['score_reason'] = '本月观察满足评级条件；局部不可观察部分未补分，资料限制保留。'
    elif any(g['status']=='open' for g in result['gap_dispositions']):
        result['score_reason'] = '本月仍有未处理必要缺口，相关维度按相应最低缺口分计分。'
    result['limitations'] = ['本轮为冻结本地记录的Codex评审结果；资料提示逐条保留，不等同于员工错误或扣分。']
    return result, computed


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--assessments',type=Path,required=True,help='显式已核资料处置文件，须保留original_result引用')
    args=p.parse_args()
    root=Path(__file__).resolve().parents[2]
    sys.path.insert(0,str(root/'outputs/zhiyongli-september-frozen-retest-20261105'))
    import retest as source
    old=root/'outputs/zhiyongli-high-anchor-audit-20261105'
    assessments=json.loads(args.assessments.read_text())
    reports,checks=[],[]
    for user in source.USERS:
        manifest=json.loads((old/f'manifests/{user}.json').read_text())
        units={}
        for ref in manifest['periods']:
            snap=load_ref(ref);row=snap['row']
            if not row['source_keys']: continue
            week=row['window']['start'][8:10]
            unit=source.current_week(user)[0] if week=='07' else source.assemble(user,week)[0]
            # 在导出边界重新校验原生引文、本人归属、集合及版本。
            from scoring_pipeline.weekly_performance.month_rollup import bound_period
            current=bound_period(unit,snap['value'])
            for k in ('cells','source_keys','source_gaps','evidence_ids'):
                if current[k]!=row[k]: raise ValueError('期间快照与原生输入不匹配：'+k)
            units[row['period_id']]=unit
        entry=dict(user_email=user+'@apsat.com',source_user_id=user,period_type='month',period_start='2026-09-01',period_end='2026-10-01')
        report,computed=month_report(entry,manifest,assessments[user],units)
        reports.append(report)
        checked=assess(computed,assessments[user]['issues'],report['eligibility'])
        checks.append(dict(user=user,score=computed['score'],exact=computed['exact'],
                           gradeable=checked['gradeable'],assessment_version=checked['version']))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:
        json.dump(dict(schema_version=1,source_name='high-anchor-audit-20261105+eligibility-20261106.1',reports=reports),f,ensure_ascii=False,indent=2)
    with args.output.with_suffix('.checks.json').open('x') as f:json.dump(checks,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(reports=len(reports),sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())))

if __name__=='__main__':main()
