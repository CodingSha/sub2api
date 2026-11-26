"""仅处理本次冻结人员与日期：正式合同验包、草稿接入、统一发布和回执核对。"""
from pathlib import Path
import hashlib,json,os,subprocess,sys,urllib.request
R=Path('/data/zhiyongli/releases/conversation-20261125-v14');S=Path('/data/zhiyongli/state');C=S/'conversation-campaign-20261125';F=S/'conversation-campaign-20261125/frozen'
phase=sys.argv[1];assert phase in {'draft-day','publish-day','publish-week'}
period='week' if phase=='publish-week' else 'day'
manifest=json.loads((F/'source-manifest-v3.json').read_text());users={e['user_id']:e['user_email'] for e in manifest['entries']}
assert len(users)==99 and len(manifest['entries'])==495
proof=json.loads((C/'control/prepare-proof.json').read_text());key=Path(proof['worker_key_file']).read_text().strip();assert len(key)>=32
acceptance=json.loads((C/'control/quality-acceptance.json').read_text());assert acceptance['status']=='accepted'
scope=acceptance['engine']+':'+proof['source_manifest_sha256'][:24]
def q(sql):return subprocess.check_output(['docker','exec','sub2api-postgres','sh','-c','psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qAtc "$1"','sh',sql]).decode().strip()
def literal(value):return "'"+value.replace("'","''")+"'"
def save(path,value):
 raw=json.dumps(value,ensure_ascii=False,indent=2).encode()
 if path.exists():assert path.read_bytes()==raw,'导入事前合同或已核回执发生变化'
 else:
  temporary=path.with_suffix('.tmp');temporary.write_bytes(raw);temporary.replace(path)
 path.chmod(0o600)
paths=sorted((C/'weeks').glob('*/bundle.json')) if period=='week' else sorted((C/'days').glob('*/*/bundle.json'))
assert len(paths)==(99 if period=='week' else 495)
# 使用与线上同一Go Result.Validate，只读验包，禁止网络和数据库访问。
check=['docker','run','--rm','--label','com.apsat.zhiyongli.owner=zhiyongli','--label','com.apsat.zhiyongli.role=ephemeral','--network','none','--read-only','--entrypoint','/check','-v',str(R/'zhiyongli-bundle-check-product')+':/check:ro','-v',str(C)+':/candidates:ro','sub2api-local:conversation-20261126-product']
subprocess.check_call(check+['/candidates/'+str(p.relative_to(C)) for p in paths])
rows=[]
for path in paths:
 raw=path.read_bytes();bundle=json.loads(raw);assert len(bundle['reports'])==1
 report=bundle['reports'][0];uid=int(path.parent.name if period=='week' else path.parent.parent.name)
 assert users[uid]==report['user_email'] and report['period_type']==period
 assert report['period_start'] in (['2026-11-16'] if period=='week' else ['2026-11-'+str(i) for i in range(16,21)])
 assert report['policy_versions']['review']==('weekly-conversation-20261125.1' if period=='week' else 'daily-conversation-review-20261125.1')
 rows.append((uid,path,raw,bundle,report))
assert len({(uid,r['period_start']) for uid,_,_,_,r in rows})==len(rows)
out=C/'imports'/phase;out.mkdir(parents=True,exist_ok=True,mode=0o700)
for uid,path,raw,bundle,report in rows:
 day=report['period_start'];work=json.dumps(report['workday_scope'],separators=(',',':'))
 sql="""BEGIN;SELECT pg_advisory_xact_lock(241241);INSERT INTO zhiyongli_jobs(user_id,period_type,period_start,period_end,rules_version,created_by,scope_key,workday_scope) SELECT id,%s,%s,%s,'1.6',28,%s,%s::jsonb FROM users u WHERE id=%d AND email=%s AND deleted_at IS NULL AND NOT EXISTS(SELECT 1 FROM audit_whitelist_users w WHERE w.user_id=u.id) ON CONFLICT(user_id,period_type,period_start,rules_version,scope_key) DO NOTHING;COMMIT;"""%(literal(period),literal(day),literal(report['period_end']),literal(scope),literal(work),uid,literal(users[uid]))
 q(sql)
 jid=int(q("SELECT id FROM zhiyongli_jobs WHERE user_id=%d AND period_type=%s AND period_start=%s AND scope_key=%s"%(uid,literal(period),literal(day),literal(scope))))
 endpoint='http://127.0.0.1:8180/api/v1/internal/zhiyongli/jobs/'+str(jid)+('/import-draft' if phase=='draft-day' else '/import')
 target=out/(str(uid)+'-'+day+'.json');intent=target.with_suffix('.intent.json')
 save(intent,dict(job_id=jid,user_id=uid,day=day,endpoint=endpoint,bundle_sha256=hashlib.sha256(raw).hexdigest()))
 if target.exists():
  received=json.loads(target.read_text());runid=int(received['data']['id'])
  status=q('SELECT publication_status FROM zhiyongli_reports WHERE run_id='+str(runid))
  assert status in (['draft','published'] if phase=='draft-day' else ['published'])
  continue
 q("UPDATE zhiyongli_jobs SET state='running',phase='importing',message='新版评审已完成，正在保存结果',updated_at=now() WHERE id="+str(jid))
 request=urllib.request.Request(endpoint,data=raw,headers={'Content-Type':'application/json','X-Zhiyongli-Worker-Key':key})
 try:
  with urllib.request.urlopen(request,timeout=90) as stream:received=json.load(stream)
 except urllib.error.HTTPError as error:
  (out/(str(uid)+'-'+day+'.error.json')).write_bytes(error.read());raise RuntimeError('接入失败，已保留服务端原始错误：'+str(jid)) from None
 assert received['code']==0 and received['data']['report_count']==1 and received['data']['source_name']==bundle['source_name']
 runid=int(received['data']['id']);assert runid>0
 status=q('SELECT publication_status FROM zhiyongli_reports WHERE run_id='+str(runid));assert status==('draft' if phase=='draft-day' else 'published')
 save(target,received)
 message='新版日评草稿已保存，待日周统一发布' if phase=='draft-day' else '新版评分已发布'
 q("UPDATE zhiyongli_jobs SET state='completed',phase=%s,completed_units=1,total_units=1,message=%s,updated_at=now() WHERE id=%d"%(literal('draft_ready' if phase=='draft-day' else 'done'),literal(message),jid))
 print(json.dumps(dict(phase=phase,user_id=uid,day=day,job_id=jid,run_id=runid,status=status)),flush=True)
if phase=='draft-day':
 ids=','.join(str(json.loads(p.read_text())['data']['id']) for p in out.glob('*.json') if not p.name.endswith(('.intent.json','.error.json')))
 sql="SELECT coalesce(json_agg(json_build_object('report_id',r.id,'user_id',r.user_id,'user_email',u.email,'day',r.period_start,'source_digest',r.body->>'source_digest','request_digest',r.body->'daily_review'->>'request_digest','review',r.body->'daily_review') ORDER BY r.user_id,r.period_start),'[]') FROM zhiyongli_reports r JOIN users u ON u.id=r.user_id WHERE r.run_id IN ("+ids+")"
 actual=json.loads(q(sql));assert len(actual)==495;save(C/'actual-days.json',dict(reports=actual));os.chown(C/'actual-days.json',1000,1000)
print('IMPORT_PHASE_COMPLETE',phase,len(rows),flush=True)
