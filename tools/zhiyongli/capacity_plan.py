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
    if type(plan.get('version')) is not int or plan['version'] not in (1, 2) or plan.get('basis') not in {'synthetic', 'measured'}:
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

    if plan['version'] == 2:
        def nonnegative(value, name, integer=False):
            if value == 0 and type(value) in ((int,) if integer else (int, float)):
                return
            positive(value, name, integer)
        nonnegative(plan['initial_used_requests'], '全局既有预算', True)
        groups = plan['budget_groups']
        if not isinstance(groups, dict) or not groups:
            raise ValueError('v2须显式登记累计预算组')
        for name, group in groups.items():
            if not isinstance(name, str) or not name:
                raise ValueError('预算组键无效')
            positive(group['limit'], '组累计预算', True)
            nonnegative(group['used'], '组既有预算', True)
        edges = {}
        for job in jobs:
            nonnegative(job['release_after_seconds'], '到达时间')
            nonnegative(job['deadline_seconds'], '任务截止时间')
            deps = job['depends_on']
            if (not isinstance(deps, list) or any(not isinstance(d, str) or d not in keys for d in deps)
                    or len(set(deps)) != len(deps) or job['budget_group'] not in groups):
                raise ValueError('依赖须为已知唯一任务且预算组须登记')
            edges[job['key']] = set(deps)
        # 拓扑剥离，拒绝自依赖和循环；不把未知准备状态当完成。
        done = set()
        while len(done) < len(edges):
            ready = {key for key, deps in edges.items() if key not in done and deps <= done}
            if not ready:
                raise ValueError('任务依赖存在循环')
            done.update(ready)


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
        self.v2 = plan['version'] == 2
        self.initial_used = plan.get('initial_used_requests', 0) if self.v2 else 0
        self.groups = {key: dict(value) for key, value in plan['budget_groups'].items()} if self.v2 else {}
        if self.v2:
            for job, spec in zip(self.jobs, plan['jobs']):
                job.update(release=spec['release_after_seconds'], deadline=spec['deadline_seconds'],
                           dependencies=spec['depends_on'], group=spec['budget_group'])
        self.by_key = {job['key']: job for job in self.jobs}
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
            if self.v2 and (job['release'] > self.now or any(
                    self.by_key[key]['state'] != 'completed' for key in job['dependencies'])):
                self.ready.append(index); continue
            stage = job['stages'][job['stage']]; reserve = stage['reserved_tokens']
            reason = None
            if reserve > self.limits['reserved_tokens_per_minute']:
                reason = 'single_request_exceeds_tpm'
            elif job['sent'] >= self.limits['max_requests_per_job']:
                reason = 'per_job_budget_exhausted'
            elif self.v2 and self.groups[job['group']]['used'] >= self.groups[job['group']]['limit']:
                reason = 'group_budget_exhausted'
            elif self.sent + self.initial_used >= self.limits['max_requests']:
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
            if self.v2:self.groups[job['group']]['used'] += 1
            self.peak_active = max(self.peak_active, len(self.active))
            self.peak_window_requests = max(self.peak_window_requests, len(self.quota))
            self.peak_window_tokens = max(self.peak_window_tokens, self.tokens)

    def run(self):
        while self.ready or self.active:
            self.complete(); self.dispatch()
            if not self.ready and not self.active:
                break
            times = [q[0][0] for q in (self.active, self.quota) if q]
            if self.v2:
                times.extend(self.jobs[i]['release'] for i in self.ready if self.jobs[i]['release'] > self.now)
            if not times:
                if self.v2:
                    for index in self.ready:self.jobs[index]['state'] = 'dependency_not_completed'
                break
            self.now = min(times)
        completed = [j for j in self.jobs if j['state'] == 'completed']
        last = max((j['finished'] for j in completed), default=0)
        window = self.plan['window_hours'] * 3600
        result = dict(version=self.plan['version'], basis=self.plan['basis'], assumptions=self.plan['assumptions'],
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
                                 '未模拟未列入阶段的补读、说明生成与复查、重试、unknown、熔断、进程重启、数据库和轮询开销',
                                 '用户阶段串行，延迟和配额由输入明确指定；不提供实际配额',
                                 '起点未装载实际Worker的分钟窗口、活动请求及冷却状态；v1累计用量从零开始，v2由输入显式扣减',
                                 '以无usage的保守token预留核窗口，不是模型实际token计费量'])

        if self.v2:
            late = [job['key'] for job in completed if job['finished'] > job['deadline']]
            result.update(initial_used_requests=self.initial_used, budget_groups=self.groups,
                          late_job_deadlines=late,
                          job_results=[dict(key=job['key'], state=job['state'],
                                            finished_seconds=job['finished'], deadline_seconds=job['deadline'])
                                       for job in self.jobs])
            result['all_within_window'] = result['all_within_window'] and not late
            result['limitations'].extend([
                'v2依赖只按completed放行；未决或预算阻断依赖不推定成功',
                '既有预算仅扣累计额度，未模拟既有分钟窗口、活动请求、冷却和人工审核',
                'requirements仅列输入工作量及单任务下界，不含预算组和既有用量；须结合组额度及模拟阻断判断',
                'elapsed_until_stop可能包含等待无关到达或旧窗口过期，不能当首次阻断时刻或实际ETA',
                '截止与到达时间须由输入提供；未提供真实全阶段负荷不能据此承诺期限'])
        return result


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
