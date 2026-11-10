"""固定语义提交的校验与逐目标轨迹计分；不猜测语义结论。"""

from fractions import Fraction

from .common import Evidence, ContractError, require, digest, encoded, display, review_context, moment, valid_span
from .policy import VERSION, ITEMS, STATES, CLAIMS, RESPONSIBILITY, FEEDBACK_REQUIRED


def coverage_check(packet, coverage):
    all_ids = {e["event_id"] for e in packet["events"]}
    accounted = coverage["accounted_event_ids"]
    read, unread = coverage["read_event_ids"], coverage["unread_event_ids"]
    excluded = coverage["excluded_events"]
    omitted = [e["event_id"] for e in excluded]
    require(len(accounted) == len(set(accounted)) and set(accounted) == all_ids, "未全量机械对账")
    require(all(e.get("reason", "").strip() for e in excluded), "排除上下文须逐事件说明依据")
    combined = read + unread + omitted
    require(len(combined) == len(set(combined)) and set(combined) == all_ids, "实际阅读/排除/未读须互斥且覆盖全包")
    require(not set(packet["active_human_event_ids"]) & set(omitted), "当期本人材料不能以无关上下文排除")
    require(coverage["method"] in {"semantic", "partial", "structural_only"}, "须区分语义阅读、部分阅读和机械对账")
    require(bool(coverage.get("opportunity_audit")), "缺逐子项机会审查说明")
    partial = coverage.get("partial_events", [])
    require(len({p["event_id"] for p in partial}) == len(partial), "部分阅读事件重复")
    events = {e["event_id"]: e for e in packet["events"]}
    for part in partial:
        require(part["event_id"] in events and part.get("reason"), "部分阅读须定位事件并说明范围")
        require(part["event_id"] in unread, "部分阅读不能并入全文已读集合")
        for start, end in part.get("read_spans", []):
            require(valid_span(events[part["event_id"]]["text"], start, end), "部分阅读字符范围越界")
    require(coverage["method"] != "semantic" or not partial, "部分阅读不得自动升级semantic")
    complete = coverage["method"] == "semantic" and not unread and set(packet["active_human_event_ids"]) <= set(read)
    return complete, set(read)


def complex_control(cell, task_class, relations, ev):
    if not cell["item"].startswith("M"):
        return {"status": "none", "weight_exact": "1", "relation_ids": []}
    data = cell["complex_control"]
    require(data["status"] in {"none", "confirmed", "unresolved"} and bool(data.get("reason")), "复杂机会缺状态/理由")
    ids, opportunities = data["relation_ids"], data["opportunity_ids"]
    require(len(ids) == len(set(ids)) and all(r in relations for r in ids), "复杂机会关系不存在或重复")
    require(len(opportunities) == len(set(opportunities)) and set(opportunities) <= {o["id"] for o in cell["opportunities"]}, "复杂机会引用本项未知机会")
    ev.refs(data["evidence_ids"], needed=data["status"] == "confirmed")
    if data["status"] == "confirmed":
        require(task_class == "C2" and ids and opportunities, "复杂加权须有C2关系及本项实际机会，不能仅凭成功")
    if data["status"] == "none":
        require(not ids and not opportunities, "普通权重不能携带已确认复杂机会")
    require(task_class != "CU" or cell["status"] != "scored" or data["status"] != "none", "复杂性未知不能静默当作普通项")
    return {"status": data["status"], "weight_exact": {"none": "1", "confirmed": "3/2", "unresolved": None}[data["status"]],
            "relation_ids": ids, "reason": data["reason"]}


def claim_value(claim, ev):
    require(claim["value"] in {"yes", "no", "unknown"} and bool(claim.get("reason")), "条件缺状态或理由")
    ev.refs(claim["evidence_ids"], needed=claim["value"] == "yes")
    return claim["value"]


def confirmed_feedback(cell, ev, observations):
    own = ev.refs(sorted({eid for oid in cell["observation_ids"] for eid in observations[oid]["evidence_ids"]}))
    links = cell.get("feedback_links", [])
    # 旧引用字段只提供定位，语义关系必须显式提交，不能按最近消息自动生成。
    require(bool(links), "实际反馈须提交行为与反馈的明确配对及理由")
    linked = set()
    for link in links:
        require(link.get("reason") and link.get("relation") in {"execution_result", "result_disposition", "reuse_result", "recovery_result"}, "反馈配对缺具体关系或理由")
        actions = ev.refs(link["action_evidence_ids"], needed=True)
        feedback = ev.refs(link["feedback_evidence_ids"], needed=True)
        require(all(a["id"] in {o["id"] for o in own} for a in actions), "反馈配对行为须属于本项主归")
        require(all(f["id"] in cell["feedback_evidence_ids"] for f in feedback), "反馈配对与子项引用不一致")
        for proof in feedback:
            require(proof["kind"] in {"person_action", "person_report", "artifact", "verification_result"} and not proof["source_uncertain"], "反馈须为可确认的实际事实")
            require(all(moment(ev.events[proof["event_id"]]["at"]) >= moment(ev.events[a["event_id"]]["at"]) for a in actions), "反馈早于所支持的行为，不能按时序错配")
            require(all(proof["event_id"] != a["event_id"] or proof["end"] <= a["start"] or a["end"] <= proof["start"] for a in actions), "同一片段不能兼作行为和独立反馈")
            linked.add(proof["id"])
    require(linked == set(cell["feedback_evidence_ids"]), "反馈字段含未说明关系的引用")
    # 行为与反馈的独立性由上面的显式配对逐一验证。
    # 同一反馈还可佐证行为已经发生，不能因此把佐证引用误当成本人行为本身。
    return bool(linked)


def complexity_check(data, ev):
    value = data["class"]
    require(value in {"C0", "C1", "C2", "CU"} and bool(data.get("reason")), "复杂度缺标签或理由")
    ev.refs(data["evidence_ids"], needed=value != "CU")
    relations = {}
    for relation in data["relations"]:
        require(relation["id"] not in relations and relation["type"] in {"D", "U", "T", "V"}, "复杂关系ID或类型错误")
        require(all(relation.get(k) for k in ("condition_a", "condition_b", "interaction", "routine_insufficient", "decision_affected")), "复杂关系描述不完整")
        ev.refs(relation["evidence_ids"], needed=True,
                kinds={"person_input", "person_report", "artifact", "verification_result", "context"})
        relations[relation["id"]] = relation
    require((value == "C2") == bool(relations), "C2须有真实关系；其他标签不能携带已确认复杂关系")
    return value, relations


def band_for(cell, ev, observations):
    status = cell["status"]
    require(cell["item"] in ITEMS and status in STATES and bool(cell.get("reason")), "子项、状态或理由错误")
    require("band" not in cell, "不得自由填写band")
    own = cell["observation_ids"]
    related = cell["related_observation_ids"]
    require(len(own) == len(set(own)) and len(related) == len(set(related)), "行为引用重复")
    require(all(x in observations and observations[x]["primary_item"] == cell["item"] for x in own), "行为跨子项重复计分")
    require(all(x in observations and observations[x]["primary_item"] != cell["item"] for x in related), "关联行为须指向别项主归")
    opportunities = cell["opportunities"]
    require(len({o["id"] for o in opportunities}) == len(opportunities), "机会ID重复")
    for opportunity in opportunities:
        require(bool(opportunity["description"]) and opportunity["result"] in {"handled", "gap", "unknown"}, "机会描述或处置错误")
        ev.refs(opportunity["evidence_ids"], needed=True)
        ev.refs(opportunity["result_evidence_ids"], needed=opportunity["result"] != "unknown")
    if status == "related_only":
        require(related and not own and opportunities, "关联归别项须有实际机会及主归行为，不能伪装无机会")
    if status in {"not_applicable", "no_observed_opportunity"}:
        require(not opportunities and not own and not related, "已有机会或行为不能记不适用/无机会")
    if status != "scored":
        return None
    require(own and opportunities, "可评项缺本人行为或具体机会")
    require(set(cell["claims"]) == set(CLAIMS), "语义条件不完整")
    flags = {k: claim_value(cell["claims"][k], ev) for k in CLAIMS}
    require(set(cell["responsibility"]) == set(RESPONSIBILITY), "归责条件不完整")
    responsibility = {k: claim_value(cell["responsibility"][k], ev) for k in RESPONSIBILITY}
    ev.refs(cell["feedback_evidence_ids"])
    low = any(flags[k] == "yes" for k in CLAIMS[:3])
    if low:
        require(all(v == "yes" for v in responsibility.values()), "低档缺知情、可控、处理机会依据")
        require(all(flags[k] != "yes" for k in ("adequate", "reasoned_control", "closed_loop")), "必要控制失效与充分处理条件矛盾")
        require(any(o["result"] == "gap" for o in opportunities), "低档缺具体未妥善处理机会")
        if cell["item"].startswith("E"):
            data = cell.get("inefficiency", {})
            require(all(data.get(k) for k in ("avoidable_behavior", "unnecessary_at_time", "feasible_alternative")), "效率低档缺不必要性或可行替代")
            ev.refs(data["evidence_ids"], needed=True)
        if flags["deliberate_breach"] == "yes":
            return 0
        if flags["major_failure"] == "yes":
            return 40
        require(flags["basic_effective"] == "yes", "60档缺正面有效处理")
        return 60
    require(all(flags[k] == "no" for k in CLAIMS[:3]), "反面事实未确定，应待复核而非默认高分")
    require(all(o["result"] == "handled" for o in opportunities), "高档存在未处理/未知的必要机会")
    require(flags["basic_effective"] == "yes" and flags["adequate"] == "yes", "不因没看到错误就默认80")
    # P评价输入本身；其他项须有实际跟进/反馈，AI完成声明不能独立证明闭环。
    if cell["item"] in FEEDBACK_REQUIRED:
        require(confirmed_feedback(cell, ev, observations), "实际处理项缺独立可确认反馈，不能把同一要求重标person_action充当执行结果")
    if flags["reasoned_control"] != "yes":
        require(flags["closed_loop"] != "yes", "100缺90前提")
        return 80
    if flags["closed_loop"] != "yes":
        return 90
    if not cell["item"].startswith("P"):
        require(confirmed_feedback(cell, ev, observations), "完整过程闭环须有独立可确认反馈，AI自报或缺时间不能证明")
    closure = cell["closure"]
    require(len(closure) == 5 and {c["check"] for c in closure} == {"scope", "basis", "action", "feedback", "boundary"}, "100档闭环清单不完整")
    for check in closure:
        require(check["status"] in {"met", "not_applicable"} and bool(check.get("reason")), "100档有未决闭环")
        ev.refs(check["evidence_ids"], needed=True)
    return 100


def evaluate(packet, review, *, isolate_cell_errors=False):
    require(review.get("policy_version") == VERSION and review["packet_id"] == packet["packet_id"], "评审版本或包ID不一致")
    require(bool(review.get("reviewer_id")), "评审者身份缺失")
    context = review_context(review.get("review_context"))
    coverage = review["review_coverage"]
    semantic_complete, read_ids = coverage_check(packet, coverage)
    ev = Evidence(packet, review["evidence"])
    partial_ranges = {p['event_id']: p.get('read_spans', []) for p in coverage.get('partial_events', [])}
    require(all(e["event_id"] in read_ids or any(a <= e['start'] < e['end'] <= b for a, b in partial_ranges.get(e['event_id'], []))
                for e in review["evidence"]), "证据引用了未实际语义阅读的事件或字符范围")
    task_class, relations = complexity_check(review["complexity"], ev)
    observations, signatures, facts = {}, {}, set()
    for observation in review["observations"]:
        key = observation["id"]
        require(key not in observations and observation["primary_item"] in ITEMS, "行为ID或主要归属错误")
        fact = observation["fact"].strip()
        require(fact and fact not in facts, "重复本人行为主张")
        require(ev.period_human(observation["scope_event_id"]), "缺本期本人活动依据，不得把旧周行为重复算新周")
        records = ev.refs(observation["evidence_ids"], needed=True, kinds={"person_input", "person_action", "person_report"})
        require(any(e["event_id"] == observation["scope_event_id"] and e["kind"].startswith("person_") for e in records), "本期锚点未被引用为本人证据")
        for proof in records:
            if not proof["kind"].startswith("person_"):
                continue
            signature = ev.signature(proof)
            if signature in signatures:
                require(bool(observation.get("shared_span_justification"))
                        and all(o.get("shared_span_justification") for o in signatures[signature]), "同引文不同主张须说明独立事实差异并复核")
            signatures.setdefault(signature, []).append(observation)
        observations[key] = observation
        facts.add(fact)
    cells = review["cells"]
    require(len(cells) == len(ITEMS) and {c["item"] for c in cells} == set(ITEMS), "必须逐项处置PMJE全部12项")
    require(semantic_complete or all(c["status"] != "scored" for c in cells)
            or isolate_cell_errors and coverage['method'] == 'partial', "机械对账或未读完相关材料不能生成个人分")
    require(packet["boundary"] == "confirmed" or all(c["status"] != "scored" for c in cells), "父目标边界未定，不能先给个人分")
    cell_results, cell_errors = [], []
    used = set()
    for cell in cells:
        try:
            band = band_for(cell, ev, observations)
            control = complex_control(cell, task_class, relations, ev)
            if band is not None:
                owned = {ev.signature(e) for oid in cell["observation_ids"] for e in ev.refs(observations[oid]["evidence_ids"])}
                for key in ("reasoned_control", "closed_loop"):
                    if cell["claims"][key]["value"] == "yes":
                        proofs = ev.refs(cell["claims"][key]["evidence_ids"])
                        require(any(e["kind"].startswith("person_") and ev.signature(e) in owned for e in proofs), "高档claim须锚定本项主归本人事实，不能借别项依据")
                        for proof in proofs:
                            if proof["kind"].startswith("person_"):
                                owners = signatures.get(ev.signature(proof), [])
                                if any(o["primary_item"] != cell["item"] for o in owners):
                                    require(all(o.get("shared_span_justification") for o in owners), "跨项高档claim共享本人事实须说明独立差异并复核")
        except (ContractError, KeyError, TypeError, ValueError) as exc:
            if not isolate_cell_errors:
                raise
            # 单项执行失败不抹去同包其他合法判断；失败项仍占未知分母并阻断发布。
            cell_errors.append({"item": cell["item"], "reason": str(exc)})
            cell_results.append({"item": cell["item"], "status": "review_failed", "band": None,
                                 "reason": "评审合同未闭合：" + str(exc), "complex_control": {"status": "unresolved", "weight_exact": None, "relation_ids": []},
                                 "weight_exact": None, "opportunity_count": len(cell.get("opportunities", [])), "unobserved_upper_anchors": []})
            used.update(cell["observation_ids"])
            continue
        used.update(cell["observation_ids"])
        cell_results.append({"item": cell["item"], "status": cell["status"], "band": band, "reason": cell["reason"],
                             "complex_control": control, "weight_exact": control["weight_exact"],
                             "opportunity_count": len(cell["opportunities"]),
                             "unobserved_upper_anchors": [k for k in ("reasoned_control", "closed_loop") if cell.get("claims", {}).get(k, {}).get("value") == "unknown"]})
    require(used == set(observations), "本人行为有未进入本项处置的悬空记录")
    handling = review["complex_handling"]
    require(handling["status"] in {"supported", "unknown", "not_observed", "not_applicable"} and bool(handling.get("reason")), "复杂关系处理缺处置")
    relation_ids = handling["relation_ids"]
    action_ids = handling["observation_ids"]
    require(len(relation_ids) == len(set(relation_ids)) and all(r in relations for r in relation_ids), "复杂处理引用未知关系")
    require(len(action_ids) == len(set(action_ids)) and all(a in observations and observations[a]["primary_item"].startswith("M") for a in action_ids), "复杂任务驾驭须有M项本人行为，J关联不另加M分")
    if task_class != "C2":
        require(handling["status"] == "not_applicable" and not relation_ids and not action_ids, "非C2不生成复杂驾驭表现")
    if handling["status"] == "supported":
        require(task_class == "C2" and relation_ids and action_ids, "复杂处理缺真实关系或本人行为")
        require(set(relation_ids) == set(relations), "完整复杂驾驭不能只选已处理的部分关键关系")
        proofs = ev.refs(handling["feedback_evidence_ids"], needed=True, kinds={"person_report", "artifact", "verification_result", "person_action"})
        require(any(p["kind"] != "assistant_claim" and not p["source_uncertain"] for p in proofs), "复杂处理反馈配对/时间未知")
    m_cells = [c for c in cell_results if c["item"].startswith("M")]
    complex_cells = [c for c in m_cells if c["status"] == "scored" and c["complex_control"]["status"] == "confirmed"]
    cm = sum(Fraction(c["band"]) for c in complex_cells) / len(complex_cells) if complex_cells else None
    for note in review["outcome_notes"]:
        require(bool(note["statement"]), "成果记录为空")
        ev.refs(note["evidence_ids"], needed=True, kinds={"person_report", "artifact", "verification_result"})
    return {"policy_version": VERSION, "packet_id": packet["packet_id"], "user_id": packet["user_id"],
            "goal_id": packet["goal_id"], "title": packet["title"], "period_id": packet["period_id"],
            "boundary": packet["boundary"], "review_status": "partial" if cell_errors else "submitted", "cell_errors": cell_errors,
            "reviewer_id": review["reviewer_id"],
            "review_context": context, "semantic_complete": semantic_complete,
            "score_scope": "complete_review" if semantic_complete else "read_evidence_draft",
            "review_sha256": digest(encoded(review)), "task_class": task_class,
            "cells": cell_results, "complex_handling": handling["status"], "complex_m_exact": str(cm) if cm is not None else None,
            "complex_m_score": display(cm), "critical_control_failure": any(c["band"] in (0, 40) for c in cell_results),
            "complex_covered_items": len(complex_cells),
            "shared_evidence": any(len(v) > 1 for v in signatures.values()),
            "source_limitations": any(e["truncated"] or e["response_ambiguous"] or e.get("response_timestamp_unknown", False) or e.get("source_snapshot_conflict", False) for e in packet["events"]),
            "observation_facts": [{"fact": o["fact"], "primary_item": o["primary_item"], "scope_event_id": o["scope_event_id"],
                                    "signatures": [list(ev.signature(e)) for e in ev.refs(o["evidence_ids"]) if e["kind"].startswith("person_")]} for o in observations.values()],
            "outcome_notes": review["outcome_notes"]}
