"""连续对话候选的独立回放；保留原模型累计账簿，不导入生产。"""
import argparse
from dataclasses import replace
import fcntl
import hashlib
import json
from pathlib import Path

from bounded_read import checkpoint
from conversation_review import assess, VERSION
from daily_assessment import checked_call
from model_config import ModelConfig
from model_flow import ModelFlow
from runtime import atomic, digest
from task_review_policy import RUBRIC
from worker import JobClient


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('source','reference','config','flow-state','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--source-sha256',required=True)
    parser.add_argument('--reference-sha256',required=True)
    parser.add_argument('--flow-revision',type=Path)
    parser.add_argument('--request-limit',type=int,default=32)
    parser.add_argument('--reasoning-effort',choices=['low','medium','high'])
    parser.add_argument('--comparison-model',choices=['gpt-6.1-sol','gpt-6.2-sol'],help='仅在同一公司入口进行隔离模型对照；不改变正式模型配置或导出合同')
    parser.add_argument('--reuse-root',type=Path,help='同一来源继续验证时复用原阶段和原任务请求账簿')
    parser.add_argument('--budget-root',type=Path,help='多轮接续使用同一原任务请求账簿，不重置发送次数')
    parser.add_argument('--service-recovery-proof',type=Path,help='原三次过载续跑已耗尽且有新健康回执时，仅对绑定原请求增加一次恢复机会')
    args=parser.parse_args()
    if not 1<=args.request_limit<=272:parser.error('隔离验证的原任务累计预算须为1—272')
    for path,sha in ((args.source,args.source_sha256),(args.reference,args.reference_sha256)):
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:parser.error('冻结文件哈希不符')
    source=json.loads(args.source.read_text());reference=json.loads(args.reference.read_text())
    if args.reuse_root:
        parent=args.reuse_root.parent
        if (json.loads((parent/'frozen-source.json').read_text())!=source
                or json.loads((parent/'frozen-reference.json').read_text())!=reference
                or not ((args.budget_root or parent)/'request-budget.json').is_file()):
            parser.error('接续须绑定同一冻结来源、参考及原请求账簿')
    elif args.budget_root:
        if (json.loads((args.budget_root/'frozen-source.json').read_text())!=source
                or json.loads((args.budget_root/'frozen-reference.json').read_text())!=reference
                or not (args.budget_root/'request-budget.json').is_file()):
            parser.error('模型对照须绑定同一冻结来源、参考及原请求账簿')
    if (reference.get('reviewer')!='codex_direct_review' or reference.get('candidate_seen_before_reference') is not False
            or reference.get('source_sha256')!=args.source_sha256 or reference.get('rubric_sha256')!=digest(RUBRIC)):
        parser.error('参考须独立冻结并绑定当前来源及规则')
    contract=json.loads((args.flow_state/'model-contract.json').read_text())
    settings=contract['settings']
    if args.flow_revision:
        revision=json.loads(args.flow_revision.read_text())
        if (revision['parent_contract_sha256']!=digest(contract)
                or revision['settings']['budget_key']!=settings['budget_key']
                or not revision.get('reason') or revision['ledger_count_at_revision']<settings['max_requests']):
            parser.error('预算修订缺少原合同及原账簿绑定')
        settings=revision['settings']
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    config=ModelConfig.load(args.config)
    if args.comparison_model:config=replace(config,model=args.comparison_model,timeout=max(900,config.timeout))
    budget_root=args.budget_root or (args.reuse_root.parent if args.reuse_root else root)
    checkpoint(root/'frozen-source.json',source);checkpoint(root/'frozen-reference.json',reference)
    checkpoint(root/'replay-contract.json',dict(version=VERSION,source_sha256=args.source_sha256,
        reference_sha256=args.reference_sha256,model=config.model,endpoint=config.responses_url,
        settings=settings,request_limit=args.request_limit,
        request_budget_root=str(budget_root),comparison_only=bool(args.comparison_model),production_model_changed=False))
    with (root/'replay.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        client=JobClient(config,root,args.request_limit,args.request_limit,
            flow=ModelFlow(args.flow_state/'flow',settings),state_root=args.flow_state,
            budget_root=budget_root)
        if args.reuse_root:client.reuse_root=args.reuse_root
        client.reasoning_effort=args.reasoning_effort;client.temperature=0
        client.dimension_coverage=True
        if args.service_recovery_proof:
            import time
            from request_retry import read_receipt
            from model_config import response_text
            recovery=json.loads(args.service_recovery_proof.read_text())
            if (not args.reuse_root or recovery.get('max_extra_requests')!=1
                    or recovery.get('budget_key')!=settings['budget_key']
                    or recovery.get('budget_root')!=str(budget_root.resolve())
                    or recovery.get('source_sha256')!=args.source_sha256):
                parser.error('恢复合同缺少原来源、账簿或单次追加上限')
            def receipt(row):
                value=read_receipt(Path(row['path']))
                if digest(value)!=row['sha256'] or value.get('status')!='received':
                    parser.error('恢复合同回执签名变化或终态不明')
                return value
            failures=[receipt(row) for row in recovery['prior_failed_receipts']]
            if len(failures)!=4:parser.error('恢复须保留原请求及三次过载续跑的全部回执')
            for value in failures:
                response=value.get('response')or{};error=response.get('error')or{}
                if (value.get('request_sha256')!=recovery['request_sha256']
                        or value.get('model')!=config.model or value.get('endpoint')!=config.responses_url
                        or response.get('status')!='failed' or response.get('output')
                        or error.get('type')!='service_unavailable_error' or error.get('code')!='server_error'):
                    parser.error('仅已收到、无输出的同一过载请求允许受限恢复')
            healthy=receipt(recovery['healthy_probe']);response=healthy.get('response')or{}
            if (healthy.get('model')!=config.model or healthy.get('endpoint')!=config.responses_url
                    or response.get('status')!='completed' or response_text(response).strip()!='OK'
                    or healthy['sent_at']<=max(row['received_at'] for row in failures)
                    or not 0<=time.time()-healthy['received_at']<=900):
                parser.error('须提供全部失败之后十五分钟内同模型、同入口的新健康回执')
            client.service_recovery=dict(stage=recovery['stage'],request_sha256=recovery['request_sha256'],
                verified_at=healthy['received_at'],contract_sha256=digest(recovery),max_extra_requests=1)
            checkpoint(root/'service-recovery-contract.json',recovery)
        try:
            result,reads=assess(client,root/'candidate',source['tasks'],source['originals'],checked_call)
            atomic(root/'result.json',dict(status='candidate',value=result,reads=reads,
                semantic_acceptance=False,production_changed=False))
            print(json.dumps(dict(status='candidate',production_changed=False,semantic_acceptance=False)))
        except Exception as error:
            atomic(root/'failure.json',dict(type=type(error).__name__,reason=str(error),
                state=getattr(error,'state',None),reason_code=getattr(error,'reason_code',None),production_changed=False))
            raise


if __name__=='__main__':main()
