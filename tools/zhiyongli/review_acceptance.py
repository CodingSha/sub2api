"""完整人员材料的付费前预检与发布门槛记录；部分候选不能冒充全链路验收。"""
import argparse
from collections import Counter
import json
from pathlib import Path
from types import SimpleNamespace

from bounded_read import raw_size, checkpoint
from candidate_segments import split
from goal_batches import plan
from navigation_access import Access
from pipeline import reading_preflight
from review_replay import select, file_sha
from runtime import CompanyClient, MAX_REQUEST_BYTES, atomic, digest, policy
from source_index import build_view, SourceIndex


def preflight(path, expected_sha, root):
    if file_sha(path)!=expected_sha:raise ValueError('验收原件签名变化')
    packet=select(json.loads(Path(path).read_text()))
    client=CompanyClient(SimpleNamespace(model='deepseek-v4.1-flash-local',timeout=180))
    client.evidence_first=True
    reading_preflight(packet,client)
    view=split(build_view(packet,root));pages=plan(view)
    nav=SourceIndex(root/'context-navigation',packet);rows=[]
    for index,page in enumerate(pages):
        try:
            current=Access(nav,root/'access'/str(index),initial_directory=True).start(
                page,initial_payload=client.identification_payload)
            size=len(json.dumps(client.identification_payload(current),ensure_ascii=False).encode())
            rows.append(dict(batch=index,state='fits' if size<=MAX_REQUEST_BYTES else 'wire_too_large',
                material_bytes=raw_size(current),wire_bytes=size,remaining_bytes=current['context_access']['remaining_bytes']))
        except ValueError as ex:
            rows.append(dict(batch=index,state='blocked',error_type=type(ex).__name__,error=str(ex)))
    if file_sha(path)!=expected_sha:raise ValueError('预检期间原件变化')
    result=dict(source_sha256=expected_sha,events=len(packet['events']),
        candidates=sum(e['actor_class']=='candidate_user' for e in packet['events']),
        key_identity_missing=sum(e.get('api_key_id') is None for e in packet['events']),
        timestamp_basis=dict(Counter(e.get('timestamp_basis','missing') for e in packet['events'])),
        batches=len(pages),initial_batches_fit=all(r['state']=='fits' for r in rows),
        identification_call_lower_bound=len(pages),model_requests=0,whole_pipeline_acceptance=False,rows=rows)
    atomic(root/'result.json',result)
    return packet,result


def inspect_run(packet, root):
    root=Path(root);terminal=root/'model-result.json'
    if not terminal.exists():return dict(state='not_completed',whole_person=False)
    result=json.loads(terminal.read_text())
    source=root/'run/packet.json'
    whole=source.exists() and digest(json.loads(source.read_text()))==digest(packet)
    stamp=root/'run/input.json'
    current=stamp.exists() and json.loads(stamp.read_text()).get('policy_sha256')==policy()
    bundle=root/'run/bundle.json'
    invocations={digest(result):result}
    for path in (root/'model-invocations').glob('*.json'):
        value=json.loads(path.read_text());invocations[digest(value)]=value
    return dict(state=result['state'],whole_person=whole,current_contract=current,
        candidate_file_present=bundle.is_file(),
        source_to_local_product_import_verified=False,
        cumulative_budget_requests=result.get('flow',{}).get('request_budget',{}).get('used'),
        invocation_seconds=result.get('invocation_seconds'),
        total_invocation_seconds=round(sum(r.get('invocation_seconds',0) for r in invocations.values()),3),
        cumulative_budget_wall_seconds=result.get('cumulative_wall_seconds'),
        peak_rss_bytes=result.get('process_peak_rss_bytes'))


def assess(runs, profiles):
    # 不用请求次数、测试数量或有候选推导质量、最大用户容量及9小时SLA。
    completed=[r for r in runs if r['state']=='candidate_generated' and r['whole_person']
        and r.get('current_contract') and r.get('candidate_file_present')]
    failed=[r for r in runs if r['state'] not in {'candidate_generated','not_completed'}
        or r['state']=='candidate_generated' and not r['whole_person']]
    gates=[dict(name='完整包初始批次装配',state='passed' if profiles and all(p['initial_batches_fit'] for p in profiles) else 'failed'),
        dict(name='所选完整人员范围到评分候选',state='failed' if failed else 'passed' if runs and len(completed)==len(runs)==len(profiles) else 'unverified'),
        dict(name='原采集到真实产品导入及完成状态',state='unverified'),
        dict(name='新08至21点范围及晚到修订',state='unverified'),
        dict(name='服务Key精确排除',state='failed' if any(p['key_identity_missing'] for p in profiles) else 'unverified'),
        dict(name='每日调度接入',state='failed'),
        dict(name='周期封口及月度汇总接入',state='failed'),
        dict(name='最大用户及完整名单9小时吞吐',state='unverified'),
        dict(name='独立语义和反证标注验收',state='unverified'),
        dict(name='故障恢复不依赖逐人临时脚本',state='unverified')]
    return dict(deployment_eligible=all(g['state']=='passed' for g in gates),gates=gates,
        full_person_candidates=len(completed),
        limitation='该报告仅按现有证据判定；未测量项不视为通过，历史三日范围不等同新日窗口。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text());root=args.output.resolve()
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    checkpoint(root/'input.json',dict(manifest_sha256=digest(manifest)))
    profiles=[];runs=[]
    for index,item in enumerate(manifest['packets']):
        packet,profile=preflight(Path(item['path']),item['sha256'],root/'preflight'/str(index))
        profiles.append(profile)
        run=inspect_run(packet,item['run_root']) if item.get('run_root') else dict(state='not_completed',whole_person=False)
        run['source_sha256']=item['sha256'];runs.append(run)
    result=assess(runs,profiles)
    history=[]
    for item in manifest.get('prior_runs',[]):
        packet=select(json.loads(Path(item['path']).read_text()))
        if file_sha(item['path'])!=item['sha256']:raise ValueError('历史验收原件签名变化')
        history.append(dict(source_sha256=item['sha256'],**inspect_run(packet,item['run_root'])))
    result.update(profiles=profiles,runs=runs,prior_runs=history,policy_sha256=policy())
    atomic(root/'assessment.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in {'profiles','runs'}},ensure_ascii=False))


if __name__=='__main__':main()
