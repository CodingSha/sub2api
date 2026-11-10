"""只用于合同演示和程序回归的合成记录，绝不作为真人语义金标准。"""

import json
from pathlib import Path

from .common import rows, write_rows, encoded
from .policy import VERSION, ITEMS, CLAIMS, RESPONSIBILITY


def source_record():
    statements = [
        "合成演示：目标是兼容旧导出接口，完成后保持原调用有效。",
        "合成演示：旧接口为v1，新内核为v2，材料在本条给出。",
        "合成演示：保留旧协议，不写生产库，验收采用隔离环境。",
        "合成演示：先隔离协议适配层，由AI修改、本人确认接口边界。",
        "合成演示：适配层完成后再切换内核，避免旧协议被直接覆盖。",
        "合成演示：已确认内核变更，先更新迁移顺序，再按新分工执行。",
        "合成演示：原日志是旧版本，本次报错要按新版本证据判断。",
        "合成演示：请分别测试v1旧调用和v2新调用，并给出实际结果。",
        "合成演示：两条隔离调用通过，仅在隔离范围接受，生产尚未核验。",
        "合成演示：将同一阶段的独立反馈合并一次提交，保留各自定位。",
        "合成演示：这一独立重试已确认无效，停止该循环并保留现场。",
        "合成演示：将稳定导出步骤复用于下一批同格式数据，并核对差异。",
    ]
    return {"user_email": "demo@apsat.com", "session_id": "synthetic-contract-example",
            "trace": [{"at": "2026-09-02T10:{:02d}:00+08:00".format(i), "prompt": text,
                       "response": "合成反馈记录{}：对应安排已经执行，隔离范围结果已记录。".format(i)} for i, text in enumerate(statements)]}


def plan_for(events):
    return {"policy_version": VERSION, "planner_id": "synthetic-initial-context",
            "review_context": {"team_id": "synthetic-team", "agent_id": "synthetic-initial", "context_id": "synthetic-initial-context"},
            "materials": [{"event_id": e["event_id"], "classification": "human", "human_spans": [[0, len(e["text"])]],
                           "reason": "合成用例中约定当轮本人内容"} for e in events if e["field"] == "prompt"],
            "goals": [{"goal_id": "demo-goal", "user_id": "demo", "title": "合成接口迁移演示",
                       "boundary": "confirmed", "boundary_reason": "合成同一父目标，不作语义校准", "event_ids": [e["event_id"] for e in events]}],
            "unassigned_events": []}


def review_for(packet):
    evidence, observations, cells = [], [], []
    for index, item in enumerate(ITEMS):
        prompt = next(e for e in packet["events"] if e["trace_index"] == index and e["field"] == "prompt")
        response = next(e for e in packet["events"] if e["trace_index"] == index and e["field"] == "response")
        for name, event, kind in ((item, prompt, "person_input" if item.startswith("P") else "person_action"), (item + "-feedback", response, "artifact")):
            evidence.append({"id": name, "event_id": event["event_id"], "start": 0, "end": len(event["text"]), "kind": kind,
                             "source_basis": "合成夹具约定为实际执行记录；不作为真人或模型语义证据"})
        observations.append({"id": item, "fact": "合成的{}独立行为事实".format(item), "primary_item": item,
                             "evidence_ids": [item], "scope_event_id": prompt["event_id"]})
        claims = {k: {"value": "yes" if k in {"basic_effective", "adequate"} else "unknown" if k in {"reasoned_control", "closed_loop"} else "no",
                      "evidence_ids": [item, item + "-feedback"], "reason": "仅合成固定条件，不声明真人语义正确"} for k in CLAIMS}
        cells.append({"item": item, "status": "scored", "reason": "合成充分处理条件",
                      "opportunities": [{"id": "op-" + item, "description": "合成的{}机会".format(item), "evidence_ids": [item],
                                         "result": "handled", "result_evidence_ids": [item + "-feedback"]}],
                      "observation_ids": [item], "feedback_evidence_ids": [item + "-feedback"], "related_observation_ids": [],
                      "feedback_links": [{"action_evidence_ids": [item], "feedback_evidence_ids": [item + "-feedback"],
                                          "relation": "execution_result", "reason": "合成夹具明确约定同次独立执行结果，不作为真实语义判断"}],
                      "claims": claims, "responsibility": {k: {"value": "unknown", "evidence_ids": [], "reason": "合成高档不作负面归责"} for k in RESPONSIBILITY},
                      "closure": [],
                      "complex_control": {"status": "none", "relation_ids": [], "opportunity_ids": [], "evidence_ids": [], "reason": "合成常规机会"}})
    return {"policy_version": VERSION, "packet_id": packet["packet_id"], "reviewer_id": "synthetic-initial-context",
            "review_context": {"team_id": "synthetic-team", "agent_id": "synthetic-initial", "context_id": "synthetic-initial-context"},
            "review_coverage": {"method": "semantic", "accounted_event_ids": [e["event_id"] for e in packet["events"]],
                                "read_event_ids": [e["event_id"] for e in packet["events"]], "excluded_events": [], "unread_event_ids": [],
                                "opportunity_audit": "合成12项全机会，用于程序合同回归；不声称实际进行真实语义评审"},
            "evidence": evidence, "complexity": {"class": "C0", "reason": "合成常规条件，不作真实复杂性判断", "evidence_ids": ["P1"], "relations": []},
            "observations": observations, "cells": cells,
            "complex_handling": {"status": "not_applicable", "reason": "合成C0不出CM", "relation_ids": [], "observation_ids": [], "feedback_evidence_ids": []},
            "outcome_notes": []}


def audits_for(queue, events):
    by_id = {e["event_id"]: e for e in events}
    audits = []
    for item in queue:
        event = by_id[item["required_event_ids"][0]] if item["required_event_ids"] else None
        audits.append({"audit_id": item["audit_id"], "reviewed_sha256": item["reviewed_sha256"], "reviewer_id": "synthetic-second-context",
                       "review_context": {"team_id": "synthetic-team", "agent_id": "synthetic-second", "context_id": "synthetic-second-context"},
                       "method": "semantic", "read_event_ids": item["required_event_ids"],
                       "verdict": "confirmed", "reason": "纯合成复核合同演示，不声明已进行真人或模型独立评审",
                       "checked_event_ids": item["required_event_ids"], "findings": [{"check": "participation" if item["kind"] == "usage" else "attribution", "statement": "合成引用及复核结构演示", "impact": "none",
                       "evidence": [{"event_id": event["event_id"], "start": 0, "end": len(event["text"]), "quote": event["text"]}] if event else []}]})
    return audits


def run_demo(output):
    from .runner import prepare, packets
    from .reporting import check_or_score
    output = Path(output)
    from .common import require
    require(not output.exists(), "演示目录已存在")
    output.mkdir(parents=True, mode=0o700)
    source = output / "source"
    source.mkdir()
    write_rows(source / "demo_at_apsat.com.jsonl", [source_record()])
    run = output / "run"
    manifest = prepare(source, run, ["demo"], "2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00", "both")
    events = rows(run / "events.jsonl")
    (run / "plan.json").write_bytes(encoded(plan_for(events)))
    packets(run, run / "plan.json")
    packet_rows = rows(run / "packets/packets.jsonl")
    write_rows(run / "reviews.jsonl", [review_for(packet) for packet in packet_rows])
    usage = []
    for period in manifest["periods"]:
        if period["kind"] != "weekly":
            continue
        group = [p for p in packet_rows if p["period_id"] == period["period_id"]]
        event = next(e for e in group[0]["events"] if e["event_id"] in group[0]["active_human_event_ids"]) if group else None
        usage.append({"user_id": "demo", "period_id": period["period_id"], "reviewer_id": "synthetic-initial-context",
                      "review_context": {"team_id": "synthetic-team", "agent_id": "synthetic-initial", "context_id": "synthetic-initial-context"},
                      "coverage": "confirmed", "coverage_basis": "合成完整采集与评价适用性约定，不是真实核查", "activity": "substantive" if group else "none",
                      "reason": "合成实质工作/未使用约定，不是实际人员判断", "packet_ids": [p["packet_id"] for p in group],
                      "evidence": [{"event_id": event["event_id"], "start": 0, "end": len(event["text"]), "quote": event["text"]}] if event else []})
    (run / "usage.json").write_bytes(encoded({"policy_version": VERSION, "windows": usage}))
    check_or_score(run, run / "reviews.jsonl", run / "checks", usage_path=run / "usage.json")
    queue = rows(run / "checks/audit-queue.jsonl")
    write_rows(run / "audits.jsonl", audits_for(queue, events))
    result = check_or_score(run, run / "reviews.jsonl", run / "scored", audit_path=run / "audits.jsonl", report=True, usage_path=run / "usage.json")
    (output / "SYNTHETIC-ONLY.json").write_bytes(encoded({"synthetic": True, "semantic_validation": False, "human_calibration": False,
                                                       "purpose": "有效JSON形状、命令和数值合同演示，不代表真实评审"}))
    return result
