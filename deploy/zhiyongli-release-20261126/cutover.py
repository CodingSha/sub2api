"""全部日候选验包后仅切换应用镜像；不发布分数，失败时恢复已备份镜像。"""
from pathlib import Path
import copy,json,subprocess,time,urllib.request
R=Path('/data/zhiyongli/releases/conversation-20261125-v14');C=Path('/data/zhiyongli/state/conversation-campaign-20261125')

def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
def health():
 try:return urllib.request.urlopen('http://127.0.0.1:8180/health',timeout=5).status==200
 except Exception:return False

def apply(path,log):
 with log.open('a') as stream:
  subprocess.check_call(['docker','compose','-p','deploy','-f',str(path),'up','-d','--no-deps','sub2api'],stdout=stream,stderr=subprocess.STDOUT)
 for _ in range(60):
  if health():return
  time.sleep(2)
 raise RuntimeError('应用健康检查未通过')

acceptance=json.loads((C/'control/quality-acceptance.json').read_text());assert acceptance['status']=='accepted'
image='sub2api-local:conversation-20261126-product';before=json.loads((R/'app.before.json').read_text())
assert before['name']=='deploy' and (R/'zhiyongli-before.dump').stat().st_size>0
paths=sorted((C/'days').glob('*/*/bundle.json'));assert len(paths)==495
subprocess.check_call(['docker','run','--rm','--label','com.apsat.zhiyongli.owner=zhiyongli','--label','com.apsat.zhiyongli.role=ephemeral','--network','none','--read-only','--entrypoint','/check','-v',str(R/'zhiyongli-bundle-check-product')+':/check:ro','-v',str(C)+':/candidates:ro',image]+['/candidates/'+str(p.relative_to(C)) for p in paths])
current=inspect('sub2api');expected=inspect(image)['Id']
if current['Image']==expected:
 assert health();print('APP_ALREADY_ON_EXPECTED_IMAGE',expected);raise SystemExit(0)
assert current['Image']==before['services']['sub2api']['image'],'应用已被其他发布改变，须先核对'
env=dict(line.split('=',1) for line in current['Config']['Env'] if '=' in line)
assert all(env.get(k)==str(v) for k,v in before['services']['sub2api']['environment'].items()),'现网环境与备份不符'
(R/'app.pre-cutover-proof.json').write_text(json.dumps(dict(image=current['Image'],mounts=current['Mounts'],health=health(),scores_published=False),indent=2))
after=copy.deepcopy(before);after['services']['sub2api']['image']=expected
path=R/'app.after.json';path.write_text(json.dumps(after,indent=2));path.chmod(0o600)
try:
 apply(path,R/'app-cutover.log')
 assert inspect('sub2api')['Image']==expected
except Exception:
 apply(R/'app.before.json',R/'app-rollback.log')
 raise
proof=dict(image=expected,health=True,at=time.time(),scores_published=False,configuration_change='services.sub2api.image')
(R/'app-cutover-proof.json').write_text(json.dumps(proof,indent=2));print('APP_CUTOVER',json.dumps(proof))
