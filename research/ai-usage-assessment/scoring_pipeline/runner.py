"""本地 PMJE 评审流程：冻结、分包、语义提交校验、复核、报告。"""

import argparse
from collections import Counter, defaultdict
from datetime import timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile

from .common import (ContractError, require, digest, encoded, rows, write_rows,
                     moment, valid_span, code_hashes, file_digest, copy_snapshot, review_context, injection_hints)
from .policy import VERSION, CONTRACT, ITEMS
from .review import evaluate
from .source_hints import validate_current_request_disposition


SHANGHAI = timezone(timedelta(hours=8))


def periods_for(start, end, mode):
    first, last = moment(start).astimezone(SHANGHAI), moment(end).astimezone(SHANGHAI)
    require(first < last and mode in {"weekly", "window", "both"}, "时间窗或期间模式错误")
    result = []
    if mode in {"weekly", "both"}:
        current = first
        while current < last:
            midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
            following = midnight + timedelta(days=7 - current.weekday())
            finish = min(following, last)
            week_start = midnight - timedelta(days=current.weekday())
            complete = current == week_start and finish == following
            result.append({"period_id": "week-" + current.date().isoformat(), "kind": "weekly",
                           "start": current.isoformat(), "end": finish.isoformat(),
                           "calendar_start": week_start.isoformat(), "calendar_end": following.isoformat(),
                           "publish_on": following.date().isoformat(), "complete_week": complete,
                           "publication_status": "完整周" if complete else "周内片段，待补齐整周"})
            current = finish
    if mode in {"window", "both"}:
        current = first
        while current < last:
            following = current.replace(year=current.year + (current.month == 12), month=current.month % 12 + 1,
                                        day=1, hour=0, minute=0, second=0, microsecond=0)
            finish = min(following, last)
            complete = current.day == 1 and current.hour == current.minute == current.second == current.microsecond == 0 and finish == following
            kind = "monthly" if complete else "window"
            result.append({"period_id": ("month-" if complete else "window-") + current.date().isoformat(), "kind": kind,
                           "start": current.isoformat(), "end": finish.isoformat(),
                           "publish_on": following.date().isoformat() if complete else None})
            current = finish
    return result


def load_run(run, load_events=True):
    run = Path(run)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["policy_version"] == VERSION, "版本不一致，须使用该批冻结入口或新建批次")
    require(manifest["code_hashes"] == code_hashes(), "代码与冻结版本不一致，禁止热更新")
    for name, sha in manifest["frozen_files"].items():
        require(file_digest(run / name) == sha, "冻结文件变化：" + name)
    events = rows(run / "events.jsonl") if load_events else []
    require(len({e["event_id"] for e in events}) == len(events), "冻结事件ID重复")
    return manifest, {e["event_id"]: e for e in events}


def source_events(tasks_dir, users, start, end):
    """逐来源会话展开，避免全量正文常驻内存。"""
    session_keys = set()
    for user in users:
        path = Path(tasks_dir) / (user + "_at_apsat.com.jsonl")
        sha = file_digest(path)
        with path.open(encoding="utf-8") as source:
            for line_no, line in enumerate(source, 1):
                if not line.strip():
                    continue
                record = json.loads(line)
                require(record.get("user_email") in {user, user + "@apsat.com"}, "源材料账号不一致")
                require(not record.get("n_repeats_folded", 0), "源材料已按同文折叠真实请求；须用中性审计展开源")
                sid = record["session_id"]
                require(isinstance(sid, str) and sid and (user, sid) not in session_keys, "会话标识缺失或重复")
                session_keys.add((user, sid))
                for index, turn in enumerate(record["trace"]):
                    if moment(turn["at"]) >= moment(end):
                        continue
                    for field in ("prompt", "response"):
                        at = (turn.get("response_at") or turn["at"]) if field == "response" else turn["at"]
                        if moment(at) >= moment(end):
                            continue
                        text = turn.get(field, "")
                        require(isinstance(text, str), "原文必须为字符串")
                        side = 'request' if field == 'prompt' else 'response'
                        cut = turn.get(side + '_truncated')
                        if 'truncation_version' not in turn:
                            # 老来源只有合并标记时，不能反推哪一侧完整。
                            cut = None if turn.get('truncated') else False
                        status = 'truncated' if cut is True else 'not_truncated' if cut is False else 'unknown'
                        event_id = digest(f"{user}:{sha}:{line_no}:{sid}:{index}:{field}".encode())
                        yield {"event_id": event_id, "user_id": user, "session_id": sid, "trace_index": index,
                               "session_id_status": record.get('session_id_status', 'unavailable' if sid.startswith('audit-row-') else 'observed'),
                               "source_line": line_no, "source_path": "sources/" + path.name, "source_sha256": sha,
                               "field": field, "at": at, "in_window": moment(at) >= moment(start), "text": text,
                               "truncated": cut is not False,
                               "truncation_status": status,
                               "session_request_truncated": bool(turn.get('session_request_truncated')),
                               "session_response_truncated": bool(turn.get('session_response_truncated')),
                               "response_ambiguous": bool(turn.get("response_ambiguous", False)),
                               "response_timestamp_unknown": bool(turn.get("response_timestamp_unknown", False)),
                               "source_snapshot_conflict": bool(turn.get("source_snapshot_conflict", False)),
                               "provenance": turn.get("provenance", {}), "audit_id": turn.get("audit_id"), "request_id": turn.get("request_id")}


def prepare(tasks_dir, output, users, start, end, period_mode="both"):
    from .handoff import prompt_for
    from .goal_policy import CONTRACT as GOAL_CONTRACT, INSTRUCTIONS as GOAL_INSTRUCTIONS
    periods = periods_for(start, end, period_mode)
    require(users and len(users) == len(set(users)), "用户清单为空或重复")
    require(all(re.fullmatch(r"[a-zA-Z0-9_-]+", u) for u in users), "用户名格式错误")
    output = Path(output)
    require(not output.exists(), "输出已存在，禁止覆盖：" + str(output))
    output.parent.mkdir(parents=True, exist_ok=True)
    grouped, counts, source_hashes = defaultdict(list), Counter(), {}
    # 失败时只清理本次临时目录；成功后整体发布为新的冻结目录。
    with tempfile.TemporaryDirectory(prefix=output.name + "-prepare-", dir=str(output.parent)) as staging:
        stage = Path(staging)
        (stage / "sources").mkdir(mode=0o700)
        engine = stage / "engine/scoring_pipeline"
        engine.mkdir(parents=True, mode=0o700)
        for name in code_hashes():
            (engine / name).write_bytes((Path(__file__).parent / name).read_bytes())
        (stage / "engine/run_scoring.py").write_bytes((Path(__file__).parent.parent / "run_scoring.py").read_bytes())
        with (stage / "events.jsonl").open("wb") as body, (stage / "event-index.jsonl").open("w", encoding="utf-8") as index_file:
            for event in source_events(tasks_dir, users, start, end):
                raw = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
                pointer = {"event_id": event["event_id"], "user_id": event["user_id"], "session_id": event["session_id"],
                           "field": event["field"], "at": event["at"], "text_characters": len(event["text"]),
                           "byte_offset": body.tell(), "byte_length": len(raw), "sha256": digest(raw)}
                index_file.write(json.dumps(pointer, ensure_ascii=False) + "\n")
                body.write(raw)
                grouped[(event["user_id"], event["session_id"])].append(event["event_id"])
                counts[event["field"]] += 1
                source_hashes[event["source_path"]] = event["source_sha256"]
        require(counts, "没有可整理的材料")
        for user in users:
            source = Path(tasks_dir) / (user + "_at_apsat.com.jsonl")
            target = stage / "sources" / source.name
            copy_snapshot(source, target)
            expected_sha = source_hashes["sources/" + source.name] if "sources/" + source.name in source_hashes else file_digest(source)
            require(file_digest(target) == expected_sha, "准备期间源文件变化")
        ingest = {"input_kind": "provided_trace_unknown_upstream", "unresolved_ingest_count": 0}
        for name in ("ingest-manifest.json", "unresolved-ingest.jsonl"):
            path = Path(tasks_dir) / name
            if path.exists():
                shutil.copyfile(path, stage / "sources" / name)
                if name == "ingest-manifest.json":
                    ingest = json.loads(path.read_text(encoding="utf-8"))
        write_rows(stage / "intake-packets.jsonl", ({"user_id": u, "session_id": sid, "event_ids": ids,
                   "instruction": "来源会话索引不是父目标；用read或event-index读原文，逐prompt处置并完整分配事件。"} for (u, sid), ids in grouped.items()))
        (stage / "judge-contract.json").write_bytes(encoded(CONTRACT))
        (stage / "goal-contract.json").write_bytes(encoded(GOAL_CONTRACT))
        (stage / "DSH_PROMPT.md").write_text(prompt_for(VERSION) + '\n\n## 目标识别补充合同\n\n' + GOAL_INSTRUCTIONS + '\n', encoding="utf-8")
        (stage / "templates").mkdir()
        for name in ("plan", "review", "usage"):
            (stage / "templates" / (name + ".shape.json")).write_bytes(encoded(CONTRACT[name + "_shape"]))
        names = [str(p.relative_to(stage)) for p in stage.rglob('*') if p.is_file()]
        manifest = {"policy_version": VERSION, "users": users, "start": start, "end": end, "periods": periods,
                    "code_hashes": code_hashes(), "frozen_files": {name: file_digest(stage / name) for name in names},
                    "counts": dict(counts), "source_sessions": dict(Counter(u for u, _ in grouped)),
                    "input_provenance": ingest, "production_ready": False, "human_calibration": "not_established"}
        (stage / "manifest.json").write_bytes(encoded(manifest))
        require(not output.exists(), "准备期间输出目录已被占用，禁止覆盖")
        stage.rename(output)
    return manifest


def refreeze(source_run, output, period_mode="both"):
    """保留事件ID和来源，只为新版本建立独立批次，不迁移旧语义结论。"""
    from .handoff import prompt_for
    from .goal_policy import CONTRACT as GOAL_CONTRACT, INSTRUCTIONS as GOAL_INSTRUCTIONS
    source, output = Path(source_run), Path(output)
    require(not output.exists(), "新批次已存在，禁止覆盖")
    old = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=output.name + "-refreeze-", dir=str(output.parent)) as temp:
        stage = Path(temp)
        for name, sha in old["frozen_files"].items():
            if not (name.startswith("sources/") or name in {"events.jsonl", "event-index.jsonl", "intake-packets.jsonl"}):
                continue
            require(file_digest(source / name) == sha, "旧批次输入已变化：" + name)
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            copy_snapshot(source / name, dest)
            require(file_digest(dest) == sha, "新批次输入复制不一致：" + name)
        require(all((stage / n).exists() for n in ("events.jsonl", "event-index.jsonl", "intake-packets.jsonl")), "旧批次缺完整中性输入")
        engine = stage / "engine/scoring_pipeline"
        engine.mkdir(parents=True)
        for name in code_hashes():
            (engine / name).write_bytes((Path(__file__).parent / name).read_bytes())
        (stage / "engine/run_scoring.py").write_bytes((Path(__file__).parent.parent / "run_scoring.py").read_bytes())
        (stage / "judge-contract.json").write_bytes(encoded(CONTRACT))
        (stage / "goal-contract.json").write_bytes(encoded(GOAL_CONTRACT))
        (stage / "DSH_PROMPT.md").write_text(prompt_for(VERSION) + '\n\n## 目标识别补充合同\n\n' + GOAL_INSTRUCTIONS + '\n', encoding="utf-8")
        (stage / "templates").mkdir()
        for name in ("plan", "review", "usage"):
            (stage / "templates" / (name + ".shape.json")).write_bytes(encoded(CONTRACT[name + "_shape"]))
        names = [str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file()]
        manifest = {"policy_version": VERSION, "users": old["users"], "start": old["start"], "end": old["end"],
                    "periods": periods_for(old["start"], old["end"], period_mode), "code_hashes": code_hashes(),
                    "frozen_files": {n: file_digest(stage / n) for n in names}, "counts": old["counts"],
                    "source_sessions": old["source_sessions"], "input_provenance": old.get("input_provenance", {}),
                    "refrozen_from_manifest_sha256": file_digest(source / "manifest.json"),
                    "production_ready": False, "human_calibration": "not_established"}
        (stage / "manifest.json").write_bytes(encoded(manifest))
        require(not output.exists(), "新目录准备期间已被占用")
        stage.rename(output)
    return {"policy_version": VERSION, "counts": manifest["counts"], "periods": manifest["periods"], "input_unchanged": True}


def packets(run, plan_path):
    manifest, events = load_run(run)
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    require(plan.get("policy_version") == VERSION and bool(plan.get("planner_id")), "plan版本或初审身份缺失")
    context = review_context(plan.get("review_context"))
    materials = {}
    for material in plan["materials"]:
        eid = material["event_id"]
        require(eid in events and events[eid]["field"] == "prompt" and eid not in materials, "prompt处置未知或重复")
        require(material["classification"] in {"human", "system", "auxiliary", "duplicate", "unresolved"} and bool(material.get("reason")), "材料处置缺标签/理由")
        require("human_spans" in material, "材料缺human_spans（非本人须显式[]）：" + eid)
        spans = material["human_spans"]
        require(isinstance(spans, list), "human_spans须为列表")
        validate_current_request_disposition(events[eid]["text"], material)
        if material["classification"] == "human":
            require(bool(spans), "本人内容须定位片段")
            for span in spans:
                require(isinstance(span, list) and len(span) == 2 and valid_span(events[eid]["text"], *span), "本人片段越界")
                require(bool(events[eid]["text"][span[0]:span[1]].strip()), "本人片段空白")
            ordered = sorted(spans)
            require(all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])), "本人片段重叠")
            if injection_hints(events[eid]["text"]):
                check = material.get("origin_check", {})
                require(check.get("decision") in {"person", "mixed"} and bool(check.get("reason")), "命中平台来源候选须说明本人归属和片段边界，不能按词表自动排除：" + eid)
        else:
            require(not spans, "非本人材料不能携带本人片段")
        materials[eid] = material
    require(set(materials) == {k for k, e in events.items() if e["field"] == "prompt"}, "prompt清单未全量对账")
    for eid, material in materials.items():
        if material["classification"] == "duplicate":
            original = material.get("duplicate_of")
            require(original in materials and original != eid and materials[original]["classification"] not in {"duplicate", "unresolved"}, "重复原件不存在/循环/未定")
            require(events[original]["user_id"] == events[eid]["user_id"] and events[original]["text"] == events[eid]["text"], "重复折叠须为同账号逐字原文；相似不等于重复")
            same_request = any(events[eid].get(k) and events[eid].get(k) == events[original].get(k) for k in ("audit_id", "request_id"))
            require(same_request and bool(material.get("duplicate_reason")), "同文/时间间隔不能证明快照重复；须同审计请求及明确依据：" + eid)
    goals, assigned = {}, set()
    for goal in plan["goals"]:
        key = (goal["user_id"], goal["goal_id"])
        require(key not in goals and goal["user_id"] in manifest["users"], "目标重复或跨用户：" + repr(key))
        require(goal["boundary"] in {"confirmed", "unresolved"} and goal.get("title") and goal.get("boundary_reason"), "目标边界描述不完整")
        ids = goal["event_ids"]
        require(ids and len(ids) == len(set(ids)) and all(k in events and events[k]["user_id"] == goal["user_id"] for k in ids), "目标事件重复/未知/跨用户")
        goals[key] = goal
        assigned.update(ids)
    unassigned = {}
    for row in plan["unassigned_events"]:
        eid = row["event_id"]
        require(eid in events and eid not in assigned and eid not in unassigned and row.get("reason"), "未分配清单无效或与目标冲突")
        unassigned[eid] = row
    require(assigned | set(unassigned) == set(events), "全部prompt/response事件须有去向，不能遗漏反面反馈")
    human = {k for k, m in materials.items() if m["classification"] == "human"}
    require(human <= assigned, "本人材料未进入父目标账本")
    result = []
    for goal in goals.values():
        for period in manifest["periods"]:
            active = [events[k] for k in goal["event_ids"] if k in human and moment(period["start"]) <= moment(events[k]["at"]) < moment(period["end"])]
            if not active:
                continue
            visible = sorted([events[k] for k in goal["event_ids"] if moment(events[k]["at"]) < moment(period["end"])],
                             key=lambda e: (moment(e["at"]), e["source_line"], e["trace_index"], e["field"] != "prompt"))
            packet_id = digest(f'{goal["user_id"]}:{goal["goal_id"]}:{period["period_id"]}'.encode())
            result.append({"policy_version": VERSION, "packet_id": packet_id, "user_id": goal["user_id"],
                           "goal_id": goal["goal_id"], "title": goal["title"], "boundary": goal["boundary"],
                           "boundary_reason": goal["boundary_reason"], "period_id": period["period_id"],
                           "period_start": period["start"], "period_end": period["end"],
                           "active_human_event_ids": [e["event_id"] for e in active], "events": visible,
                           "materials": [materials[e["event_id"]] for e in visible if e["field"] == "prompt"]})
    destination = Path(run) / "packets"
    require(not destination.exists(), "评审包已存在，禁止热更新；计划修订须新建批次")
    destination.mkdir()
    (destination / "plan.json").write_bytes(encoded(plan))
    write_rows(destination / "packets.jsonl", result)
    data = {"policy_version": VERSION, "source_manifest_sha256": digest((Path(run) / "manifest.json").read_bytes()),
            "packet_count": len(result), "goal_count": len(goals), "assigned_events": len(assigned), "unassigned_events": len(unassigned),
            "materials": dict(Counter(m["classification"] for m in materials.values())),
            "review_context": context,
            "origin_review_event_ids": [k for k, m in materials.items() if m["classification"] == "human" and injection_hints(events[k]["text"])],
            "unresolved_material_users": sorted({events[k]["user_id"] for k, m in materials.items() if m["classification"] == "unresolved"}),
            "unresolved_material_ids": [k for k, m in materials.items() if m["classification"] == "unresolved"],
            "unresolved_goal_ids": [list(k) for k, g in goals.items() if g["boundary"] == "unresolved"],
            "inactive_goals": [list(k) for k, g in goals.items() if not any(e in human and events[e]["in_window"] for e in g["event_ids"])],
            "sha256": {name: digest((destination / name).read_bytes()) for name in ("plan.json", "packets.jsonl")}}
    (destination / "manifest.json").write_bytes(encoded(data))
    return {"packet_count": len(result), "goals": len(goals), "materials": data["materials"]}


def load_packets(run):
    manifest, events = load_run(run, load_events=False)
    source = Path(run) / "packets"
    packet_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    require(packet_manifest["policy_version"] == VERSION and packet_manifest["source_manifest_sha256"] == digest((Path(run) / "manifest.json").read_bytes()), "分包批次不一致")
    for name, sha in packet_manifest["sha256"].items():
        require(digest((source / name).read_bytes()) == sha, "计划或评审包已变化")
    return manifest, packet_manifest, rows(source / "packets.jsonl")


def read_events(run, event_ids=None, user=None, session=None, offset=0, limit=5, start=0, max_characters=12000):
    """有界读取并核验选中正文；整批核验由verify承担，避免每次读取数GB。"""
    run = Path(run)
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["policy_version"] == VERSION and manifest["code_hashes"] == code_hashes(), "读取入口与冻结版本不同")
    require(file_digest(run / "event-index.jsonl") == manifest["frozen_files"]["event-index.jsonl"], "事件索引已变化")
    require((event_ids or user or session) and offset >= 0 and limit > 0 and start >= 0 and max_characters > 0, "须指定读取范围及有效分页参数")
    result, matched = [], 0
    with (run / "event-index.jsonl").open(encoding="utf-8") as index, (run / "events.jsonl").open("rb") as body:
        for line in index:
            pointer = json.loads(line)
            if event_ids and pointer["event_id"] not in event_ids:
                continue
            if user and pointer["user_id"] != user or session and pointer["session_id"] != session:
                continue
            matched += 1
            if matched <= offset:
                continue
            body.seek(pointer["byte_offset"])
            raw = body.read(pointer["byte_length"])
            require(digest(raw) == pointer["sha256"], "选中事件正文已变化")
            event = json.loads(raw)
            text = event.pop("text")
            require(start <= len(text), "字符起点超过原文")
            end = min(start + max_characters, len(text))
            result.append(dict(event, text_excerpt=text[start:end], excerpt_start=start, excerpt_end=end,
                               total_characters=len(text), has_more=end < len(text)))
            if len(result) >= limit:
                break
    return {"events": result, "verification": "已核验冻结索引及选中事件；整批核验另用verify", "offset": offset, "returned": len(result)}


def inspect_reviews(run, review_path):
    manifest, packet_manifest, packet_rows = load_packets(run)
    packets_by_id = {p["packet_id"]: p for p in packet_rows}
    reviews = {}
    for review in rows(review_path):
        key = review["packet_id"]
        require(key in packets_by_id and key not in reviews, "重复评审或未知评审包")
        reviews[key] = review
    results, errors = [], []
    fact_usage, span_usage = {}, defaultdict(list)
    for key, packet in packets_by_id.items():
        if key not in reviews:
            errors.append({"packet_id": key, "issue": "missing_review"})
            continue
        try:
            result = evaluate(packet, reviews[key], isolate_cell_errors=True)
            errors.extend({"packet_id": key, "issue": "invalid_cell", **error} for error in result["cell_errors"])
            if not result['semantic_complete']:
                errors.append({"packet_id": key, "issue": "incomplete_read", "reason": "已读证据可保留草稿分，未读相关材料仍阻断发布"})
            fact_keys = [(packet["user_id"], packet["period_id"], o["scope_event_id"], o["fact"]) for o in result["observation_facts"]]
            require(all(k not in fact_usage for k in fact_keys), "同期间跨目标重复计入同一本人行为主张")
            for fact_key in fact_keys:
                fact_usage[fact_key] = key
            for observation in result["observation_facts"]:
                for sig in observation["signatures"]:
                    span_usage[(packet["period_id"], *sig)].append(key)
            results.append(result)
        except (ContractError, KeyError, TypeError, ValueError) as exc:
            errors.append({"packet_id": key, "issue": "invalid_review", "reason": str(exc)})
    shared_packets = {key for keys in span_usage.values() if len(set(keys)) > 1 for key in keys}
    for result in results:
        if result["packet_id"] in shared_packets:
            result["shared_evidence"] = True
    # 缺失/失败仍留在目标和逐项分母中，不剔除后抬高均分。
    valid = {r["packet_id"] for r in results}
    for key, packet in packets_by_id.items():
        if key not in valid:
            results.append({"packet_id": key, "user_id": packet["user_id"], "goal_id": packet["goal_id"], "title": packet["title"],
                            "period_id": packet["period_id"], "boundary": packet["boundary"], "review_status": "review_failed",
                            "task_class": "CU", "complex_handling": "unknown", "complex_m_exact": None,
                            "semantic_complete": False,
                            "critical_control_failure": False, "cells": [{"item": item, "status": "review_failed", "band": None,
                            "reason": "评审缺失或合同失败", "opportunity_count": 0} for item in ITEMS]})
    return manifest, packet_manifest, packets_by_id, reviews, results, errors


def main():
    parser = argparse.ArgumentParser(description="PMJE 本地试评；不调用生产或模型API")
    sub = parser.add_subparsers(dest="command", required=True)
    first = sub.add_parser("prepare", help="冻结原文、代码、判据和DSH提示词")
    first.add_argument("--tasks-dir", type=Path, required=True)
    first.add_argument("--output", type=Path, required=True)
    first.add_argument("--users", required=True)
    first.add_argument("--start", required=True)
    first.add_argument("--end", required=True)
    first.add_argument("--period-mode", choices=["weekly", "window", "both"], default="both")
    fresh = sub.add_parser("refreeze", help="以同一冻结输入创建新版本批次，不复制旧语义判分")
    fresh.add_argument("--source-run", type=Path, required=True)
    fresh.add_argument("--output", type=Path, required=True)
    fresh.add_argument("--period-mode", choices=["weekly", "window", "both"], default="both")
    intake = sub.add_parser("import-audit", help="从本地审计缓存中性展开，保留同文真实请求")
    intake.add_argument("--cache-dir", type=Path, action="append", required=True)
    intake.add_argument("--output", type=Path, required=True)
    intake.add_argument("--users", required=True)
    intake.add_argument("--start", required=True)
    intake.add_argument("--end", required=True)
    demo = sub.add_parser("demo", help="生成完整合成示例，不作真人评分")
    demo.add_argument("--output", type=Path, required=True)
    verify = sub.add_parser("verify", help="核验原文及冻结引擎哈希")
    verify.add_argument("--run", type=Path, required=True)
    presentation = sub.add_parser("render-report", help="从既有评分数据重新生成对外简报与内部分析附件，不重跑评分")
    presentation.add_argument("--data", type=Path, required=True)
    presentation.add_argument("--output", type=Path, required=True)
    reader = sub.add_parser("read", help="按冻结索引分页读取事件原文，不改动数据")
    reader.add_argument("--run", type=Path, required=True)
    reader.add_argument("--event-id", action="append", default=[])
    reader.add_argument("--user")
    reader.add_argument("--session-id")
    reader.add_argument("--offset", type=int, default=0)
    reader.add_argument("--limit", type=int, default=5)
    reader.add_argument("--start", type=int, default=0, help="Python字符位置")
    reader.add_argument("--max-characters", type=int, default=12000)
    second = sub.add_parser("packets", help="验证全量父目标计划并生成期间包")
    second.add_argument("--run", type=Path, required=True)
    second.add_argument("--plan", type=Path, required=True)
    for command in ("check", "score"):
        stage = sub.add_parser(command, help="生成复核清单" if command == "check" else "生成四维报告与诊断数据")
        stage.add_argument("--run", type=Path, required=True)
        stage.add_argument("--reviews", type=Path, required=True)
        stage.add_argument("--output", type=Path, required=True)
        stage.add_argument("--sample-rate", type=float, default=0.1, help="独立随机复核预算比例，不是能力/发布门槛")
        stage.add_argument("--usage", type=Path, help="周度实质参与与采集范围的语义判断；缺失时仅出表现分草稿")
        if command == "score":
            stage.add_argument("--audits", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "import-audit":
            from .intake import import_audit
            result = import_audit(args.cache_dir, args.output, args.users.split(","), args.start, args.end)
        elif args.command == "demo":
            from .examples import run_demo
            result = run_demo(args.output)
        elif args.command == "prepare":
            result = prepare(args.tasks_dir, args.output, args.users.split(","), args.start, args.end, args.period_mode)
        elif args.command == "refreeze":
            result = refreeze(args.source_run, args.output, args.period_mode)
        elif args.command == "verify":
            manifest, events = load_run(args.run, load_events=False)
            result = {"policy_version": VERSION, "verified": True, "counts": manifest["counts"], "source_sessions": manifest["source_sessions"]}
        elif args.command == "render-report":
            from .reader_report import render_report
            require(not args.output.exists(), "报告输出已存在，禁止覆盖原结果")
            data = json.loads(args.data.read_text(encoding="utf-8"))
            require(isinstance(data.get("summary"), list), "缺评分汇总数据")
            args.output.mkdir(parents=True, mode=0o700)
            render_report(data, args.output)
            (args.output / "analysis/source-report-data.json").write_bytes(encoded(data))
            result = {"report": str(args.output / "report.html"), "scoring_changed": False,
                      "source_data_sha256": file_digest(args.data), "report_version": "reader-brief-1"}
            (args.output / "analysis/render-receipt.json").write_bytes(encoded(result))
        elif args.command == "read":
            result = read_events(args.run, args.event_id, args.user, args.session_id, args.offset, args.limit, args.start, args.max_characters)
        elif args.command == "packets":
            result = packets(args.run, args.plan)
        else:
            from .reporting import check_or_score
            result = check_or_score(args.run, args.reviews, args.output, args.sample_rate,
                                    getattr(args, "audits", None), report=args.command == "score", usage_path=getattr(args, "usage", None))
    except (ContractError, KeyError, TypeError, ValueError, OSError) as exc:
        parser.exit(2, "流程校验失败：" + str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command in {"check", "score"} and not result["structural_complete"]:
        raise SystemExit(2)
    if args.command == "score" and not result["report_complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
