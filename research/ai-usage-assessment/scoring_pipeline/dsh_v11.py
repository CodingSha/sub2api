"""DSH v11交接命令：只做本地装配、引文校验与汇总，不执行语义评分。"""
import argparse
import collections
import hashlib
import json
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from .common import require, moment, display
from .progressive_handoff import JUDGE, AUDIT, check_review, raw_payload
from .progressive_review import RUBRIC, VERSION, summarize_period, apply_monthly
from .revised_review import level


def read(path):return json.loads(Path(path).read_text())
def write(path,data):
 path=Path(path);require(not path.exists(),'输出已存在，请使用新路径或先归档，不能覆盖冻结产物')
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def keyhash(key):return hashlib.sha256(key.encode()).hexdigest()[:24]


@lru_cache(maxsize=350)
def source(path):return read(path)


def task(data,key):
 u=dict(data['tasks'][key]);events={}
 for gid in u.pop('source_groups'):
  s=data['source_groups'][gid]
  for e in source(s['path'])['events']:
   eid=gid+':'+e['event'];events[eid]=dict(e,event=eid)
 u['events']=list(events.values());require(set(u['person_events'])<=set(events),'任务本人事件缺来源')
 return u


def check_provenance(a):
 for name in ['reviewer','auditor']:
  p=a.get(name,{})
  require(all(isinstance(p.get(k),str)and p[k].strip()for k in ['team_id','agent_id','context_id','method']), '缺真实评审来源 '+name)
  receipt=p.get('receipt',{})
  require(receipt.get('path') and receipt.get('sha256') and sha(receipt['path'])==receipt['sha256'],'缺评审原始记录或SHA不符 '+name)
 require(a['reviewer']['context_id']!=a['auditor']['context_id'],'主评复核须不同实际上下文，禁止改显示名伪造')
 require(a.get('audit',{}).get('decision')=='accepted'and a['audit'].get('reason'),'缺独立复核接受说明')


def load_reviews(data,directory,allow_partial=False):
 files=list(Path(directory).glob('*.json'));reviews={}
 for p in files:
  a=read(p);key=a.get('key');require(key in data['tasks'],'未知任务key '+str(key));require(key not in reviews,'重复任务key')
  require(a.get('policy_version')==VERSION,'评分版本不符');check_provenance(a)
  u=task(data,key);checked=check_review(u,a)
  if u['local_only']:
   require(all(a[n]['method']=='local_exception'for n in ['reviewer','auditor']),'受限任务须明确分列本地例外方法')
  for r in checked:r['review_provenance']=dict(reviewer=a['reviewer'],auditor=a['auditor'])
  reviews[key]=dict(rows=checked,usage=a['usage'],source_file=str(p.resolve()),sha256=sha(p))
 require(allow_partial or set(reviews)==set(data['tasks']),'未完成全量，缺少'+str(len(set(data['tasks'])-set(reviews)))+'任务')
 return reviews


def packet(data,key):
 meta=data['tasks'][key]
 fields=['key','user_id','title','purpose','boundary_reason','periods','person_events','source_gaps','local_only']
 pack={k:meta[k]for k in fields};pack['policy_version']=VERSION
 if meta['local_only']:
  # 受限来源不进入普通任务包，连上下文预览也不输出。
  pack['raw_material']=None
  pack['handling']='受限本地例外：常规发送和context补读均禁止。仅在已获准本地或人工渠道按固定合同重新评审并独立复核；渠道不可用则记录执行阻断，不能复用旧分或声称全量完成。'
 else:
  pack['raw_material']=raw_payload(task(data,key))
  pack['context_fetch']='使用context命令按event从本地来源补读；未逐字读取的工具正文不得声称已读。'
 return pack


def adjusted_bounds(performance,windows,smooth=Fraction(1),maximum=Fraction(15)):
 """未知参与仅取有限可能范围，不给未知点值；这不是置信区间。"""
 if performance is None:return None
 ws=[w for w in windows if moment(w['start']).weekday()==0 and(moment(w['end'])-moment(w['start'])).days==7 and w.get('applicability','applicable')!='not_applicable']
 if not ws:return None
 low=[];high=[]
 for w in ws:
  n=w['participation_units'];upper=n+w.get('possible_additional_units',0)
  low.append(Fraction(n,n+smooth))
  high.append(Fraction(upper,upper+smooth) if w['coverage']=='confirmed' else Fraction(1))
 lo=max(Fraction(),Fraction(performance)-maximum*(1-sum(low,Fraction())/len(ws)))
 hi=max(Fraction(),Fraction(performance)-maximum*(1-sum(high,Fraction())/len(ws)))
 return dict(low_exact=str(lo),high_exact=str(hi),low=display(lo),high=display(hi),low_rating=level(lo),high_rating=level(hi),reason='仅参与单位归属/来源未定的影响界限，非统计置信区间，也未替未知取点值。')


def run_summary(data,reviews):
 rows=[r for v in reviews.values()for r in v['rows']];by_usage=collections.defaultdict(list);summaries=[]
 for key,v in reviews.items():
  uid=data['tasks'][key]['user_id']
  for w in v['usage']:by_usage[(uid,w['period_id'])].append(dict(w,goal_id=key))
 for t in data['period_templates']:
  uid,p=t['user_id'],t['period'];rs=[r for r in rows if r['user_id']==uid and r['period_id']==p['period_id']];s=summarize_period(rs)
  cells=[c for r in rs for c in r['cells']]
  s.update(user_id=uid,period=p,task_count=len(rs),parent_task_count=sum(not r['goal_id'].startswith('local:')for r in rs),bounded_local_count=sum(r['goal_id'].startswith('local:')for r in rs),state_counts=dict(collections.Counter(c['status']for c in cells)),band_counts=dict(collections.Counter(str(c['band'])for c in cells if c['status']=='scored')),complex_tasks=sum(any(c.get('opportunity_complexity',{}).get('status')=='confirmed'for c in r['cells'])for r in rs))
  if p['kind']=='monthly':
   windows=[]
   for old in data['participation_coverage'][uid]:
    family=by_usage[(uid,old['period_id'])]
    parents=[w for w in family if not w['goal_id'].startswith('local:')]
    n=sum(len(w['engagements'])for w in parents)
    unresolved_tasks=sum(w['activity']=='unknown'for w in parents)
    local_units=sum(w['activity']in {'substantive','unknown'}for w in family if w['goal_id'].startswith('local:'))
    external_unknown=[x for x in data['non_task_dispositions']if x['user_id']==uid and x['status']=='unknown_source'and moment(old['start'])<=moment(x['at'])<moment(old['end'])]
    # 一个来源未定输入最多可能贡献三类；与已有任务可能重合，故下界不额外累加。
    extra=3*(unresolved_tasks+local_units+len(external_unknown))
    w=dict(old,participation_units=n,engagement_counts=dict(collections.Counter(e['kind']for v in parents for e in v['engagements'])),participation_known=extra==0,possible_additional_units=extra,unresolved_non_task_inputs=len(external_unknown),uncertain_task_units=unresolved_tasks+local_units)
    windows.append(w)
   s.update(apply_monthly(s['exact'],p,windows),performance_reference_rating=level(Fraction(s['exact']))if s['exact']is not None else None,qualification='本平台已观察行为的版本化参考结果，非独立真人校准。')
   bounds=adjusted_bounds(s['exact'],windows)
   s['monthly_bounds']=bounds
   if s['monthly_score']is None and bounds:
    s['trial_rating']=bounds['low_rating']if bounds['low_rating']==bounds['high_rating']else None
    s['rating_scope']='月度综合在参与界限两端等级一致'if s['trial_rating']else '参与影响跨等级，展示等级范围及独立的表现参考等级；不得冒充单一月度综合评级'
   sensitivity={}
   for name,weight in [('complex_weight_1',Fraction(1)),('complex_weight_2',Fraction(2))]:
    alt=summarize_period(rs,complex_weight=weight)
    sensitivity[name]=dict(performance=alt['score'],monthly_bounds=adjusted_bounds(alt['exact'],windows))
   for name,smooth,maximum in [('smooth_0.5',Fraction(1,2),Fraction(15)),('smooth_2',Fraction(2),Fraction(15)),('maximum_10',Fraction(1),Fraction(10))]:
    sensitivity[name]=adjusted_bounds(s['exact'],windows,smooth,maximum)
   item_dims=[]
   for dim in s['dimensions']:
    vals=[Fraction(v['exact'])for k,v in s['items'].items()if k.startswith(dim)and v['exact']is not None]
    if vals:item_dims.append(sum(vals,Fraction())/len(vals))
   sensitivity['item_equal_performance']=display(sum(item_dims,Fraction())/4)if len(item_dims)==4 else None
   s['sensitivity']=sensitivity
  summaries.append(s)
 return dict(policy_version=VERSION,source_scope=data['scope'],task_count=len(data['tasks']),rows=summaries,human_calibration=False,production_ready=False)


def main():
 p=argparse.ArgumentParser(description='v11 DSH本地装配、校验与汇总；不联网、不评分')
 p.add_argument('--dataset',required=True);sub=p.add_subparsers(dest='command',required=True)
 v=sub.add_parser('verify');v.add_argument('--output',required=True)
 a=sub.add_parser('prepare');a.add_argument('--output-dir',required=True)
 c=sub.add_parser('context');c.add_argument('--key',required=True);c.add_argument('--events',nargs='+',required=True);c.add_argument('--output',required=True)
 s=sub.add_parser('validate');s.add_argument('--reviews',required=True);s.add_argument('--output',required=True);s.add_argument('--allow-partial',action='store_true')
 s=sub.add_parser('summarize');s.add_argument('--reviews',required=True);s.add_argument('--output',required=True)
 args=p.parse_args();data=read(args.dataset);require(data['policy_version']==VERSION,'数据合同版本不符')
 if args.command=='verify':
  for g,d in data['source_groups'].items():require(sha(d['path'])==d['sha256'],'来源SHA变化 '+g)
  require(len(data['tasks'])==849 and len(data['source_groups'])==312,'范围不符')
  for f in data.get('frozen_files',[]):require(sha(f['path'])==f['sha256'],'冻结规则变化 '+f['path'])
  write(args.output,dict(policy_version=VERSION,source_groups=312,tasks=849,source_hashes_verified=True,rule_hashes_verified=True,counts=data['scope']))
 elif args.command=='prepare':
  dest=Path(args.output_dir);require(not dest.exists(),'输出目录已存在，使用新的运行目录')
  index=[]
  for key,meta in data['tasks'].items():
   pack=packet(data,key);u=meta
   path=dest/(keyhash(key)+'.json');write(path,pack);index.append(dict(key=key,user_id=u['user_id'],packet=str(path.resolve()),local_only=u['local_only']))
  write(dest/'index.json',dict(policy_version=VERSION,units=index));write(dest/'rubric.json',RUBRIC)
  write(dest/'instructions.json',dict(judge=JUDGE,audit=AUDIT));print(json.dumps(dict(packets=len(index),source_groups=312,local_only=sum(x['local_only']for x in index)),ensure_ascii=False))
 elif args.command=='context':
  u=task(data,args.key);require(not u['local_only'],'本地例外不得通过普通补读通道输出敏感正文；按例外说明仅本地复核')
  events={e['event']:e for e in u['events']};require(set(args.events)<=set(events),'补读超出本任务来源')
  write(args.output,dict(key=args.key,events=[events[e]for e in args.events],scope='指定事件的完整本地缓存；仍可能有原始采集缺失'))
 else:
  reviews=load_reviews(data,args.reviews,allow_partial=getattr(args,'allow_partial',False))
  if args.command=='validate':write(args.output,dict(policy_version=VERSION,tasks=len(reviews),complete=set(reviews)==set(data['tasks']),missing_tasks=sorted(set(data['tasks'])-set(reviews)),task_period_rows=sum(len(v['rows'])for v in reviews.values()),all_12_items_accounted=True,provenance_and_quotes_checked=True,semantics_verified_by_program=False,review_files=[dict(key=k,sha256=v['sha256'])for k,v in reviews.items()]))
  else:write(args.output,run_summary(data,reviews))


if __name__=='__main__':main()
