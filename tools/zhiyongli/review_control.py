"""本地运维核对入口：有理由修订、逐项人工/Codex处置；不越过unknown或发布权限。"""
import argparse
import copy
import fcntl
import hashlib
import json
from pathlib import Path
import time

from runtime import atomic,digest,policy,response_value,validate_review,RUBRIC
from semantic_audit import validate as validate_audit
from shared_weeks import reference,read_ref,source_identity
from request_retry import read_receipt


def verify_files(refs):
    for ref in refs:
        if hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()!=ref['sha256']:
            raise ValueError('前序执行文件变化，不能沿用原修订授权')


def selected(base, *, allow_historical=False):
    base=Path(base);pointer=base/'active-revision.json'
    if not pointer.exists():return base
    proof=read_ref(json.loads(pointer.read_text()))
    directory=Path(proof['directory'])
    stamp=proof.get('policy_sha256')
    if (directory.parent!=base.resolve()/'revisions' or not isinstance(stamp,str) or len(stamp)!=64
        or any(c not in '0123456789abcdef' for c in stamp)
        or not allow_historical and stamp!=policy()):
        raise ValueError('当前修订目录或实际合同不符')
    if allow_historical and (directory/'input.json').exists():
        if json.loads((directory/'input.json').read_text()).get('policy_sha256')!=stamp:
            raise ValueError('历史修订的实际输入合同与修订声明不符')
    verify_files(proof['parent_files'])
    return directory


def is_revision(directory,parent,packet):
    path=Path(directory)/'revision.json'
    if not path.exists():return False
    proof=json.loads(path.read_text())
    if (proof.get('parent_directory')!=str(Path(parent).resolve()) or proof.get('source_identity')!=source_identity(packet)
        or proof.get('policy_sha256')!=policy() or not proof.get('reviewer') or not proof.get('reason')):return False
    verify_files(proof['parent_files'])
    return True


def revise(base,reviewer,reason):
    # 仅创建有理由的新修订时允许定位旧合同；正常执行仍拒绝沿用旧合同结果。
    base=Path(base).resolve();parent=selected(base,allow_historical=True)
    if not reviewer.strip() or not reason.strip():raise ValueError('修订须记录核对人和具体原因')
    if (parent/'bundle.json').exists():raise ValueError('已有已核候选不能通过失败任务修订入口覆盖，须另走报告修订')
    receipts=list(base.rglob('*.receipt.json'))
    if any(read_receipt(p).get('status') in {'unknown','requesting','budget_paused'} for p in receipts):
        raise ValueError('存在结果不明或预算会话，须先核对原请求；不能换目录重发')
    packet=json.loads((parent/'packet.json').read_text())
    files=[reference(p) for p in parent.rglob('*') if p.is_file() and 'revisions' not in p.relative_to(parent).parts
        and p.name not in {'active-revision.json'} and not p.name.endswith('.lock')]
    proof=dict(parent_directory=str(parent),source_identity=source_identity(packet),parent_files=files,
        policy_sha256=policy(),reviewer=reviewer,reason=reason,at=time.time())
    directory=base/'revisions'/digest(proof);directory.mkdir(parents=True,exist_ok=False,mode=0o700)
    proof['directory']=str(directory);atomic(directory/'revision.json',proof)
    atomic(base/'active-revision.json',reference(directory/'revision.json'))
    return directory


def parent_proof(root,unit):
    root=Path(root)
    from audit_readback import receipt as audit_receipt
    result=dict(unit_sha256=digest(unit),policy_sha256=policy(),
        model_receipt=reference(model_receipt(root,unit)),
        model_audit_receipt=reference(audit_receipt(root,unit)) if (root/'audit.receipt.json').exists() else None)
    if (root/'current-model-audit.json').exists():
        from audit_readback import files as audit_files
        result['model_audit_context']=reference(root/'current-model-audit.json')
        result['model_audit_chain']=[reference(path) for path in audit_files(root)]
    return result


def model_receipt(root,unit):
    """处置须绑定补读后的实际候选，不能仍指向首次补读请求。"""
    root=Path(root).resolve();pointer=root/'current-model-review.json'
    if not pointer.exists():return root/'review.receipt.json'
    value=json.loads(pointer.read_text())
    target=Path(value['receipt']['path']).resolve()
    if value.get('unit_sha256')!=digest(unit) or root not in target.parents:
        raise ValueError('当前模型候选未绑定实际补读输入或越出本轮目录')
    read_ref(value['receipt'])
    if value.get('model_context'):
        from model_view import verify, hydrate
        for field in ('native_input','model_context'):
            if root not in Path(value[field]['path']).resolve().parents:raise ValueError('模型视图引用越出本轮目录')
        stamp=read_ref(value['native_input']);view=read_ref(value['model_context'])
        native=copy.deepcopy(unit);appended=set(stamp['appended_event_ids'])
        native['events']=[e for e in native['events'] if e['event'] not in appended]
        if digest(native)!=stamp['native_sha256']:raise ValueError('原生评分输入签名不符')
        packet=json.loads((root/'packet.json').read_text())
        verify(native,view,packet)
        if hydrate(native,view,packet)!=unit:raise ValueError('最终校验材料与已提供的模型视图不符')
        from model_view import check_review
        check_review(view,response_value(unit,read_receipt(target))[0])
    return target


def resolution(root,unit):
    path=Path(root)/'review-resolution.json'
    if not path.exists():return None
    proof=read_ref(json.loads(path.read_text()))
    return validate_resolution(root,unit,proof,reference(path))


def validate_resolution(root,unit,proof,proof_ref):
    if proof.get('parent')!=parent_proof(root,unit):raise ValueError('处置未绑定当前实际输入、模型候选或复查回执')
    if proof.get('reviewer_kind') not in {'human','codex'} or not proof.get('reviewer') or not proof.get('reason'):
        raise ValueError('处置须明确核对者、方式和理由；Codex自检不得称真人独立复核')
    read_ref(proof['parent']['model_receipt'])
    prior=read_receipt(proof['parent']['model_receipt']['path'])
    if prior.get('status')!='received':raise ValueError('未知或被拒请求不能靠语义处置升级为已收到结果')
    review=proof['review'];validate_review(unit,review)
    from scoring_pipeline.weekly_performance.review_policy import REVIEW_POLICY
    if review.get('review_policy')!=REVIEW_POLICY:raise ValueError('处置未采用当前语义合同')
    from audit_readback import context as audit_context
    audit=validate_audit(unit,review,proof['assessment'],RUBRIC['items'],context=audit_context(root,unit))
    if not audit['supported']:raise ValueError('关键争议仍未解决，不能激活完成标记')
    return review,audit,dict(reviewer=proof['reviewer'],reviewer_kind=proof['reviewer_kind'],reason=proof['reason'],
        proof=proof_ref,resolution_sha256=digest(proof),independent_human_review=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True,help='Worker状态根；须先停止该Worker，命令与Worker共用进程锁')
    sub=parser.add_subparsers(dest='command',required=True)
    rev=sub.add_parser('revise');rev.add_argument('--directory',type=Path,required=True)
    rev.add_argument('--reviewer',required=True);rev.add_argument('--reason',required=True)
    for name in ('resolution-template','activate-resolution'):
        p=sub.add_parser(name);p.add_argument('--directory',type=Path,required=True);p.add_argument('--file',type=Path,required=True)
    args=parser.parse_args()
    if args.state.resolve() not in args.directory.resolve().parents:
        parser.error('处置目录必须属于指定Worker的私有状态目录')
    with (args.state/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        execute(args)


def execute(args):
    if args.command=='revise':
        print(json.dumps(dict(directory=str(revise(args.directory,args.reviewer,args.reason)))));return
    root=selected(args.directory);unit=json.loads((root/'unit.json').read_text())
    if args.command=='resolution-template':
        prior=read_receipt(model_receipt(root,unit))
        try:review=response_value(unit,prior)[0]
        except (ValueError,KeyError,TypeError):review={}
        value=dict(parent=parent_proof(root,unit),reviewer='',reviewer_kind='codex',reason='',review=review,
            assessment=dict(items={i:dict(verdict='unresolved',reason='',source_quotes=[],problem_codes=[]) for i in RUBRIC['items']},global_issues=[]))
        if args.file.exists():raise ValueError('处置草稿已存在，不覆盖')
        atomic(args.file,value);return
    # 先在隔离目录完整验证，失败不留下激活指针。
    value=json.loads(args.file.read_text())
    pointer=root/'review-resolution.json'
    if pointer.exists():raise ValueError('已有处置不能覆盖，须显式修订')
    # 固定文件副本只存一次；其后修改草稿不会影响激活结果。
    target=root/('resolution-'+digest(value)+'.json')
    atomic(target,value)
    validate_resolution(root,unit,value,reference(target))
    atomic(pointer,reference(target))
    print(json.dumps(dict(state='resolution_valid',model_requests=0,published=False)))


if __name__=='__main__':main()
