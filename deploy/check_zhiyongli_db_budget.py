#!/usr/bin/env python3
"""离线核对解析后Compose的数据库连接预算，不连接数据库、不修改配置。"""
import argparse
import json
from pathlib import Path
import sys


def count(value, name, optional=False):
    if optional and value is None:
        return None
    if type(value) is str and value.isdecimal():
        value = int(value)
    if type(value) is not int or value < 0:
        raise ValueError(name+'须为非负整数'+('或null（尚未核定）' if optional else ''))
    return value


def compose_limits(compose):
    services = compose['services']
    app = services['sub2api']['environment']
    opened = count(app['DATABASE_MAX_OPEN_CONNS'], '应用最大连接')
    idle = count(app['DATABASE_MAX_IDLE_CONNS'], '应用空闲连接')
    if opened < 1 or idle > opened:
        raise ValueError('应用最大连接须为正数且不小于空闲连接')
    command = services['zhiyongli-worker']['command']
    if not isinstance(command, list) or not all(isinstance(part, str) for part in command):
        raise ValueError('须提供Compose解析后的Worker命令列表')
    if command.count('--workers') != 1:
        raise ValueError('须显式且只配置一次--workers')
    workers = count(command[command.index('--workers')+1], 'Worker线程')
    if not 1 <= workers <= 32:
        raise ValueError('Worker线程须在实际支持的1至32之间')
    for name in ('sub2api', 'zhiyongli-worker'):
        service = services[name]
        if service.get('scale', 1) != 1 or service.get('deploy', {}).get('replicas', 1) != 1:
            raise ValueError('本检查只支持生产当前的单应用、单Worker进程，不能漏算额外副本')
    return opened, idle, workers


def cluster_budget(key, spec, app_connections, per_worker, workers):
    total = count(spec['max_connections'], 'PG最大连接')
    reserved = count(spec['superuser_reserved_connections'], 'PG超级用户保留')
    reserved += count(spec['reserved_connections'], 'PG其他保留')
    if total <= reserved:
        raise ValueError('PG普通连接上限须大于零')
    ordinary = total-reserved
    other = count(spec['other_services_connections'], '其他服务额度', optional=True)
    operations = count(spec['operations_connections'], '运维及只读查询额度', optional=True)
    unknown = [name for name, value in (('other_services_connections', other),
                                       ('operations_connections', operations)) if value is None]
    known = app_connections+workers*per_worker+(other or 0)+(operations or 0)
    status = 'exceeded' if known > ordinary else 'unknown' if unknown else 'fits_declared_budget'
    maximum = None if unknown else max(0, min(32, (ordinary-app_connections-other-operations)//per_worker))
    return dict(cluster=key, ordinary_connection_ceiling=ordinary,
                application_pool_connections=app_connections, worker_connections=workers*per_worker,
                worker_connections_per_thread=per_worker, other_services_connections=other,
                operations_connections=operations, known_connection_demand=known,
                unknown_allocations=unknown, over_ceiling_by=max(0, known-ordinary),
                maximum_workers_for_declared_budget=maximum, status=status)


def assess(plan, compose):
    if not isinstance(plan, dict) or not isinstance(compose, dict):
        raise ValueError('连接预算及Compose须为JSON对象')
    if type(plan.get('version')) is not int or plan['version'] != 1:
        raise ValueError('连接预算版本须为1')
    if (plan.get('basis') not in ('synthetic', 'observed') or
            not isinstance(plan.get('notes'), str) or not plan['notes'].strip()):
        raise ValueError('须声明synthetic/observed及证据、假设和遗漏范围')
    product, source = plan['product_cluster'], plan['source_cluster']
    if not all(isinstance(key, str) and key.strip() for key in (product, source)):
        raise ValueError('须由部署负责人确认任务库和来源库的实例标识，不能猜测DSN别名')
    if not isinstance(plan['clusters'], dict) or set(plan['clusters']) != {product, source}:
        raise ValueError('连接额度须逐一覆盖实际实例，不得遗漏或混入未使用实例')
    opened, idle, workers = compose_limits(compose)
    rows = [cluster_budget(key, spec, opened if key == product else 0,
                           int(key == product)+int(key == source), workers)
            for key, spec in sorted(plan['clusters'].items())]
    fits = all(row['status'] == 'fits_declared_budget' for row in rows)
    return dict(version=1, basis=plan['basis'], notes=plan['notes'], workers=workers,
                application_max_open_connections=opened, application_max_idle_connections=idle,
                clusters=rows, database_budget_fits=fits, production_ready=False,
                limitations=['仅核对声明的单应用、单Worker进程连接上限，不验证实际负载或配额',
                             '实例归属须人工核实；不同DSN或不同库名不代表独立PG实例',
                             '额度未知不能当零；即使额度符合也不代表百人评分按期完成'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--compose-json', required=True, help='解析后的JSON文件；-表示从标准输入读取，避免落盘完整私有配置')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Compose可能含真实凭证；只输出计数，不回显原配置或异常正文。
    try:
        raw = sys.stdin.read() if args.compose_json == '-' else Path(args.compose_json).read_text()
        result = assess(json.loads(args.plan.read_text()), json.loads(raw))
    except (ValueError, KeyError, IndexError, TypeError):
        raise SystemExit('连接预算输入无效；核对实例、额度和解析后的单进程Compose，未输出配置正文')
    with args.output.open('w', encoding='utf-8') as target:
        target.write(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(database_budget_fits=result['database_budget_fits'],
                          clusters=[dict(cluster=row['cluster'], status=row['status']) for row in result['clusters']],
                          production_ready=False), ensure_ascii=False))
    raise SystemExit(0 if result['database_budget_fits'] else 2)


if __name__ == '__main__':
    main()
