"""遥测不能把缺失补零、截断当完成或把重启核账耗时当模型延迟。"""
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from model_config import ModelConfig
from model_flow import ModelFlow
from runtime import atomic,CompanyClient
from worker import JobClient


class MeasurementsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=10000
        self.config=dict(version=1,budget_key='synthetic',approved_by='合成验收',reason='仅离线遥测',
            max_concurrency=2,requests_per_minute=100,reserved_tokens_per_minute=100000,max_requests=10,
            transient_failure_limit=2,cooldown_seconds=60)

    def store(self,name,record):
        flow=ModelFlow(self.root/'flow',self.config,now=lambda:self.now)
        with flow.slot(name,self.root/name,b'x',1):atomic(self.root/name,record)
        return flow

    def test_completed_and_incomplete_outputs_are_separate_and_missing_usage_is_not_zero(self):
        self.store('a',dict(status='received',request_kind='zhiyongli_source_reading',client_elapsed_seconds=12.5,
            response=dict(status='completed',usage=dict(input_tokens=50,output_tokens=20,total_tokens=70))))
        flow=self.store('b',dict(status='received',request_kind='zhiyongli_source_reading',client_elapsed_seconds=9,
            response=dict(status='incomplete')))
        value=flow.snapshot();measure=value['measurements'];rows={r['terminal_state']:r for r in measure['stages']}
        self.assertEqual((2,2,1),(measure['terminal_requests'],measure['measured_latency_requests'],measure['measured_input_output_requests']))
        self.assertEqual(12.5,rows['completed']['mean_client_seconds'])
        self.assertIsNone(rows['incomplete']['output_tokens'])
        self.assertEqual(20,rows['completed']['output_tokens'])
        self.assertEqual(dict(used=2,limit=10,remaining=8),value['request_budget'])

    def test_recovery_does_not_inflate_client_latency_or_rewrite_measurement(self):
        flow=self.store('a',dict(status='received',request_kind='zhiyongli_semantic_audit',client_elapsed_seconds=3,
            response=dict(status='completed')))
        with flow.connect() as db:
            db.execute("INSERT INTO attempts VALUES('late','synthetic','sha',?,?,1,'active',NULL,NULL,NULL,1)",(str(self.root/'late'),self.now-9999))
        atomic(self.root/'late',dict(status='received',request_kind='zhiyongli_semantic_audit',client_elapsed_seconds=4,
            response=dict(status='completed')))
        with (self.root/'worker.lock').open('a') as lock:flow.reconcile_startup(lock)
        with flow.connect() as db:flow.finish(db,'late',dict(status='received',client_elapsed_seconds=999),self.now+999)
        rows=flow.snapshot()['measurements']['stages']
        self.assertEqual((3.5,4),(rows[0]['mean_client_seconds'],rows[0]['maximum_client_seconds']))
        self.assertEqual(2,rows[0]['requests'])

    def test_old_readonly_volume_reports_unmeasured_without_creating_schema(self):
        flow=self.store('legacy',dict(status='received',response=dict(usage=dict(total_tokens=7))))
        with flow.connect() as db:db.execute('DROP TABLE measurements')
        value=ModelFlow(self.root/'flow',self.config,read_only=True).snapshot()['measurements']
        self.assertFalse(value['measurement_table_available'])
        self.assertEqual((1,0,[]),(value['terminal_requests'],value['measured_latency_requests'],value['stages']))
        with sqlite3.connect(flow.path) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='measurements'").fetchone())

    def test_budget_keys_are_isolated_and_invalid_measurements_remain_missing(self):
        self.store('invalid',dict(status='unknown',request_kind='员工正文不能成为阶段名',client_elapsed_seconds=float('nan'),
            response=dict(usage=dict(input_tokens=True,output_tokens=-2,total_tokens='7'))))
        other=ModelFlow(self.root/'flow',dict(self.config,budget_key='other'),now=lambda:self.now).snapshot()
        self.assertEqual(0,other['request_budget']['used'])
        self.assertEqual([],other['measurements']['stages'])
        first=ModelFlow(self.root/'flow',self.config,read_only=True).snapshot()['measurements']['stages'][0]
        self.assertEqual('unclassified',first['request_kind'])
        self.assertIsNone(first['mean_client_seconds']);self.assertIsNone(first['input_tokens'])
        self.assertIsNone(first['output_tokens']);self.assertIsNone(first['total_tokens'])

    def test_transmit_measures_monotonic_elapsed_even_when_wall_clock_moves_backwards(self):
        key=self.root/'key';key.write_text('synthetic-key');key.chmod(0o600)
        client=CompanyClient(ModelConfig('https://ai.apsatcom.com',key))
        payload=dict(text=dict(format=dict(name='zhiyongli_source_reading')))
        response=dict(id='synthetic',status='incomplete',usage=dict(input_tokens=10,output_tokens=5,total_tokens=15))
        event=('data: '+json.dumps(dict(type='response.incomplete',response=response))+'\n\n').encode()
        opener=unittest.mock.Mock();opener.open.return_value=io.BytesIO(event)
        with patch.object(ModelConfig,'opener',return_value=opener),patch('runtime.time.time',side_effect=[10000,9000,8000,7000]),patch('runtime.time.monotonic',side_effect=[10,10,11,11,12,12,13,13]):
            record=client._call(payload,self.root/'response.json')
        self.assertEqual('incomplete',record['response']['status'])
        self.assertEqual('zhiyongli_source_reading',record['request_kind'])
        self.assertGreater(record['client_elapsed_seconds'],0)
        self.assertLess(record['received_at'],record['sent_at'])
        self.assertEqual(1,opener.open.call_count)
        self.assertEqual(record,client._call(payload,self.root/'response.json'))

    def test_worker_terminal_metrics_preserve_budget_and_cached_failures_do_not_resend(self):
        key=self.root/'key';key.write_text('synthetic-key');key.chmod(0o600)
        flow=ModelFlow(self.root/'flow',self.config,now=lambda:self.now)
        payload=dict(text=dict(format=dict(name='zhiyongli_source_reading')))
        for terminal in ('completed','incomplete','failed'):
            client=JobClient(ModelConfig('https://ai.apsatcom.com',key),self.root/terminal,10,flow=flow)
            response=dict(status=terminal,usage=dict(input_tokens=10,output_tokens=5,total_tokens=15))
            event=('data: '+json.dumps(dict(type='response.'+terminal,response=response))+'\n\n').encode()
            opener=unittest.mock.Mock();opener.open.return_value=io.BytesIO(event)
            with patch.object(ModelConfig,'opener',return_value=opener):
                record=client._call(payload,self.root/(terminal+'.json'))
                self.assertEqual(record,client._call(payload,self.root/(terminal+'.json')))
            self.assertEqual(1,opener.open.call_count)
            self.assertEqual(1,client.budget.used())
        value=flow.snapshot();rows=value['measurements']['stages']
        self.assertEqual({'completed','incomplete','failed'},{r['terminal_state'] for r in rows})
        self.assertEqual(3,value['request_budget']['used'])
        self.assertEqual(3,value['measurements']['measured_latency_requests'])
        self.assertEqual(45,sum(r['total_tokens'] for r in rows))

    def test_worker_transport_rejection_and_unknown_remain_separate_and_usage_unknown(self):
        key=self.root/'key';key.write_text('synthetic-key');key.chmod(0o600)
        for name,error in [('rejected',urllib.error.HTTPError('https://ai.apsatcom.com',429,'limit',{},io.BytesIO(b''))),
                           ('unknown',TimeoutError('synthetic timeout'))]:
            flow=ModelFlow(self.root/name/'flow',self.config,now=lambda:self.now)
            client=JobClient(ModelConfig('https://ai.apsatcom.com',key),self.root/name/'job',10,flow=flow)
            opener=unittest.mock.Mock();opener.open.side_effect=error
            receipt=self.root/(name+'.json');payload=dict(text=dict(format=dict(name='zhiyongli_review')))
            with patch.object(ModelConfig,'opener',return_value=opener):
                record=client._call(payload,receipt)
                self.assertEqual(record,client._call(payload,receipt))
            self.assertEqual(1,opener.open.call_count)
            value=flow.snapshot();row=value['measurements']['stages'][0]
            self.assertEqual(name,row['terminal_state'])
            self.assertIsNone(row['total_tokens'])
            self.assertEqual(1,value['request_budget']['used'])
            self.assertEqual(1,row['measured_latency_requests'])


if __name__=='__main__':unittest.main()
