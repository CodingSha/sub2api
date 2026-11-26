"""比对独立生成的候选与Codex参考；结构测试不能充当发布质量结论。"""
from runtime import digest


def compare(reference, replay_root):
    """输入为同一冻结来源、规则的十二项结果。语义目标由Codex另行核验。"""
    from review_provenance import candidate_from_replay
    candidate=candidate_from_replay(replay_root,reference)
    report=compare_verified(reference,candidate)
    report['provenance_verified']=True
    return report


def compare_verified(reference, candidate):
    """纯比较函数仅供统计；发布证据必须经compare从磁盘回执重建。"""
    if reference['source_sha256']!=candidate['source_sha256'] or reference['rubric_sha256']!=candidate['rubric_sha256']:
        raise ValueError('参考与候选的原文或规则不一致')
    if reference.get('reviewer')!='codex_direct_review' or not reference.get('review_notes'):
        raise ValueError('缺少Codex实际复核记录')
    if candidate.get('origin')!='verified_model_receipts' or not candidate.get('receipt_hashes'):
        raise ValueError('候选须来自独立模型回执，不能把参考结论复制为候选')
    if reference.get('candidate_seen_before_reference') is not False:
        raise ValueError('参考必须先于查看候选冻结，避免循环校准')
    expected={r['case_id']:r for r in reference['cases']}
    actual={r['case_id']:r for r in candidate['cases']}
    if len(expected)!=len(reference['cases']) or len(actual)!=len(candidate['cases']):
        raise ValueError('校准案例ID重复')
    details=[];cited=valid=matched=items=0;fp=fn=positive=negative=0
    conflicts=[]
    for case_id in sorted(set(expected)&set(actual)):
        exp,got=expected[case_id],actual[case_id]
        if set(exp['items'])!=set(got['items']):
            conflicts.append(dict(case_id=case_id,kind='item_set'));continue
        for code,expected_item in exp['items'].items():
            item=got['items'][code];items+=1
            if (expected_item['state'],expected_item['band'])==(item['state'],item['band']): matched+=1
            else: conflicts.append(dict(case_id=case_id,item=code,expected=dict(state=expected_item['state'],band=expected_item['band']),
                actual=dict(state=item['state'],band=item['band'])))
            evidence=item['evidence'];rows=got['source_records']
            if item['state']=='observed' and not evidence:
                conflicts.append(dict(case_id=case_id,item=code,kind='missing_evidence'))
            for quote in evidence:
                cited+=1
                row=rows.get(quote['record_id'])
                if row and row['role']=='user' and quote['quote'] and row['text'].count(quote['quote'])==1:valid+=1
            if code=='E3':
                e=expected_item['state']=='observed';a=item['state']=='observed'
                positive+=int(e);negative+=int(not e);fp+=int(a and not e);fn+=int(e and not a)
        details.append(dict(case_id=case_id,requires_goal_and_attribution_check=True))
    def rate(n,d):return n/d if d else None
    return dict(provenance_verified=False,source_sha256=reference['source_sha256'],rubric_sha256=reference['rubric_sha256'],
        implementation_sha256=candidate.get('implementation_sha256'),reference_sha256=digest(reference),candidate_sha256=digest(candidate),cases=len(details),items=items,
        state_and_band_agreement=rate(matched,items),cited_quotes=cited,valid_quotes=valid,
        citation_validity=rate(valid,cited),e3_positive_cases=positive,e3_negative_cases=negative,
        e3_false_positives=fp,e3_false_negatives=fn,e3_false_positive_rate=rate(fp,negative),e3_false_negative_rate=rate(fn,positive),
        missing_cases=sorted(set(expected)-set(actual)),unexpected_cases=sorted(set(actual)-set(expected)),
        conflicts=conflicts,details=details,semantic_acceptance=False)


def release_gate(report, acceptance, operational):
    """只有绑定实际对照结果的Codex裁定和运行证明，才可建议发布。"""
    failures=[]
    if report.get('provenance_verified') is not True:failures.append('未核验模型原始回执')
    if not report['cases'] or report['missing_cases'] or report['unexpected_cases']:failures.append('校准案例不完整')
    if report['valid_quotes']!=report['cited_quotes']:failures.append('引文无效')
    if not report['e3_positive_cases'] or not report['e3_negative_cases']:failures.append('缺少真实E3正例或反例')
    if acceptance.get('comparison_sha256')!=digest(report) or acceptance.get('reviewer')!='codex_direct_review':
        failures.append('缺少绑定当前比较结果的Codex复核')
    for key in ('goal_checked','actor_checked','e3_checked','missing_evidence_checked','conflicts_resolved'):
        if acceptance.get(key) is not True:failures.append('未核验：'+key)
    if not acceptance.get('rationale') or acceptance.get('approved') is not True:failures.append('Codex未放行')
    if not report.get('implementation_sha256') or operational.get('implementation_sha256')!=report['implementation_sha256']:
        failures.append('待发布实现与已校准实现不同')
    for key in ('source_frozen','all_users_complete','arithmetic_verified','failure_isolation_verified','no_unresolved_requests','no_unresolved_truncations','no_unresolved_contract_errors'):
        if operational.get(key) is not True:failures.append('运行条件未通过：'+key)
    return dict(status='pass' if not failures else 'blocked',failures=failures,
        comparison_sha256=digest(report),production_changed=False)


def main():
    import argparse
    import json
    from pathlib import Path
    from runtime import atomic
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--replay-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--acceptance',type=Path)
    parser.add_argument('--operational',type=Path)
    args=parser.parse_args()
    reference=json.loads((args.replay_root/'frozen-reference.json').read_text())
    report=compare(reference,args.replay_root)
    acceptance=json.loads(args.acceptance.read_text()) if args.acceptance else {}
    operational=json.loads(args.operational.read_text()) if args.operational else {}
    gate=release_gate(report,acceptance,operational)
    atomic(args.output,dict(comparison=report,release_gate=gate))
    print(json.dumps(dict(cases=report['cases'],items=report['items'],release_status=gate['status'],
        conflicts=len(report['conflicts']),production_changed=False),ensure_ascii=False))


if __name__=='__main__':main()
