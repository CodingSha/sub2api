#!/usr/bin/env python3
"""把持久Worker中已核结果接到既有产品导入合同；不调用模型或自动发布。"""
import argparse
import json
from pathlib import Path

from runtime import Queue, digest, load_ref, validate_review
from export_results import convert
from export_reviewed import month_report


def bundle(q, manifest):
    reports=[]
    for entry in manifest['reports']:
        job=q.job(entry['job'])
        spec=json.loads(job['spec'])
        if job['kind']=='review':
            if job['state']!='accepted':raise ValueError('评审未完成语义核对，不导出')
            result=json.loads(job['result']);unit=load_ref(spec['unit'])
            validate_review(unit,result['value'])
            if unit['user_id']!=entry['source_user_id']:raise ValueError('本人身份与显式映射不符')
            period=unit['periods'][0]
            source=dict(entry,period_type='week',period_start=period['start'][:10],period_end=period['end'][:10],
                evidence_checked=True,periods=[dict(unit={'queue_ref':'unit'},review={'queue_ref':'review'})])
            report,_=convert(Path('.'),source,lambda r:unit if r['queue_ref']=='unit' else result['value'])
        else:
            if job['state']!='completed' or not spec.get('manifest'):raise ValueError('月来源未完成或缺已签名的覆盖清单')
            native=load_ref(spec['manifest'])
            if native['user_id']!=entry['source_user_id']:raise ValueError('月来源身份与映射不符')
            if spec.get('product_bundle'):
                # 已经原生导出的月份复用同一份证据包，不再复制全部原文装配输入。
                cached=load_ref(spec['product_bundle']);proof=load_ref(spec['export_proof'])
                if proof.get('manifest_sha256')!=spec['manifest']['sha256'] or proof.get('bundle_sha256')!=spec['product_bundle']['sha256'] or proof.get('result_sha256')!=digest(json.loads(job['result'])) or not proof.get('reviewer') or not proof.get('reason'):
                    raise ValueError('月度导出缓存的来源核对声明失效')
                candidates=[r for r in cached['reports'] if r['user_email']==entry['user_email'] and r['period_type']=='month'
                    and r['period_start']==native['start'][:10] and r['period_end']==native['end'][:10]]
                if len(candidates)!=1:raise ValueError('月度导出缓存身份/窗口不唯一')
                report=candidates[0]
                # 缓存沿用实际采用的合同，不能在缺观察来源时冒充1.3。
                computed=json.loads(job['result'])
                if any(i['exact']!=computed['items'][i['item_code']]['exact'] for i in report['items']):
                    raise ValueError('月度导出分项与当前队列汇总不一致')
            elif not spec.get('assessment') or not spec.get('period_units'):
                raise ValueError('导出月份须显式提供资料处置和逐期间原生输入，不猜测文件位置')
            else:
                assessment=load_ref(spec['assessment'])
                units={pid:load_ref(ref) for pid,ref in spec['period_units'].items()}
                from scoring_pipeline.weekly_performance.month_rollup import bound_period
                for ref in native['periods']:
                    snap=load_ref(ref);row=snap['row']
                    if not row['source_keys']:continue
                    current=bound_period(units[row['period_id']],snap['value'])
                    for field in ('cells','source_keys','source_gaps','evidence_ids'):
                        if current[field]!=row[field]:raise ValueError('月期间输入/快照不一致：'+field)
                source=dict(entry,period_type='month',period_start=native['start'][:10],period_end=native['end'][:10])
                report,computed=month_report(source,native,assessment,units)
                if computed!=json.loads(job['result']):raise ValueError('队列汇总结果与当前原生来源不一致')
        report['source_digest']=digest(dict(job=job['id'],policy=job['policy'],result=json.loads(job['result']),
            exported_source=report['source_digest']))
        reports.append(report)
    return dict(schema_version=1,source_name=manifest['source_name'],reports=reports)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rules-version',choices=('source','1.4','1.5','1.6'),default='1.6',help='默认1.6，须有当前语义评审声明，月包须包含对应完整周；source仅供保留源合同的历史导出')
    args=parser.parse_args()
    q=Queue(args.state)
    try:
        value=bundle(q,json.loads(args.manifest.read_text()))
        if args.rules_version in {'1.4', '1.5', '1.6'}:
            from qualify_results import qualify_bundle
            value=qualify_bundle(value, rules=args.rules_version)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2)
        print(json.dumps(dict(reports=len(value['reports']),sha256=__import__('hashlib').sha256(args.output.read_bytes()).hexdigest())))
    finally:q.db.close()


if __name__=='__main__':main()
