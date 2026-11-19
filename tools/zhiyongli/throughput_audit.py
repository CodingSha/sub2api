"""只读核对模型账簿的阶段成本与重复成功；不发送、不恢复、不改队列。"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sqlite3


def finite(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def latency(values):
    values = sorted(v for v in values if finite(v))
    if not values:
        return dict(measured=0, sum_seconds=None, p50_seconds=None, p95_seconds=None)
    return dict(measured=len(values), sum_seconds=sum(values),
                p50_seconds=values[math.ceil(len(values) * .5) - 1],
                p95_seconds=values[math.ceil(len(values) * .95) - 1])


def receipt_evidence(row, state_root=None):
    """只输出摘要、状态与签名，不复制员工正文、模型输出或错误原文。"""
    path = Path(row['receipt'])
    if state_root is not None:
        if not path.is_relative_to('/state'):
            return dict(available=False, reason='outside_state_mapping')
        path = Path(state_root) / path.relative_to('/state')
    if not path.is_file():
        return dict(available=False, reason='receipt_not_available')
    raw = path.read_bytes()
    try:
        record = json.loads(raw)
        response = record.get('response')
        response = response if isinstance(response, dict) else {}
        return dict(available=True, file_sha256=hashlib.sha256(raw).hexdigest(),
                    request_sha256=record.get('request_sha256'),
                    request_matches_ledger=record.get('request_sha256') == row['request_sha'],
                    status=record.get('status'), response_status=response.get('status'),
                    response_id=record.get('response_id'), request_kind=record.get('request_kind'))
    except (ValueError, AttributeError):
        return dict(available=False, reason='invalid_receipt_json',
                    file_sha256=hashlib.sha256(raw).hexdigest())


def analyze(database, since, until, budget_key, state_root=None):
    """对一个一致性SQLite快照核账；缺遥测不以账簿落账时间替代API时延。"""
    if not finite(since) or not finite(until) or until <= since:
        raise ValueError('审计窗口必须为有效的半开时间区间')
    database = Path(database).resolve()
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        has_measurements = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='measurements'").fetchone() is not None
        attempts = [dict(r) for r in db.execute(
            'SELECT * FROM attempts WHERE budget_key=? AND started>=? AND started<? ORDER BY started,id',
            (budget_key, since, until))]
        measurements = {r['attempt_id']: dict(r) for r in db.execute(
            '''SELECT m.* FROM measurements m JOIN attempts a ON a.id=m.attempt_id
               WHERE a.budget_key=? AND a.started>=? AND a.started<?''',
            (budget_key, since, until))} if has_measurements else {}
        budget_used = db.execute(
            "SELECT count(*) FROM attempts WHERE budget_key=? AND status!='cancelled'", (budget_key,)).fetchone()[0]
    stages = defaultdict(list)
    successes = defaultdict(list)
    for row in attempts:
        measured = measurements.get(row['id'], {})
        kind = measured.get('request_kind', 'unmeasured')
        terminal = measured.get('terminal_state', row['status'])
        stages[kind].append((row, measured, terminal))
        if terminal == 'completed' and row['status'] == 'received':
            successes[row['request_sha']].append(row)
    results = []
    for kind, rows in sorted(stages.items()):
        counts = Counter(r[2] for r in rows)
        ended = sum(v for k, v in counts.items() if k not in {'active', 'cancelled'})
        failed = sum(counts[k] for k in ('failed', 'incomplete', 'unknown', 'rejected'))
        tokens = [r[1]['total_tokens'] for r in rows if finite(r[1].get('total_tokens'))]
        results.append(dict(request_kind=kind, requests=len(rows), states=dict(counts),
                            unsuccessful_or_unknown=failed,
                            unsuccessful_or_unknown_rate=failed / ended if ended else None,
                            latency=latency(r[1].get('client_seconds') for r in rows),
                            measured_token_requests=len(tokens),
                            total_tokens=sum(tokens) if tokens else None))
    duplicates = []
    for signature, rows in sorted(successes.items()):
        if len(rows) < 2:
            continue
        duplicates.append(dict(request_sha256=signature, requests=len(rows),
            distinct_receipts=len({r['receipt'] for r in rows}),
            started_span_seconds=rows[-1]['started'] - rows[0]['started'],
            attempts=[dict(attempt_id=r['id'], receipt=r['receipt'], started=r['started'],
                           finished=r['finished'], evidence=receipt_evidence(r, state_root)) for r in rows]))
    return dict(version=1, since=since, until=until, budget_key=budget_key,
        budget_used_at_snapshot=budget_used, attempts_in_window=len(attempts),
        measurement_table_available=has_measurements, measured_requests=len(measurements),
        stages=results, completed_duplicate_groups=duplicates,
        unknown_attempt_ids=[r['id'] for r in attempts if r['status'] == 'unknown'],
        limitations=['客户端时延含传输及流处理，不能拆成纯模型等待。',
                    '并发请求累计秒数不能当作墙钟总耗时；落账时间不替代客户端时延。',
                    '窗口外重复不在本报告内；相同哈希不自动授权跨人员或跨合同复用。',
                    '未发送的装配、分页、校验、排队、配额等待、维护暂停和缓存命中须有独立观测。'])


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('审计时间必须包含时区')
    return result.timestamp()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True, help='已取得的一致性账簿快照')
    parser.add_argument('--since', required=True)
    parser.add_argument('--until', required=True)
    parser.add_argument('--budget-key', required=True)
    parser.add_argument('--state-root', type=Path, help='离线完整状态根，映射原/state回执')
    args = parser.parse_args()
    print(json.dumps(analyze(args.database, timestamp(args.since), timestamp(args.until),
                            args.budget_key, args.state_root), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
