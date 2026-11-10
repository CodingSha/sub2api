"""周度实质使用与月度参与候选；不将低用量改写为低能力。"""

import json
from datetime import timedelta
from fractions import Fraction
from pathlib import Path

from .common import require, moment, display, valid_span, review_context
from .policy import VERSION
from .aggregation import level_for


def seconds_between(start, end):
    return Fraction(max(0, (end - start) // timedelta(microseconds=1)), 1000000)


def apply_participation(summary, manifest, packets, results, path=None):
    """缺声明时保留未知；声明只是可追溯语义判断，不冒充机器已证明。"""
    submitted = json.loads(Path(path).read_text(encoding="utf-8")) if path else {"policy_version": VERSION, "windows": []}
    require(submitted.get("policy_version") == VERSION, "参与判断版本不一致")
    periods = {p["period_id"]: p for p in manifest["periods"] if p["kind"] == "weekly"}
    expected = {(u, p) for u in manifest["users"] for p in periods}
    decisions = {}
    for entry in submitted["windows"]:
        key = (entry["user_id"], entry["period_id"])
        require(key in expected and key not in decisions, "参与判断用户/周窗越界或重复")
        require(bool(entry.get("reviewer_id")), "缺参与判断者身份")
        review_context(entry.get("review_context"))
        require(entry["coverage"] in {"confirmed", "incomplete", "unknown"} and entry.get("coverage_basis"), "缺采集范围及适用性依据")
        require(entry["activity"] in {"substantive", "limited", "none", "unknown"} and entry.get("reason"), "缺实质使用判断或理由")
        ids = entry["packet_ids"]
        require(len(ids) == len(set(ids)), "参与父目标包重复")
        require(all(p in packets and packets[p]["user_id"] == key[0] and packets[p]["period_id"] == key[1] for p in ids), "参与包跨人或跨周")
        own = {e for p in ids for e in packets[p]["active_human_event_ids"]}
        events = {e["event_id"]: e for p in ids for e in packets[p]["events"]}
        materials = {m["event_id"]: m for p in ids for m in packets[p]["materials"]}
        for proof in entry["evidence"]:
            require(proof["event_id"] in own, "参与证据须为本周本人材料")
            text = events[proof["event_id"]]["text"]
            require(valid_span(text, proof["start"], proof["end"]) and text[proof["start"]:proof["end"]] == proof["quote"], "参与引文与原文不一致")
            material = materials[proof["event_id"]]
            require(material["classification"] == "human" and any(a <= proof["start"] < proof["end"] <= b for a, b in material["human_spans"]), "参与引文越出本人表达范围")
        if entry["activity"] in {"substantive", "limited"}:
            require(ids and entry["evidence"] and all(packets[p]["boundary"] == "confirmed" for p in ids), "实质/零星使用判断须有确认父目标及本人原文；不依赖评分是否完成")
        if entry["activity"] == "none":
            require(not any(p["user_id"] == key[0] and p["period_id"] == key[1] and p["active_human_event_ids"] for p in packets.values()), "已有本人活动不能记未使用；仅探针/尝试应记limited或unknown")
        decisions[key] = entry

    for row in summary:
        row["performance_rating"] = row["rating"]
        row["monthly_candidate_score"] = row["monthly_candidate_exact"] = None
        row["trial_rating"] = None
        row["participation_score"] = row["participation_exact"] = None
        row["usage"] = decisions.get((row["user_id"], row["period"]["period_id"]),
            {"coverage": "unknown", "activity": "unknown", "reason": "尚未提交实质使用与采集范围判断"})
        if row["period"]["kind"] != "monthly":
            continue
        start, end = moment(row["period"]["start"]), moment(row["period"]["end"])
        parts, covered, duration, known = [], Fraction(), Fraction(), True
        for pid, period in periods.items():
            left, right = max(start, moment(period["start"])), min(end, moment(period["end"]))
            if left >= right:
                continue
            span = seconds_between(left, right)
            decision = decisions.get((row["user_id"], pid), {})
            resolved = decision.get("coverage") == "confirmed" and decision.get("activity") in {"substantive", "limited", "none"}
            known = known and resolved
            duration += span
            if resolved and decision["activity"] == "substantive":
                covered += span
            parts.append({"period_id": pid, "days": str(span / 86400), "resolved": resolved, **decision})
        known = known and duration == seconds_between(start, end)
        row["participation_windows"] = parts
        row["rating"] = None
        row["rating_status"] = "参与材料待核，月级观察中"
        if not known:
            continue
        activity = 100 * covered / duration
        row["participation_exact"], row["participation_score"] = str(activity), display(activity)
        if row["overall_exact"] is None:
            row["rating_status"] = "无完整四维表现分，未评级"
            continue
        candidate = Fraction(9, 10) * Fraction(row["overall_exact"]) + activity / 10
        row["monthly_candidate_exact"], row["monthly_candidate_score"] = str(candidate), display(candidate)
        # 观察试评等级与正式评级分字段；缺证不补分，也不伪称材料已充分。
        if row.get('semantic_review_complete') and row.get('semantic_audit_closed'):
            row['trial_rating'] = level_for(candidate)
            row['trial_rating_scope'] = '仅对应已观察四维表现及本平台参与候选；不是确定能力或正式发布评级'
        row["rating_status"] = "评审/复核待闭合，月级观察中"
        if row.get("evaluation_complete"):
            row["rating"] = level_for(candidate)
            row["rating_status"] = "候选月级，待本轮验证"
        elif row.get('trial_rating'):
            row['rating_status'] = '观察试评月级，材料缺证单列'
        # 观察试评等级也须防止显示分跨过精确等级边界。
        shown_rating = row.get('rating') or row.get('trial_rating')
        if shown_rating and level_for(Fraction(row["monthly_candidate_score"])) != shown_rating:
            from decimal import Decimal, ROUND_DOWN
            row["monthly_candidate_score"] = str((Decimal(candidate.numerator) / Decimal(candidate.denominator)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
    monthly_windows_covered = all(sum((seconds_between(max(moment(p['start']), moment(m['start'])), min(moment(p['end']), moment(m['end'])))
        for p in periods.values()), Fraction()) == seconds_between(moment(m['start']), moment(m['end']))
        for m in manifest['periods'] if m['kind'] == 'monthly')
    return {"required": len(expected), "submitted": len(decisions), "complete": monthly_windows_covered and len(decisions) == len(expected)
            and all(d["coverage"] == "confirmed" and d["activity"] != "unknown" for d in decisions.values()),
            "status": "90%表现+10%参与为产品候选，不代表已确认上线"}
