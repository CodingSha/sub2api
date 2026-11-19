"""离线测量来源阅读成本；仅输出计数，不改评审材料、不连接模型或数据库。"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from bounded_read import plan, raw_size, request_size, source_page
from runtime import atomic, digest


def response_shape(text):
    if not text:
        return 'empty'
    if text.lstrip().startswith(('data:', 'event:')):
        return 'possible_sse'
    try:
        value = json.loads(text)
    except ValueError:
        return 'plain_text'
    # 生成的JSON也可能是实际成果，不能因可解析就当协议外壳删除。
    if isinstance(value, dict) and (value.get('object') in {'chat.completion', 'response'} or
                                   value.get('type') in {'response.completed', 'response.output_text.delta'}):
        return 'possible_protocol_json'
    return 'content_json'


def reading_cost(packet):
    from pipeline import DIRECT_BYTES
    reading = plan(packet, include_text=False)
    events = {e['event']: e for e in packet['events']}
    needs_reading = raw_size(packet) > DIRECT_BYTES or any(
        raw_size(e) > 85_000 for e in packet['events'] if e['actor_class'] == 'candidate_user')
    return dict(pages=len(reading['pages']),
                reading_stage_required=needs_reading,
                reading_stage_pages=len(reading['pages']) if needs_reading else 0,
                fragments=sum(len(p['fragments']) for p in reading['pages']),
                text_bytes=sum(len(e['text'].encode()) for e in packet['events']),
                request_bytes=sum(request_size(source_page(p, events)) for p in reading['pages']))


def experimental_events(packet, variant):
    """返回对照视图；删除上下文的试验仅量上限，不宣称评分等价。"""
    events = packet['events']
    if variant == 'without_empty_context':
        return [e for e in events if e['actor_class'] != 'context' or e['text'] != '']
    if variant == 'without_assistant_context':
        return [e for e in events if not (e['actor_class'] == 'context' and e.get('protocol_role') == 'assistant')]
    if variant == 'without_tool_context':
        return [e for e in events if not (e['actor_class'] == 'context' and e.get('protocol_role') == 'tool')]
    if variant == 'candidate_user_only':
        return [e for e in events if e['actor_class'] == 'candidate_user']
    raise ValueError('未识别的离线对照')


def factor_text(packet):
    """实验性字典仅复用相同正文；每次出现的身份、顺序和来源元数据全部保留。"""
    counts = Counter(e['text'] for e in packet['events'])
    bodies = {}
    events = []
    for e in packet['events']:
        if len(e['text'].encode()) >= 128 and counts[e['text']] > 1:
            key = hashlib.sha256(e['text'].encode()).hexdigest()
            bodies[key] = e['text']
            events.append(dict({k: v for k, v in e.items() if k != 'text'}, profile_text_ref=key))
        else:
            events.append(dict(e))
    return dict(packet, events=events, profile_text_bodies=bodies)


def restore_text(view):
    result = {k: v for k, v in view.items() if k != 'profile_text_bodies'}
    result['events'] = []
    for e in view['events']:
        event = dict(e)
        if 'profile_text_ref' in event:
            key = event.pop('profile_text_ref')
            text = view['profile_text_bodies'][key]
            if hashlib.sha256(text.encode()).hexdigest() != key:
                raise ValueError('实验正文引用签名变化')
            event['text'] = text
        result['events'].append(event)
    return result


def profile(packet):
    original = digest(packet)
    roles = defaultdict(lambda: dict(events=0, text_bytes=0, empty=0, exact_repeat_events=0, exact_repeat_bytes=0,
                                    repeated_long_line_bytes=0, truncated_events=0))
    seen, lines = set(), set()
    shapes = Counter()
    for e in packet['events']:
        role = e.get('protocol_role', 'unknown')
        group = roles[role]
        text = e['text']
        size = len(text.encode())
        group['events'] += 1
        group['text_bytes'] += size
        group['empty'] += text == ''
        group['truncated_events'] += bool(e.get('truncated'))
        # 无会话标识时仅限同一审计行；相同文字不证明同一次行为。
        scope = e.get('session_id') or 'row:' + e.get('audit_row_id', e['event'])
        fingerprint = (scope, role, hashlib.sha256(text.encode()).hexdigest())
        if size and fingerprint in seen:
            group['exact_repeat_events'] += 1
            group['exact_repeat_bytes'] += size
        seen.add(fingerprint)
        current_lines = set()
        for line in text.splitlines(keepends=True):
            if len(line.encode()) < 64:
                continue
            identity = (scope, role, hashlib.sha256(line.encode()).hexdigest())
            if identity in lines:
                group['repeated_long_line_bytes'] += len(line.encode())
            current_lines.add(identity)
        lines.update(current_lines)
        if e.get('field') == 'response':
            shapes[response_shape(text)] += 1
    variants = {}
    for name in ('without_empty_context', 'without_assistant_context', 'without_tool_context', 'candidate_user_only'):
        selected = experimental_events(packet, name)
        variants[name] = dict(reading_cost(dict(packet, events=selected)),
                              omitted_events=len(packet['events']) - len(selected),
                              interpretation='仅成本对照，不是已批准的新阅读或评分合同')
    if digest(packet) != original:
        raise ValueError('离线测量意外改变原材料')
    factored = factor_text(packet)
    if digest(restore_text(factored)) != original:
        raise ValueError('实验正文复用未能还原完整原材料')
    return dict(packet_sha256=original, packet_bytes=raw_size(packet),
                user_id=packet.get('user_id'), since=packet.get('since'), until=packet.get('until'),
                events=len(packet['events']), roles=dict(roles), response_shapes=dict(shapes),
                source_issues=len(packet.get('issues', [])), baseline=reading_cost(packet), variants=variants,
                exact_body_dictionary=dict(serialized_bytes=raw_size(factored), bodies=len(factored['profile_text_bodies']),
                    occurrence_count=len(factored['events']), restored_packet_sha256=original,
                    interpretation='可逆结构的序列化字节对照；未接入阅读分页，不是已验证模型节省或评分等价'),
                limits=['页数是当前分页器完整首读请求数，不含缓存命中、导航、评分、复查和重试。',
                        'pages为统一分页对照；短材料reading_stage_pages=0，实际直接评审，不将理论分页当付费阅读。',
                        'request_bytes为含schema和保守模型名称的序列化请求字节，不是token数、延迟或费用。',
                        '重复长行保留时点和出处才可复用；相同输出可能出现在不同执行或不同用户决策前。',
                        '去掉AI或工具上下文可能丢失失败反证、核验结果及归属依据；本试验未证明评分等价。'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packet', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    results = []
    for path in args.packet:
        raw = path.read_bytes()
        results.append(dict(source_file_sha256=hashlib.sha256(raw).hexdigest(), result=profile(json.loads(raw))))
    atomic(args.output, dict(version=1, mode='offline_no_model_no_database', cases=results))


if __name__ == '__main__':
    main()
