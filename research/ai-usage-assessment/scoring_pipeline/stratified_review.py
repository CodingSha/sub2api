"""v8：复杂机会独立于表现，分层汇总，参与只作有限扣减。"""
import copy
from collections import Counter
from datetime import timedelta
from fractions import Fraction

from .calibrated_review import validate_period as validate_v7, observations, RUBRIC as V7_RUBRIC
from .revised_review import find_proof, level
from .common import require, display, moment
from .policy import ITEMS, DIMENSIONS

VERSION='pmje-trial-20261101.8'
COMPLEX_SHARE=Fraction(3,10)
PARTICIPATION_MAX_DEDUCTION=Fraction(5)
RUBRIC=copy.deepcopy(V7_RUBRIC)
RUBRIC['version']=VERSION
RUBRIC['items']['J2']['80']='在当前阶段明确安排相称的检查内容、方法或执行者；已有具体安排可评，不因后续执行尚未采集而整项弃评。泛泛要求测试、直接请求分析不自动构成独立核验安排；已知反馈出现后须按实际跟进判断。'
RUBRIC['items']['J2']['90']='针对实际关键风险或交互选择检查范围、方法或执行者，说明选择依据并处理当前已知检查限制；后续执行未采集另列范围，不倒填已经完成。'
RUBRIC['principles']=[
 '保留312审阅家族用于对照；家族内局部先平均，每个子项分别汇总常规与已确认复杂机会，M常规70%复杂30%，正反表现同等计入；不使用旧1.5倍机会加权。'
 if 'M复杂资格' in text else
 '周只引用本期本人行为与期末前依据；月度独立看整月，不平均周分。四维各25%，月度以整月表现减完整周参与不足扣减（最多5分）；参与不加分。'
 if '月度表现90%参与10%' in text else text
 for text in RUBRIC['principles']]
RUBRIC['v8_changes']={
 'complexity':'所有M机会独立检查两个实际相互作用条件、常规路径不足。复杂机会可确认但本人处理仍缺证；资格与表现分开，不按档位、工具数或任务长短识别。',
 'mastery':'M常规与已确认复杂分别按家族内局部、子项等权汇总，再70/30合成；只有一层可评分时仅报告该层，不给另一层补分。',
 'unknown':'先判断当前已证行为能否评分，更高档、执行结果或未来采用未知另列；真正影响本档判断才unknown。不得为消除未知改成无机会。',
 'verification_stage':'J2按当前阶段评具体核验安排，已知实际反馈出现后再检查跟进；安排不冒充完成，泛泛测试意向不补分。所有J2不论原状态都重新审查。',
 'participation':'月度以四维表现为基础，完整自然周持续使用不足最多扣5分，不加分，首尾周片段不扣分；覆盖未知不当未使用。',
 'preserve_anchors':'六档数值映射和等级门槛不变；J2按当前阶段修正判据，其余沿用v7。不按预期分布调整。'}


def leaf_key(period_id,item,leaf):
 return period_id+'/'+item+'/'+leaf.get('unit_id','_whole')


def validate_period(data,period,submitted):
 """沿用主体、引文和档位核验；复杂资格不再要求先有表现分。"""
 temporary=copy.deepcopy(submitted);meta={}
 for outer in temporary['cells']:
  for leaf in observations(outer):
   key=leaf_key(period['period_id'],outer['item'],leaf)
   if outer['item'].startswith('M'):
    meta[key]=leaf.pop('opportunity_complexity',None)
    leaf['complex']={'status':'none'}
 out=validate_v7(data,period,temporary)
 events={e['event']:e for e in data['events']}
 for outer in out['cells']:
  for leaf in observations(outer):
   if not outer['item'].startswith('M'):continue
   key=leaf_key(period['period_id'],outer['item'],leaf);classification=meta[key]
   require(isinstance(classification,dict),key+'缺独立复杂机会判断opportunity_complexity')
   state=classification.get('status')
   require(state in {'none','confirmed','pending'} and classification.get('reason'),key+'复杂资格须状态和具体理由')
   if state=='confirmed':
    fields=['condition_a','condition_b','interaction','routine_insufficient','evidence']
    require(all(classification.get(k)for k in fields),key+'复杂资格缺两条件、相互作用、常规不足或原文证据')
    classification['evidence']=[find_proof(p,events,period)for p in classification['evidence']]
    require(leaf['status']in {'scored','unknown'},key+'无独立处理机会不能计入复杂评分机会')
    if leaf['status']=='scored':require(classification.get('person_handling'),key+'已评分复杂机会缺本人处理说明')
   if state=='pending':require(classification.get('needed'),key+'待核复杂资格须指出决定性缺什么')
   leaf['opportunity_complexity']=classification
   leaf['weight_exact']='1';leaf['weight_pending']=state=='pending'
 out['policy_version']=VERSION
 return out


def _pool(rows,item,select):
 families=[];raw=Counter();known_mass=Fraction();total_mass=Fraction()
 for row in rows:
  outer=next(c for c in row['cells']if c['item']==item)
  selected=[p for p in observations(outer)if select(p)]
  raw.update(p['status']for p in selected)
  eligible=[p for p in selected if p['status']in {'scored','unknown'}]
  scored=[p for p in eligible if p['status']=='scored']
  if eligible:
   total_mass+=1;known_mass+=Fraction(len(scored),len(eligible))
  if scored:families.append(sum(Fraction(p['band'])for p in scored)/len(scored))
 value=sum(families,Fraction())/len(families)if families else None
 # 以下仅是“本项已识别机会”的最坏/最好情景，不给未知估分。
 low=[];high=[]
 for row in rows:
  outer=next(c for c in row['cells']if c['item']==item)
  eligible=[p for p in observations(outer)if select(p)and p['status']in {'scored','unknown'}]
  if eligible:
   numerator=sum((Fraction(p['band'])for p in eligible if p['status']=='scored'),Fraction())
   nunknown=sum(p['status']=='unknown'for p in eligible)
   low.append(numerator/len(eligible));high.append((numerator+100*nunknown)/len(eligible))
 return dict(exact=str(value)if value is not None else None,score=display(value),
  scored_families=len(families),scored=raw['scored'],unknown=raw['unknown'],states=dict(raw),
  coverage_exact=str(known_mass/total_mass)if total_mass else None,
  source_sensitivity_low=str(sum(low)/len(low))if low else None,
  source_sensitivity_high=str(sum(high)/len(high))if high else None)


def _mean(values):
 used=[Fraction(v)for v in values if v is not None]
 return sum(used)/len(used)if used else None


def _complex(leaf):return (leaf.get('opportunity_complexity')or {}).get('status')=='confirmed'


def summarize_period(rows):
 require(all(r.get('policy_version')==VERSION for r in rows),'v8汇总拒绝混用其他版本')
 identities=[(r['user_id'],r['period_id'],r['goal_id'])for r in rows]
 require(len(identities)==len(set(identities)),'同一家族期间不能重复加权')
 require(len({(r['user_id'],r['period_id'])for r in rows})<=1,'单次汇总只能同人同期间')
 items={}
 for item in ITEMS:
  all_pool=_pool(rows,item,lambda p:True)
  value=dict(exact=all_pool['exact'],score=all_pool['score'],base_exact=all_pool['exact'],base_score=all_pool['score'],
   scored=all_pool['scored_families'],local_scored=all_pool['scored'],unknown=all_pool['unknown'],
   local_status_counts=all_pool['states'],coverage_exact=all_pool['coverage_exact'],
   source_sensitivity_low=all_pool['source_sensitivity_low'],source_sensitivity_high=all_pool['source_sensitivity_high'])
  if item.startswith('M'):
   value['regular']=_pool(rows,item,lambda p:not _complex(p))
   value['complex']=_pool(rows,item,_complex)
  items[item]=value
 dims={}
 for name,spec in DIMENSIONS.items():
  value=_mean([items[i]['exact']for i in spec['items']])
  if name=='M':
   regular=_mean([items[i]['regular']['exact']for i in spec['items']])
   complex_value=_mean([items[i]['complex']['exact']for i in spec['items']])
   if regular is not None and complex_value is not None:
    value=(1-COMPLEX_SHARE)*regular+COMPLEX_SHARE*complex_value;scope='regular_and_complex'
   else:value=regular if regular is not None else complex_value;scope='regular_only'if regular is not None else 'complex_only'if complex_value is not None else 'unobserved'
   dims[name]=dict(regular_exact=str(regular)if regular is not None else None,regular_score=display(regular),
    complex_exact=str(complex_value)if complex_value is not None else None,complex_score=display(complex_value),
    complex_share=str(COMPLEX_SHARE)if regular is not None and complex_value is not None else None,scope=scope)
  else:dims[name]={}
  coverage=_mean([items[i]['coverage_exact']for i in spec['items']])
  dims[name].update(exact=str(value)if value is not None else None,score=display(value),
   covered_items=sum(items[i]['exact']is not None for i in spec['items']),
   local_scored=sum(items[i]['local_scored']for i in spec['items']),unknown=sum(items[i]['unknown']for i in spec['items']),
   coverage_exact=str(coverage)if coverage is not None else None,
   coverage_score=display(100*coverage)if coverage is not None else None)
 total=_mean([d['exact']for d in dims.values()])if all(d['exact']is not None for d in dims.values())else None
 pending=sum((p.get('opportunity_complexity')or {}).get('status')=='pending'for r in rows for c in r['cells']for p in observations(c))
 return dict(policy_version=VERSION,items=items,dimensions=dims,exact=str(total)if total is not None else None,score=display(total),
  weight_pending=pending,scope='已观察表现，未知未估分；复杂待核部分保留常规评分并单列资格限制。')


def apply_monthly(performance,period,windows):
 """完整自然周不足只扣不加；首尾片段不改变月度扣减。"""
 start,end=moment(period['start']),moment(period['end']);complete=[];seen=set()
 for w in windows:
  a,b=moment(w['start']),moment(w['end'])
  require(a<b,'参与窗口起止须递增')
  require((a,b)not in seen,'参与窗口不能重复')
  seen.add((a,b))
  if start<=a and b<=end and a.weekday()==0 and b-a==timedelta(days=7):complete.append(w)
 first=start.replace(hour=0,minute=0,second=0,microsecond=0)
 first+=timedelta(days=(7-first.weekday())%7)
 if first<start:first+=timedelta(days=7)
 expected=set()
 while first+timedelta(days=7)<=end:
  expected.add((first,first+timedelta(days=7)));first+=timedelta(days=7)
 credit={'substantive':Fraction(1),'limited':Fraction(1,2),'none':Fraction(0)}
 known=bool(expected)and {(moment(w['start']),moment(w['end']))for w in complete}==expected and all(w.get('coverage')=='confirmed'and w.get('activity')in credit for w in complete)
 rate=sum((credit[w['activity']]for w in complete),Fraction())/len(complete)if known else None
 deduction=PARTICIPATION_MAX_DEDUCTION*(1-rate)if rate is not None else None
 value=max(Fraction(),Fraction(performance)-deduction)if performance is not None and deduction is not None else None
 return dict(participation_windows=windows,complete_week_count=len(complete),participation_exact=str(100*rate)if rate is not None else None,
  participation_score=display(100*rate)if rate is not None else None,participation_deduction_exact=str(deduction)if deduction is not None else None,
  participation_deduction=display(deduction),monthly_exact=str(value)if value is not None else None,monthly_score=display(value),
  trial_rating=level(value)if value is not None else None,participation_scope='仅完整自然周；首尾片段单列，覆盖未知不当未用。')
