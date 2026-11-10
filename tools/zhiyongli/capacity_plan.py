"""离线容量测算；只读取计数与假设，不连接数据库或模型，不产生员工评分。"""
import argparse
from collections import deque
import hashlib
import heapq
import json
import math
from pathlib import Path


def positive(value, name, integer=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            or integer and type(value) is not int):
        raise ValueError(name+'须为有限正数'+('整数' if integer else ''))
    return value


def validate(plan):
    if type(plan.get('version')) is not int or plan['version'] != 1 or plan.get('basis') not in {'synthetic', 'measured'}:
        raise ValueError('须声明版本及synthetic/measured依据，不能把假设当实测')
    if not isinstance(plan.get('assumptions'), str) or not plan['assumptions'].strip():
        raise ValueError('须写明测量来源或假设及遗漏范围')
    positive(plan['window_hours'], '处理窗口')
    positive(plan['workers'], '执行线程', True)
    limits = plan['limits']
    for name in ('max_concurrency', 'requests_per_minute', 'reserved_tokens_per_minute',
                 'max_requests', 'max_requests_per_job'):
        positive(limits[name], name, True)
    if not 1 <= plan['workers'] <= min(32, limits['max_concurrency']):
        raise ValueError('线程数须符合Worker的1至32及不超过模型并发限制')
    jobs = plan['jobs']
    if not isinstance(jobs, list) or not jobs or len(jobs) > 10000:
        raise ValueError('须提供1至10000个用户周期')
    keys = []
    for job in jobs:
        if not isinstance(job.get('key'), str) or not job['key'].strip() or not job.get('stages'):
            raise ValueError('用户周期须有稳定键及非空阶段')
        keys.append(job['key'])
        for stage in job['stages']:
            positive(stage['count'], '阶段请求数', True)
            positive(stage['latency_seconds'], '阶段延迟')
            positive(stage['reserved_tokens'], '阶段保守token预留', True)
    if len(set(keys)) != len(keys):
        raise ValueError('用户周期键重复')
    if sum(s['count'] for j in jobs for s in j['stages']) > 1000000:
        raise ValueError('模拟超过100万次请求；先按明确负载分期测算')


def requirements(plan):
    """给出资源必要下界；顺序依赖、突发、失败与后台处理另由模拟或实测核查。"""
    validate(plan)
    jobs = plan['jobs']; minutes = plan['window_hours'] * 60
    count = sum(s['count'] for j in jobs for s in j['stages'])
    tokens = sum(s['count'] * s['reserved_tokens'] for j in jobs for s in j['stages'])
    seconds = sum(s['count'] * s['latency_seconds'] for j in jobs for s in j['stages'])
    return dict(requests=count, reserved_tokens=tokens, minimum_total_request_budget=count,
                minimum_per_job_request_budget=max(sum(s['count'] for s in j['stages']) for j in jobs),
                minimum_workers=math.ceil(seconds / (minutes * 60)),
                # 空账簿可在起点使用一整分钟额度；有限时窗不能把稳态均值冒称必要下界。
                minimum_rpm=math.ceil(count / (minutes + 1)),
                minimum_reserved_tpm=max(math.ceil(tokens / (minutes + 1)),
                                         max(s['reserved_tokens'] for j in jobs for s in j['stages'])),
                longest_serial_job_hours=max(sum(s['count'] * s['latency_seconds'] for s in j['stages'])
                                             for j in jobs) / 3600)


class Simulation:
    """按用户轮转，每用户阶段串行；未返回请求的预留与现有ModelFlow一样持续占额。"""
    def __init__(self, plan):
        validate(plan)
        self.plan = plan; self.limits = plan['limits']; self.now = 0.0
        self.jobs = [dict(key=j['key'], stages=j['stages'], stage=0, remaining=j['stages'][0]['count'],
                          sent=0, state='queued', finished=None) for j in plan['jobs']]
        self.ready = deque(range(len(self.jobs)))
        self.active = []; self.quota = []; self.tokens = 0; self.sent = 0
        self.peak_active = 0; self.peak_window_requests = 0; self.peak_window_tokens = 0

    def complete(self):
        while self.active and self.active[0][0] <= self.now:
            _, index = heapq.heappop(self.active); job = self.jobs[index]
            job['remaining'] -= 1
            if not job['remaining']:
                job['stage'] += 1
                if job['stage'] == len(job['stages']):
                    job.update(state='completed', finished=self.now); continue
                job['remaining'] = job['stages'][job['stage']]['count']
            job['state'] = 'queued'; self.ready.append(index)
        while self.quota and self.quota[0][0] <= self.now:
            _, _, tokens = heapq.heappop(self.quota); self.tokens -= tokens

    def dispatch(self):
        for _ in range(len(self.ready)):
            if len(self.active) >= self.plan['workers']:
                break
            index = self.ready.popleft(); job = self.jobs[index]
            stage = job['stages'][job['stage']]; reserve = stage['reserved_tokens']
            reason = None
            if reserve > self.limits['reserved_tokens_per_minute']:
                reason = 'single_request_exceeds_tpm'
            elif job['sent'] >= self.limits['max_requests_per_job']:
                reason = 'per_job_budget_exhausted'
            elif self.sent >= self.limits['max_requests']:
                reason = 'global_budget_exhausted'
            if reason:
                job['state'] = reason; continue
            if (len(self.quota) >= self.limits['requests_per_minute']
                    or self.tokens + reserve > self.limits['reserved_tokens_per_minute']):
                self.ready.append(index); continue
            finish = self.now + stage['latency_seconds']
            heapq.heappush(self.active, (finish, index))
            # ModelFlow的窗口含近60秒和仍active的请求；没有实测usage时保留完整预留。
            heapq.heappush(self.quota, (max(self.now + 60, finish), self.sent, reserve))
            self.tokens += reserve; self.sent += 1; job['sent'] += 1; job['state'] = 'active'
            self.peak_active = max(self.peak_active, len(self.active))
            self.peak_window_requests = max(self.peak_window_requests, len(self.quota))
            self.peak_window_tokens = max(self.peak_window_tokens, self.tokens)

    def run(self):
        while self.ready or self.active:
            self.complete(); self.dispatch()
            if not self.ready and not self.active:
                break
            times = [q[0][0] for q in (self.active, self.quota) if q]
            if not times:
                break
            self.now = min(times)
        completed = [j for j in self.jobs if j['state'] == 'completed']
        last = max((j['finished'] for j in completed), default=0)
        window = self.plan['window_hours'] * 3600
        return dict(version=1, basis=self.plan['basis'], assumptions=self.plan['assumptions'],
                    input_sha256=hashlib.sha256(json.dumps(self.plan,ensure_ascii=False,sort_keys=True,
                                                          separators=(',', ':')).encode()).hexdigest(),
                    workers=self.plan['workers'], limits=dict(self.limits),
                    requirements=requirements(self.plan), window_hours=self.plan['window_hours'],
                    jobs=len(self.jobs), completed_jobs=len(completed), requests_sent=self.sent,
                    simulation_hours=last / 3600 if len(completed) == len(self.jobs) else None,
                    elapsed_until_stop_hours=self.now / 3600,
                    all_within_window=len(completed) == len(self.jobs) and last <= window,
                    peak_active=self.peak_active, peak_window_requests=self.peak_window_requests,
                    peak_window_reserved_tokens=self.peak_window_tokens,
                    jobs_not_completed=[dict(key=j['key'], state=j['state']) for j in self.jobs
                                        if j['state'] != 'completed'],
                    late_jobs=[j['key'] for j in completed if j['finished'] > window],
                    production_ready=False, real_model_requests=0,
                    limitations=['离线调度模型，不执行真实评分或证明百人模型吞吐',
                                 '未模拟未列入阶段的补读、重试、unknown、熔断、进程重启、数据库和轮询开销',
                                 '用户阶段串行，延迟和配额由输入明确指定；不提供实际配额',
                                 '起点使用空配额账簿，不读取或重置实际Worker已有预算及冷却状态',
                                 '以无usage的保守token预留核窗口，不是模型实际token计费量'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = Simulation(json.loads(args.input.read_text())).run()
    from runtime import atomic
    atomic(args.output, result)
    print(json.dumps({k: result[k] for k in ('jobs', 'completed_jobs', 'simulation_hours',
                                           'all_within_window', 'production_ready')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
