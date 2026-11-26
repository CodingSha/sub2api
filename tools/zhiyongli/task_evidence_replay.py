"""新任务证据流程的独立回放入口；只产生候选，不触发生产导入。"""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path

from bounded_read import checkpoint
from daily_assessment import checked_call
from model_config import ModelConfig
from model_flow import ModelFlow
from runtime import digest, atomic
from task_evidence_flow import run
from task_review_policy import RUBRIC
from worker import JobClient


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True,help='冻结JSON：tasks与originals，后者保留task_id')
    parser.add_argument('--source-sha256',required=True)
    parser.add_argument('--reference',type=Path,required=True,help='Codex独立阅读原文后先冻结的参考')
    parser.add_argument('--reference-sha256',required=True)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--flow-state',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--request-limit',type=int,default=100)
    parser.add_argument('--turn-limit',type=int,default=None,help='本次允许新发送数，默认采用总请求预算；不再每4次强制中断')
    args=parser.parse_args()
    raw=args.source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=args.source_sha256:parser.error('冻结来源哈希不符')
    if args.turn_limit is None:args.turn_limit=args.request_limit
    if not 1<=args.turn_limit<=args.request_limit:parser.error('请求预算非法')
    reference_raw=args.reference.read_bytes()
    if hashlib.sha256(reference_raw).hexdigest()!=args.reference_sha256:parser.error('Codex参考哈希不符')
    reference=json.loads(reference_raw)
    if reference.get('candidate_seen_before_reference') is not False or reference.get('reviewer')!='codex_direct_review':
        parser.error('须先独立生成Codex参考，不能看候选后反写参考')
    if reference['source_sha256']!=args.source_sha256:parser.error('参考与回放来源不一致')
    if reference['rubric_sha256']!=digest(RUBRIC):parser.error('Codex参考规则不属于当前候选规则版本')
    source=json.loads(raw);root=args.output.resolve();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    checkpoint(root/'frozen-source.json',source)
    checkpoint(root/'frozen-reference.json',reference)
    config=ModelConfig.load(args.config)
    settings=json.loads((args.flow_state/'model-contract.json').read_text())['settings']
    checkpoint(root/'replay-contract.json',dict(source_sha256=args.source_sha256,source_payload_sha256=digest(source),reference_sha256=digest(reference),request_limit=args.request_limit,
        flow_settings_sha256=digest(settings),model=config.model,endpoint=config.responses_url))
    with (root/'replay.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        client=JobClient(config,root,args.request_limit,args.turn_limit,
            flow=ModelFlow(args.flow_state/'flow',settings),state_root=args.flow_state)
        client.reasoning_effort=None;client.temperature=0
        result=run(client,root/'candidate',source['tasks'],source['originals'],checked_call)
        if hashlib.sha256(args.source.read_bytes()).hexdigest()!=args.source_sha256:
            raise ValueError('回放期间冻结来源被改动')
        if hashlib.sha256(args.reference.read_bytes()).hexdigest()!=args.reference_sha256:
            raise ValueError('回放期间Codex参考被改动')
        atomic(root/'result.json',result)
        print(json.dumps(dict(status=result['status'],completed=len(result['results']),failed=len(result['failures']),production_changed=False)))


if __name__=='__main__':main()
