"""只自动接续可判定的来源/幂等导入连接故障，不重发未知模型请求。"""
import json
import time
import urllib.error

from runtime import atomic,digest

MAX_RESUMES=3


def transient_connection(error,phase):
    if phase not in {'collecting','importing'}:return False
    if isinstance(error,urllib.error.HTTPError):
        return phase=='importing' and error.code in {408,429,500,502,503,504}
    if isinstance(error,(ConnectionError,TimeoutError,urllib.error.URLError)):return True
    return type(error).__name__=='OperationalError' and type(error).__module__.startswith('psycopg')


def reserve(root,*,now=None):
    """调用方持有该任务锁；事前持久化接续次数，重启不重置上限和冷却。"""
    now=time.time() if now is None else now
    failure=root/'failure.json'
    if not failure.exists():return False
    value=json.loads(failure.read_text())
    if (value.get('category')!='transient_connection' or value.get('phase') not in {'collecting','importing'}
            or type(value.get('at')) not in {int,float}):return False
    target=root/'connection-recovery.json'
    prior=json.loads(target.read_text()) if target.exists() else dict(version=1,attempts=[])
    if prior.get('version')!=1 or not isinstance(prior.get('attempts'),list):raise ValueError('连接恢复账簿格式错误')
    attempts=prior['attempts']
    if len(attempts)>=MAX_RESUMES:return False
    wait=60*(2**len(attempts))
    if now<max(value['at'],attempts[-1]['reserved_at'] if attempts else value['at'])+wait:return False
    attempts.append(dict(failure_sha256=digest(value),reserved_at=now,phase=value['phase']))
    atomic(target,prior)
    return True
