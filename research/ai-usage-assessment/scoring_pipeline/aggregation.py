"""确定性汇总：复杂机会权重、适用项分母、周分与月级。"""

from collections import Counter
from decimal import Decimal, ROUND_DOWN
from fractions import Fraction

from .common import display
from .policy import DIMENSIONS, ITEMS, EXEMPT_STATES, LEVELS


def level_for(value):
    for lower, code, name in LEVELS:
        if value >= lower:
            return {"id": code, "name": name}
    return None


def weighted_bounds(scored):
    """只计算权重资格争议的端点范围；不是对缺失表现补分。"""
    fixed = [(Fraction(c.get("weight_exact") or 1), Fraction(c["band"]))
             for c in scored if c.get("weight_exact", "1") is not None]
    unknown = [Fraction(c["band"]) for c in scored if c.get("weight_exact", "1") is None]
    n = sum((w * s for w, s in fixed), Fraction()) + sum(unknown)
    d = sum((w for w, _ in fixed), Fraction()) + len(unknown)
    if not d:
        return None
    endpoints = [n / d]
    for ordered in (sorted(unknown), sorted(unknown, reverse=True)):
        numerator, denominator = n, d
        for score in ordered:
            numerator += score / 2
            denominator += Fraction(1, 2)
            endpoints.append(numerator / denominator)
    return {"lower_exact": str(min(endpoints)), "upper_exact": str(max(endpoints)),
            "lower": display(min(endpoints)), "upper": display(max(endpoints))}


def summarize(results, users, periods):
    summary = []
    for user in users:
        for period in periods:
            group = [r for r in results if r["user_id"] == user and r["period_id"] == period["period_id"]]
            items = {}
            for item in ITEMS:
                pairs = [(r, next(c for c in r["cells"] if c["item"] == item)) for r in group]
                cells = [c for _, c in pairs]
                scored = [c for c in cells if c["status"] == "scored"]
                unresolved = any(c.get("weight_exact", "1") is None for c in scored)
                total_weight = sum((Fraction(c.get("weight_exact") or 1) for c in scored), Fraction())
                value = sum((Fraction(c.get("weight_exact") or 1) * c["band"] for c in scored), Fraction()) / total_weight if scored and not unresolved else None
                baseline = sum((Fraction(c["band"]) for c in scored), Fraction()) / len(scored) if scored else None
                unknown = sum(c["status"] not in EXEMPT_STATES | {"scored"} for c in cells)
                complex_cells = [c for c in scored if c.get("complex_control", {}).get("status") == "confirmed"]
                cm = sum((Fraction(c["band"]) for c in complex_cells), Fraction()) / len(complex_cells) if complex_cells else None
                items[item] = {
                    "observed_exact": str(value) if value is not None else None, "observed_score": display(value),
                    "unweighted_exact": str(baseline) if baseline is not None else None, "unweighted_score": display(baseline),
                    "weight_pending": unresolved, "weight_sensitivity": weighted_bounds(scored) if unresolved else None,
                    "scored_goals": len(scored), "active_goals": len(group), "unknown_goals": unknown,
                    "known_opportunity_goals": sum(c["opportunity_count"] > 0 for c in cells),
                    "status_counts": dict(Counter(c["status"] for c in cells)),
                    "band_counts": dict(Counter(str(c["band"]) for c in scored)),
                    "excluded_as_inapplicable": bool(cells) and not scored and unknown == 0,
                    "complex_goals": len(complex_cells), "complex_observed_exact": str(cm) if cm is not None else None,
                    "complex_observed_score": display(cm),
                    "weighted_details": [{"goal_id": r["goal_id"], "packet_id": r["packet_id"], "band": c["band"],
                                          "weight_exact": c.get("weight_exact", "1"), "complex_control": c.get("complex_control")}
                                         for r, c in pairs if c["status"] == "scored"] if item.startswith("M") else [],
                }
            dimensions = {}
            for key, dimension in DIMENSIONS.items():
                eligible = [i for i in dimension["items"] if not items[i]["excluded_as_inapplicable"]]
                values = [Fraction(items[i]["observed_exact"]) for i in eligible if items[i]["observed_exact"] is not None]
                base_values = [Fraction(items[i]["unweighted_exact"]) for i in eligible if items[i]["unweighted_exact"] is not None]
                value = sum(values) / len(values) if values else None
                baseline = sum(base_values) / len(base_values) if base_values else None
                complete = bool(values) and len(values) == len(eligible) and all(items[i]["unknown_goals"] == 0 and not items[i]["weight_pending"] for i in eligible)
                dimensions[key] = {
                    "name": dimension["name"], "observed_score": display(value), "observed_exact": str(value) if value is not None else None,
                    "unweighted_score": display(baseline), "unweighted_exact": str(baseline) if baseline is not None else None,
                    "covered_items": len(values), "applicable_items": len(eligible), "expected_items": 3,
                    "excluded_items": [i for i in dimension["items"] if i not in eligible],
                    "fully_observed": complete,
                    "qualification": "适用子项等权的观察分；无机会不补分，未知及争议保留，不能外推未观察能力",
                }
            values = [d["observed_exact"] for d in dimensions.values()]
            total = sum(Fraction(v) for v in values) / 4 if all(v is not None for v in values) else None
            cm_values = [Fraction(items[i]["complex_observed_exact"]) for i in ("M1", "M2", "M3") if items[i]["complex_observed_exact"] is not None]
            complex_rows = [r for r in group if r["task_class"] == "C2"]
            summary.append({
                "user_id": user, "period": period, "active_goals": len(group), "items": items, "dimensions": dimensions,
                "overall_score": display(total), "overall_exact": str(total) if total is not None else None,
                "rating": None, "rating_status": "待复核" if period["kind"] == "monthly" else "本期不评级",
                "material_sufficient": bool(group) and all(d["fully_observed"] for d in dimensions.values())
                    and all(r.get("semantic_complete", False) and r["boundary"] == "confirmed" for r in group),
                "semantic_review_complete": bool(group) and all(r.get("semantic_complete", False)
                    and not r.get("cell_errors") and all(c['status'] not in {'pending_review', 'review_failed'}
                    for c in r['cells']) for r in group),
                "task_classes": dict(Counter(r["task_class"] for r in group)), "complex_goals": len(complex_rows),
                "complex_handling": dict(Counter(r["complex_handling"] for r in complex_rows)),
                "complex_scored_goals": sum(r.get("complex_m_exact") is not None for r in complex_rows),
                "complex_covered_items": len(cm_values),
                "observed_complex_m_score": display(sum(cm_values) / len(cm_values)) if cm_values else None,
                "critical_control_failures": sum(r["critical_control_failure"] for r in group),
            })
    return summary


def qualify_ratings(summary, audit_status, unresolved_users, unresolved_ingest):
    """每个人独立检查发布条件；其他用户的问题不牵连已闭合用户。"""
    for row in summary:
        audit_closed = audit_status.get("users", {}).get(row["user_id"], {}).get("closed", False)
        row["semantic_audit_closed"] = audit_closed
        row["evaluation_complete"] = row["material_sufficient"] and audit_closed and row["user_id"] not in unresolved_users and not unresolved_ingest
        if row["period"]["kind"] == "monthly":
            row["rating_status"] = "已形成试评月级" if row["evaluation_complete"] else "观察中"
            if row["evaluation_complete"] and row["overall_exact"] is not None:
                exact = Fraction(row["overall_exact"])
                row["rating"] = level_for(exact)
                if level_for(Fraction(row["overall_score"])) != row["rating"]:
                    row["overall_score"] = str((Decimal(exact.numerator) / Decimal(exact.denominator)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
