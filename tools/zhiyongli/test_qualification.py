"""资格转换与算术回归；不是语义评分准确率验证。"""
import copy
from datetime import date, timedelta
import unittest
from qualify_results import qualify as qualify_versioned, shown, NAMES, REVIEW_POLICY


def qualify(report, weekly_reports=()):
    # 历史1.4合同仍按旧门槛回归。
    return qualify_versioned(report, weekly_reports, rules='1.4')


def weekly(tasks=20, start='2026-09-07'):
    observations=[dict(source_ref=f'{start}:{i}',source_hash='b'*64,occurred_at=start+'T09:00:00+08:00',task_keys=[f'task:{i}']) for i in range(tasks)]
    items=[]
    for d in 'PMJE':
        for n in '123':
            items.append(dict(item_code=d+n,state='observed',exact='80',necessary_gap=False,
                evidence=[dict(source_ref=observations[0]['source_ref'],origin='direct_expression',occurred_at=observations[0]['occurred_at'])],
                period_states={},components=[],reason='本期实际行为已核。'))
    return dict(user_email='test@example.test',period_type='week',period_start=start,
        period_end=(date.fromisoformat(start)+timedelta(days=7)).isoformat(),policy_versions=dict(rules='1.3',aggregation='023',review=REVIEW_POLICY),
        eligibility=dict(sources_complete=True, observations=observations,dimension_refs={d:[observations[0]['source_ref']] for d in 'PMJE'}),
        items=items,dimensions=[dict(code=d,name=NAMES[d]) for d in 'PMJE'],source_digest='a'*64,gap_dispositions=[],score='old')


def monthly(weeks):
    r=weekly()
    r.update(period_type='month',period_start='2026-09-01',period_end='2026-10-01',coverage=[])
    dates=['2026-09-01','2026-09-07','2026-09-14','2026-09-21','2026-09-28','2026-10-01']
    r['coverage']=[dict(period_start=a,period_end=b,evidence_checked=True) for a,b in zip(dates,dates[1:])]
    r['eligibility']['observations']=[e for w in weeks for e in w['eligibility']['observations']]
    r['eligibility']['dimension_refs']={d:[w['eligibility']['observations'][0]['source_ref'] for w in weeks] for d in 'PMJE'}
    for i,it in enumerate(r['items']):
        it['period_states']={c['period_start']:'no_opportunity' for c in r['coverage']}
        it['components']=[];it['evidence']=[]
        for w in weeks:
            it['period_states'][w['period_start']]='observed'
            it['components'].append(dict(period_start=w['period_start'],period_end=w['period_end'],exact='80',necessary_gap=False))
            it['evidence'].extend(w['items'][i]['evidence'])
    return r


class QualificationTests(unittest.TestCase):
    def test_twenty_tasks_one_day_and_single_dimension_source(self):
        for count in (1,19,20,100):
            original=weekly(count);before=copy.deepcopy(original)
            result=qualify(original)
            self.assertEqual(count>=20,result['eligibility']['eligible'])
            self.assertEqual('80.0' if count>=20 else None,result['score'])
            self.assertEqual(before,original)

    def test_month_three_weeks_no_zero_or_extra_month_gate(self):
        weeks=[weekly(start=s) for s in ('2026-09-07','2026-09-14','2026-09-21')]
        for count in (1,2,3):
            result=qualify(monthly(weeks[:count]),weeks[:count])
            self.assertEqual(count,result['eligibility']['valid_weeks'])
            self.assertEqual('80.0' if count==3 else None,result['score'])
            self.assertEqual('L4' if count==3 else None,result['rating'])
        self.assertEqual(20,result['eligibility']['task_count'])

    def test_low_performance_counts_and_unknown_source_not_zero(self):
        report=weekly()
        for it in report['items']: it['exact']='60'
        result=qualify(report)
        self.assertEqual('60.0',result['score'])
        report['eligibility']['sources_complete']=False
        result=qualify(report)
        self.assertEqual('data_pending',result['score_status'])
        self.assertIsNone(result['score'])

    def test_exact_rounding_and_version_isolation(self):
        self.assertEqual('77.3',shown('309/4'))
        self.assertEqual('90.0',shown('8999/100'))
        with self.assertRaises(ValueError):qualify(qualify(weekly()))
        report=weekly();report['eligibility']['observations'].append(report['eligibility']['observations'][0])
        with self.assertRaises(ValueError):qualify(report)


class EvidenceOnlyQualificationTests(unittest.TestCase):
    def test_one_goal_one_day_can_score_without_task_count_gate(self):
        for count in (1, 4, 5, 19, 20, 100):
            report = weekly(count)
            result = qualify_versioned(report)
            self.assertEqual('1.6', result['policy_versions']['rules'])
            self.assertTrue(result['eligibility']['eligible'])
            self.assertEqual('80.0', result['score'])
        self.assertIsNone(qualify(weekly(1))['score'])

    def test_missing_evidence_or_unfinished_source_still_blocks(self):
        report = weekly(1)
        report['eligibility']['dimension_refs']['E'] = []
        self.assertIsNone(qualify_versioned(report)['score'])
        report = weekly(1)
        report['eligibility']['sources_complete'] = False
        self.assertEqual('data_pending', qualify_versioned(report)['score_status'])

    def test_three_small_weeks_can_support_month(self):
        weeks = [weekly(1, start=s) for s in ('2026-09-07', '2026-09-14', '2026-09-21')]
        result = qualify_versioned(monthly(weeks), weeks)
        self.assertEqual(3, result['eligibility']['valid_weeks'])
        self.assertEqual(1, result['eligibility']['task_count'])
        self.assertEqual('80.0', result['score'])
        self.assertTrue(all(w['policy_versions']['rules'] == '1.6' for w in result['weekly_reports']))

    def test_semantic_version_cannot_be_added_by_qualification(self):
        report = weekly(1)
        del report['policy_versions']['review']
        with self.assertRaisesRegex(ValueError, '语义评审声明'):
            qualify_versioned(report)
        historical = qualify_versioned(report, rules='1.5')
        self.assertEqual('80.0', historical['score'])
        self.assertNotIn('review', historical['policy_versions'])
        weeks = [weekly(1, start=s) for s in ('2026-09-07','2026-09-14','2026-09-21')]
        month = monthly(weeks)
        del weeks[0]['policy_versions']['review']
        with self.assertRaisesRegex(ValueError, '语义评审声明'):
            qualify_versioned(month, weeks)


if __name__=='__main__':unittest.main()
