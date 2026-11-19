"""会话证据读取入口：按调用定位原文，不做本人认领、任务切分或评分。"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from runtime import digest
from navigation_access import freeze

VERSION = 'session-evidence-20261117.2'
MAX_BYTES = 24_000
LIMITATION = '目录和预览只作定位；未读正文、Skill内容和AI执行不自动证明本人行为或验收。'


def captured_events(packet):
    """读取已知AIHub捕获合同，保留工具事件种类；旧提取行为不变。"""
    from protocol_events import extract
    result = extract(packet)
    typed = {}
    responses = {}
    opaque_responses = set()
    opaque = []
    for frame in packet['frames']:
        try:
            body = json.loads(frame['text'])
        except (ValueError, TypeError):
            continue
        if not isinstance(body, dict) or not isinstance(body.get('audit_events'), list):
            continue
        valid = []
        for index, item in enumerate(body['audit_events']):
            if (not isinstance(item, dict) or item.get('kind') not in
                    {'user', 'assistant', 'tool_call', 'tool_result'} or not isinstance(item.get('text'), str)):
                continue
            key = frame['frame']+':audit_events/'+str(index)
            typed[key] = item['kind']
            valid.append((key, index, item))
        if frame['field'] == 'response':
            # 响应也可能是同一捕获合同，不能整体降为不可查询的字符串。
            if len(valid) == len(body['audit_events']) and valid:
                responses[frame['frame']] = valid
            else:
                opaque_responses.add(frame['frame'])
                result['issues'].append(dict(frame=frame['frame'], code='opaque_response_audit_events'))
        elif len(valid) != len(body['audit_events']):
            opaque.append(frame)
    events = []
    for event in result['events']:
        replacement = responses.get(event['event'])
        if replacement:
            for key, index, item in replacement:
                current = copy.deepcopy(event)
                current.update(event=key, text=item['text'], content_kind='text',
                    protocol_role='tool' if item['kind'].startswith('tool_') else item['kind'],
                    actor_class='context', audit_kind=item['kind'],
                    text_sha256=hashlib.sha256(item['text'].encode()).hexdigest())
                current['source']['pointer'] = 'audit_events/'+str(index)
                events.append(current)
        else:
            current = copy.deepcopy(event)
            if event['event'] in typed:
                current['audit_kind'] = typed[event['event']]
            if event['event'] in opaque_responses:
                current['audit_kind'] = 'protocol_payload'
            events.append(current)
    for frame in opaque:
        # 不支持的事件保留完整捕获正文，不能靠跳过它宣称覆盖完成。
        base = extract(dict(packet, frames=[dict(frame, field='response')]))['events'][0]
        base.update(event=frame['frame']+':opaque_audit_events', audit_kind='protocol_payload')
        events.append(base)
    frames = {frame['frame']: frame for frame in packet['frames']}
    for event in events:
        frame = frames[event['source']['frame']]
        if 'group_id' in frame:
            event['group_id'] = frame['group_id']
        if frame.get('source'):
            event['source']['capture_source'] = copy.deepcopy(frame['source'])
    result['events'] = events
    result['capture_contract'] = VERSION
    return result


def sections(text):
    """只识别单个明确包装区间；标签内仍是候选，绝不自动认领为本人。"""
    opening, closing = '<user_query>', '</user_query>'
    if text.count(opening) == text.count(closing) == 1:
        start, end = text.index(opening)+len(opening), text.index(closing)
        if start <= end:
            return [dict(kind=kind, start=a, end=b,
                text_sha256=hashlib.sha256(text[a:b].encode()).hexdigest())
                for kind, a, b in [('surrounding_payload', 0, start),
                    ('tagged_query_candidate', start, end), ('surrounding_payload', end, len(text))] if a < b]
    return [dict(kind='whole_payload', start=0, end=len(text),
        text_sha256=hashlib.sha256(text.encode()).hexdigest())]


def descriptor(event):
    kind = event.get('audit_kind')
    if kind is None:
        role = event.get('protocol_role')
        kind = ('user_payload' if role == 'user' else 'tool_unspecified' if role == 'tool'
                else 'assistant_payload' if role == 'assistant' else 'protocol_payload')
    tool = {}
    # 只有已知AIHub工具捕获的文本头才解析；普通员工文字不按关键词改角色。
    if kind in {'tool_call', 'tool_result'}:
        head = event['text'].split('\n\n', 1)[0]
        for label, field in [('Tool', 'name'), ('Call ID', 'call_id'), ('Type', 'type'), ('Status', 'status')]:
            matches = re.findall(r'^'+re.escape(label)+r': ([^\r\n]+)$', head, re.M)
            if len(matches) == 1:
                tool[field] = matches[0]
    return dict(kind=kind, protocol_role=event.get('protocol_role'), actor_class=event.get('actor_class'),
        at=event.get('at'), timestamp_unknown=event.get('timestamp_unknown'),
        timestamp_basis=event.get('timestamp_basis'), truncated=bool(event.get('truncated')),
        source=copy.deepcopy(event.get('source', {})), tool=tool,
        sections=sections(event['text']) if event.get('actor_class') == 'candidate_user' else [],
        characters=len(event['text']), text_bytes=len(event['text'].encode()))


class SessionEvidence:
    """会话、调用、事件三级目录；正文按需读取，所有操作限定同一来源包。"""
    def __init__(self, directory):
        self.root = Path(directory)
        self.manifest = json.loads((self.root/'manifest.json').read_text())
        if self.manifest['version'] != VERSION:
            raise ValueError('会话证据版本不符')
        frozen = json.loads((self.root/'input.json').read_text())
        if any(self.manifest.get(k) != v for k, v in frozen.items()):
            raise ValueError('会话证据与冻结输入不符')
        if (any(digest(scope) != key or scope['user_id'] != frozen['user_id']
                for key, scope in self.manifest['sessions'].items())
                or any(digest(scope) != key or scope['session_key'] not in self.manifest['sessions']
                    for key, scope in self.manifest['requests'].items())):
            raise ValueError('会话或调用范围签名不符')
        self.db = sqlite3.connect('file:'+str(self.root/'evidence.sqlite')+'?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row

    def close(self):
        self.db.close()

    @classmethod
    def build(cls, packet, directory):
        root = Path(directory); root.mkdir(parents=True, exist_ok=True, mode=0o700)
        identity = dict(version=VERSION, packet_sha256=digest(packet), user_id=str(packet['user_id']),
            since=packet['since'], until=packet['until'])
        freeze(root/'input.json', identity)
        if (root/'manifest.json').exists():
            result = cls(root)
            result.verify()
            return result
        path = root/'evidence.sqlite'
        # 未完成建库只重建派生目录；输入已冻结，不覆盖原始来源。
        with sqlite3.connect(path) as db:
            db.executescript('''DROP TABLE IF EXISTS events; DROP TABLE IF EXISTS bodies;
                CREATE TABLE bodies(sha TEXT PRIMARY KEY,text TEXT NOT NULL);
                CREATE TABLE events(key TEXT PRIMARY KEY,origin TEXT NOT NULL,session TEXT NOT NULL,
                    request TEXT NOT NULL,position INTEGER NOT NULL,body_sha TEXT NOT NULL,meta TEXT NOT NULL);
                CREATE INDEX event_session ON events(session,position);
                CREATE INDEX event_request ON events(request,position);''')
            keys = []; sessions = {}; request_scopes = {}
            origins = set()
            for position, event in enumerate(packet['events']):
                if event['event'] in origins:
                    raise ValueError('来源事件重复，不能覆盖')
                origins.add(event['event'])
                user = str(event.get('source_user_id', packet['user_id']))
                if user != str(packet['user_id']):
                    raise ValueError('会话材料混入其他人员')
                source = event.get('source', {})
                session = dict(user_id=user, api_key_id=event.get('api_key_id'), group_id=event.get('group_id'), session_id=event.get('session_id'),
                    missing_session_source=None if event.get('session_id') else source.get('frame', event['event']))
                sid = digest(session); sessions[sid] = session
                request = dict(session_key=sid, request_id=event.get('request_id'),
                    missing_request_source=None if event.get('request_id') else source.get('frame', event['event']))
                rid = digest(request); request_scopes[rid] = request
                meta = descriptor(event); body = hashlib.sha256(event['text'].encode()).hexdigest()
                key = digest(dict(origin=event['event'], session=sid, request=rid,
                    position=position, body_sha=body, meta=meta))
                db.execute('INSERT OR IGNORE INTO bodies VALUES(?,?)', (body, event['text']))
                db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)',
                    (key, event['event'], sid, rid, position, body, json.dumps(meta, ensure_ascii=False)))
                keys.append(key)
        os.chmod(path, 0o600)
        freeze(root/'manifest.json', dict(identity, events=len(keys), event_keys_sha256=digest(keys),
            sessions=sessions, requests=request_scopes, limitation=LIMITATION,
            ordering='保留采集包顺序；采集时间不证明员工消息时序或跨调用因果'))
        result = cls(root); result.verify(); return result

    def verify(self):
        keys = []
        for row in self.db.execute('SELECT * FROM events ORDER BY position'):
            self._descriptor(row)
            text = self.db.execute('SELECT text FROM bodies WHERE sha=?', (row['body_sha'],)).fetchone()[0]
            if hashlib.sha256(text.encode()).hexdigest() != row['body_sha']:
                raise ValueError('会话正文签名不符')
            keys.append(row['key'])
        if len(keys) != self.manifest['events'] or digest(keys) != self.manifest['event_keys_sha256']:
            raise ValueError('会话事件覆盖不符')

    def _descriptor(self, row):
        meta = json.loads(row['meta'])
        if digest(dict(origin=row['origin'], session=row['session'], request=row['request'],
                position=row['position'], body_sha=row['body_sha'], meta=meta)) != row['key']:
            raise ValueError('会话定位签名不符')
        return dict(event_key=row['key'], origin_event=row['origin'], body_sha256=row['body_sha'], **meta)

    def _page(self, rows, cursor, limit, project=lambda value:value, **extra):
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('分页参数超出范围')
        def response(items, end):
            return dict(items=items, next_cursor=end if end < len(rows) else None,
                total=len(rows), complete=end >= len(rows), previews_are_evidence=False, **extra)
        result = []; end = cursor
        if len(json.dumps(response(result, end), ensure_ascii=False).encode()) > MAX_BYTES:
            raise ValueError('目录返回包装超过容量')
        for source in rows[cursor:cursor+limit]:
            value = project(source)
            probe = response(result+[value], end+1)
            if len(json.dumps(probe, ensure_ascii=False).encode()) > MAX_BYTES:
                if not result:
                    raise ValueError('单个目录项超过返回容量，须细化目录，禁止静默截断')
                break
            result.append(value); end += 1
        return response(result, end)

    def list_sessions(self, cursor=0, limit=20):
        def project(pair):
            key, scope = pair
            counts = self.db.execute("SELECT count(*),count(DISTINCT request),sum(json_extract(meta,'$.actor_class')='candidate_user') FROM events WHERE session=?", (key,)).fetchone()
            return dict(session_key=key, **scope, events=counts[0], model_calls=counts[1], candidate_inputs=counts[2])
        return self._page(list(self.manifest['sessions'].items()), cursor, limit, project)

    def list_inputs(self, cursor=0, limit=20):
        """直接定位全部候选输入，缺会话ID时也不必遍历大量工具背景分组。"""
        rows = self.db.execute("SELECT * FROM events WHERE json_extract(meta,'$.actor_class')='candidate_user' ORDER BY position").fetchall()
        def project(row):
            item = self._descriptor(row)
            return dict(event_key=row['key'], session_key=row['session'], request_key=row['request'],
                body_sha256=row['body_sha'], kind=item['kind'], actor_class=item['actor_class'],
                at=item['at'], timestamp_unknown=item['timestamp_unknown'], timestamp_basis=item['timestamp_basis'],
                truncated=item['truncated'], characters=item['characters'], text_bytes=item['text_bytes'],
                sections=item['sections'])
        return self._page(rows, cursor, limit, project,
            limitation='覆盖全部候选user正文的定位，不代表全部为员工本人，也不代表任务或执行次数。')

    def list_calls(self, session_key, cursor=0, limit=20):
        if session_key not in self.manifest['sessions']:
            raise ValueError('会话不在当前人员冻结来源')
        rows = self.db.execute('SELECT request,min(position) p FROM events WHERE session=? GROUP BY request ORDER BY p', (session_key,)).fetchall()
        def project(request):
            events = [self._descriptor(r) for r in self.db.execute('SELECT * FROM events WHERE request=? ORDER BY position', (request['request'],))]
            return dict(request_key=request['request'], **self.manifest['requests'][request['request']],
                kinds=dict(Counter(e['kind'] for e in events)),
                capture_times=sorted({e['at'] for e in events if e['at'] is not None}),
                tools=sorted({e['tool']['name'] for e in events if e['tool'].get('name')}),
                truncated_events=sum(e['truncated'] for e in events), text_bytes=sum(e['text_bytes'] for e in events))
        return self._page(rows, cursor, limit, project)

    def list_events(self, request_key, cursor=0, limit=20):
        if request_key not in self.manifest['requests']:
            raise ValueError('调用不在当前人员冻结来源')
        rows = [self._descriptor(row) for row in self.db.execute('SELECT * FROM events WHERE request=? ORDER BY position', (request_key,))]
        return self._page(rows, cursor, limit)

    def read_event(self, event_key, start=0, max_characters=3000):
        if type(start) is not int or start < 0 or type(max_characters) is not int or not 1 <= max_characters <= 6000:
            raise ValueError('原文读取范围无效')
        row = self.db.execute('SELECT * FROM events WHERE key=?', (event_key,)).fetchone()
        if row is None:
            raise ValueError('事件不在当前人员冻结来源')
        meta = self._descriptor(row)
        text = self.db.execute('SELECT text FROM bodies WHERE sha=?', (row['body_sha'],)).fetchone()[0]
        if hashlib.sha256(text.encode()).hexdigest() != row['body_sha']:
            raise ValueError('会话正文签名不符')
        if start > len(text):
            raise ValueError('原文起点越界')
        end = min(len(text), start+max_characters)
        result = dict(event_key=event_key, origin_event=row['origin'], body_sha256=row['body_sha'],
            start=start, end=end, text=text[start:end], next_start=end if end < len(text) else None,
            source=meta['source'], truncated=meta['truncated'], actor_class=meta['actor_class'])
        while len(json.dumps(result, ensure_ascii=False).encode()) > MAX_BYTES and end > start:
            end = start+(end-start)//2
            result.update(end=end, text=text[start:end], next_start=end)
        if len(json.dumps(result, ensure_ascii=False).encode()) > MAX_BYTES:
            raise ValueError('原文来源定位超返回容量')
        if end == start and start < len(text):
            raise ValueError('单个原文字符及来源定位无法装入返回容量')
        return result

    def read_events(self, requests):
        """批量交付连续工作材料；完整包装有界，未交付部分显式续读。"""
        if not isinstance(requests,list) or not 1 <= len(requests) <= 50:
            raise ValueError('批量原文读取须包含1至50个明确区间')
        for request in requests:
            if (not isinstance(request,dict) or set(request)!={'event_key','start','max_characters'}
                    or not isinstance(request['event_key'],str)
                    or type(request['start']) is not int or request['start'] < 0
                    or type(request['max_characters']) is not int or not 1 <= request['max_characters'] <= 6000):
                raise ValueError('批量原文读取区间格式无效')
        result = dict(items=[],next_requests=copy.deepcopy(requests),complete=False)
        for index, request in enumerate(requests):
            maximum = request['max_characters']
            while True:
                piece = self.read_event(**dict(request,max_characters=maximum))
                pending = copy.deepcopy(requests[index+1:])
                if piece['next_start'] is not None:
                    pending.insert(0,dict(request,start=piece['next_start']))
                probe = dict(items=result['items']+[piece],next_requests=pending,complete=not pending)
                if len(json.dumps(probe,ensure_ascii=False).encode()) <= MAX_BYTES:
                    result = probe
                    break
                if maximum == 1:
                    if not result['items']:
                        raise ValueError('批量读取的来源与续读定位超过返回容量')
                    return result
                maximum = max(1,maximum//2)
            if piece['next_start'] is not None:
                break
        return result

    def search(self, session_key, literal, cursor=0, limit=20):
        if session_key not in self.manifest['sessions'] or not isinstance(literal, str) or not 1 <= len(literal) <= 200:
            raise ValueError('搜索须指定本人的会话和非空字面文字')
        rows = []
        for row in self.db.execute('SELECT e.*,b.text FROM events e JOIN bodies b ON b.sha=e.body_sha WHERE e.session=? AND instr(b.text,?)>0 ORDER BY e.position', (session_key, literal)):
            item = self._descriptor(row)
            if hashlib.sha256(row['text'].encode()).hexdigest() != row['body_sha']:
                raise ValueError('搜索正文签名不符')
            rows.append(dict(event_key=item['event_key'], origin_event=item['origin_event'], kind=item['kind'],
                first_match_start=row['text'].index(literal), matches=row['text'].count(literal)))
        return self._page(rows, cursor, limit,
            limitation='字面检索只提供定位；未命中不证明没有语义相关证据或反证。')

    def tool_trace(self, session_key, call_id, cursor=0, limit=20):
        """仅按明确Call ID找关联；同一调用在后续请求重现也保留，不冒充多次执行。"""
        if session_key not in self.manifest['sessions'] or not isinstance(call_id, str) or not 1 <= len(call_id) <= 200:
            raise ValueError('工具关联须指定本人的会话和明确调用ID')
        rows = self.db.execute("SELECT * FROM events WHERE session=? AND json_extract(meta,'$.tool.call_id')=? ORDER BY position",
            (session_key, call_id)).fetchall()
        return self._page(rows,cursor,limit,lambda row:dict(request_key=row['request'],**self._descriptor(row)),
            limitation='同一工具调用可能在后续请求上下文重现；返回出现记录，不是执行次数。')

    def query(self, operation, arguments):
        methods = {name:getattr(self, name) for name in ('list_sessions','list_inputs','list_calls','list_events','read_event','read_events','search','tool_trace')}
        if operation not in methods or not isinstance(arguments, dict):
            raise ValueError('未知会话证据操作')
        return methods[operation](**arguments)

    def tool_call(self, operation, arguments, receipt):
        """模型工具适配器；每次读取独立验签，恢复直接复用，不累计重发全部正文。"""
        stamp = dict(version=VERSION, packet_sha256=self.manifest['packet_sha256'],
            operation=operation, arguments=arguments)
        receipt = Path(receipt)
        if receipt.exists():
            prior = json.loads(receipt.read_text())
            if prior['input'] != stamp or digest(prior['result']) != prior['result_sha256']:
                raise ValueError('会话读取回执与请求或结果不符')
            return prior['result']
        result = self.query(operation, arguments)
        freeze(receipt, dict(input=stamp, result=result, result_sha256=digest(result)))
        return result

    def cite(self, receipt, quote, *, event_key=None):
        """只认实际交付原文中的唯一逐字引文，目录、预览和未读正文不能成为证据。"""
        proof = json.loads(Path(receipt).read_text())
        if (proof['input']['packet_sha256'] != self.manifest['packet_sha256']
                or proof['input']['operation'] not in {'read_event','read_events'} or digest(proof['result']) != proof['result_sha256']):
            raise ValueError('引文缺少本次原文读取回执')
        pieces = proof['result']['items'] if proof['input']['operation']=='read_events' else [proof['result']]
        selected = [piece for piece in pieces if event_key is None or piece['event_key']==event_key]
        if (not isinstance(quote,str) or not quote.strip()
                or sum(piece['text'].count(quote) for piece in selected)!=1):
            raise ValueError('引文未在实际交付原文中唯一逐字出现')
        delivered = next(piece for piece in selected if quote in piece['text'])
        current = self.read_event(delivered['event_key'], delivered['start'],
            max_characters=max(1, delivered['end']-delivered['start']))
        if current != delivered:
            raise ValueError('交付原文与当前冻结来源不符')
        start = delivered['start']+delivered['text'].index(quote)
        return dict(event_key=delivered['event_key'], origin_event=delivered['origin_event'],
            start=start, end=start+len(quote), quote=quote, body_sha256=delivered['body_sha256'],
            source=delivered['source'], actor_class=delivered['actor_class'],
            attribution_confirmed=False)


def model_tools():
    """Responses函数工具合同；只读会话数据，没有修改、发布或评分状态操作。"""
    string = dict(type='string')
    cursor = dict(type='integer', minimum=0)
    limit = dict(type='integer', minimum=1, maximum=50)
    read_arguments = dict(event_key=string,start=cursor,max_characters=dict(type='integer',minimum=1,maximum=6000))
    definitions = [
        ('list_sessions', '列出当前人员的来源会话；不把会话认作任务。', dict(cursor=cursor,limit=limit)),
        ('list_inputs', '先定位全部候选用户输入和原文分区，再读取、核归属及任务；不先遍历全部工具目录。', dict(cursor=cursor,limit=limit)),
        ('list_calls', '按采集顺序列出会话内模型调用及工具目录。', dict(session_key=string,cursor=cursor,limit=limit)),
        ('list_events', '列出调用内候选用户、工具和回复的原文定位。', dict(request_key=string,cursor=cursor,limit=limit)),
        ('read_event', '读取指定原文区间，next_start非空时还有正文；仅实际交付的正文可提供引文证据。',
            read_arguments),
        ('read_events', '批量读取已定位的用户输入、相关回复或工具原文；next_requests非空须续读，只有已交付正文可作引文。',
            dict(requests=dict(type='array',minItems=1,maxItems=50,items=dict(type='object',
                properties=read_arguments,required=list(read_arguments),additionalProperties=False)))),
        ('search', '在指定会话内按字面查找定位；未命中不证明没有反证。',
            dict(session_key=string,literal=dict(type='string',minLength=1,maxLength=200),cursor=cursor,limit=limit)),
        ('tool_trace', '按明确工具调用ID定位调用与结果的出现记录，不按出现数统计执行次数。',
            dict(session_key=string,call_id=dict(type='string',minLength=1,maxLength=200),cursor=cursor,limit=limit)),
    ]
    return [dict(type='function',name=name,description=description,strict=True,
        parameters=dict(type='object',properties=properties,required=list(properties),additionalProperties=False))
        for name,description,properties in definitions]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build')
    build.add_argument('--packet', type=Path, required=True)
    build.add_argument('--expected-sha256', required=True)
    build.add_argument('--output', type=Path, required=True)
    query = commands.add_parser('query')
    query.add_argument('--store', type=Path, required=True)
    query.add_argument('--operation', required=True)
    query.add_argument('--arguments', default='{}')
    args = parser.parse_args()
    if args.command == 'build':
        raw = args.packet.read_bytes()
        if hashlib.sha256(raw).hexdigest() != args.expected_sha256:
            raise ValueError('原始冻结包签名不符')
        store = SessionEvidence.build(json.loads(raw), args.output)
        result = dict(events=store.manifest['events'], sessions=len(store.manifest['sessions']),
            model_calls=len(store.manifest['requests']), model_requests=0, semantic_acceptance=False)
    else:
        store = SessionEvidence(args.store)
        result = store.query(args.operation, json.loads(args.arguments))
    try:
        print(json.dumps(result, ensure_ascii=False))
    finally:
        store.close()


if __name__ == '__main__':
    main()
