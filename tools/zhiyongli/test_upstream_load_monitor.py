"""监控三块独立失败与只读命令合同；所有命令均mock，禁止真实外部调用。"""
import ast
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import upstream_load_monitor as monitor


class UpstreamLoadMonitorTests(unittest.TestCase):
    def setUp(self):
        self.args=SimpleNamespace(account_id=7,review_key_id=9,account_limit=20,worker_container='synthetic-worker',
            flow_path='/run/synthetic/flow.json',heartbeat_path='/state/synthetic-heartbeat.json',
            flow_db_path='/state/synthetic-flow.sqlite',pause_path='/state/synthetic-pause',heartbeat_max_age=120)
        self.calls=[];self.fail=set();self.worker_state='running';self.age=15;self.redis_values=[2,3,4,5,6]
        # 即使未来改动绕过run，也不准落到真实docker/Redis/PG进程。
        guard=patch.object(monitor.subprocess,'check_output',side_effect=AssertionError('禁止真实命令'))
        guard.start();self.addCleanup(guard.stop)
        runner=patch.object(monitor,'run',side_effect=self.fake_run);runner.start();self.addCleanup(runner.stop)
        clock=patch.object(monitor.time,'time',return_value=1000);clock.start();self.addCleanup(clock.stop)

    def fake_run(self,args,payload=None):
        self.calls.append((args,payload))
        if 'label=com.docker.compose.service=redis' in args:
            if 'redis' in self.fail:raise RuntimeError('secret credential stderr command')
            return 'redis-id'
        if 'label=com.docker.compose.service=postgres' in args:
            if 'postgres' in self.fail:raise RuntimeError('secret credential stderr command')
            return 'postgres-id'
        if args[:3]==['docker','ps','-a']:
            if 'worker' in self.fail:raise RuntimeError('secret credential stderr command')
            return '' if self.worker_state=='missing' else 'worker-id'
        if args[:2]==['docker','inspect']:
            if 'inspect' in self.fail:raise RuntimeError('secret credential stderr command')
            return json.dumps([dict(Id='worker-id',RestartCount=2,
                State=dict(Running=self.worker_state=='running',StartedAt='2026-11-16T00:00:00Z'),
                HostConfig=dict(Memory=512000,NanoCpus=2000000000))])
        if 'EVAL_RO' in args:
            if 'redis-query' in self.fail:raise RuntimeError('secret credential stderr command')
            return json.dumps(self.redis_values)
        if 'postgres-id' in args:return '' if 'empty-pg' in self.fail else 'synthetic-model|8|3|140|20'
        if 'worker-id' in args and 'exec' in args:
            if 'runtime' in self.fail:raise RuntimeError('secret credential stderr command')
            return json.dumps(dict(states=dict(received=4),recent_5m_429=1,memory=dict(memory_usage='3000'),
                heartbeat_age_seconds=self.age,heartbeat_at=1000-self.age,workers=2,
                config=dict(max_concurrency=2,requests_per_minute=12,max_requests=30000,budget_key='synthetic'),paused=False))
        raise AssertionError('未声明的命令')

    def assert_read_only(self):
        for args,payload in self.calls:
            self.assertEqual('docker',args[0]);self.assertIn(args[1],('ps','inspect','exec'))
            self.assertFalse(set(args)&{'start','stop','restart','update','rm','kill','compose'})
            if 'redis-id' in args and 'exec' in args:self.assertIn('EVAL_RO',args)
            if 'postgres-id' in args and 'exec' in args:
                self.assertIn('BEGIN READ ONLY;',payload);self.assertIn('SELECT model,count(*)',payload)
                self.assertNotIn('DELETE',payload);self.assertNotIn('UPDATE',payload)
            if 'worker-id' in args and 'exec' in args:
                self.assertEqual([self.args.flow_path,self.args.heartbeat_path,self.args.flow_db_path,self.args.pause_path],args[-4:])
                self.assertIn('?mode=ro',args[args.index('-c')+1])

    def test_observed_keeps_existing_fields_and_original_queries(self):
        value=monitor.sample(self.args)
        self.assertEqual('observed',value['status']);self.assertEqual((5,4,11),
            (value['account_reserved_slots'],value['account_waiting'],value['review_key_reserved_slots']))
        self.assertEqual(['synthetic-model|8|3|140|20'],value['usage_5m_by_model'])
        self.assertEqual((2,2.0,512000),(value['worker_restart_count'],value['worker_cpu_limit'],value['worker_memory_limit_bytes']))
        self.assertEqual(15,value['worker']['heartbeat_age_seconds'])
        for block in value['observations'].values():
            self.assertLessEqual(block['sample_started_at'],block['sample_finished_at'])
            self.assertLessEqual(block['sample_finished_at'],value['at'])
        self.assertFalse(value['automatic_increase_enabled']);self.assertEqual('observe_only',value['control'])
        self.assert_read_only()

    def test_stopped_and_missing_workers_do_not_block_upstream_or_exec_worker(self):
        for state in ('stopped','missing'):
            with self.subTest(state=state):
                self.calls=[];self.worker_state=state;value=monitor.sample(self.args)
                self.assertEqual('partial',value['status'])
                self.assertEqual(state,value['observations']['worker']['container_state'])
                self.assertEqual('observed',value['observations']['redis']['status'])
                self.assertEqual('observed',value['observations']['postgres']['status'])
                self.assertNotIn('worker',value)
                self.assertFalse(any('exec' in args and 'worker-id' in args for args,_ in self.calls))
                self.assert_read_only()

    def test_each_independent_failure_retains_other_blocks_without_zero_or_sensitive_errors(self):
        for failure,block in (('redis-query','redis'),('postgres','postgres'),('runtime','worker'),('inspect','worker')):
            with self.subTest(failure=failure):
                self.calls=[];self.fail={failure};value=monitor.sample(self.args)
                self.assertEqual('partial',value['status']);self.assertEqual('unavailable',value['observations'][block]['status'])
                self.assertTrue(all(v['status']=='observed' for k,v in value['observations'].items() if k!=block))
                for secret in ('secret','credential','stderr','command'):self.assertNotIn(secret,json.dumps(value))
                if block=='redis':self.assertNotIn('account_reserved_slots',value)
                if failure=='inspect':self.assertNotEqual('worker_missing',value['observations'][block]['reason'])
                self.assert_read_only()

    def test_all_failed_is_unavailable_not_zero(self):
        self.fail={'redis','postgres','worker'};value=monitor.sample(self.args)
        self.assertEqual('unavailable',value['status']);self.assertNotIn('account_reserved_slots',value)
        self.assertNotIn('usage_5m_by_model',value);self.assertNotIn('worker',value)
        self.assertTrue(all(b['status']=='unavailable' for b in value['observations'].values()))
        self.assert_read_only()

    def test_stale_heartbeat_is_not_a_fresh_runtime_value(self):
        self.age=10000;value=monitor.sample(self.args)
        self.assertEqual('partial',value['status']);self.assertNotIn('worker',value)
        self.assertEqual('worker_heartbeat_stale',value['observations']['worker']['reason'])
        self.assert_read_only()

    def test_unselected_worker_is_not_configured_not_stopped(self):
        self.args.worker_container=None;value=monitor.sample(self.args)
        self.assertEqual('worker_not_configured',value['observations']['worker']['reason'])
        self.assertFalse(any(args[:2]==['docker','inspect'] for args,_ in self.calls))
        self.assert_read_only()

    def test_redis_zero_requires_successful_integer_observation(self):
        self.redis_values=[0,0,0,0,0];value=monitor.sample(self.args)
        self.assertEqual('observed',value['status']);self.assertEqual(0,value['account_reserved_slots'])
        self.redis_values=[False,0,0,0,0];value=monitor.sample(self.args)
        self.assertEqual('partial',value['status']);self.assertNotIn('account_reserved_slots',value)
        self.assertEqual('unavailable',value['observations']['redis']['status'])
        self.assert_read_only()

    def test_worker_sqlite_uri_encodes_hash_and_is_actually_read_only(self):
        # 直接执行嵌入脚本的原连接语句，Docker仍mock；仅本机临时合成库。
        with tempfile.TemporaryDirectory() as directory:
            database=Path(directory)/'synthetic#flow.sqlite'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE synthetic(value INTEGER)')
                connection.execute('INSERT INTO synthetic VALUES(7)')
            before={p.name:p.read_bytes() for p in Path(directory).iterdir()}
            tree=ast.parse(monitor.WORKER)
            statement=next(n for n in tree.body if isinstance(n,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id=='d' for t in n.targets))
            namespace=dict(sqlite3=sqlite3,Path=Path,sys=SimpleNamespace(argv=['worker','flow','heartbeat',str(database)]))
            exec(compile(ast.Module(body=[statement],type_ignores=[]),'<worker-readonly-connection>','exec'),namespace)
            connection=namespace['d']
            try:
                self.assertEqual([(7,)],connection.execute('SELECT value FROM synthetic').fetchall())
                with self.assertRaisesRegex(sqlite3.OperationalError,'readonly'):
                    connection.execute('INSERT INTO synthetic VALUES(8)')
            finally:connection.close()
            self.assertEqual(before,{p.name:p.read_bytes() for p in Path(directory).iterdir()})
            self.assertFalse(self.calls)

    def test_successful_empty_pg_rows_remain_an_empty_observation(self):
        self.fail={'empty-pg'};value=monitor.sample(self.args)
        self.assertEqual('observed',value['status']);self.assertEqual([],value['usage_5m_by_model'])
        self.assert_read_only()


if __name__=='__main__':unittest.main()
