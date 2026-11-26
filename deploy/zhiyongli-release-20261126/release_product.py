"""等待本轮日候选完成后，按已核顺序验包、导入与发布；任何阻断立即留档停止。"""
from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP,localcontext
from fractions import Fraction
from collections import Counter
import fcntl,json,subprocess,sys,time,urllib.request,hashlib,traceback
R=Path('/data/zhiyongli/releases/conversation-20261125-v14');S=Path('/data/zhiyongli/state');C=S/'conversation-campaign-20261125'

def save(name,value):
 path=R/name;temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2));temporary.replace(path)

def q(sql):
 return subprocess.check_output(['docker','exec','sub2api-postgres','sh','-c','psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qAtc "$1"','sh',sql]).decode().strip()

def collection():
 return json.loads(q("SELECT json_build_object('at',now(),'audit_recent_capped', (SELECT count(*) FROM (SELECT id FROM llm_audit_logs WHERE updated_at>=now()-interval '15 minutes' AND user_id<>22 LIMIT 500) a),'usage_recent_capped',(SELECT count(*) FROM (SELECT id FROM usage_logs WHERE created_at>=now()-interval '15 minutes' AND user_id<>22 LIMIT 500) u))"))

def phase(name,args,done):
 save('release-product-progress.json',dict(phase=name,state='running',completed=done,at=time.time()))
 with (R/('release-product-'+name+'.log')).open('a') as log:rc=subprocess.call(args,stdout=log,stderr=subprocess.STDOUT)
 if rc:raise RuntimeError(name+'退出码'+str(rc)+'，保留原回执和结果，不继续发布')
 done.append(name);save('release-product-progress.json',dict(phase=name,state='completed',completed=done,at=time.time()))

def formal(period,expected):
 paths=sorted((C/'days').glob('*/*/bundle.json')) if period=='day' else sorted((C/'weeks').glob('*/bundle.json'))
 assert len(paths)==expected,(period,len(paths),expected)
 args=['docker','run','--rm','--label','com.apsat.zhiyongli.owner=zhiyongli','--label','com.apsat.zhiyongli.role=ephemeral','--network','none','--read-only','--entrypoint','/check','-v',str(R/'zhiyongli-bundle-check-product')+':/check:ro','-v',str(C)+':/candidates:ro','sub2api-local:conversation-20261126-product']+['/candidates/'+str(p.relative_to(C)) for p in paths]
 subprocess.check_call(args)
 return dict(count=len(paths),bundle_hashes=[hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])

def published():
 sql="""SELECT coalesce(json_agg(json_build_object('id',r.id,'user_id',r.user_id,'period_type',r.period_type,'day',r.period_start,'score',r.body->>'score','exact',r.body->>'exact','dimensions',r.body->'dimensions','policy_versions',r.body->'policy_versions','source_digest',r.body->>'source_digest','request_digest',r.body->'daily_review'->>'request_digest','sources',r.body->'weekly_review'->'sources') ORDER BY r.user_id,r.period_type,r.period_start),'[]') FROM zhiyongli_reports r WHERE r.publication_status='published' AND ((r.period_type='day' AND r.period_start BETWEEN '2026-11-16' AND '2026-11-20' AND r.body->'policy_versions'->>'review'='daily-conversation-review-20261125.1') OR (r.period_type='week' AND r.period_start='2026-11-16' AND r.body->'policy_versions'->>'review'='weekly-conversation-20261125.1'))"""
 return json.loads(q(sql))

def audit(manifest):
 rows=published();days=[row for row in rows if row['period_type']=='day'];weeks=[row for row in rows if row['period_type']=='week'];expected={(e['user_id'],e['day']) for e in manifest['entries']};users={e['user_id'] for e in manifest['entries']}
 assert len(days)==495 and len(weeks)==99
 assert {(r['user_id'],r['day']) for r in days}==expected and {r['user_id'] for r in weeks}==users
 actual=json.loads((C/'actual-days.json').read_text())['reports'];assert {r['id'] for r in days}=={r['report_id'] for r in actual}
 index={r['id']:r for r in days};weights=dict(P=20,M=35,J=30,E=15);policies=[]
 for week in weeks:
  assert len(week['sources'])==5
  for source in week['sources']:
   day=index[source['report_id']];assert day['user_id']==week['user_id'] and day['day']==source['period_start'] and day['source_digest']==source['source_digest'] and day['request_digest']==source['request_digest']
  total=sum((Fraction(d['exact'])*weights[d['code']]/100 for d in week['dimensions'] if d['exact'] is not None),Fraction())
  assert week['score'] is not None and Fraction(week['exact'])==total
  with localcontext() as context:
   context.prec=50;display=str((Decimal(total.numerator)/Decimal(total.denominator)).quantize(Decimal('0.1'),rounding=ROUND_HALF_UP))
  assert week['score']==display,(week['user_id'],week['score'],display)
  policies.append(week['policy_versions'])
 assert all(policy==policies[0] for policy in policies)
 score_counts=Counter(row['score'] for row in weeks);mine=next(row for row in weeks if row['user_id']==28)
 rank=dict(user_id=28,position=1+sum(Decimal(row['score'])>Decimal(mine['score']) for row in weeks),total=99,tied=score_counts[mine['score']]>1)
 health=urllib.request.urlopen('http://127.0.0.1:8180/health',timeout=10).status;assert health==200
 return dict(users=99,published_days=495,published_weeks=99,all_weekly_scores_present=True,exact_scores_checked=99,daily_source_links_checked=495,score_distribution=dict(score_counts),expected_company_rank=rank,app_health=health,collection=collection(),at=time.time())

def main():
 manifest=json.loads((C/'frozen/source-manifest-v3.json').read_text());assert len(manifest['entries'])==495 and not manifest['failures']
 acceptance=json.loads((C/'control/quality-acceptance.json').read_text());assert acceptance['status']=='accepted'
 done=[]
 with (R/'release-product.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  try:
   save('release-product-progress.json',dict(phase='waiting_daily',state='running',at=time.time()))
   outcome=R/'day-product-driver-result.json'
   while not outcome.exists():time.sleep(15)
   assert json.loads(outcome.read_text())['rc']==0,'日候选存在真实阻断，未执行发布'
   save('release-product-day-formal.json',formal('day',495))
   users=sorted({e['user_id'] for e in manifest['entries']});current=json.loads(q("SELECT coalesce(json_agg(id ORDER BY id),'[]') FROM users u WHERE deleted_at IS NULL AND id<>22 AND NOT EXISTS(SELECT 1 FROM audit_whitelist_users w WHERE w.user_id=u.id)"));assert users==current,'人员范围变化，须先补齐来源'
   save('collection-pre-product-cutover.json',collection())
   phase('app_cutover',['python3',str(R/'cutover.py')],done)
   phase('draft_day',['python3',str(R/'imports.py'),'draft-day'],done)
   phase('week_candidates',['python3',str(R/'candidates.py'),'week'],done)
   save('release-product-week-formal.json',formal('week',99))
   phase('publish_day',['python3',str(R/'imports.py'),'publish-day'],done)
   phase('publish_week',['python3',str(R/'imports.py'),'publish-week'],done)
   proof=audit(manifest);save('release-product-audit.json',proof);save('release-product-result.json',dict(rc=0,state='published_and_data_verified',completed=done,proof=proof,at=time.time()))
   print('RELEASE_PUBLISHED',json.dumps(proof,ensure_ascii=False),flush=True)
  except Exception as error:
   save('release-product-result.json',dict(rc=1,state='stopped_for_review',completed=done,error_type=type(error).__name__,reason=str(error),at=time.time()));raise

if __name__=='__main__':main()
