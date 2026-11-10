"""公司 Responses 接口：独立请求、有限重试及紧凑回执，不保存重复请求正文。"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

COMPANY_URL = 'https://ai.apsatcom.com'


class ModelError(RuntimeError):
    def __init__(self, code, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w') as out:
        json.dump(value, out, ensure_ascii=False, separators=(',', ':'))
        out.flush()
        os.fsync(out.fileno())
    temp.replace(path)


def configured_model():
    # 只读取当前使用的配置；不打印配置文件或认证。
    config = (Path.home() / '.codex/config.toml').read_text()
    model = re.search(r'^\s*"?model"?\s*=\s*"([^"\n]+)"', config, re.M)
    provider = re.search(r'^\s*"?model_provider"?\s*=\s*"([^"\n]+)"', config, re.M)
    if not model or not provider:
        raise ModelError('configuration_missing')
    section = re.search(r'^\[\s*"?model_providers"?\s*\.\s*"?' + re.escape(provider[1]) +
                        r'"?\s*\]\s*$(.*?)(?=^\[|\Z)', config, re.M | re.S)
    selected = section[1] if section else re.split(r'^\[', config, maxsplit=1, flags=re.M)[0]
    endpoint = re.search(r'^\s*"?base_url"?\s*=\s*"([^"\n]+)"', selected, re.M)
    if not endpoint or endpoint[1].rstrip('/') != COMPANY_URL:
        raise ModelError('company_endpoint_mismatch')
    return model[1]


def terminal_response(stream):
    """只接受完整 completed 终态；断流、拒绝和截断均不是成功。"""
    lines = []
    for raw in stream:
        if len(raw) > 4_000_000:
            raise ModelError('response_too_large')
        line = raw.decode('utf-8').rstrip('\r\n')
        if line.startswith('data:'):
            lines.append(line[5:].lstrip())
        elif not line and lines:
            try:
                packet = json.loads('\n'.join(lines))
            except json.JSONDecodeError:
                raise ModelError('invalid_stream', True) from None
            lines = []
            kind = packet.get('type', '')
            if kind in {'response.completed', 'response.incomplete', 'response.failed'}:
                return packet['response']
            if kind == 'error':
                raise ModelError('stream_error')
    raise ModelError('stream_interrupted', True)


def response_text(response):
    if response.get('status') != 'completed':
        code = response.get('error') or response.get('incomplete_details') or {}
        raise ModelError('response_' + str(code.get('code') or code.get('reason') or response.get('status')))
    parts = []
    for item in response.get('output', []):
        for content in item.get('content', []):
            if content.get('type') == 'refusal':
                raise ModelError('model_refusal')
            if content.get('type') == 'output_text':
                parts.append(content['text'])
    if not parts:
        raise ModelError('empty_model_output')
    return ''.join(parts)


class ResponsesClient:
    def __init__(self, model, timeout=900, attempts=3, opener=None, sleep=None):
        self.model, self.timeout, self.attempts = model, timeout, attempts
        self.opener, self.sleep = opener or urllib.request.urlopen, sleep or time.sleep

    def call(self, instructions, data, cache, run_id):
        if configured_model() != self.model:
            raise ModelError('model_configuration_changed')
        key = json.loads((Path.home() / '.codex/auth.json').read_text()).get('OPENAI_API_KEY')
        if not key:
            raise ModelError('credentials_missing')
        payload = dict(model=self.model, instructions=instructions,
                       input=json.dumps(data, ensure_ascii=False, separators=(',', ':')),
                       stream=True, store=False, max_output_tokens=30000,
                       reasoning={'effort': 'medium'},
                       metadata={'purpose': 'zhiyongli_assessment', 'run_id': run_id,
                                 'source_period': '2026-09'})
        signature = digest(payload)
        path = Path(cache) / (signature + '.json')
        if path.exists():
            record = json.loads(path.read_text())
            if (record['request_sha256'] != signature or digest(record['response']) != record['response_sha256']
                    or record['text'] != record['response']['text']):
                raise ModelError('response_cache_corrupted')
            return record
        body = json.dumps(payload, ensure_ascii=False).encode()
        history = []
        for attempt in range(self.attempts):
            started = time.monotonic()
            try:
                request = urllib.request.Request(COMPANY_URL + '/responses', data=body,
                    headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
                with self.opener(request, timeout=self.timeout) as stream:
                    response = terminal_response(stream)
                text = response_text(response)
                record = dict(request_sha256=signature, response_id=response.get('id'),
                              model=self.model, usage=response.get('usage'), text=text,
                              response={'id': response.get('id'), 'status': response['status'], 'text': text},
                              attempts=history + [dict(attempt=attempt+1, status='completed',
                                                      seconds=round(time.monotonic()-started, 2))])
                record['response_sha256'] = digest(record['response'])
                atomic_json(path, record)
                return record
            except urllib.error.HTTPError as ex:
                # 错误正文不写终端；拒绝不换模型、不改写原文绕过。
                error_body = ex.read(8000).decode('utf-8', 'replace')
                refusal = any(x in error_body.lower() for x in ('cyber_policy', 'content_policy', 'policy_violation'))
                err = ModelError('policy_blocked' if refusal else 'http_' + str(ex.code),
                                 not refusal and ex.code in {408, 409, 429, 500, 502, 503, 504})
            except ModelError as ex:
                err = ex
            except (OSError, TimeoutError, UnicodeError) as ex:
                err = ModelError('transport_' + type(ex).__name__, True)
            history.append(dict(attempt=attempt+1, code=err.code, retryable=err.retryable,
                                seconds=round(time.monotonic()-started, 2)))
            atomic_json(Path(cache) / (signature + '.failure.json'),
                        dict(request_sha256=signature, attempts=history, status='execution_failed'))
            if not err.retryable or attempt + 1 == self.attempts:
                raise err
            self.sleep(min(30, 5 * 2**attempt))
