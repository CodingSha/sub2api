"""容量模型验证依赖顺序、分钟限额和持久预算的必要边界；无真实请求。"""
import copy
import unittest

from capacity_plan import Simulation, requirements


def plan(users=100, requests=3, latency=10):
    return dict(version=1, basis='synthetic', assumptions='合成计数与固定延迟，仅验证调度',
                window_hours=54, workers=4,
                limits=dict(max_concurrency=4, requests_per_minute=600,
                            reserved_tokens_per_minute=100000, max_requests=10000,
                            max_requests_per_job=1000),
                jobs=[dict(key='synthetic-'+str(n), stages=[dict(count=requests,
                           latency_seconds=latency, reserved_tokens=100)]) for n in range(users)])


class CapacityTests(unittest.TestCase):
    def test_hundred_jobs_finish_without_exceeding_declared_limits(self):
        value=Simulation(plan()).run()
        self.assertEqual((100,300,4),(value['completed_jobs'],value['requests_sent'],value['peak_active']))
        self.assertAlmostEqual(750/3600,value['simulation_hours'])
        self.assertTrue(value['all_within_window']);self.assertFalse(value['production_ready'])

    def test_serial_long_job_cannot_be_accelerated_by_other_workers(self):
        spec=plan(users=1,requests=10,latency=90)
        value=Simulation(spec).run();self.assertEqual(.25,value['simulation_hours'])
        self.assertEqual(.25,requirements(spec)['longest_serial_job_hours'])

    def test_minute_limits_include_active_requests_older_than_a_minute(self):
        spec=plan(users=4,requests=1,latency=90)
        spec['limits'].update(requests_per_minute=1)
        value=Simulation(spec).run()
        self.assertEqual(1,value['peak_window_requests'])
        self.assertEqual(.1,value['simulation_hours'])
        spec=plan(users=4,requests=1,latency=1)
        spec['limits'].update(reserved_tokens_per_minute=200)
        value=Simulation(spec).run()
        self.assertEqual(200,value['peak_window_reserved_tokens'])
        self.assertAlmostEqual(61/3600,value['simulation_hours'])

    def test_budget_and_oversize_blocked_jobs_are_not_success(self):
        spec=plan(users=2,requests=3)
        for change,reason,sent in [({'max_requests_per_job':2},'per_job_budget_exhausted',4),
                                   ({'max_requests':1},'global_budget_exhausted',1),
                                   ({'reserved_tokens_per_minute':99},'single_request_exceeds_tpm',0)]:
            current=copy.deepcopy(spec);current['limits'].update(change)
            value=Simulation(current).run()
            self.assertFalse(value['all_within_window']);self.assertEqual(sent,value['requests_sent'])
            self.assertIsNone(value['simulation_hours'])
            self.assertTrue(all(j['state']==reason for j in value['jobs_not_completed']))

    def test_finite_window_starts_with_one_minute_of_quota(self):
        spec=plan(users=2,requests=1,latency=1)
        spec['window_hours']=61/3600
        spec['limits'].update(requests_per_minute=1,reserved_tokens_per_minute=100)
        value=Simulation(spec).run()
        self.assertTrue(value['all_within_window'])
        self.assertEqual((1,100),(value['requirements']['minimum_rpm'],
                                  value['requirements']['minimum_reserved_tpm']))

    def test_deadline_and_resource_lower_bounds(self):
        spec=plan(users=100,requests=2204,latency=30)
        spec['limits'].update(max_requests=300000,max_requests_per_job=3000,
                              requests_per_minute=1000,reserved_tokens_per_minute=100000000)
        spec['workers']=4
        req=requirements(spec)
        self.assertEqual((220400,2204,35,69),(req['requests'],req['minimum_per_job_request_budget'],
                                           req['minimum_workers'],req['minimum_rpm']))
        result=Simulation(spec).run()
        self.assertEqual(100,result['completed_jobs']);self.assertFalse(result['all_within_window'])
        self.assertAlmostEqual(459.1666666667,result['simulation_hours'])

    def test_missing_or_nonfinite_assumptions_and_invalid_workers_rejected(self):
        for key,value in [('basis','unknown'),('window_hours',float('nan')),('workers',33)]:
            spec=plan();spec[key]=value
            with self.assertRaises(ValueError):Simulation(spec)
        spec=plan();spec['jobs'][1]['key']=spec['jobs'][0]['key']
        with self.assertRaises(ValueError):Simulation(spec)


if __name__=='__main__':unittest.main()
