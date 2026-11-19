"""新周期的确定性正文复用；每次事件仍单独留档、索引及核归属。"""
import hashlib
import json
from pathlib import Path

from runtime import digest

STRATEGY = 'content-once-20261114.1'


def partition(packet):
    """只免读零字符背景、同一人同会话同角色的逐字相同正文。"""
    events = []; records = []; seen = {}
    ids = [e['event'] for e in packet['events']]
    if len(ids) != len(set(ids)):
        raise ValueError('正文复用来源事件ID重复')
    for event in packet['events']:
        text = event['text']; context = event['actor_class'] == 'context'
        proof = dict(event_id=event['event'], source_event_sha256=digest(event))
        if context and text == '':
            records.append(dict(proof, method='empty_saved_text'))
            continue
        session = event.get('session_id'); role = event.get('protocol_role')
        user = event.get('source_user_id')
        reusable = (context and bool(text) and isinstance(session, str) and bool(session.strip())
            and role in {'assistant', 'tool'} and user is not None
            and str(user) == str(packet.get('user_id')))
        if reusable:
            key = (str(user), session, role, hashlib.sha256(text.encode()).hexdigest())
            original = seen.get(key)
            if original is not None:
                if original['text'] != text:
                    raise ValueError('同文哈希与实际正文不符')
                records.append(dict(proof, method='identical_context_text',
                    canonical_event_id=original['event'], canonical_source_event_sha256=digest(original)))
                continue
            seen[key] = event
        events.append(event)
    return events, records


def checked_plan(packet, manifest):
    """程序记录不能冒充模型回执；重算整个计划核验事件身份及引用。"""
    from bounded_read import plan
    if manifest.get('reading_strategy') != STRATEGY:
        raise ValueError('未识别的正文复用合同')
    expected = plan(packet, manifest['page_bytes'], include_text=False,
        content_identity=manifest['content_identity'], efficiency=True,page_navigation='page_navigation_mode' in manifest)
    if manifest != expected:
        raise ValueError('正文复用计划、原文或来源映射变化')


def check_evidence(packet, directory):
    """共享周评复用时回到实际阅读回执；引用文件验签不能代替原回执核验。"""
    from bounded_read import source_page, receipt_value, checked
    from model_config import response_text
    root=Path(directory);manifest=json.loads((root/'plan.json').read_text())
    checked_plan(packet,manifest)
    status=json.loads((root/'status.json').read_text())
    if (status.get('state')!='reading_complete_pending_reconciliation'
            or status.get('received_pages')!=list(range(len(manifest['pages']))) or status.get('blocked')
            or status.get('packet_sha256')!=digest(packet)
            or status.get('procedural_sha256')!=digest(manifest['procedural_events'])):
        raise ValueError('正文复用的原阅读状态不完整或程序证明变化')
    events={e['event']:e for e in packet['events']}
    for index,page_index in enumerate(manifest['pages']):
        page=source_page(page_index,events)
        proof=json.loads((root/(str(index)+'.checked.json')).read_text())
        _,record=receipt_value(Path(proof.get('receipt_path',root/(str(index)+'.receipt.json'))))
        if (record.get('status')!='received' or proof.get('page_sha256')!=digest(page)
                or proof.get('receipt_sha256')!=digest(record)
                or proof.get('request_sha256')!=record.get('request_sha256')):
            raise ValueError('正文复用的原阅读回执或检查点变化')
        value=checked(page,json.loads(response_text(record['response'])),page_navigation='page_navigation_mode' in manifest)
        if value!=proof['value']:raise ValueError('正文复用的原阅读结论变化')
    return manifest


def procedural_notes(packet, manifest, notes):
    """正文阅读可复用，摘要判断、成功状态和人的行为不跨事件继承。"""
    checked_plan(packet, manifest)
    by_event = {}
    for note in notes:
        by_event.setdefault(note['event_id'], []).append(note)
    events = {e['event']: e for e in packet['events']}
    result = []
    for record in manifest['procedural_events']:
        event = events[record['event_id']]
        proof = dict(strategy=STRATEGY, **record)
        limits = ['程序记录只核正文与来源；不代表已核实行为、归属或执行结果。']
        if event.get('truncated'):
            limits.append('本次来源已标记截断，正文相同不消除缺失。')
        if record['method'] == 'empty_saved_text':
            result.append(dict(event_id=event['event'], start=0, end=0,
                summary='已保存正文为0字符；不等于AI未回复、工具未执行或来源完整。',
                limitations=limits, open_questions=[], procedural=proof))
            continue
        canonical = record['canonical_event_id']
        spans = sorted(by_event.get(canonical, []), key=lambda n: n['start'])
        stop = 0
        for span in spans:
            if span['start'] != stop or not stop < span['end'] <= len(event['text']):
                raise ValueError('复用正文的原阅读片段存在缺口、重叠或越界')
            stop = span['end']
            result.append(dict(event_id=event['event'], start=span['start'], end=span['end'],
                summary='本次正文与事件'+canonical+'逐字相同，正文已在该事件阅读；'
                    '每次出现单独保留，本次时点、角色、归属及结果仍须结合上下文判断，不能继承其他事件结论。',
                limitations=limits, open_questions=[], procedural=proof))
        if not spans or stop != len(event['text']):
            raise ValueError('复用正文尚未完整阅读，不能生成程序覆盖记录')
    return result
