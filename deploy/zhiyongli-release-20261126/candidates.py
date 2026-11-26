"""在原状态卷、同一账簿生成本轮候选；服务冷却后原位接续，未知终态停机核对。"""
from pathlib import Path
import json,subprocess,sys,time
R=Path('/data/zhiyongli/releases/conversation-20261125-v14');S=Path('/data/zhiyongli/state');C=S/'conversation-campaign-20261125';F=S/'conversation-campaign-20261125/frozen'
period=sys.argv[1];assert period in {'day','week'}
proof=json.loads((C/'control/prepare-proof.json').read_text());assert (C/'control/quality-acceptance.json').exists()
assert not subprocess.check_output(['docker','ps','-q','--filter','name=^/zhiyongli-eff-20261123$']).strip()
name='zhiyongli-conversation-'+period+'-20261125'
base=['docker','run','--rm','--label','com.apsat.zhiyongli.owner=zhiyongli','--label','com.apsat.zhiyongli.role=ephemeral','--name',name,'--cpus','1','--memory','2g','--pids-limit','128','--read-only','--security-opt','no-new-privileges:true','--cap-drop','ALL','--tmpfs','/tmp:size=67108864,mode=1777','--network','deploy_sub2api-network','--entrypoint','python','-v',str(S)+':/state','-v',str(F)+':/frozen:ro','-v',proof['private_mount']+':/run/zhiyongli:ro','zhiyongli-worker-local:conversation-20261126-repair-envelope','/app/tools/zhiyongli/conversation_batch.py','--manifest','/frozen/source-manifest-v3.json','--output','/state/conversation-campaign-20261125','--state','/state','--model-config','/state/conversation-campaign-20261125/control/model.json','--flow-config','/state/conversation-campaign-20261125/control/flow.json','--acceptance','/state/conversation-campaign-20261125/control/quality-acceptance.json','--week','2026-11-16','--period',period,'--workers','4','--request-limit','64','--company-api']
if period=='week':base+=['--daily-reports','/state/conversation-campaign-20261125/actual-days.json']
base+=['--held-results','/state/conversation-campaign-20261125/control/held-results-'+period+'.json']
import os
manifest=json.loads((F/'source-manifest-v3.json').read_text())
hold_path=C/'control'/('held-results-'+period+'.json')
if not hold_path.exists():hold_path.write_text('[]');os.chown(hold_path,1000,1000);hold_path.chmod(0o600)
for attempt in range(160):
 with (R/(period+'-candidates.log')).open('a') as log:rc=subprocess.call(base,stdout=log,stderr=subprocess.STDOUT)
 if rc==0:break
 p=C/(period+'-progress.json')
 if not p.exists():raise RuntimeError('候选程序未生成进度，须先检查日志')
 value=json.loads(p.read_text());failed=[row for row in value['results'] if row['status']=='failed'];held=json.loads(hold_path.read_text())
 for row in failed:
  if row.get('error_type')=='Paused' and row.get('state')=='queued':continue
  identity=(row['user_id'],row.get('day'))
  if any((x['user_id'],x['day'])==identity for x in held):raise RuntimeError('已隔离任务仍发生发送或状态异常')
  sources=[x['sha256'] for x in manifest['entries'] if x['user_id']==row['user_id'] and (period=='week' or x['day']==row['day'])]
  if not sources:raise RuntimeError('失败记录缺冻结来源')
  held.append(dict(user_id=row['user_id'],day=row.get('day'),sources=sources,reason=row['reason'],original_error=row,at=time.time()))
 hold_path.write_text(json.dumps(held,ensure_ascii=False,indent=2));os.chown(hold_path,1000,1000);hold_path.chmod(0o600)
 waiting=[row for row in failed if row.get('error_type')=='Paused' and row.get('state')=='queued']
 if not waiting:raise RuntimeError('其他独立任务已处理，隔离候选仍待直接核对；不发布、不重发隔离请求')
 (R/'campaign-wait.json').write_text(json.dumps(dict(period=period,attempt=attempt,waiting=len(waiting),held=len(held),at=time.time())))
 time.sleep(60)
else:raise RuntimeError('候选接续达到本次运行上限')
print('CANDIDATES_COMPLETE',period,flush=True)
