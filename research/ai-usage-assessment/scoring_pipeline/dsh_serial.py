"""v11单主Agent执行器：分批装配、续跑和程序检查，最终合理性由Codex验收。"""
import argparse
import collections
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import dsh_v11 as base
from .common import require
from .progressive_handoff import check_review
from .progressive_review import VERSION

EXECUTION_VERSION='serial-20261102.1'

def payload_hash(a):
 b={k:v for k,v in a.items()if k not in {'execution','acceptance_status'}}
 return hashlib.sha256(json.dumps(b,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def validate(data,a):
 require(a.get('key')in data['tasks']and a.get('policy_version')==VERSION,'未知key或评分版本不符')
 if a.get('auditor'):
  base.check_provenance(a)
 else:
  e=a.get('execution',{});require(e.get('mode')in {'standalone','legacy_draft'},'缺真实执行方式')
  require(e.get('run_id')and e.get('receipt'),'缺执行批次或记录')
  r=e['receipt'];require(base.sha(r['path'])==r['sha256'],'执行记录SHA不符')
  record=base.read(r['path']);require(record['payload_sha256']==payload_hash(a),'评审内容与冻结执行记录不符')
  if e['mode']=='standalone':
   require(e.get('session_id_status')in {'observed','unavailable'},'会话来源状态缺失')
   require(bool(e.get('session_id'))==(e['session_id_status']=='observed'),'会话ID来源不一致')
   require(a.get('self_check',{}).get('decision')=='completed'and a['self_check'].get('reason'),'缺单Agent自检说明；不可冒称独立复核')
 require(a.get('acceptance_status')in {None,'pending_codex'},'不得自行宣布Codex验收通过')
 return check_review(base.task(data,a['key']),a)

def inventory(data,run):
 good={};fail=[]
 for p in sorted((run/'reviews').glob('*.json')):
  try:
   a=base.read(p);rows=validate(data,a);require(a['key']not in good,'重复key')
   good[a['key']]=dict(path=str(p.resolve()),value=a,rows=rows)
  except Exception as ex:fail.append(dict(path=str(p),error=type(ex).__name__+': '+str(ex)))
 blocked=[k for k,u in data['tasks'].items()if u['local_only']and k not in good]
 pending=[k for k,u in data['tasks'].items()if not u['local_only']and k not in good]
 return good,pending,blocked,fail

def progress(data,run):
 good,pending,blocked,fail=inventory(data,run)
 d=dict(policy_version=VERSION,execution_version=EXECUTION_VERSION,source_total=len(data['tasks']),completed_contract_checks=len(good),task_period_rows=sum(len(x['rows'])for x in good.values()),pending_keys=pending,blocked_keys=blocked,invalid_files=fail,acceptance_status='pending_codex',semantics_or_fairness_validated=False)
 run.mkdir(parents=True,exist_ok=True);tmp=run/'progress.tmp';tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');tmp.replace(run/'progress.json')
 return d

def freeze(data,run,a,mode,session_id=None,origin=None):
 # 内容先经过合同检查；该检查不替代人工/模型的真实语义审查。
 require(a.get('policy_version')==VERSION,'评分版本不符')
 require(a.get('key')in data['tasks'],'未知key')
 if mode=='standalone':require(not data['tasks'][a['key']]['local_only'],'受限单元不能由普通主Agent通道处理')
 source_refs=[data['source_groups'][g]for g in data['tasks'][a['key']].get('source_groups',[])]
 for src in source_refs:require(base.sha(src['path'])==src['sha256'],'当前任务来源变化，停止依赖')
 check_review(base.task(data,a['key']),a)
 if mode=='standalone':
  require(a.get('self_check',{}).get('decision')=='completed'and a['self_check'].get('reason'),'草稿须实际完成自检并写明理由')
  require(not a.get('auditor')and not a.get('reviewer'),'普通主Agent草稿不伪造团队身份；旧稿请通过init导入')
 rid=str(uuid.uuid4());record=dict(run_id=rid,execution_version=EXECUTION_VERSION,policy_version=VERSION,key=a['key'],mode=mode,session_id=session_id,session_id_status='observed'if session_id else 'unavailable',recorded_at=datetime.now(timezone.utc).isoformat(),payload_sha256=payload_hash(a),source_refs=source_refs,origin=origin,scope='执行内容的本地快照与程序校验，非客户端独立执行证明或最终合理性验收。')
 rp=run/'receipts'/(rid+'.json');base.write(rp,record)
 a=dict(a,acceptance_status='pending_codex',execution=dict(mode=mode,run_id=rid,session_id=session_id,session_id_status=record['session_id_status'],receipt=dict(path=str(rp.resolve()),sha256=base.sha(rp))))
 validate(data,a);base.write(run/'reviews'/(base.keyhash(a['key'])+'.json'),a)

def initialize(data,run,old):
 for src in list(data['source_groups'].values())+data.get('frozen_files',[]):require(base.sha(src['path'])==src['sha256'],'来源或冻结规则变化')
 require(not run.exists(),'续跑使用status/next，不重复init或覆盖目录');run.mkdir(parents=True)
 used={};rejected=[]
 if old:
  # 已复核结果优先；剩余草稿只标明待Codex验收，绝不冒充独立审定。
  for p in sorted((old/'reviews').glob('*.json')):
   try:
    a=base.read(p);base.check_provenance(a);check_review(base.task(data,a['key']),a)
    require(a.get('policy_version')==VERSION,'旧版不复用')
    dest=run/'reviews'/p.name;base.write(dest,a);used[a['key']]='legacy_peer_review'
   except Exception as ex:rejected.append(dict(path=str(p),error=type(ex).__name__+': '+str(ex)))
  for p in sorted((old/'drafts').glob('*/*.json')):
   try:
    a=base.read(p)
    if a['key']in used:continue
    require(a.get('policy_version')==VERSION,'旧版不复用')
    if a.get('reviewer'):
     r=a['reviewer']['receipt'];require(base.sha(r['path'])==r['sha256'],'旧主评记录变化')
    freeze(data,run,a,'legacy_draft',origin=dict(path=str(p.resolve()),sha256=base.sha(p)));used[a['key']]='legacy_draft_pending_codex'
   except Exception as ex:rejected.append(dict(path=str(p),error=type(ex).__name__+': '+str(ex)))
 base.write(run/'import-record.json',dict(imported=collections.Counter(used.values()),keys=used,rejected=rejected,scope='仅复用同版合同有效内容；并未由程序确认评分合理。'))
 return progress(data,run)

def next_batch(data,run,count):
 require(1<=count<=10,'每批1至10个单元，默认5');state=progress(data,run)
 require(not state['invalid_files'],'存在无效结果文件，先修复而非掩盖')
 # 接续下一批前清理已完成批次的可再生成输入；结果、回执和来源不动。
 clean_inputs(data,run)
 pending=set(state['pending_keys'])
 for p in sorted((run/'batches').glob('*.json')):
  b=base.read(p);left=[k for k in b['keys']if k in pending]
  if left:return dict(batch=str(p.resolve()),pending_in_batch=left,resume=True)
 keys=state['pending_keys'][:count]
 if not keys:return dict(done_regular=True,blocked=state['blocked_keys'],acceptance_status='pending_codex')
 name='batch-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:6]
 files=[]
 for k in keys:
  f=run/'inputs'/name/(base.keyhash(k)+'.json');base.write(f,base.packet(data,k));files.append(dict(key=k,path=str(f.resolve()),sha256=base.sha(f)))
 path=run/'batches'/(name+'.json');base.write(path,dict(policy_version=VERSION,keys=keys,files=files))
 return dict(batch=str(path.resolve()),pending_in_batch=keys,resume=False)

def clean_inputs(data,run):
 good,_,_,fail=inventory(data,run);require(not fail,'先处理无效结果');removed=[]
 for p in sorted((run/'batches').glob('*.json')):
  b=base.read(p)
  if not all(k in good for k in b['keys']):continue
  for f in b['files']:
   x=Path(f['path']);require(run.resolve()in x.resolve().parents and 'inputs'in x.relative_to(run.resolve()).parts,'清理超出运行输入目录')
   if x.exists():require(base.sha(x)==f['sha256'],'输入变化，不能清理');x.unlink();removed.append(str(x))
 return dict(removed_regenerable_inputs=len(removed),scope='只清理已落盘且合同有效批次的装配输入；批次索引、来源、回执、结果保留。')

def summarize(data,run,allow_blocked=False):
 good,pending,blocked,fail=inventory(data,run)
 require(not pending and not fail and (not blocked or allow_blocked),'普通队列未完成或仍有受限单元；受限部分只能显式声明阶段汇总')
 reviews={k:dict(rows=v['rows'],usage=v['value']['usage'])for k,v in good.items()};summary=base.run_summary(data,reviews)
 summary.update(execution_version=EXECUTION_VERSION,task_count=len(good),expected_task_count=len(data['tasks']),complete=not blocked,unreviewed_units=blocked,acceptance_status='pending_codex',independent_peer_review_for_new_standalone_results=False)
 for row in summary['rows']:
  row['unreviewed_units']=[k for k in blocked if data['tasks'][k]['user_id']==row['user_id']and any(p['period_id']==row['period']['period_id']for p in data['tasks'][k]['periods'])]
  row['result_status']='provisional_with_unreviewed_units'if row['unreviewed_units']else 'pending_codex_acceptance'
 return summary

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',required=True);p.add_argument('--run',required=True);s=p.add_subparsers(dest='command',required=True)
 q=s.add_parser('init');q.add_argument('--reuse-from')
 s.add_parser('status');q=s.add_parser('next');q.add_argument('--count',type=int,default=5)
 q=s.add_parser('finalize');q.add_argument('--draft',required=True);q.add_argument('--session-id')
 s.add_parser('clean-inputs');q=s.add_parser('summarize');q.add_argument('--output',required=True);q.add_argument('--allow-blocked',action='store_true')
 a=p.parse_args();data=base.read(a.dataset);require(data['policy_version']==VERSION,'数据版本不符');run=Path(a.run).resolve()
 if a.command=='init':result=initialize(data,run,Path(a.reuse_from)if a.reuse_from else None)
 else:
  require((run/'import-record.json').exists(),'先init，或使用已经初始化的运行目录')
  if a.command=='status':result=progress(data,run)
  elif a.command=='next':result=next_batch(data,run,a.count)
  elif a.command=='finalize':
   value=base.read(a.draft);key=value['key'];require(not(run/'reviews'/(base.keyhash(key)+'.json')).exists(),'已有结果，不能覆盖冻结评分')
   freeze(data,run,value,'standalone',session_id=a.session_id);result=progress(data,run)
  elif a.command=='clean-inputs':result=clean_inputs(data,run)
  else:
   summary=summarize(data,run,allow_blocked=a.allow_blocked)
   base.write(a.output,summary);result=dict(summary=a.output,acceptance_status='pending_codex')
 if a.command in {'status','init','finalize'}:result={k:v for k,v in result.items()if k not in {'pending_keys','blocked_keys'}}|dict(pending=len(result['pending_keys']),blocked=len(result['blocked_keys']))
 print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
