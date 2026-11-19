"""冻结审计协议帧，保留完整正文及来源位置；不将 user role 自动认作本人行为。"""
import hashlib
import json
import re
from pathlib import Path


def read_signed(root, ref):
    path = (root / ref['path']).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('来源路径越界')
    with path.open('rb') as stream:
        signature=hashlib.sha256()
        for chunk in iter(lambda:stream.read(1024*1024),b''):signature.update(chunk)
        if signature.hexdigest() != ref['sha256']:
            raise ValueError('来源签名变化')
        stream.seek(0)
        return json.load(stream)


def iter_frames(root, snapshot, *, include_at=None, user_id=None, include_group=None, metadata_only=False, cache_root=None):
    """逐帧释放来源行；元数据扫描不保留正文或计算无用的正文签名。"""
    if cache_root is not None:
        from source_frame_cache import FrameCache
        cache=FrameCache(cache_root)
        for ref in snapshot['rows']:
            yield from cache.frames(Path(root),ref,include_at=include_at,user_id=user_id,
                include_group=include_group,metadata_only=metadata_only)
        return
    for ref in snapshot['rows']:
        # 子生成器结束即释放整行，避免读取下一大行时仍持有上一行的解析对象。
        yield from _row_frames(read_signed(Path(root),ref),ref,include_at,user_id,include_group,metadata_only)


def _row_frames(row,ref,include_at,user_id,include_group,metadata_only):
    if user_id is not None and row['user_id']!=user_id:return
    group=row.get('session_id') or 'audit:'+str(row['id'])
    if include_group is not None and not include_group(group):return
    for field in ('request', 'response'):
        yield from _field_frames(row,ref,field,include_at,metadata_only)


def _field_frames(row,ref,field,include_at,metadata_only):
    raw = row[field + '_body']
    # 只有顶层数组可能是聚合包装；普通大请求对象由协议提取器解析一次。
    # 不用 lstrip，避免为寻找首字符复制整条长正文。
    parsed = None
    if isinstance(raw,str) and re.match(r'[ \t\r\n]*\[',raw):
        try:parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):pass
    is_rollup = (isinstance(parsed, list) and all(isinstance(x, dict) and
        {'content', 'request_id', 'created_at'} <= set(x) for x in parsed))
    entries = parsed if is_rollup else [dict(content=raw, request_id=row['request_id'],
        created_at=row['created_at'], truncated=row[field + '_truncated'])]
    for i, entry in enumerate(entries):
        key = f"audit:{row['id']}:{field}:{i}"
        # 在展开协议和正文副本之前选定采集日；完整周调用仍沿用原路径。
        if include_at is not None and not include_at(entry['created_at']):continue
        content = entry['content']
        if not isinstance(content, str):
            raise ValueError('协议帧正文不是字符串，不能猜测改写')
        uncertain = not is_rollup and ((row.get('request_count') or 1) > 1 or isinstance(parsed, list))
        # 聚合标记不能扩散到每一轮；旧记录不能定位时另列未知。
        truncated = bool(entry.get('truncated', False)) if is_rollup else bool(row[field + '_truncated'])
        issues=[]
        if is_rollup and row[field + '_truncated'] and 'truncated' not in entry:
            issues.append(dict(frame=key, code='aggregate_truncation_location_unknown'))
        if uncertain or truncated:
            issues.append(dict(frame=key, code='source_truncated' if truncated else 'legacy_rollup_time_unknown'))
        frame=dict(frame=key, user_id=row['user_id'], session_id=row['session_id'],
            request_id=entry['request_id'], field=field, at=entry['created_at'],
            timestamp_unknown=uncertain, truncated=truncated,
            source=dict(path=ref['path'], sha256=ref['sha256'], field=field, index=i),
            actor_class='transport_request' if field == 'request' else 'transport_response')
        if 'api_key_id' in row:
            frame['api_key_id']=row['api_key_id']
        if not metadata_only:
            frame.update(text=content,text_sha256=hashlib.sha256(content.encode()).hexdigest())
        yield frame,issues


def frames(root, snapshot, *, include_at=None, user_id=None):
    """数组格式只接受已知迁移154包装，旧单条明确保留时间不确定。"""
    result,issues=[],[]
    for frame,problems in iter_frames(root,snapshot,include_at=include_at,user_id=user_id):
        result.append(frame);issues.extend(problems)
    return dict(version=1, snapshot_at=snapshot['snapshot_at'], frames=result, issues=issues,
                human_attribution='尚未完成语义归属，禁止直接用于本人评分',
                task_identification='尚未执行；会话、请求或协议帧均不等同独立需求任务')
