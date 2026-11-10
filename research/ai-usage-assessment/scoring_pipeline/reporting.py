"""保留适用分母、语义复核状态及可追溯的周分/月级。"""

from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from .common import require, digest, encoded, rows, write_rows, valid_span, review_context, moment
from .policy import VERSION
from .aggregation import summarize, qualify_ratings
from .participation import apply_participation
from .reader_report import attach_reader_evidence, render_report


def selected(values, seed, rate):
    """确定性随机顺序用于QA预算；不是能力样本量门槛或置信度。"""
    if not values:
        return []
    ordered = sorted(values, key=lambda v: digest((seed + str(v)).encode()))
    return ordered[:max(1, math.ceil(len(ordered) * rate))]


def audit_queue(run, manifest, packets, results, sample_rate, usage_path=None):
    require(0 < sample_rate <= 1 and math.isfinite(sample_rate), "抽检比例须在(0,1]")
    plan = json.loads((Path(run) / "packets/plan.json").read_text(encoding="utf-8"))
    seed = digest((Path(run) / "manifest.json").read_bytes())
    strata = defaultdict(list)
    with (Path(run) / "event-index.jsonl").open(encoding="utf-8") as index:
        pointers = [json.loads(line) for line in index]
        event_users = {e["event_id"]: e["user_id"] for e in pointers}
    for row in plan["materials"]:
        strata[(event_users[row["event_id"]], row["classification"])].append(row["event_id"])
    queue = []
    for user in manifest["users"]:
        ids, details = [], {}
        for (owner, classification), values in sorted(strata.items()):
            if owner != user:
                continue
            subset = values if classification in {"unresolved", "duplicate"} else selected(values, seed + user + classification, sample_rate)
            ids.extend(subset)
            details[classification] = {"total": len(values), "checked": len(subset)}
        if not ids:
            continue
        ids.extend(k for k in json.loads((Path(run) / "packets/manifest.json").read_text())["origin_review_event_ids"] if event_users[k] == user)
        for classification in details:
            details[classification]["checked"] = len(set(ids) & set(strata[(user, classification)]))
        # 即使没有活动包，也要检查排除及目标边界，不能只复核已经打分的材料。
        queue.append({"audit_id": "intake-" + user, "kind": "intake", "user_id": user,
                      "reviewed_sha256": digest((Path(run) / "packets/plan.json").read_bytes()),
                      "initial_reviewer_id": plan["planner_id"], "required_event_ids": sorted(set(ids)),
                      "initial_review_context": plan["review_context"],
                      "reason": ["来源身份、系统/重复排除与父目标边界抽检"], "strata": details,
                      "goal_ids": [g["goal_id"] for g in plan["goals"] if g["user_id"] == user]})
    selected_packets = set()
    by_scope = defaultdict(list)
    for result in results:
        if result["review_status"] == "submitted":
            by_scope[(result["user_id"], result["period_id"])].append(result["packet_id"])
    for scope, ids in by_scope.items():
        selected_packets.update(selected(ids, seed + repr(scope), sample_rate))
    for result in results:
        if result["review_status"] != "submitted":
            continue
        reasons = []
        bands = {c["band"] for c in result["cells"]}
        if bands & {0, 40, 60}:
            reasons.append("已证低档及归责")
        if bands & {90, 100}:
            reasons.append("高档依据与完整机会核查")
        if result["task_class"] in {"C2", "CU"}:
            reasons.append("复杂关系及处理/复杂性未知")
        if result["source_limitations"]:
            reasons.append("原文截断或归属歧义")
        if result["shared_evidence"]:
            reasons.append("同引文/跨目标独立事实区分")
        if any(c["status"] in {"pending_review", "review_failed", "attribution_unclear"} for c in result["cells"]):
            reasons.append("评审或归属未决")
        if result["packet_id"] in selected_packets:
            reasons.append("独立于关键词筛选的分层随机抽检")
        if reasons:
            packet = packets[result["packet_id"]]
            queue.append({"audit_id": "packet-" + result["packet_id"], "kind": "packet", "packet_id": result["packet_id"],
                          "user_id": result["user_id"], "reviewed_sha256": result["review_sha256"],
                          "initial_reviewer_id": result["reviewer_id"], "required_event_ids": [e["event_id"] for e in packet["events"]],
                          "initial_review_context": result["review_context"],
                          "reason": reasons})
    if usage_path:
        periods = {p["period_id"]: p for p in manifest["periods"] if p["kind"] == "weekly"}
        usage = json.loads(Path(usage_path).read_text(encoding="utf-8"))
        for decision in usage["windows"]:
            period = periods[decision["period_id"]]
            evidence_ids = {p["event_id"] for p in decision["evidence"]}
            evidence_ids.update(e["event_id"] for pid in decision["packet_ids"] for e in packets[pid]["events"])
            if not evidence_ids:
                candidates = [p["event_id"] for p in pointers if p["user_id"] == decision["user_id"]
                              and p["field"] == "prompt" and moment(period["start"]) <= moment(p["at"]) < moment(period["end"])]
                evidence_ids.update(selected(candidates, seed + decision["user_id"] + period["period_id"], sample_rate))
            queue.append({"audit_id": "usage-" + decision["user_id"] + "-" + period["period_id"], "kind": "usage",
                          "user_id": decision["user_id"], "period_id": period["period_id"],
                          "reviewed_sha256": digest(encoded(decision)), "initial_reviewer_id": decision["reviewer_id"],
                          "initial_review_context": decision["review_context"], "required_event_ids": sorted(evidence_ids),
                          "reason": ["独立核对实质参与、采集范围及评价适用性；影响月度候选综合"]})
    return queue


def validate_audits(run, queue, path):
    if path is None:
        return {"required": len(queue), "submitted": 0, "missing": len(queue), "disputed": 0, "closed": not queue,
                "users": {u: {"closed": False} for u in {q["user_id"] for q in queue}}}
    expected = {row["audit_id"]: row for row in queue}
    submitted = rows(path)
    needed = {eid for audit in submitted for eid in audit["checked_event_ids"]}
    events = {}
    with (Path(run) / "event-index.jsonl").open(encoding="utf-8") as index, (Path(run) / "events.jsonl").open("rb") as body:
        for line in index:
            pointer = json.loads(line)
            if pointer["event_id"] in needed:
                body.seek(pointer["byte_offset"])
                raw = body.read(pointer["byte_length"])
                require(digest(raw) == pointer["sha256"], "复核引用事件正文变化")
                events[pointer["event_id"]] = json.loads(raw)
    seen, disputed, limited, incomplete, open_findings = set(), 0, 0, 0, 0
    user_states = {u: {"required": sum(q["user_id"] == u for q in queue), "submitted": 0, "disputed": 0, "incomplete": 0, "open_findings": 0} for u in {q["user_id"] for q in queue}}
    for audit in submitted:
        aid = audit["audit_id"]
        require(aid in expected and aid not in seen, "复核重复或引用本轮清单之外的条目")
        item = expected[aid]
        require(audit["reviewed_sha256"] == item["reviewed_sha256"], "复核针对旧版plan或reviews；需对变更重核")
        require(audit.get("reviewer_id") and audit["reviewer_id"] != item["initial_reviewer_id"], "复核须为另一个实际评审上下文，不得自审")
        context = review_context(audit.get("review_context"))
        require(context["context_id"] != item["initial_review_context"]["context_id"], "改reviewer_id不能代替实际独立上下文")
        require(audit["verdict"] in {"confirmed", "confirmed_with_limits", "disputed"} and audit.get("reason"), "复核缺结论/依据")
        checked = audit["checked_event_ids"]
        require(len(checked) == len(set(checked)) and set(checked) == set(item["required_event_ids"]), "复核未对账指定原文范围")
        actual = audit["read_event_ids"]
        require(len(actual) == len(set(actual)) and set(actual) <= set(checked), "实际复核阅读范围越界/重复")
        require(audit["method"] in {"semantic", "partial", "structural_only"}, "复核须区分语义判断和机械对账")
        semantic = audit["method"] == "semantic" and set(actual) == set(checked)
        require(bool(audit["findings"]), "复核须保留独立发现与原文证据，不能只写同意")
        pending_findings = 0
        for finding in audit["findings"]:
            require(finding.get("statement") and finding["impact"] in {"none", "limitation", "dispute"}
                    and (finding["evidence"] or item["kind"] == "usage" and not checked), "复核发现不完整")
            require(finding.get("check") in {"authorship", "boundary", "opportunity", "band", "attribution", "complexity", "counterevidence", "participation"}, "复核须说明具体语义检查对象，结构计数不能代替")
            require(finding["impact"] != "dispute" or audit["verdict"] == "disputed", "发现实质争议不能勾选复核通过")
            # 未说明限制是否需要补正时，不能从with_limits推断问题已解决。
            if finding["impact"] == "limitation":
                resolution = finding.get("resolution", {})
                pending_findings += not (resolution.get("status") in {"resolved", "accepted_scope"} and resolution.get("reason"))
            for proof in finding["evidence"]:
                require(proof["event_id"] in actual, "复核引用超出实际阅读范围")
                text = events[proof["event_id"]]["text"]
                require(valid_span(text, proof["start"], proof["end"]) and text[proof["start"]:proof["end"]] == proof["quote"], "复核引用与原文不一致")
        disputed += audit["verdict"] == "disputed"
        limited += audit["verdict"] == "confirmed_with_limits"
        incomplete += not semantic
        state = user_states[item["user_id"]]
        state["submitted"] += 1
        state["disputed"] += audit["verdict"] == "disputed"
        state["incomplete"] += not semantic
        state["open_findings"] += pending_findings
        open_findings += pending_findings
        seen.add(aid)
    for state in user_states.values():
        state["closed"] = state["required"] == state["submitted"] and not state["disputed"] and not state["incomplete"] and not state["open_findings"]
    return {"required": len(queue), "submitted": len(seen), "missing": len(queue) - len(seen),
            "disputed": disputed, "confirmed_with_limits": limited, "semantic_incomplete": incomplete,
            "open_findings": open_findings,
            "closed": len(seen) == len(queue) and disputed == 0 and incomplete == 0 and open_findings == 0, "users": user_states}


def check_or_score(run, review_path, output, sample_rate=0.1, audit_path=None, report=False, usage_path=None):
    from .runner import inspect_reviews
    manifest, packet_manifest, packets, reviews, results, errors = inspect_reviews(run, review_path)
    participation_status = apply_participation([], manifest, packets, results, usage_path)
    queue = audit_queue(run, manifest, packets, results, sample_rate, usage_path)
    audit_status = validate_audits(run, queue, audit_path)
    pending = [{"packet_id": r["packet_id"], "item": c["item"], "status": c["status"]}
               for r in results for c in r["cells"] if c["status"] in {"pending_review", "review_failed"}]
    semantic_incomplete = [r["packet_id"] for r in results if not r.get("semantic_complete", False)]
    unresolved_ingest = manifest.get("input_provenance", {}).get("unresolved_ingest_count", 0)
    data = {"policy_version": VERSION, "source_manifest_sha256": digest((Path(run) / "manifest.json").read_bytes()),
            "packet_manifest_sha256": digest((Path(run) / "packets/manifest.json").read_bytes()),
            "reviews_sha256": digest(Path(review_path).read_bytes()),
            "audits_sha256": digest(Path(audit_path).read_bytes()) if audit_path else None,
            "expected_packets": len(packets), "submitted_reviews": len(reviews), "invalid_or_missing_reviews": errors,
            "material_dispositions": packet_manifest["materials"], "unresolved_material_ids": packet_manifest["unresolved_material_ids"],
            "unresolved_ingest_count": unresolved_ingest,
            "unresolved_goal_ids": packet_manifest["unresolved_goal_ids"], "semantic_pending": pending,
            "semantic_incomplete_packets": semantic_incomplete,
            "audit_budget": {"sample_rate": sample_rate, "seed": digest((Path(run) / "manifest.json").read_bytes()),
                             "meaning": "随机QA预算加全量风险复核，不是统计置信度或能力发布门槛"},
            "audit_status": audit_status, "structural_complete": not errors,
            "participation_status": participation_status,
            "trial_complete": not errors and audit_status["closed"] and not pending and not semantic_incomplete and not packet_manifest["unresolved_material_ids"] and not packet_manifest["unresolved_goal_ids"] and not unresolved_ingest,
            "production_ready": False, "human_calibration": "not_established",
            "overall_score": None, "rating": None, "overall_reason": "分数及月级按summary中各用户各期间分别计算；不生成跨人总分或排名",
            "limits": "适用项观察均分与未知分母并列；复杂权重3/2为产品候选，非岗位校正；执行闭合不等于真人校准或已上线。"}
    output = Path(output)
    require(not output.exists(), "输出已存在，禁止覆盖")
    output.mkdir(parents=True, mode=0o700)
    write_rows(output / "audit-queue.jsonl", queue)
    write_rows(output / "review-queue.jsonl", errors + pending + [{"issue": "semantic_incomplete", "packet_id": p} for p in semantic_incomplete]
               + [{"issue": "unresolved_material", "event_id": e} for e in data["unresolved_material_ids"]])
    (output / "submitted-reviews.jsonl").write_bytes(Path(review_path).read_bytes())
    if audit_path:
        (output / "submitted-audits.jsonl").write_bytes(Path(audit_path).read_bytes())
    if usage_path:
        (output / "submitted-usage.json").write_bytes(Path(usage_path).read_bytes())
    if report:
        data["summary"] = summarize(results, manifest["users"], manifest["periods"])
        qualify_ratings(data["summary"], audit_status, packet_manifest["unresolved_material_users"], unresolved_ingest)
        data["participation_status"] = apply_participation(data["summary"], manifest, packets, results, usage_path)
        attach_reader_evidence(data["summary"], packets, reviews, results)
        data["report_complete"] = data["trial_complete"] and data["participation_status"]["complete"]
        data["band_diagnostics"] = dict(Counter(str(c["band"]) for r in results for c in r["cells"] if c["status"] == "scored"))
        data["window_diagnostics"] = [{"user_id": user,
            "distinct_active_goals": len({r["goal_id"] for r in results if r["user_id"] == user}),
            "distinct_complex_goals": len({r["goal_id"] for r in results if r["user_id"] == user and r["task_class"] == "C2"}),
            "goal_period_observations": sum(r["user_id"] == user for r in results)} for user in manifest["users"]]
        write_rows(output / "results.jsonl", results)
        render_report(data, output)
    (output / "report-data.json").write_bytes(encoded(data))
    files = [p for p in output.rglob('*') if p.is_file()]
    (output / "output-manifest.json").write_bytes(encoded({"policy_version": VERSION, "files": {str(p.relative_to(output)): digest(p.read_bytes()) for p in files}}))
    keys = ("policy_version", "expected_packets", "submitted_reviews", "structural_complete", "trial_complete", "audit_status", "participation_status", "production_ready")
    return {k: data[k] for k in keys} | ({"report_complete": data["report_complete"]} if report else {})

