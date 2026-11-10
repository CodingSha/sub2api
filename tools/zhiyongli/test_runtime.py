"""持久化、幂等、崩溃与未知结果隔离测试；不使用真实API。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime as r
from release_scope import assess, signature


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.q=r.Queue(self.root/'state')
        self.addCleanup(self.q.db.close)
        self.unit=dict(version=r.VERSION,key='person:week',evaluation_scope='person_week',
                       periods=[dict(start='2026-09-07T00:00:00+08:00',end='2026-09-14T00:00:00+08:00')])
        self.ref=self.file('unit.json',self.unit)

    def file(self,name,value):
        p=self.root/name;p.write_text(json.dumps(value));return dict(path=str(p),sha256=__import__('hashlib').sha256(p.read_bytes()).hexdigest())

    def catalog(self,rev=1):
        return dict(version=1,source='current-index',revision=rev,policy_sha256=r.policy(),jobs=[
            dict(key=self.unit['key'],kind='review',unit=self.ref,due='2026-09-14T00:00:00+08:00')])

    def test_incremental_atomic_cursor_and_idempotence(self):
        c=self.catalog();first=self.q.ingest(c)
        self.assertEqual(first,self.q.ingest(c))
        self.assertEqual(1,self.q.db.execute('SELECT count(*) FROM jobs').fetchone()[0])
        c['jobs'][0]['due']='2026-09-15T00:00:00+08:00'
        with self.assertRaises(ValueError):self.q.ingest(c)
        self.assertEqual('queued',self.q.job(first[0])['state'])

    def test_not_due_and_default_never_calls_api(self):
        self.q.ingest(self.catalog())
        client=unittest.mock.Mock()
        self.assertEqual(0,r.tick(self.q,'2026-09-13T00:00:00+08:00',client=client))
        client.call.assert_not_called()
        self.assertEqual(0,r.tick(self.q,'2026-09-15T00:00:00+08:00'))

    def test_killed_request_is_unknown_and_blocks_new_revision(self):
        jid=self.q.ingest(self.catalog())[0]
        with self.q.db:self.q.set(jid,'requesting')
        client=unittest.mock.Mock()
        r.tick(self.q,'2026-09-15T00:00:00+08:00',client=client)
        self.assertEqual('unknown',self.q.job(jid)['state']);client.call.assert_not_called()
        c=self.catalog(2);c['jobs'][0]['due']='2026-09-16T00:00:00+08:00'
        with self.assertRaises(ValueError):self.q.ingest(c)
        self.assertEqual(1,self.q.db.execute('SELECT revision FROM cursors').fetchone()[0])

    def test_removed_index_job_does_not_run(self):
        self.q.ingest(self.catalog());c=self.catalog(2);c['jobs']=[];self.q.ingest(c)
        client=unittest.mock.Mock()
        self.assertEqual(0,r.tick(self.q,'2026-09-15T00:00:00+08:00',client=client))

    def test_double_worker_is_rejected(self):
        with self.q.lock():
            other=r.Queue(self.q.root)
            try:
                with self.assertRaises(ValueError):
                    with other.lock():pass
            finally:other.db.close()

    def test_received_invalid_is_preserved_and_not_retried(self):
        jid=self.q.ingest(self.catalog())[0]
        client=unittest.mock.Mock()
        client.call.return_value=dict(status='received',response=dict(status='completed',output=[
            dict(content=[dict(type='output_text',text='{"version":"bad"}')])]))
        r.tick(self.q,'2026-09-15T00:00:00+08:00',client=client)
        self.assertEqual('invalid_response',self.q.job(jid)['state'])
        r.tick(self.q,'2026-09-15T00:00:00+08:00',client=client);self.assertEqual(1,client.call.call_count)

    def test_received_incomplete_is_not_mislabeled_preflight_or_retried(self):
        jid=self.q.ingest(self.catalog())[0]
        client=unittest.mock.Mock();client.call.return_value=dict(status='received',request_sha256='kept',
            response=dict(status='incomplete',incomplete_details=dict(reason='max_output_tokens')))
        r.tick(self.q,'2026-09-15T00:00:00+08:00',client=client)
        self.assertEqual('invalid_response',self.q.job(jid)['state'])
        r.tick(self.q,'2026-09-15T00:00:00+08:00',client=client)
        self.assertEqual(1,client.call.call_count)

    def test_unknown_not_retried(self):
        jid=self.q.ingest(self.catalog())[0]
        client=unittest.mock.Mock();client.call.return_value=dict(status='unknown',error='http_504')
        r.tick(self.q,'2026-09-15T00:00:00+08:00',client=client)
        r.tick(self.q,'2026-09-15T00:00:00+08:00',client=client)
        self.assertEqual('unknown',self.q.job(jid)['state']);self.assertEqual(1,client.call.call_count)

    def test_source_drift_is_preflight_failure_no_call(self):
        jid=self.q.ingest(self.catalog())[0];Path(self.ref['path']).write_text('{}')
        client=unittest.mock.Mock();r.tick(self.q,'2026-09-15T00:00:00+08:00',client=client)
        self.assertEqual('preflight_failed',self.q.job(jid)['state']);client.call.assert_not_called()

    def test_received_after_crash_is_rechecked_without_api(self):
        jid=self.q.ingest(self.catalog())[0]
        with self.q.db:self.q.set(jid,'requesting')
        r.atomic(self.q.root/'responses'/(jid+'.json'),dict(status='received',response=dict(status='completed',output=[
            dict(content=[dict(type='output_text',text='{"version":"bad"}')])])) )
        r.tick(self.q,'2026-09-15T00:00:00+08:00')
        self.assertEqual('invalid_response',self.q.job(jid)['state'])

    def test_reuse_proof_is_bound_to_exact_input_and_review(self):
        c=self.catalog();vref=self.file('review.json',{'value':{'key':'person:week'}})
        proof=self.file('proof.json',dict(unit_sha256=self.ref['sha256'],review_sha256=vref['sha256'],reviewer='test',reason='prior review'))
        c['jobs'][0].update(review=vref,proof=proof)
        with patch.object(r,'validate_review',return_value=[]):
            jid=self.q.ingest(c)[0]
            r.tick(self.q,'2026-09-15T00:00:00+08:00')
        self.assertEqual('accepted',self.q.job(jid)['state'])
        bad=dict(c);bad['revision']=2
        Path(proof['path']).write_text('{}')
        with self.assertRaises(ValueError):self.q.ingest(bad)

    def test_scheduler_restart_reuses_cursor_and_completed_jobs(self):
        c=self.catalog();ref=self.file('catalog.json',c)
        with patch.object(r,'tick',return_value=0) as tick:
            self.assertEqual(1,r.run(self.q,ref['path'],cycles=1))
            self.assertEqual(1,r.run(self.q,ref['path'],cycles=1))
        self.assertEqual(2,tick.call_count)
        self.assertEqual(1,self.q.db.execute('SELECT count(*) FROM jobs').fetchone()[0])

    def test_known_location_adapter_preserves_judgment_and_rejects_conflict(self):
        value=dict(evidence=[dict(origin='person_event')],period_reviews=[dict(
            context={},challenge=dict(level='substantial'),cells=[dict(performance=dict(verdict='good'))])])
        def record(v):return dict(response=dict(status='completed',output=[dict(content=[dict(type='output_text',text=json.dumps(v))])]))
        result,adapt=r.response_value({'evaluation_scope':'local'},record(value))
        self.assertEqual(value['evidence'],result['evidence']) # 不自动认领自创来源枚举。
        self.assertEqual(value['period_reviews'][0]['cells'],result['period_reviews'][0]['cells'])
        self.assertEqual(1,len(adapt['operations']))
        value['period_reviews'][0]['context']['challenge']={}
        with self.assertRaises(ValueError):r.response_value({'evaluation_scope':'local'},record(value))

    def test_responsibility_location_preserves_negative_judgment(self):
        responsibility=dict(known_at_time='已知',person_control='可控',feasible_alternative='替代',actual_gap='实际缺口')
        value=dict(evidence=[dict(id='反证')],period_reviews=[dict(cells=[dict(item='J1',state='observed',
            quality=dict(label='minor_gap'),performance=dict(verdict='partial'),responsibility=responsibility)])])
        result,adapt=r.normalize_review_locations({'evaluation_scope':'person_week'},value)
        cell=result['period_reviews'][0]['cells'][0]
        self.assertEqual(responsibility,cell['quality']['responsibility'])
        self.assertEqual({'verdict':'partial'},cell['performance'])
        self.assertEqual(value['evidence'],result['evidence'])
        self.assertIn('responsibility',value['period_reviews'][0]['cells'][0])
        self.assertFalse(adapt['changes_scores'])
        self.assertEqual(1,len(adapt['operations']))
        cell['responsibility']=responsibility
        with self.assertRaises(ValueError):r.normalize_review_locations({'evaluation_scope':'person_week'},result)

    def test_month_waits_all_semantically_checked_dependencies(self):
        jid=self.q.ingest(self.catalog())[0]
        c=self.catalog(2);c['jobs'].append(dict(key='person:month',kind='month',dependencies=[jid],
            start='2026-09-01T00:00:00+08:00',end='2026-10-01T00:00:00+08:00',due='2026-10-01T00:00:00+08:00'))
        month=self.q.ingest(c)[1]
        self.assertEqual(0,r.tick(self.q,'2026-10-02T00:00:00+08:00'))
        self.assertEqual('queued',self.q.job(month)['state'])

    def test_actual_unobservable_cannot_be_waived(self):
        g='week-2026-09-28/P2：unobservable'
        d=dict(id=signature(g),source=g,status='limitation',reason='不能豁免',basis=['real'])
        with self.assertRaises(ValueError):assess(dict(source_gaps=[g],exact='80'),[d])
        d['status']='blocking'
        self.assertFalse(assess(dict(source_gaps=[g],exact='80'),[d])['gradeable'])
        with self.assertRaises(ValueError):assess(dict(source_gaps=[g],exact='80'),[])

if __name__=='__main__':unittest.main()
