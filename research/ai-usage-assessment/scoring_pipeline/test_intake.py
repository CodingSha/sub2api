"""回归真实发生过的来源丢失、同文折叠和响应配对问题。"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from .common import encoded, rows, ContractError, moment
from .intake import import_audit, entries
from .runner import prepare


class IntakeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.caches = [self.root / "a", self.root / "b"]
        for cache in self.caches:
            (cache / "demo_at_apsat.com").mkdir(parents=True)
        self.row = {"id": 123, "user_email": "demo@apsat.com", "session_id": "s1", "created_at": "2026-09-02T10:00:00+08:00",
                    "request_body": json.dumps([
                        {"request_id": "r1", "created_at": "2026-09-02T10:00:00.123456789+08:00", "content": "执行"},
                        {"request_id": "r2", "created_at": "2026-09-02T10:01:00+08:00", "content": "执行"}], ensure_ascii=False),
                    "response_body": json.dumps([
                        {"request_id": "r2", "created_at": "2026-09-02T10:01:01+08:00", "content": "第二次已经成功"},
                        {"request_id": "r1", "created_at": "2026-09-02T10:00:01+08:00", "content": "第一次失败"}], ensure_ascii=False)}
        self.write_page(self.caches[0], self.row)

    def write_page(self, cache, row):
        (cache / "demo_at_apsat.com/page-1.json").write_bytes(encoded({"data": {"items": [row]}}))

    def export(self):
        output = self.root / "export"
        result = import_audit(self.caches, output, ["demo"], "2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00")
        trace = rows(output / "demo_at_apsat.com.jsonl")[0]["trace"]
        return output, result, trace

    def test_identical_text_real_requests_and_different_responses_survive(self):
        _, result, trace = self.export()
        self.assertEqual(result["turns_preserved"], 2)
        self.assertEqual([t["prompt"] for t in trace], ["执行", "执行"])
        self.assertEqual([t["response"] for t in trace], ["第一次失败", "第二次已经成功"])

    def test_pair_by_request_id_not_response_array_order(self):
        _, _, trace = self.export()
        self.assertEqual(trace[0]["provenance"]["response_index"], 1)
        self.assertFalse(trace[0]["response_ambiguous"])

    def test_identical_cache_page_does_not_duplicate_the_same_call(self):
        self.write_page(self.caches[1], self.row)
        _, result, _ = self.export()
        self.assertEqual(result["turns_preserved"], 2)

    def test_user_may_exist_in_only_one_cache_source(self):
        (self.caches[1] / "demo_at_apsat.com").rmdir()
        _, result, _ = self.export()
        self.assertEqual(result["turns_preserved"], 2)

    def test_snapshot_disagreement_not_silently_overwritten(self):
        variant = deepcopy(self.row)
        request = json.loads(variant["request_body"])
        request[0]["content"] = "不一样的缓存版本"
        variant["request_body"] = json.dumps(request, ensure_ascii=False)
        self.write_page(self.caches[1], variant)
        _, result, trace = self.export()
        self.assertEqual(result["unresolved_ingest_count"], 1)
        self.assertEqual(sum(t.get("source_snapshot_conflict", False) for t in trace), 2)

    def test_duplicate_response_id_retains_candidates_and_unknown_pairing(self):
        reply = json.loads(self.row["response_body"])
        reply.append(dict(reply[-1], content="另一个可能的响应"))
        self.row["response_body"] = json.dumps(reply, ensure_ascii=False)
        self.write_page(self.caches[0], self.row)
        _, _, trace = self.export()
        self.assertTrue(trace[0]["response_ambiguous"])
        self.assertIn("另一个可能的响应", trace[0]["response"])

    def test_protocol_messages_are_one_call_and_roles_are_preserved(self):
        self.row["request_body"] = json.dumps([{"role": "system", "content": "系统"}, {"role": "user", "content": "本人"}], ensure_ascii=False)
        self.row["response_body"] = "原始单次响应"
        self.write_page(self.caches[0], self.row)
        _, result, trace = self.export()
        self.assertEqual(result["turns_preserved"], 1)
        self.assertIn('"role":"system"', trace[0]["prompt"])
        self.assertIn("本人", trace[0]["prompt"])

    def test_unknown_aggregated_turn_time_is_exposed(self):
        requests = json.loads(self.row["request_body"])
        requests[0].pop("created_at")
        self.row["request_body"] = json.dumps(requests, ensure_ascii=False)
        self.write_page(self.caches[0], self.row)
        _, result, _ = self.export()
        self.assertEqual(result["unresolved_ingest_count"], 1)

    def test_prepare_uses_response_time_and_keeps_source_provenance(self):
        source, _, _ = self.export()
        run = self.root / "run"
        prepare(source, run, ["demo"], "2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00")
        events = rows(run / "events.jsonl")
        self.assertEqual(events[1]["at"], "2026-09-02T10:00:01+08:00")
        self.assertEqual(events[0]["audit_id"], 123)
        self.assertTrue(events[0]["provenance"]["cache_page_sha256"])
        self.assertTrue((run / "sources/ingest-manifest.json").exists())

    def test_folded_legacy_source_is_rejected(self):
        source, _, _ = self.export()
        records = rows(source / "demo_at_apsat.com.jsonl")
        records[0]["n_repeats_folded"] = 1
        from .common import write_rows
        write_rows(source / "demo_at_apsat.com.jsonl", records)
        with self.assertRaises(ContractError):
            prepare(source, self.root / "run", ["demo"], "2026-09-01T00:00:00+08:00", "2026-10-01T00:00:00+08:00")

    def test_nanosecond_source_is_not_rejected_by_old_python(self):
        self.assertEqual(moment("2026-09-02T10:00:00.123456789+08:00").microsecond, 123456)

    def test_response_cut_does_not_taint_input_or_later_turns(self):
        requests = json.loads(self.row['request_body'])
        responses = json.loads(self.row['response_body'])
        for q in requests:
            q['truncated'] = False
        for r in responses:
            r['truncated'] = r['request_id'] == 'r1'
        self.row.update(request_body=json.dumps(requests), response_body=json.dumps(responses),
                        request_truncated=False, response_truncated=True)
        self.write_page(self.caches[0], self.row)
        source, _, trace = self.export()
        self.assertEqual([False, False], [t['request_truncated'] for t in trace])
        self.assertEqual([True, False], [t['response_truncated'] for t in trace])
        self.assertEqual(['执行', '执行'], [t['prompt'] for t in trace])
        self.assertEqual(['第一次失败', '第二次已经成功'], [t['response'] for t in trace])
        run = self.root / 'run'
        prepare(source, run, ['demo'], '2026-09-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00')
        events = rows(run / 'events.jsonl')
        self.assertEqual([False, True, False, False], [e['truncated'] for e in events])
        self.assertEqual([True]*4, [e['session_response_truncated'] for e in events])
        self.assertTrue((run / 'goal-contract.json').exists())

    def test_legacy_aggregate_cut_keeps_unknown_location(self):
        self.row['request_truncated'] = True
        self.row['response_truncated'] = False
        self.write_page(self.caches[0], self.row)
        source, _, trace = self.export()
        self.assertEqual([None, None], [t['request_truncated'] for t in trace])
        self.assertTrue(all(t['truncation_scope_unknown'] for t in trace))
        run = self.root / 'run'
        prepare(source, run, ['demo'], '2026-09-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00')
        events = rows(run / 'events.jsonl')
        self.assertEqual(['unknown', 'not_truncated', 'unknown', 'not_truncated'], [e['truncation_status'] for e in events])

    def test_request_cut_is_local_even_when_session_marked(self):
        requests = json.loads(self.row['request_body'])
        for i, q in enumerate(requests):
            q['truncated'] = i == 0
        self.row.update(request_body=json.dumps(requests), request_truncated=True, response_truncated=False)
        self.write_page(self.caches[0], self.row)
        _, _, trace = self.export()
        self.assertEqual([True, False], [t['truncated'] for t in trace])

    def test_missing_truncation_metadata_cannot_prove_completeness(self):
        source, _, trace = self.export()
        self.assertTrue(all(t['request_truncated'] is None for t in trace))
        run = self.root / 'run'
        prepare(source, run, ['demo'], '2026-09-01T00:00:00+08:00', '2026-10-01T00:00:00+08:00')
        events = rows(run / 'events.jsonl')
        self.assertTrue(all(e['truncation_status'] == 'unknown' and e['truncated'] for e in events))


if __name__ == "__main__":
    unittest.main()
