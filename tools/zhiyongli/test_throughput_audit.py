"""核对只读审计的窗口、缺遥测和重复成功证据，避免误报吞吐。"""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from throughput_audit import analyze, timestamp


class ThroughputAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'flow.sqlite'
        with sqlite3.connect(self.path) as db:
            db.execute('''CREATE TABLE attempts(id TEXT PRIMARY KEY,budget_key TEXT,request_sha TEXT,
                receipt TEXT,started REAL,status TEXT,finished REAL)''')
            db.execute('''CREATE TABLE measurements(attempt_id TEXT PRIMARY KEY,request_kind TEXT,
                terminal_state TEXT,client_seconds REAL,total_tokens INTEGER)''')

    def add(self, aid, started, *, status='received', terminal='completed', kind='reading',
            signature='same', budget='original', seconds=3):
        receipt = self.root / (aid + '.json')
        receipt.write_text(json.dumps(dict(status=status, request_sha256=signature,
            response=dict(status=terminal), response_id=aid, private_text='不能复制到报告')))
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)',
                       (aid, budget, signature, str(receipt), started, status, started+9000))
            if terminal is not None:
                db.execute('INSERT INTO measurements VALUES(?,?,?,?,?)',
                           (aid, kind, terminal, seconds, 20))
        return receipt

    def test_duplicate_receipts_have_original_evidence_without_text_or_writes(self):
        a = self.add('a', 11)
        self.add('b', 12)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = analyze(self.path, 10, 20, 'original')
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).hexdigest())
        group = result['completed_duplicate_groups'][0]
        self.assertEqual((2, 2, 1), (group['requests'], group['distinct_receipts'], group['started_span_seconds']))
        self.assertEqual(hashlib.sha256(a.read_bytes()).hexdigest(), group['attempts'][0]['evidence']['file_sha256'])
        self.assertNotIn('不能复制', json.dumps(result, ensure_ascii=False))
        self.assertEqual(3, result['stages'][0]['latency']['p95_seconds'])

    def test_failed_and_unmeasured_are_not_duplicate_success_or_latency(self):
        self.add('a', 11)
        self.add('b', 12, terminal='failed')
        self.add('c', 13, status='unknown', terminal=None)
        result = analyze(self.path, 10, 20, 'original')
        self.assertEqual([], result['completed_duplicate_groups'])
        self.assertEqual(['c'], result['unknown_attempt_ids'])
        self.assertEqual(.5, result['stages'][0]['unsuccessful_or_unknown_rate'])
        self.assertIsNone(result['stages'][1]['latency']['sum_seconds'])
        self.assertIsNone(result['stages'][1]['total_tokens'])

    def test_window_and_budget_do_not_mix_different_executions(self):
        for aid, at, budget in [('old', 9, 'original'), ('start', 10, 'original'),
                                ('end', 20, 'original'), ('other', 11, 'different')]:
            self.add(aid, at, budget=budget)
        result = analyze(self.path, 10, 20, 'original')
        self.assertEqual(1, result['attempts_in_window'])
        self.assertEqual(3, result['budget_used_at_snapshot'])
        self.assertEqual([], result['completed_duplicate_groups'])

    def test_missing_measurement_table_and_bad_receipts_remain_unconfirmed(self):
        self.add('a', 11)
        with sqlite3.connect(self.path) as db:
            db.execute('DROP TABLE measurements')
        result = analyze(self.path, 10, 20, 'original')
        self.assertFalse(result['measurement_table_available'])
        self.assertEqual(0, result['measured_requests'])
        self.assertEqual('received', next(iter(result['stages'][0]['states'])))
        self.assertEqual([], result['completed_duplicate_groups'])

    def test_timezone_is_explicit(self):
        self.assertEqual(timestamp('2026-11-17T00:00:00+08:00'), timestamp('2026-11-16T16:00:00Z'))
        with self.assertRaises(ValueError):
            timestamp('2026-11-17T00:00:00')


if __name__ == '__main__':
    unittest.main()
