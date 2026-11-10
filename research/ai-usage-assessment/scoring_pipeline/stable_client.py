"""固定公司模型的JSON传输：有限重试、完整终态、回执和错误保留。"""
import json
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

from .batch_client import COMPANY_URL, ModelError, atomic_json, configured_model, digest, response_text


class JSONClient:
    def __init__(self,model,timeout=360,attempts=2,opener=None,sleep=None):
        self.model,self.timeout,self.attempts=model,timeout,attempts
        self.opener,self.sleep=opener or urllib.request.urlopen,sleep or time.sleep

    def call(self,instructions,data,cache,run_id):
        if configured_model()!=self.model:raise ModelError('model_configuration_changed')
        key=json.loads((Path.home()/'.codex/auth.json').read_text()).get('OPENAI_API_KEY')
        if not key:raise ModelError('credentials_missing')
        payload=dict(model=self.model,instructions=instructions,input=json.dumps(data,ensure_ascii=False,separators=(',',':')),
            stream=False,store=False,max_output_tokens=24000,reasoning={'effort':'medium'},
            metadata={'purpose':'zhiyongli_assessment','run_id':run_id,'source_period':'2026-09'})
        signature=digest(payload);cache=Path(cache);path=cache/(signature+'.json')
        if path.exists():
            record=json.loads(path.read_text())
            if record['request_sha256']!=signature or digest(record['response'])!=record['response_sha256'] or record['text']!=record['response']['text']:
                raise ModelError('response_cache_corrupted')
            return record
        history=[];invocation_id=uuid.uuid4().hex;body=json.dumps(payload,ensure_ascii=False).encode()
        for attempt in range(self.attempts):
            started=time.monotonic()
            try:
                request=urllib.request.Request(COMPANY_URL+'/responses',data=body,
                    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
                with self.opener(request,timeout=self.timeout) as stream:raw=stream.read(20_000_001)
                if len(raw)>20_000_000:raise ModelError('response_too_large')
                try:response=json.loads(raw)
                except (UnicodeError,json.JSONDecodeError):raise ModelError('invalid_json_response',True) from None
                text=response_text(response)
                record=dict(request_sha256=signature,response_id=response.get('id'),model=self.model,
                    usage=response.get('usage'),text=text,response={'id':response.get('id'),'status':response['status'],'text':text},
                    attempts=history+[dict(attempt=attempt+1,status='completed',seconds=round(time.monotonic()-started,2))])
                record['response_sha256']=digest(record['response']);atomic_json(path,record)
                return record
            except urllib.error.HTTPError as ex:
                error=ex.read(8000).decode('utf-8','replace').lower()
                refused=any(s in error for s in ('cyber_policy','content_policy','policy_violation'))
                err=ModelError('policy_blocked' if refused else 'http_'+str(ex.code),not refused and ex.code in {408,409,429,500,502,503,504})
            except ModelError as ex:err=ex
            except (OSError,TimeoutError,UnicodeError) as ex:err=ModelError('transport_'+type(ex).__name__,True)
            history.append(dict(attempt=attempt+1,code=err.code,retryable=err.retryable,seconds=round(time.monotonic()-started,2)))
            failure=dict(request_sha256=signature,invocation_id=invocation_id,attempts=history,status='execution_failed')
            # 同一请求续跑也不覆盖此前每次传输失败；顶层文件只保留最近状态。
            atomic_json(cache/'failures'/(signature+'.'+invocation_id+'.'+str(attempt+1)+'.json'),failure)
            atomic_json(cache/(signature+'.failure.json'),failure)
            if not err.retryable or attempt+1==self.attempts:raise err
            self.sleep(min(20,5*2**attempt))
