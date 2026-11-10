"""冻结审计协议帧，保留完整正文及来源位置；不将 user role 自动认作本人行为。"""
import hashlib
import json
from pathlib import Path


def read_signed(root, ref):
    path = (root / ref['path']).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('来源路径越界')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('来源签名变化')
    return json.loads(raw)


def frames(root, snapshot):
    """数组格式只接受已知迁移154包装，旧单条明确保留时间不确定。"""
    result, issues = [], []
    for ref in snapshot['rows']:
        row = read_signed(Path(root), ref)
        for field in ('request', 'response'):
            raw = row[field + '_body']
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            is_rollup = (isinstance(parsed, list) and all(isinstance(x, dict) and
                {'content', 'request_id', 'created_at'} <= set(x) for x in parsed))
            entries = parsed if is_rollup else [dict(content=raw, request_id=row['request_id'],
                created_at=row['created_at'], truncated=row[field + '_truncated'])]
            for i, entry in enumerate(entries):
                key = f"audit:{row['id']}:{field}:{i}"
                content = entry['content']
                if not isinstance(content, str):
                    raise ValueError('协议帧正文不是字符串，不能猜测改写')
                uncertain = not is_rollup and ((row.get('request_count') or 1) > 1 or isinstance(parsed, list))
                # 聚合标记不能扩散到每一轮；旧记录不能定位时另列未知。
                truncated = bool(entry.get('truncated', False)) if is_rollup else bool(row[field + '_truncated'])
                if is_rollup and row[field + '_truncated'] and 'truncated' not in entry:
                    issues.append(dict(frame=key, code='aggregate_truncation_location_unknown'))
                if uncertain or truncated:
                    issues.append(dict(frame=key, code='source_truncated' if truncated else 'legacy_rollup_time_unknown'))
                result.append(dict(frame=key, user_id=row['user_id'], session_id=row['session_id'],
                    request_id=entry['request_id'], field=field, at=entry['created_at'],
                    timestamp_unknown=uncertain, truncated=truncated, text=content,
                    text_sha256=hashlib.sha256(content.encode()).hexdigest(),
                    source=dict(path=ref['path'], sha256=ref['sha256'], field=field, index=i),
                    actor_class='transport_request' if field == 'request' else 'transport_response'))
    return dict(version=1, snapshot_at=snapshot['snapshot_at'], frames=result, issues=issues,
                human_attribution='尚未完成语义归属，禁止直接用于本人评分',
                task_identification='尚未执行；会话、请求或协议帧均不等同独立需求任务')
