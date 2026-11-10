"""仅装配明确提交的阅读状态，不推断阅读完成或生成语义反馈。"""

from .common import require, valid_span


def assemble_coverage(packet, record):
    """部分事件始终留在未全文阅读集合；补读须提交新的真实记录。"""
    events = {e['event_id']: e for e in packet['events']}
    read = set(record.get('events_read', [])) & set(events)
    partial = []
    for entry in record.get('events_partial', []):
        eid = entry['event_id']
        if eid not in events:
            continue
        require(entry.get('reason'), '部分阅读须保留具体限制')
        spans = entry.get('read_spans', [])
        require(all(valid_span(events[eid]['text'], a, b) for a, b in spans), '部分阅读范围越界')
        partial.append({'event_id': eid, 'read_spans': spans, 'reason': entry['reason']})
        read.discard(eid)
    unread = set(events) - read
    method = record.get('method', 'partial')
    require(method in {'semantic', 'partial', 'structural_only'}, '未知阅读方式')
    if method == 'semantic' and (partial or unread):
        method = 'partial'
    return {'method': method, 'accounted_event_ids': sorted(events), 'read_event_ids': sorted(read),
            'unread_event_ids': sorted(unread), 'partial_events': partial, 'excluded_events': [],
            'opportunity_audit': record.get('opportunity_audit') or record.get('note') or '按原阅读记录保留，未完成项继续待核',
            'read_basis': record.get('read_basis', 'submitted_record')}
