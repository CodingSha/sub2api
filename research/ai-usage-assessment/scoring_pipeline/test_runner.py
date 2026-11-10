"""流程失效边界回归；合成检查不验证真人语义、岗位公平性或量表效度。"""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from .common import ContractError, encoded, rows, write_rows, moment, display
from .examples import source_record, plan_for, review_for, audits_for
from .policy import ITEMS, CLAIMS, RESPONSIBILITY
from .review import evaluate
from .runner import prepare, packets, load_run, periods_for, inspect_reviews, read_events
from .reporting import check_or_score, selected


class PipelineTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        write_rows(self.source / "demo_at_apsat.com.jsonl", [source_record()])
        self.run = self.root / "run"
        prepare(self.source, self.run, ["demo"], "2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00", "window")
        self.events = rows(self.run / "events.jsonl")
        self.plan = plan_for(self.events)
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_bytes(encoded(self.plan))
        packets(self.run, self.plan_path)
        self.packet = rows(self.run / "packets/packets.jsonl")[0]
        self.review = review_for(self.packet)
        self.review_path = self.root / "reviews.jsonl"

    def evaluate(self):
        return evaluate(self.packet, self.review)

    def cell(self, item):
        return next(c for c in self.review["cells"] if c["item"] == item)

    def proof(self, item):
        return next(e for e in self.review["evidence"] if e["id"] == item)

    def low(self, item, claim):
        cell = self.cell(item)
        for key in CLAIMS:
            cell["claims"][key]["value"] = "yes" if key in {claim, "basic_effective"} else "no"
        for key in RESPONSIBILITY:
            cell["responsibility"][key] = {"value": "yes", "evidence_ids": [item], "reason": "合成归责事实"}
        cell["opportunities"][0]["result"] = "gap"
        return cell

    def c2(self):
        self.review["complexity"] = {"class": "C2", "reason": "合成耦合", "evidence_ids": ["M2"],
            "relations": [{"id": "r1", "type": "D", "condition_a": "旧接口", "condition_b": "新内核", "interaction": "替换影响兼容",
                           "routine_insufficient": "直接覆盖丢失协议", "decision_affected": "迁移顺序", "evidence_ids": ["P1", "M2-feedback"]}]}
        self.review["complex_handling"] = {"status": "supported", "reason": "合成处理及反馈", "relation_ids": ["r1"],
                                          "observation_ids": ["M2"], "feedback_evidence_ids": ["M2-feedback"]}
        self.cell("M2")["complex_control"] = {"status": "confirmed", "relation_ids": ["r1"], "opportunity_ids": ["op-M2"],
                                              "evidence_ids": ["M2"], "reason": "合成依赖形成本人本项机会"}

    def save_review(self):
        write_rows(self.review_path, [self.review])

    def audit_file(self, checks):
        path = self.root / "audits.jsonl"
        write_rows(path, audits_for(rows(checks / "audit-queue.jsonl"), self.events))
        return path

    def replan(self, plan):
        destination = self.root / "new-run"
        prepare(self.source, destination, ["demo"], "2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00", "window")
        path = self.root / "new-plan.json"
        path.write_bytes(encoded(plan))
        return packets(destination, path)

    def test_full_four_dimension_trial_closes_only_after_audit(self):
        self.save_review()
        draft = check_or_score(self.run, self.review_path, self.root / "checks")
        self.assertFalse(draft["trial_complete"])
        result = check_or_score(self.run, self.review_path, self.root / "scored", audit_path=self.audit_file(self.root / "checks"), report=True)
        self.assertTrue(result["trial_complete"])
        self.assertFalse(result["production_ready"])
        data = json.loads((self.root / "scored/report-data.json").read_text())
        self.assertEqual(set(data["summary"][0]["dimensions"]), {"P", "M", "J", "E"})
        self.assertTrue(all(d["observed_score"] == "80.0" for d in data["summary"][0]["dimensions"].values()))
        self.assertIsNone(data["overall_score"])
        self.assertIsNone(data["rating"])

    def test_reader_case_keeps_feedback_when_action_has_many_quotes(self):
        from .reader_report import attach_reader_evidence
        from .aggregation import summarize
        result = self.evaluate()
        observation = next(o for o in self.review['observations'] if o['id'] == 'M3')
        observation['evidence_ids'] += ['P1', 'P2']
        manifest, _ = load_run(self.run)
        summary = summarize([result], ['demo'], manifest['periods'])
        attach_reader_evidence(summary, {self.packet['packet_id']: self.packet}, {self.packet['packet_id']: self.review}, [result])
        evidence = summary[0]['reader_items']['M3']['cases'][0]['evidence']
        self.assertIn(self.proof('M3-feedback')['event_id'], [p['event_id'] for p in evidence])

    def test_frozen_cli_runs_from_unrelated_directory(self):
        demo = self.root / "cli-demo"
        result = subprocess.run(["python3", str(self.run / "engine/run_scoring.py"), "demo", "--output", str(demo)], cwd=str(self.root), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["trial_complete"])
        result = subprocess.run(["python3", str(demo / "run/engine/run_scoring.py"), "verify", "--run", str(demo / "run")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_cell_cannot_become_inapplicable(self):
        self.review["cells"].pop()
        with self.assertRaises(ContractError): self.evaluate()

    def test_model_cannot_assign_free_band(self):
        self.cell("J1")["band"] = 100
        with self.assertRaises(ContractError): self.evaluate()

    def test_one_behavior_has_one_primary_item(self):
        self.cell("M3")["observation_ids"] = ["J1"]
        with self.assertRaises(ContractError): self.evaluate()

    def test_related_opportunity_retained_without_double_scoring(self):
        self.review["observations"] = [o for o in self.review["observations"] if o["id"] != "E2"]
        self.cell("E2").update(status="related_only", observation_ids=[], related_observation_ids=["J1"])
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "out", report=True)
        row = json.loads((self.root / "out/report-data.json").read_text())["summary"][0]
        self.assertEqual(row["dimensions"]["E"]["observed_score"], "80.0")
        self.assertEqual(row["dimensions"]["E"]["excluded_items"], ["E2"])
        self.assertEqual(row["items"]["E1"]["observed_score"], "80.0")
        self.assertEqual(row["items"]["E2"]["status_counts"], {"related_only": 1})

    def test_existing_opportunity_cannot_be_no_opportunity(self):
        self.cell("J2")["status"] = "no_observed_opportunity"
        with self.assertRaises(ContractError): self.evaluate()

    def test_high_claim_failure_preserves_other_cell_scores(self):
        self.cell('J1')['claims']['reasoned_control'] = {
            'value': 'yes', 'evidence_ids': ['P1'], 'reason': '故意借用其他子项的事实'}
        result = evaluate(self.packet, self.review, isolate_cell_errors=True)
        states = {c['item']: c for c in result['cells']}
        self.assertEqual(states['J1']['status'], 'review_failed')
        self.assertEqual(states['P1']['band'], 80)
        self.assertTrue(result['cell_errors'])

    def test_unresolved_limitation_does_not_close_trial(self):
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / 'checks-limits')
        audits = self.audit_file(self.root / 'checks-limits')
        submitted = rows(audits)
        submitted[0]['verdict'] = 'confirmed_with_limits'
        submitted[0]['findings'][0]['impact'] = 'limitation'
        submitted[0]['findings'][0]['statement'] = '必要反馈仍需补正，尚未解决'
        write_rows(audits, submitted)
        data = check_or_score(self.run, self.review_path, self.root / 'limited-result', audit_path=audits)
        self.assertFalse(data['trial_complete'])
        self.assertGreater(data['audit_status']['open_findings'], 0)

    def test_system_or_agent_cannot_be_person(self):
        self.packet["materials"][0].update(classification="auxiliary", human_spans=[])
        with self.assertRaises(ContractError): self.evaluate()

    def test_person_span_does_not_include_system_fragment(self):
        self.packet["materials"][0]["human_spans"] = [[0, 4]]
        with self.assertRaises(ContractError): self.evaluate()

    def test_wrong_quote_and_future_event_rejected(self):
        self.proof("P1")["quote"] = "虚构引文"
        with self.assertRaises(ContractError): self.evaluate()
        self.proof("P1").pop("quote")
        self.proof("P1")["event_id"] = "future-event"
        with self.assertRaises(ContractError): self.evaluate()

    def test_old_week_action_is_only_context(self):
        self.packet["period_start"] = "2026-09-07T00:00:00+08:00"
        with self.assertRaises(ContractError): self.evaluate()

    def test_entire_packet_must_be_accounted_for(self):
        self.review["review_coverage"]["read_event_ids"].pop()
        with self.assertRaises(ContractError): self.evaluate()

    def test_ai_completion_claim_is_not_feedback(self):
        self.proof("J3-feedback")["kind"] = "assistant_claim"
        with self.assertRaises(ContractError): self.evaluate()

    def test_independent_feedback_can_also_support_observation(self):
        observation = next(o for o in self.review['observations'] if o['id'] == 'M3')
        observation['evidence_ids'].append('M3-feedback')
        result = self.evaluate()
        self.assertEqual(next(c for c in result['cells'] if c['item'] == 'M3')['band'], 80)

    def test_feedback_overlap_with_declared_action_is_still_rejected(self):
        observation = next(o for o in self.review['observations'] if o['id'] == 'M3')
        observation['evidence_ids'].append('M3-feedback')
        self.cell('M3')['feedback_links'][0]['action_evidence_ids'] = ['M3-feedback']
        with self.assertRaisesRegex(ContractError, '同一片段不能兼作行为和独立反馈'):
            self.evaluate()

    def test_unknown_response_time_cannot_prove_process_closure(self):
        event_id = self.proof("J3-feedback")["event_id"]
        next(e for e in self.packet["events"] if e["event_id"] == event_id)["response_timestamp_unknown"] = True
        self.proof("J3-feedback")["limitation"] = "响应实际时点未知"
        with self.assertRaises(ContractError): self.evaluate()

    def test_conflicting_source_snapshots_cannot_be_personal_activity_anchor(self):
        self.packet["events"][0]["source_snapshot_conflict"] = True
        with self.assertRaises(ContractError): self.evaluate()

    def test_bounded_reader_uses_python_characters_and_reports_remaining_text(self):
        event = self.events[0]
        result = read_events(self.run, [event["event_id"]], limit=1, start=2, max_characters=5)
        viewed = result["events"][0]
        self.assertEqual(viewed["text_excerpt"], event["text"][2:7])
        self.assertTrue(viewed["has_more"])
        self.assertEqual(viewed["excerpt_end"], 7)

    def test_bounded_reader_rejects_modified_selected_event(self):
        path = self.run / "events.jsonl"
        with path.open("r+b") as target:
            target.write(b"!")
        with self.assertRaises(ContractError): read_events(self.run, [self.events[0]["event_id"]])

    def test_p_can_be_evaluated_without_final_artifact(self):
        self.cell("P1")["feedback_evidence_ids"] = []
        self.assertEqual(self.evaluate()["cells"][0]["band"], 80)

    def test_low_band_requires_personal_control(self):
        cell = self.low("J3", "major_failure")
        cell["responsibility"]["person_control"]["value"] = "unknown"
        with self.assertRaises(ContractError): self.evaluate()
        cell["responsibility"]["person_control"]["value"] = "yes"
        result = self.evaluate()
        self.assertEqual(next(c for c in result["cells"] if c["item"] == "J3")["band"], 40)
        self.assertTrue(result["critical_control_failure"])

    def test_zero_is_not_an_empty_value(self):
        self.low("J3", "deliberate_breach")
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "out", report=True)
        row = json.loads((self.root / "out/report-data.json").read_text())["summary"][0]
        self.assertEqual(row["items"]["J3"]["observed_score"], "0.0")

    def test_efficiency_low_needs_unnecessary_behavior_and_alternative(self):
        cell = self.low("E1", "necessary_gap")
        with self.assertRaises(ContractError): self.evaluate()
        cell["inefficiency"] = {"avoidable_behavior": "已知无效重复", "unnecessary_at_time": "已获有效反馈", "feasible_alternative": "使用已有定位", "evidence_ids": ["E1"]}
        self.assertEqual(next(c for c in self.evaluate()["cells"] if c["item"] == "E1")["band"], 60)

    def test_sixty_requires_positive_effective_fact(self):
        self.low("J3", "necessary_gap")["claims"]["basic_effective"]["value"] = "no"
        with self.assertRaises(ContractError): self.evaluate()

    def test_negative_unknown_does_not_default_to_eighty(self):
        self.cell("J1")["claims"]["major_failure"]["value"] = "unknown"
        with self.assertRaises(ContractError): self.evaluate()

    def test_unknown_upper_anchor_preserves_supported_eighty(self):
        cell = self.evaluate()["cells"][0]
        self.assertEqual(cell["band"], 80)
        self.assertEqual(cell["unobserved_upper_anchors"], ["reasoned_control", "closed_loop"])

    def test_unknown_necessary_opportunity_prevents_high_score(self):
        self.cell("M2")["opportunities"][0]["result"] = "unknown"
        with self.assertRaises(ContractError): self.evaluate()

    def test_simple_task_can_score_hundred_without_transfer(self):
        cell = self.cell("P1")
        cell["claims"]["reasoned_control"]["value"] = "yes"
        cell["claims"]["closed_loop"]["value"] = "yes"
        cell["closure"] = [{"check": key, "status": "met", "reason": "实际适用条件闭合", "evidence_ids": ["P1"]} for key in ("scope", "basis", "action", "feedback", "boundary")]
        self.assertEqual(self.evaluate()["cells"][0]["band"], 100)
        cell["closure"][0]["status"] = "unknown"
        with self.assertRaises(ContractError): self.evaluate()

    def test_c2_requires_condition_relation(self):
        self.review["complexity"]["class"] = "C2"
        with self.assertRaises(ContractError): self.evaluate()
        self.c2()
        self.assertEqual(self.evaluate()["task_class"], "C2")

    def test_complex_mastery_reuses_m_and_not_j_scores(self):
        self.c2()
        self.assertEqual(self.evaluate()["complex_m_score"], "80.0")
        self.review["complex_handling"]["observation_ids"] = ["J1"]
        with self.assertRaises(ContractError): self.evaluate()

    def test_complex_exposure_is_not_mastery(self):
        self.c2()
        self.review["complex_handling"].update(status="unknown", observation_ids=[], relation_ids=[], feedback_evidence_ids=[])
        result = self.evaluate()
        self.assertEqual(result["task_class"], "C2")
        self.assertEqual(result["complex_m_score"], "80.0")
        self.assertEqual(result["complex_covered_items"], 1)

    def test_missing_complex_item_not_renormalized(self):
        self.c2()
        self.review["observations"] = [o for o in self.review["observations"] if o["id"] != "M3"]
        self.cell("M3").update(status="unobservable", observation_ids=[])
        self.assertEqual(self.evaluate()["complex_covered_items"], 1)

    def test_independent_facets_in_same_quote_need_justification(self):
        first, second = self.review["observations"][:2]
        second.update(evidence_ids=first["evidence_ids"], scope_event_id=first["scope_event_id"])
        with self.assertRaises(ContractError): self.evaluate()
        first["shared_span_justification"] = "目标侧面"
        second["shared_span_justification"] = "独立信息侧面，需语义复核"
        self.assertTrue(self.evaluate()["shared_evidence"])

    def test_same_fact_is_not_another_observation(self):
        self.review["observations"][1]["fact"] = self.review["observations"][0]["fact"]
        with self.assertRaises(ContractError): self.evaluate()

    def test_unresolved_goal_cannot_receive_scores(self):
        self.packet["boundary"] = "unresolved"
        with self.assertRaises(ContractError): self.evaluate()

    def test_truncation_requires_limitation(self):
        self.packet["events"][0]["truncated"] = True
        with self.assertRaises(ContractError): self.evaluate()
        self.proof("P1")["limitation"] = "仅支持可见片段"
        self.assertTrue(self.evaluate()["source_limitations"])

    def test_missing_reviews_stay_in_all_twelve_denominators(self):
        self.review_path.write_text("")
        result = check_or_score(self.run, self.review_path, self.root / "out", report=True)
        self.assertFalse(result["structural_complete"])
        row = json.loads((self.root / "out/report-data.json").read_text())["summary"][0]
        self.assertEqual(row["active_goals"], 1)
        self.assertEqual(row["items"]["J1"]["status_counts"], {"review_failed": 1})
        self.assertIsNone(row["dimensions"]["J"]["observed_score"])

    def test_audit_cannot_be_self_review_or_stale_hash(self):
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "checks")
        path = self.audit_file(self.root / "checks")
        audits = rows(path)
        audits[0]["reviewer_id"] = "synthetic-initial-context"
        write_rows(path, audits)
        with self.assertRaises(ContractError): check_or_score(self.run, self.review_path, self.root / "out", audit_path=path)
        audits[0]["reviewer_id"] = "synthetic-second-context"
        audits[-1]["reviewed_sha256"] = "old"
        write_rows(path, audits)
        with self.assertRaises(ContractError): check_or_score(self.run, self.review_path, self.root / "out", audit_path=path)

    def test_dispute_prevents_trial_completion(self):
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "checks")
        path = self.audit_file(self.root / "checks")
        audits = rows(path)
        audits[0]["verdict"] = "disputed"
        audits[0]["findings"][0]["impact"] = "dispute"
        write_rows(path, audits)
        self.assertFalse(check_or_score(self.run, self.review_path, self.root / "out", audit_path=path)["trial_complete"])

    def test_dispute_cannot_be_hidden_as_confirmed(self):
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "checks")
        path = self.audit_file(self.root / "checks")
        audits = rows(path)
        audits[0]["findings"][0]["impact"] = "dispute"
        write_rows(path, audits)
        with self.assertRaises(ContractError): check_or_score(self.run, self.review_path, self.root / "out", audit_path=path)

    def test_audit_requires_exact_original_quote(self):
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "checks")
        path = self.audit_file(self.root / "checks")
        audits = rows(path)
        audits[0]["findings"][0]["evidence"][0]["quote"] = "虚构"
        write_rows(path, audits)
        with self.assertRaises(ContractError): check_or_score(self.run, self.review_path, self.root / "out", audit_path=path)

    def test_qa_sample_is_stable_and_keyword_independent(self):
        self.assertEqual(selected(list(range(100)), "seed", .1), selected(list(reversed(range(100))), "seed", .1))
        self.assertEqual(len(selected(list(range(100)), "seed", .1)), 10)

    def test_response_cannot_disappear_from_plan(self):
        plan = deepcopy(self.plan)
        eid = next(e["event_id"] for e in self.events if e["field"] == "response")
        plan["goals"][0]["event_ids"].remove(eid)
        with self.assertRaises(ContractError): self.replan(plan)

    def test_human_cannot_be_unassigned(self):
        plan = deepcopy(self.plan)
        eid = plan["materials"][0]["event_id"]
        plan["goals"][0]["event_ids"].remove(eid)
        plan["unassigned_events"] = [{"event_id": eid, "reason": "错误排除本人"}]
        with self.assertRaises(ContractError): self.replan(plan)

    def test_unresolved_material_and_goal_reported(self):
        plan = deepcopy(self.plan)
        plan["materials"][0].update(classification="unresolved", human_spans=[])
        plan["goals"][0]["boundary"] = "unresolved"
        self.assertEqual(self.replan(plan)["materials"]["unresolved"], 1)
        self.assertEqual(json.loads((self.root / "new-run/packets/manifest.json").read_text())["unresolved_goal_ids"], [["demo", "demo-goal"]])

    def test_duplicate_requires_identical_nonduplicate_original(self):
        plan = deepcopy(self.plan)
        plan["materials"][0].update(classification="duplicate", human_spans=[], duplicate_of=plan["materials"][1]["event_id"])
        with self.assertRaises(ContractError): self.replan(plan)

    def test_frozen_plan_and_source_cannot_drift(self):
        self.save_review()
        (self.run / "packets/plan.json").write_text("{}")
        with self.assertRaises(ContractError): inspect_reviews(self.run, self.review_path)
        (self.run / "events.jsonl").write_text("{}\n")
        with self.assertRaises(ContractError): load_run(self.run)

    def test_never_overwrite_outputs_or_packets(self):
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "checks")
        with self.assertRaises(ContractError): check_or_score(self.run, self.review_path, self.root / "checks")
        with self.assertRaises(ContractError): packets(self.run, self.plan_path)

    def test_week_windows_clip_and_do_not_overlap(self):
        periods = periods_for("2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00", "weekly")
        self.assertEqual(len(periods), 5)
        self.assertEqual(periods[0]["end"], "2026-09-07T00:00:00+08:00")
        self.assertTrue(all(a["end"] == b["start"] for a, b in zip(periods, periods[1:])))
        self.assertEqual(periods[-1]["end"], "2026-10-01T00:00:00+08:00")

    def test_timestamp_precision_and_exact_half_up_rounding(self):
        self.assertEqual(moment("2026-09-08T11:15:38.20839+08:00").microsecond, 208390)
        self.assertEqual(display(Fraction(309, 4)), "77.3")
        with self.assertRaises(ContractError): moment("2026-09-08T11:15:38")

    def test_html_escapes_source_derived_text(self):
        from .reporting import render_report
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "out", report=True)
        data = json.loads((self.root / "out/report-data.json").read_text())
        data["summary"][0]["user_id"] = "<script>alert(1)</script>"
        render_report(data, self.root / "out")
        page = (self.root / "out/report.html").read_text()
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_structural_accounting_cannot_be_personal_scoring(self):
        self.review["review_coverage"]["method"] = "structural_only"
        with self.assertRaises(ContractError): self.evaluate()

    def test_unread_human_cannot_be_excluded_as_context(self):
        coverage = self.review["review_coverage"]
        eid = coverage["read_event_ids"].pop(0)
        coverage["excluded_events"] = [{"event_id": eid, "reason": "错误排除"}]
        with self.assertRaises(ContractError): self.evaluate()

    def test_arrangement_has_own_evidence_without_final_outcome(self):
        for item in ("M1", "M2", "J1", "J2", "E1"):
            self.cell(item)["feedback_evidence_ids"] = []
        self.assertEqual(len([c for c in self.evaluate()["cells"] if c["band"] == 80]), 12)

    def test_relabeling_same_instruction_cannot_be_execution_feedback(self):
        self.cell("J3")["feedback_evidence_ids"] = ["J3"]
        with self.assertRaises(ContractError): self.evaluate()

    def test_partial_event_cannot_be_upgraded_by_event_membership(self):
        coverage = self.review['review_coverage']
        eid = self.proof('P1')['event_id']
        coverage['partial_events'] = [{'event_id': eid, 'read_spans': [[0, 4]], 'reason': '仅读首段'}]
        with self.assertRaises(ContractError): self.evaluate()

    def test_assembler_preserves_partial_even_when_all_ids_are_present(self):
        from .assembly import assemble_coverage
        eid = self.packet['events'][0]['event_id']
        result = assemble_coverage(self.packet, {'method': 'partial', 'events_read': [e['event_id'] for e in self.packet['events']],
            'events_partial': [{'event_id': eid, 'read_spans': [[0, 4]], 'reason': '只读首段'}]})
        self.assertEqual(result['method'], 'partial')
        self.assertNotIn(eid, result['read_event_ids'])
        self.assertIn(eid, result['unread_event_ids'])

    def test_later_unrelated_role_alone_cannot_make_feedback(self):
        self.cell('M3').pop('feedback_links')
        with self.assertRaises(ContractError): self.evaluate()

    def test_feedback_before_action_is_rejected(self):
        cell = self.cell('M3')
        cell['feedback_evidence_ids'] = ['P1-feedback']
        cell['feedback_links'][0]['feedback_evidence_ids'] = ['P1-feedback']
        with self.assertRaises(ContractError): self.evaluate()

    def test_single_failed_cell_keeps_other_scored_cells(self):
        self.cell('E2').pop('feedback_links')
        result = evaluate(self.packet, self.review, isolate_cell_errors=True)
        self.assertEqual(result['review_status'], 'partial')
        cells = {c['item']: c for c in result['cells']}
        self.assertEqual(cells['E2']['status'], 'review_failed')
        self.assertIsNone(cells['E2']['band'])
        self.assertEqual(cells['P1']['band'], 80)

    def test_ai_response_requires_artifact_source_basis(self):
        self.proof("J3-feedback").pop("source_basis")
        with self.assertRaises(ContractError): self.evaluate()

    def test_high_claim_must_reference_own_behavior(self):
        self.cell("M1")["claims"]["reasoned_control"].update(value="yes", evidence_ids=["J1"])
        with self.assertRaises(ContractError): self.evaluate()

    def test_complex_opportunity_weights_only_relevant_item(self):
        self.c2()
        cells = {c["item"]: c for c in self.evaluate()["cells"]}
        self.assertEqual(cells["M2"]["weight_exact"], "3/2")
        self.assertEqual(cells["M1"]["weight_exact"], "1")
        self.assertEqual(cells["J1"]["weight_exact"], "1")

    def test_complex_low_performance_is_retained(self):
        self.c2()
        self.low("M2", "major_failure")
        self.review["complex_handling"]["status"] = "unknown"
        result = self.evaluate()
        self.assertEqual(result["complex_m_score"], "40.0")
        self.assertEqual(next(c for c in result["cells"] if c["item"] == "M2")["weight_exact"], "3/2")

    def test_complex_weight_requires_actual_relation_and_opportunity(self):
        self.c2()
        self.cell("M2")["complex_control"]["opportunity_ids"] = ["not-here"]
        with self.assertRaises(ContractError): self.evaluate()

    def test_weighted_aggregation_changes_contribution_not_raw_band(self):
        from .aggregation import summarize
        ordinary = self.evaluate()
        self.c2()
        complex_row = self.evaluate()
        complex_row["goal_id"] = "second-goal"
        next(c for c in complex_row["cells"] if c["item"] == "M2")["band"] = 100
        row = summarize([ordinary, complex_row], ["demo"], [{"period_id": self.packet["period_id"], "kind": "monthly"}])[0]
        self.assertEqual(row["items"]["M2"]["observed_score"], "92.0")
        self.assertEqual(row["items"]["M2"]["unweighted_score"], "90.0")

    def test_unknown_weight_is_not_silently_treated_as_one(self):
        from .aggregation import summarize
        self.c2()
        self.cell("M2")["complex_control"]["status"] = "unresolved"
        result = self.evaluate()
        row = summarize([result], ["demo"], [{"period_id": self.packet["period_id"], "kind": "monthly"}])[0]
        self.assertIsNone(row["items"]["M2"]["observed_score"])
        self.assertIsNotNone(row["items"]["M2"]["weight_sensitivity"])
        self.assertFalse(row["material_sufficient"])

    def test_no_opportunity_excluded_but_unknown_not_excluded(self):
        from .aggregation import summarize
        self.review["observations"] = [o for o in self.review["observations"] if o["id"] != "E2"]
        self.cell("E2").update(status="no_observed_opportunity", observation_ids=[], opportunities=[])
        row = summarize([self.evaluate()], ["demo"], [{"period_id": self.packet["period_id"], "kind": "monthly"}])[0]
        self.assertEqual(row["dimensions"]["E"]["applicable_items"], 2)
        self.assertTrue(row["dimensions"]["E"]["fully_observed"])
        self.cell("E2")["status"] = "unobservable"
        row = summarize([self.evaluate()], ["demo"], [{"period_id": self.packet["period_id"], "kind": "monthly"}])[0]
        self.assertEqual(row["dimensions"]["E"]["applicable_items"], 3)
        self.assertFalse(row["dimensions"]["E"]["fully_observed"])

    def test_monthly_rating_uses_own_full_month_packet(self):
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "checks")
        check_or_score(self.run, self.review_path, self.root / "scored", audit_path=self.audit_file(self.root / "checks"), report=True)
        row = json.loads((self.root / "scored/report-data.json").read_text())["summary"][0]
        self.assertEqual(row["period"]["kind"], "monthly")
        self.assertEqual(row["overall_score"], "80.0")
        self.assertEqual(row["performance_rating"], {"id": "L3", "name": "良好"})
        self.assertIsNone(row["rating"])  # 未提交参与材料不能发布候选月级。

    def test_partial_month_cannot_be_monthly_rating_period(self):
        result = periods_for("2026-09-02T00:00:00+08:00", "2026-10-01T00:00:00+08:00", "both")
        self.assertEqual(result[-1]["kind"], "window")

    def test_week_and_month_are_separate_not_averaged(self):
        result = periods_for("2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00", "both")
        self.assertEqual(len(result), 6)
        self.assertEqual(result[-1]["kind"], "monthly")
        self.assertEqual(sum(p["kind"] == "weekly" for p in result), 5)

    def test_level_boundary_uses_unrounded_score(self):
        from .aggregation import level_for
        self.assertEqual(level_for(Fraction(8499, 100))["id"], "L3")
        self.assertEqual(level_for(Fraction(85))["id"], "L4")

    def test_changing_display_name_does_not_create_independent_auditor(self):
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "checks")
        path = self.audit_file(self.root / "checks")
        audits = rows(path)
        audits[0]["review_context"]["context_id"] = "synthetic-initial-context"
        write_rows(path, audits)
        with self.assertRaises(ContractError): check_or_score(self.run, self.review_path, self.root / "out", audit_path=path)

    def test_structural_audit_does_not_close_semantic_review(self):
        self.save_review()
        check_or_score(self.run, self.review_path, self.root / "checks")
        path = self.audit_file(self.root / "checks")
        audits = rows(path)
        audits[0]["method"] = "structural_only"
        write_rows(path, audits)
        self.assertFalse(check_or_score(self.run, self.review_path, self.root / "out", audit_path=path)["trial_complete"])

    def test_missing_spans_error_identifies_event(self):
        self.plan["materials"][0].pop("human_spans")
        with self.assertRaisesRegex(ContractError, self.plan["materials"][0]["event_id"]): self.replan(self.plan)

    def test_refreeze_keeps_source_event_ids_and_old_output(self):
        from .runner import refreeze
        destination = self.root / "refrozen"
        before = (self.run / "manifest.json").read_bytes()
        refreeze(self.run, destination)
        self.assertEqual((self.run / "manifest.json").read_bytes(), before)
        self.assertEqual((self.run / "events.jsonl").read_bytes(), (destination / "events.jsonl").read_bytes())
        self.assertFalse((destination / "plan.json").exists())
        (destination / "sources/demo_at_apsat.com.jsonl").write_text("changed")
        self.assertNotEqual((self.run / "sources/demo_at_apsat.com.jsonl").read_text(), "changed")

    def test_month_level_boundary_is_not_shown_as_next_level_score(self):
        from .aggregation import qualify_ratings
        row = {"user_id": "demo", "period": {"kind": "monthly"}, "material_sufficient": True,
               "overall_exact": "84999/1000", "overall_score": "85.0"}
        qualify_ratings([row], {"users": {"demo": {"closed": True}}}, [], 0)
        self.assertEqual(row["rating"]["id"], "L3")
        self.assertEqual(row["overall_score"], "84.99")

    def test_other_users_unresolved_material_does_not_block_closed_user(self):
        from .aggregation import qualify_ratings
        row = {"user_id": "demo", "period": {"kind": "monthly"}, "material_sufficient": True,
               "overall_exact": "80", "overall_score": "80.0"}
        qualify_ratings([row], {"users": {"demo": {"closed": True}}}, ["other"], 0)
        self.assertEqual(row["rating"]["name"], "良好")

    def test_same_text_different_requests_cannot_be_deduplicated(self):
        record = source_record()
        record["trace"].append(dict(record["trace"][0], at="2026-09-03T10:00:00+08:00"))
        write_rows(self.source / "demo_at_apsat.com.jsonl", [record])
        run = self.root / "duplicate-run"
        prepare(self.source, run, ["demo"], "2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00")
        plan = plan_for(rows(run / "events.jsonl"))
        plan["materials"][-1].update(classification="duplicate", human_spans=[], duplicate_of=plan["materials"][0]["event_id"], duplicate_reason="只有同文，没有同请求证据")
        path = self.root / "duplicate-plan.json"
        path.write_bytes(encoded(plan))
        with self.assertRaisesRegex(ContractError, "同审计请求"): packets(run, path)

    def test_platform_hint_requires_review_but_quoted_marker_can_be_human(self):
        record = source_record()
        record["trace"][0]["prompt"] = "合成用户请求：解释Automation:标记的含义，标记是引用材料。"
        write_rows(self.source / "demo_at_apsat.com.jsonl", [record])
        run = self.root / "origin-run"
        prepare(self.source, run, ["demo"], "2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00")
        plan = plan_for(rows(run / "events.jsonl"))
        path = self.root / "origin-plan.json"
        path.write_bytes(encoded(plan))
        with self.assertRaisesRegex(ContractError, "平台来源候选"): packets(run, path)
        plan["materials"][0]["origin_check"] = {"decision": "person", "reason": "本合成用例为用户引用标记提问，非平台通知"}
        path.write_bytes(encoded(plan))
        self.assertGreater(packets(run, path)["packet_count"], 0)


if __name__ == "__main__":
    unittest.main()
