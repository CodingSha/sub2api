"""从已有审计缓存中性展开。不按词表过滤，不折叠同文真实请求，不访问生产。"""

from collections import Counter, defaultdict
import json
from pathlib import Path

from .common import require, digest, encoded, write_rows, moment

TRUNCATION_VERSION = 'audit-truncation-20261109.1'


def side_truncation(wrapped, row, side, single):
    """单次标记优先；聚合历史仅能证明曾截断，不能证明每次都截断。"""
    if isinstance(wrapped.get('truncated'), bool):
        return wrapped['truncated']
    historical = row.get(side + '_truncated')
    if historical is False:
        return False
    if single and isinstance(historical, bool):
        return historical
    return None


def parse_body(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def entries(value):
    parsed = parse_body(value)
    if isinstance(parsed, list) and parsed and all(isinstance(x, dict) and "role" in x and "request_id" not in x and "created_at" not in x for x in parsed):
        return [parsed]
    return parsed if isinstance(parsed, list) else [parsed] if parsed is not None else []


def text_of(value):
    # 结构化content完整序列化，保留角色和非文本标记；不能用前缀推测本人。
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if value is not None else ""


def envelope(value):
    # 缓存会话轮有content字段；普通provider请求对象则保留整个对象。
    return value if isinstance(value, dict) and "content" in value and "role" not in value else {"content": value}


def import_audit(cache_dirs, output, users, start, end):
    require(users and len(users) == len(set(users)), "用户清单为空或重复")
    require(moment(start) < moment(end), "时间窗必须递增")
    output = Path(output)
    require(not output.exists(), "中性展开输出已存在，禁止覆盖")
    output.mkdir(parents=True, mode=0o700)
    page_hashes, stats, all_issues = {}, {}, []
    normalizer_sha256 = digest(Path(__file__).read_bytes())
    for user in users:
        require(user.replace("_", "").replace("-", "").isalnum(), "用户名不规范")
        sessions, seen_rows, seen_turns = defaultdict(list), set(), set()
        request_variants = defaultdict(list)
        counters = Counter()
        require(any((Path(d) / (user + "_at_apsat.com")).is_dir() for d in cache_dirs), "所有缓存源均缺少账号：" + user)
        for directory in cache_dirs:
            folder = Path(directory) / (user + "_at_apsat.com")
            if not folder.is_dir():
                counters["absent_cache_directories"] += 1
                continue
            for path in sorted(folder.glob("page-*.json")):
                raw = path.read_bytes()
                sha = digest(raw)
                page_hashes[str(path.resolve())] = sha
                data = json.loads(raw)
                items = data["data"]["items"]
                require(isinstance(items, list), "缓存分页items不是列表")
                for row_index, row in enumerate(items):
                    require(row.get("user_email") == user + "@apsat.com", "审计账号与缓存目录不一致")
                    # 相同行ID的不同快照不直接覆盖，逐轮逐内容保留差异；只删除完全相同的缓存行。
                    row_signature = digest(encoded({k: row.get(k) for k in ("id", "session_id", "request_id", "created_at", "request_body", "response_body", "request_truncated", "response_truncated")}))
                    if row_signature in seen_rows:
                        counters["identical_cache_rows_deduplicated"] += 1
                        continue
                    seen_rows.add(row_signature)
                    request_body_sha = digest(text_of(row.get("request_body")).encode())
                    response_body_sha = digest(text_of(row.get("response_body")).encode())
                    request_entries = entries(row.get("request_body"))
                    response_entries = entries(row.get("response_body"))
                    response_map = defaultdict(list)
                    for response_index, response in enumerate(response_entries):
                        wrapped = envelope(response)
                        rid = wrapped.get("request_id")
                        if rid:
                            response_map[rid].append((response_index, wrapped))
                    request_counts = Counter(envelope(r).get("request_id") for r in request_entries)
                    for request_index, request in enumerate(request_entries):
                        wrapped = envelope(request)
                        at = wrapped.get("created_at") or (row.get("created_at") if len(request_entries) == 1 else None)
                        provenance = {"cache_page": str(path.resolve()), "cache_page_sha256": sha,
                                      "row_index": row_index, "audit_id": row.get("id"), "request_index": request_index,
                                      "request_body_sha256": request_body_sha, "response_body_sha256": response_body_sha}
                        if not at:
                            all_issues.append({"user_id": user, "issue": "missing_turn_timestamp", "provenance": provenance,
                                               "request": request, "responses": response_entries})
                            counters["unresolved_timestamp"] += 1
                            continue
                        try:
                            in_window = moment(start) <= moment(at) < moment(end)
                        except ValueError:
                            all_issues.append({"user_id": user, "issue": "invalid_turn_timestamp", "at": at,
                                               "provenance": provenance, "request": request})
                            counters["unresolved_timestamp"] += 1
                            continue
                        if not in_window:
                            counters["out_of_window_entries"] += 1
                            continue
                        rid = wrapped.get("request_id") or (row.get("request_id") if len(request_entries) == 1 else None)
                        candidates = response_map.get(rid, []) if rid else []
                        ambiguous = len(candidates) != 1 or request_counts.get(rid, 0) > 1
                        response_index, reply = candidates[0] if len(candidates) == 1 else (None, {})
                        if not candidates and len(request_entries) == len(response_entries) == 1:
                            # 非聚合单行的请求/响应天然同属该行；与数组按顺序猜测不同。
                            response_index, reply = 0, envelope(response_entries[0])
                            ambiguous = False
                        response = text_of(reply.get("content")) if not ambiguous else text_of([r for _, r in candidates] if candidates else response_entries)
                        response_at = reply.get("created_at") if not ambiguous else None
                        if response_at:
                            try:
                                moment(response_at)
                            except ValueError:
                                response_at = None
                        prompt = text_of(wrapped.get("content"))
                        sid = row.get("session_id") or "audit-row-" + str(row.get("id"))
                        request_cut = side_truncation(wrapped, row, 'request', len(request_entries) == 1)
                        response_cut = side_truncation(reply, row, 'response', len(response_entries) == 1) if not ambiguous else None
                        scope_unknown = request_cut is None or response_cut is None
                        turn = {"at": at, "response_at": response_at, "prompt": prompt, "response": response,
                                "truncation_version": TRUNCATION_VERSION,
                                "request_truncated": request_cut, "response_truncated": response_cut,
                                "session_request_truncated": bool(row.get('request_truncated')),
                                "session_response_truncated": bool(row.get('response_truncated')),
                                "truncation_scope_unknown": bool(scope_unknown),
                                "truncated": bool(request_cut or response_cut or scope_unknown),
                                "response_ambiguous": ambiguous, "response_timestamp_unknown": not bool(response_at),
                                "audit_id": row.get("id"), "request_id": rid, "provenance": dict(provenance, response_index=response_index,
                                pairing="request_id" if candidates and not ambiguous else "single_row" if not ambiguous else "unresolved"),
                                "request_model": wrapped.get("model") or row.get("model"), "status_code": wrapped.get("status_code") or row.get("status_code")}
                        # 只去除同一审计请求在多份缓存中完全相同的轮次；不同request_id/时点的同文请求都保留。
                        signature = digest(encoded({"audit_id": row.get("id"), "request_id": rid, "at": at,
                                                   "request_index": request_index if not rid else None, "prompt": prompt, "response": response,
                                                   "response_at": response_at, "truncated": turn["truncated"],
                                                   "request_truncated": request_cut, "response_truncated": response_cut}))
                        if signature in seen_turns:
                            counters["identical_snapshot_turns_deduplicated"] += 1
                            continue
                        seen_turns.add(signature)
                        identity = (row.get("id"), rid, at, request_index if not rid else None)
                        if request_variants[identity]:
                            for variant in request_variants[identity]:
                                variant["source_snapshot_conflict"] = True
                            turn["source_snapshot_conflict"] = True
                            all_issues.append({"user_id": user, "issue": "conflicting_request_snapshots", "provenance": provenance,
                                               "request_id": rid, "at": at})
                            counters["conflicting_snapshot_variants"] += 1
                        request_variants[identity].append(turn)
                        sessions[sid].append(turn)
                        counters["turns_preserved"] += 1
                        counters["ambiguous_responses"] += ambiguous
                        counters["response_timestamps_unknown"] += not bool(response_at)
        for trace in sessions.values():
            trace.sort(key=lambda t: (moment(t["at"]), t["audit_id"] or 0, t["provenance"]["request_index"]))
        write_rows(output / (user + "_at_apsat.com.jsonl"), [{"session_id": sid, "session_id_status": 'unavailable' if sid.startswith('audit-row-') else 'observed', "user_email": user + "@apsat.com", "trace": trace}
                   for sid, trace in sessions.items()])
        counters["source_sessions"] = len(sessions)
        stats[user] = dict(counters)
    write_rows(output / "unresolved-ingest.jsonl", all_issues)
    manifest = {"input_kind": "neutral_cached_audit_export", "start": start, "end": end,
                "truncation_version": TRUNCATION_VERSION,
                "no_semantic_filters": True, "same_text_requests_preserved": True, "normalizer_sha256": normalizer_sha256,
                "cache_pages_in_package": False, "cache_page_hashes": page_hashes,
                "per_user": stats, "unresolved_ingest_count": len(all_issues),
                "limits": "只覆盖已缓存审计内容；原始截断、非文本载荷和配对/时间未知不补造；页哈希供本机回查，整页未装入交付包。"}
    (output / "ingest-manifest.json").write_bytes(encoded(manifest))
    return {"users": len(users), "turns_preserved": sum(s.get("turns_preserved", 0) for s in stats.values()),
            "unresolved_ingest_count": len(all_issues), "pages_hashed": len(page_hashes)}
