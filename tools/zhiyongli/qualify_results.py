#!/usr/bin/env python3
"""显式转换已核1.3结果的资格版本；不重评、不抓取、不调用模型。"""
import argparse
import copy
import hashlib
import json
import sys
from datetime import date
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'research/ai-usage-assessment'))
from eligibility import moment, TZ, week
from scoring_pipeline.weekly_performance.review_policy import REVIEW_POLICY

VERSION = 'eligibility-20261109.3'
LEGACY_VERSION = 'eligibility-20261106.2'
MONTH = 'pmje-month-rollup-20261106.025'
WEIGHTS = dict(P=20, M=35, J=30, E=15)
NAMES = dict(P='提示词质量', M='任务驾驭度', J='判断核验', E='使用效率')


def signature(g):
    rows = [dict(source_ref=e['source_ref'], source_hash=e['source_hash'],
                 date=moment(e['occurred_at']).astimezone(TZ).date().isoformat(), task_keys=sorted(e['task_keys']))
            for e in sorted(g['observations'], key=lambda e:e['source_ref'])]
    return hashlib.sha256(json.dumps(dict(observations=rows, dimension_refs={d:sorted(v) for d,v in g['dimension_refs'].items()}),
        sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def coverage(events, code=''):
    days = {moment(e['occurred_at']).astimezone(TZ).date().isoformat() for e in events}
    return dict(code=code, events=len(events), tasks=len({t for e in events for t in e['task_keys']}),
                days=len(days), weeks=len({week(d) for d in days}))


def shown(value):
    n = Fraction(value) * 10
    rounded = (n.numerator * 2 + n.denominator) // (2 * n.denominator)
    return f'{rounded//10}.{rounded%10}'


def qualify(report, weekly_reports=(), *, rules='1.6'):
    """只转换已核合同；最终导入时Go再次独立核算来源、资格与精确分数。"""
    if rules not in {'1.4', '1.5', '1.6'}:
        raise ValueError('未登记的资格规则版本')
    if report['policy_versions']['rules'] != '1.3':
        raise ValueError('必须显式提供已核1.3源报告，不能混用或重复转换')
    if rules == '1.6' and report['policy_versions'].get('review') != REVIEW_POLICY:
        raise ValueError('1.6须有当前语义评审声明，资格转换不能代替上下文复核')
    r = copy.deepcopy(report)
    r['policy_versions']['rules'] = rules
    g = r['eligibility']
    g['version'] = VERSION if rules in {'1.5', '1.6'} else LEGACY_VERSION
    if len({e['source_ref'] for e in g['observations']}) != len(g['observations']):
        raise ValueError('本人来源重复')
    valid = {}
    if r['period_type'] == 'month':
        r['policy_versions']['aggregation'] = MONTH
        r['weekly_reports'] = [qualify(w, rules=rules) for w in weekly_reports]
        for w in r['weekly_reports']:
            if w['period_type'] != 'week' or w['user_email'] != r['user_email'] or not (w['period_start'] < r['period_end'] and r['period_start'] < w['period_end']):
                raise ValueError('月周评来源异人或不在本月')
            key = max(w['period_start'], r['period_start'])
            if key in valid:
                raise ValueError('月周评来源重复')
            if any(i['state'] in {'pending','attribution_unclear'} for i in w['items']):
                raise ValueError('关键未决周不可剔除后发布月分')
            valid[key] = w['score_status'] == 'scored'
            if not w['eligibility']['sources_complete']:
                g['sources_complete'] = False
        for it in r['items']:
            it['components'] = [c for c in it['components'] if valid.get(c['period_start'])]
            it['period_states'] = {s:state if valid.get(s) else 'excluded_week' for s,state in it['period_states'].items()}
            components = it['components']
            days = lambda c:(date.fromisoformat(c['period_end'])-date.fromisoformat(c['period_start'])).days
            it['exact'] = str(sum((Fraction(c['exact'])*days(c) for c in components),Fraction())/sum(map(days,components))) if components else None
            if components:
                it['state'] = 'observed'
            else:
                states = set(it['period_states'].values())
                it['state'] = next(iter(states)) if len(states)==1 else 'mixed_unscored'
            it['criterion'] = '025：仅正式周评对应的月内可评期间参与本分项汇总；缺周不补零。'
            # 原完整周判断保留在后台周快照；月解释只使用本次纳入期间。
            it['reason'] = '\n'.join(line for line in it['reason'].splitlines() if not (len(line)>10 and line[10]=='：') or valid.get(line[:10])) or '本月该项没有可计入的正式周评数值。'
            it['evidence'] = [ev for ev in it['evidence'] if valid.get(max(week(moment(ev['occurred_at']).astimezone(TZ).date().isoformat()),r['period_start']))]
        r['gap_dispositions'] = [g for g in r['gap_dispositions'] if valid.get(g['period_start'])]
        for gap in r['gap_dispositions']:
            if gap['status']=='recovered' and not valid.get(gap['recovery_period']):
                gap.update(status='open', recovery_period='', recovery_evidence_ids=[], reason=gap['reason']+'；该恢复期间无正式周评，未用于解除本月缺口。')
        r['source_digest'] = hashlib.sha256(json.dumps(dict(original=report['source_digest'],
            version=MONTH, weeks=[w['source_digest'] for w in r['weekly_reports']]),sort_keys=True).encode()).hexdigest()
    elif weekly_reports:
        raise ValueError('周报告不能嵌套周报告')

    r['dimensions'] = []
    for d in WEIGHTS:
        items = [i for i in r['items'] if i['item_code'].startswith(d) and i['exact'] is not None]
        gaps = [Fraction(i['exact']) for i in items if i['necessary_gap']]
        if r['period_type']=='month':
            gaps = [Fraction(c['exact']) for i in items for c in i['components'] if c['necessary_gap'] and
                    any(x['item_code']==i['item_code'] and x['period_start']==c['period_start'] and x['status']=='open' for x in r['gap_dispositions'])]
        value = min(gaps) if gaps else sum((Fraction(i['exact']) for i in items),Fraction())/len(items) if items else None
        r['dimensions'].append(dict(code=d, name=NAMES[d],
                                   exact=str(value) if value is not None else None,score=shown(value) if value is not None else None))
    allowed = {d:set() for d in WEIGHTS}
    by_ref = {e['source_ref']:e for e in g['observations']}
    for it in r['items']:
        it['score'] = shown(it['exact']) if it['exact'] is not None else None
        if it['exact'] is None:
            continue
        d = it['item_code'][0]
        for ev in it['evidence']:
            if ev['source_ref'] in by_ref and ev['origin'] in ({'direct_expression','explicit_adoption'}|({'quoted_or_forwarded'} if d=='P' else set())):
                allowed[d].add(ev['source_ref'])
    g['dimension_refs'] = {d:sorted(set(refs)&allowed[d]) for d,refs in g['dimension_refs'].items()}
    all_events = coverage(g['observations'])
    g.update(task_count=all_events['tasks'],active_days=all_events['days'],active_weeks=all_events['weeks'],route='standard',reasons=[],dimension_coverage=[])
    if not g['sources_complete']: g['reasons'].append('来源或期间评测尚未完成')
    for dim in r['dimensions']:
        d = dim['code']
        c = coverage([by_ref[ref] for ref in g['dimension_refs'][d]],d)
        g['dimension_coverage'].append(c)
        if not c['events']: g['reasons'].append(d+'维度尚无可评本人依据')
        if dim['exact'] is None: g['reasons'].append(d+'维度没有可评数值')
    if rules == '1.4' and r['period_type']=='week' and g['task_count']<20:
        g['route']='insufficient_task_coverage'
        g['reasons'].append(f"本周独立任务{g['task_count']}个，未达到20个，暂不形成周评分")
    if r['period_type']=='month':
        g.update(valid_weeks=sum(valid.values()),total_weeks=len(r['coverage']))
        if g['valid_weeks']<3:
            g['route']='insufficient_week_coverage'
            g['reasons'].append(f"本月有效周评{g['valid_weeks']}周，不足3周，暂不形成月评分及评级")
    if any(i['state'] in {'pending','attribution_unclear'} for i in r['items']): g['reasons'].append('存在待复核或本人归属未决的判断')
    if any(i['status']=='blocking' for i in r.get('assessment',{}).get('issues',[])): g['reasons'].append('存在尚未解决的关键资料或判断问题')
    g['eligible']=not g['reasons']
    g['source_digest']=signature(g)
    r['exact']=r['score']=r['rating']=None
    r['score_status']='scored' if g['eligible'] else 'observation_only' if g['sources_complete'] else 'data_pending'
    if g['eligible']:
        value=sum((Fraction(d['exact'])*WEIGHTS[d['code']]/100 for d in r['dimensions']),Fraction())
        r['exact'],r['score']=str(value),shown(value)
        if r['period_type']=='month': r['rating']='L'+str(1+sum(value>=v for v in (60,70,80,90)))
        r['score_reason']=''
    else:
        r['score_reason']='；'.join(g['reasons'])+'。保留已核分项和维度观察，暂不形成完整周期综合分。'
    return r


def qualify_bundle(bundle, *, rules='1.6'):
    weeks=[r for r in bundle['reports'] if r['period_type']=='week']
    reports=[qualify(r,[w for w in weeks if w['user_email']==r['user_email'] and w['period_start']<r['period_end'] and r['period_start']<w['period_end']], rules=rules)
             if r['period_type']=='month' else qualify(r, rules=rules) for r in bundle['reports']]
    return dict(schema_version=1,source_name=bundle['source_name']+'+qualification'+rules,reports=reports)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--rules-version', choices=('1.4', '1.5', '1.6'), default='1.6')
    args=p.parse_args()
    bundle=json.loads(args.input.read_text())
    value=qualify_bundle(bundle, rules=args.rules_version)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f:
        json.dump(value,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(reports=len(value['reports']),sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())))


if __name__=='__main__': main()
