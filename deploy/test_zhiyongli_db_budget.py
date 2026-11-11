"""验证实际连接冲突、未知额度与跨实例边界；不连接数据库或模型。"""
from copy import deepcopy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('db_budget', Path(__file__).with_name('check_zhiyongli_db_budget.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    plan = dict(version=1, basis='synthetic', notes='合成连接预算，不代表实际配额',
                product_cluster='pg', source_cluster='pg', clusters=dict(pg=dict(max_connections=100,
                superuser_reserved_connections=3, reserved_connections=0,
                other_services_connections=5, operations_connections=4)))
    compose = dict(services=dict(sub2api=dict(environment=dict(DATABASE_MAX_OPEN_CONNS='50', DATABASE_MAX_IDLE_CONNS='10')),
                   **{'zhiyongli-worker': dict(command=['--workers', '4'])}))
    return plan, compose


class BudgetTests(unittest.TestCase):
    def test_host_app_budget_failure_prevents_container_replacement(self):
        plan, compose = fixture()
        compose['services']['sub2api']['environment']['DATABASE_MAX_OPEN_CONNS'] = '256'
        with tempfile.TemporaryDirectory(prefix='zy-host-budget-') as directory:
            root = Path(directory)
            (root/'private').mkdir(); (root/'backup').mkdir()
            (root/'private/db-budget.json').write_text(json.dumps(plan))
            (root/'compose.json').write_text(json.dumps(compose))
            (root/'bin').mkdir()
            docker = root/'bin/docker'
            docker.write_text('#!'+sys.executable+'\n'+'''import json, os, sys
from pathlib import Path
root = Path(os.environ['ZHIYONGLI_ROOT'])
with (root/'docker-calls.jsonl').open('a') as f:
    f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1] == 'ps': print('synthetic-pg')
elif 'config' in sys.argv: print((root/'compose.json').read_text())
else: sys.exit(99)
''')
            docker.chmod(0o700)
            result = subprocess.run(['bash',str(Path(__file__).with_name('zhiyongli-host-deploy.sh')),'app'],
                env=dict(os.environ,ZHIYONGLI_ROOT=str(root),PATH=str(root/'bin')+os.pathsep+os.environ['PATH']),
                capture_output=True,text=True,timeout=10)
            self.assertEqual(2,result.returncode)
            calls=[json.loads(line) for line in (root/'docker-calls.jsonl').read_text().splitlines()]
            self.assertFalse(any('up' in call or 'inspect' in call for call in calls))

    def run_cli(self, plan, compose):
        with tempfile.TemporaryDirectory(prefix='zy-budget-test-') as directory:
            root = Path(directory)
            (root/'plan.json').write_text(json.dumps(plan))
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('check_zhiyongli_db_budget.py')),
                '--plan', str(root/'plan.json'), '--compose-json', '-', '--output', str(root/'result.json')],
                input=json.dumps(compose), capture_output=True, text=True, timeout=10)
            body = (root/'result.json').read_text() if (root/'result.json').exists() else ''
            return result, body

    def test_stdin_cli_output_does_not_copy_private_compose_environment(self):
        plan, compose = fixture()
        compose['services']['sub2api']['environment']['DATABASE_PASSWORD'] = 'synthetic-secret-never-echo'
        result, body = self.run_cli(plan, compose)
        self.assertEqual(0, result.returncode)
        self.assertTrue(json.loads(body)['database_budget_fits'])
        self.assertNotIn('synthetic-secret-never-echo', result.stdout+result.stderr+body)
        self.assertFalse(json.loads(body)['production_ready'])

    def test_stdin_cli_exceeded_budget_returns_nonzero(self):
        plan, compose = fixture()
        compose['services']['sub2api']['environment']['DATABASE_MAX_OPEN_CONNS'] = '256'
        result, body = self.run_cli(plan, compose)
        self.assertEqual(2, result.returncode)
        self.assertFalse(json.loads(body)['database_budget_fits'])

    def test_invalid_cli_input_never_echoes_private_value(self):
        plan, compose = fixture()
        compose['services']['sub2api']['environment']['DATABASE_MAX_OPEN_CONNS'] = 'synthetic-private-value'
        result, body = self.run_cli(plan, compose)
        self.assertNotEqual(0, result.returncode)
        self.assertEqual('', body)
        self.assertNotIn('synthetic-private-value', result.stdout+result.stderr)

    def test_observed_app_pool_fails_even_with_unknown_other_allocations(self):
        plan, compose = fixture()
        compose['services']['sub2api']['environment'].update(DATABASE_MAX_OPEN_CONNS='256', DATABASE_MAX_IDLE_CONNS='128')
        plan['clusters']['pg'].update(other_services_connections=None, operations_connections=None)
        result = module.assess(plan, compose)
        self.assertFalse(result['database_budget_fits'])
        self.assertEqual(('exceeded', 167), (result['clusters'][0]['status'], result['clusters'][0]['over_ceiling_by']))
        self.assertIsNone(result['clusters'][0]['maximum_workers_for_declared_budget'])

    def test_unknown_allowance_never_implies_fit(self):
        plan, compose = fixture();plan['clusters']['pg']['operations_connections'] = None
        result = module.assess(plan, compose)
        self.assertEqual('unknown', result['clusters'][0]['status'])
        self.assertFalse(result['database_budget_fits'])

    def test_exact_ceiling_and_one_more_thread(self):
        plan, compose = fixture();compose['services']['zhiyongli-worker']['command'] = ['--workers', '19']
        value = module.assess(plan, compose)
        self.assertTrue(value['database_budget_fits'])
        self.assertEqual((97,19), (value['clusters'][0]['known_connection_demand'], value['clusters'][0]['maximum_workers_for_declared_budget']))
        compose['services']['zhiyongli-worker']['command'][-1] = '20'
        self.assertEqual(2, module.assess(plan, compose)['clusters'][0]['over_ceiling_by'])

    def test_database_names_do_not_split_same_instance(self):
        plan, compose = fixture()
        self.assertEqual(8, module.assess(plan, compose)['clusters'][0]['worker_connections'])
        plan['source_cluster'] = 'source-pg'
        plan['clusters']['source-pg'] = deepcopy(plan['clusters']['pg'])
        rows = module.assess(plan, compose)['clusters']
        self.assertEqual([4,4], [row['worker_connections'] for row in rows])
        self.assertEqual([50,0], [row['application_pool_connections'] for row in rows])

    def test_actual_compose_not_copied_plan_drives_pool_and_threads(self):
        plan, compose = fixture()
        self.assertEqual(4, module.assess(plan, compose)['workers'])
        compose['services']['zhiyongli-worker']['command'][-1] = '32'
        result = module.assess(plan, compose)
        self.assertEqual(64, result['clusters'][0]['worker_connections'])
        self.assertFalse(result['database_budget_fits']);self.assertFalse(result['production_ready'])

    def test_invalid_counts_missing_topology_and_extra_replicas_rejected(self):
        plan, compose = fixture()
        for value in (-1, True, 3.5, 'Infinity'):
            current = deepcopy(plan);current['clusters']['pg']['other_services_connections'] = value
            with self.assertRaises(ValueError):module.assess(current, compose)
        current = deepcopy(plan);current['source_cluster'] = 'undeclared'
        with self.assertRaises(ValueError):module.assess(current, compose)
        compose['services']['sub2api']['deploy'] = dict(replicas=2)
        with self.assertRaises(ValueError):module.assess(plan, compose)

    def test_idle_exceeds_open_and_unlimited_or_duplicate_workers_rejected(self):
        plan, compose = fixture()
        compose['services']['sub2api']['environment']['DATABASE_MAX_IDLE_CONNS'] = '51'
        with self.assertRaises(ValueError):module.assess(plan, compose)
        plan, compose = fixture()
        for command in (['--workers','0'], ['--workers','33'], ['--workers'], ['--workers','1','--workers','2']):
            compose['services']['zhiyongli-worker']['command'] = command
            with self.assertRaises((ValueError,IndexError)):module.assess(plan, compose)


@unittest.skipUnless(shutil.which('docker'), '需要Docker Compose配置解析器，不启动容器')
class ReleaseComposeBudgetTests(unittest.TestCase):
    layer = 'release'
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='zy-release-pool-')
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.env_file=self.root/'synthetic.env'
        self.env_file.write_text('POSTGRES_PASSWORD=synthetic-unused-only\n')
        self.override=self.root/'synthetic-override.yml'
        self.override.write_text('''services:
  sub2api:
    environment:
      DATABASE_MAX_OPEN_CONNS: "256"
      DATABASE_MAX_IDLE_CONNS: "128"
''')
        self.env=dict(os.environ,POSTGRES_PASSWORD='synthetic-unused-only',
            ZHIYONGLI_PRIVATE_DIR=str(self.root/'private'),ZHIYONGLI_STATE_DIR=str(self.root/'state'),
            ZHIYONGLI_REVIEW_API_KEY_ID='999999',ZHIYONGLI_MAX_REQUESTS_PER_JOB='3',
            ZHIYONGLI_EXCLUDED_USER_IDS='22',ZHIYONGLI_WORKERS='4',ZHIYONGLI_SCHEDULE_MODE='disabled',
            ZHIYONGLI_APP_IMAGE_REPOSITORY='example.invalid/accepted-app',ZHIYONGLI_APP_IMAGE_SHA256='a'*64,
            ZHIYONGLI_WORKER_IMAGE_REPOSITORY='example.invalid/accepted-worker',ZHIYONGLI_WORKER_IMAGE_SHA256='b'*64,
            ZHIYONGLI_APP_DB_MAX_OPEN_CONNS='64',ZHIYONGLI_APP_DB_MAX_IDLE_CONNS='16')

    def render(self,env=None,release=True):
        deploy=Path(__file__).resolve().parent
        files=[deploy/'docker-compose.local.yml',self.override]
        if release:files += [deploy/'docker-compose.zhiyongli.yml',deploy/('docker-compose.zhiyongli.'+self.layer+'.yml')]
        command=['docker','compose','--env-file',str(self.env_file),'-p','zy-budget-test']
        for path in files:command += ['-f',str(path)]
        command += ['--profile','zhiyongli','config','--format','json']
        return subprocess.run(command,env=self.env if env is None else env,capture_output=True,text=True,timeout=30)

    def resolved(self,env=None,release=True):
        result=self.render(env,release)
        self.assertEqual(0,result.returncode,'合成Compose解析未成功，不输出完整私有配置')
        return json.loads(result.stdout)

    def test_pool_limits_missing_or_empty_reject_old_override(self):
        for name in ('ZHIYONGLI_APP_DB_MAX_OPEN_CONNS','ZHIYONGLI_APP_DB_MAX_IDLE_CONNS'):
            for empty in (False,True):
                with self.subTest(name=name,empty=empty):
                    env=dict(self.env)
                    if empty:env[name]=''
                    else:env.pop(name)
                    result=self.render(env)
                    self.assertNotEqual(0,result.returncode)
                    self.assertIn(name,result.stderr)

    def test_explicit_pool_replaces_old_pool_and_preserves_dependencies(self):
        before=self.resolved(release=False);after=self.resolved()
        app=after['services']['sub2api']['environment']
        self.assertEqual(('256','128'),tuple(before['services']['sub2api']['environment'][k]
            for k in ('DATABASE_MAX_OPEN_CONNS','DATABASE_MAX_IDLE_CONNS')))
        self.assertEqual(('64','16'),(app['DATABASE_MAX_OPEN_CONNS'],app['DATABASE_MAX_IDLE_CONNS']))
        for service in ('postgres','redis'):
            self.assertEqual(before['services'][service],after['services'][service])
        for volume in before['services']['sub2api']['volumes']:
            self.assertIn(volume,after['services']['sub2api']['volumes'])
        self.assertEqual('disabled',after['services']['zhiyongli-worker']['command'][-1])

    def test_actual_rendered_pool_is_checked_for_fit_and_overflow(self):
        plan,_=fixture();result=module.assess(plan,self.resolved())
        self.assertTrue(result['database_budget_fits'])
        self.assertEqual(81,result['clusters'][0]['known_connection_demand'])
        self.assertFalse(result['production_ready'])
        env=dict(self.env,ZHIYONGLI_APP_DB_MAX_OPEN_CONNS='256',ZHIYONGLI_APP_DB_MAX_IDLE_CONNS='128')
        self.assertEqual('exceeded',module.assess(plan,self.resolved(env))['clusters'][0]['status'])
        env=dict(self.env,ZHIYONGLI_APP_DB_MAX_IDLE_CONNS='65')
        with self.assertRaises(ValueError):module.assess(plan,self.resolved(env))

    def test_unknown_other_allowances_block_even_with_explicit_pool(self):
        plan,_=fixture();plan['clusters']['pg']['operations_connections']=None
        result=module.assess(plan,self.resolved())
        self.assertFalse(result['database_budget_fits'])
        self.assertEqual('unknown',result['clusters'][0]['status'])


class HostComposeBudgetTests(ReleaseComposeBudgetTests):
    layer = 'host'


if __name__ == '__main__':unittest.main()
