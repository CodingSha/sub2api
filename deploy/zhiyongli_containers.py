#!/usr/bin/env python3
"""智用力一次性评审容器定向收尾；默认仅盘点，不删除镜像或数据卷。"""
import argparse
import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

OWNER = 'com.apsat.zhiyongli.owner'
ROLE = 'com.apsat.zhiyongli.role'
IMAGE_PREFIX = 'zhiyongli-worker-local:'


def docker(*args):
    return subprocess.check_output(['docker'] + list(args), stderr=subprocess.PIPE, timeout=60)


def inspect_many(ids):
    rows = []
    for start in range(0, len(ids), 50):
        rows.extend(json.loads(docker('inspect', *ids[start:start + 50])))
    return rows


def owned(row, tags):
    config = row.get('Config') or {}
    labels = config.get('Labels') or {}
    # 新任务同时验证标签与镜像；历史任务仅按实际镜像归属，不能只按名称删除。
    image_owned = config.get('Image', '').startswith(IMAGE_PREFIX) or any(
        tag.startswith(IMAGE_PREFIX) for tag in tags.get(row['Image'], []))
    explicitly_other = OWNER in labels and labels[OWNER] != 'zhiyongli'
    persistent = ROLE in labels and labels[ROLE] != 'ephemeral'
    return image_owned and not explicitly_other and not persistent


def eligible(row, tags, now, min_age):
    state = row.get('State') or {}
    if state.get('Running') or state.get('Status') != 'exited' or not owned(row, tags):
        return False
    # 常驻 Worker 即使停止也不能按一次性任务清理。
    if (row.get('HostConfig', {}).get('RestartPolicy') or {}).get('Name', 'no') not in ('', 'no'):
        return False
    finished = state.get('FinishedAt', '')
    try:
        stamp = datetime.datetime.strptime(finished[:19], '%Y-%m-%dT%H:%M:%S')
        ended = stamp.replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return False
    return ended > 0 and now - ended >= min_age


def metadata(row):
    # 不落盘 Config.Env、凭证或完整命令参数。
    return dict(id=row['Id'], name=row['Name'], image=row['Image'],
                image_reference=row['Config']['Image'], state=row['State'],
                auto_remove=row['HostConfig'].get('AutoRemove', False),
                restart_policy=row['HostConfig'].get('RestartPolicy'),
                mounts=[dict(type=m['Type'], source=m.get('Source'), destination=m['Destination'])
                        for m in row.get('Mounts', [])])


def archive_logs(row, folder):
    target = folder / (row['Id'] + '.log.gz')
    temporary = target.with_suffix('.tmp')
    process = subprocess.Popen(['docker', 'logs', '--timestamps', row['Id']],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    digest = hashlib.sha256()
    try:
        with gzip.open(str(temporary), 'wb') as stream:
            for block in iter(lambda: process.stdout.read(1024 * 1024), b''):
                digest.update(block)
                stream.write(block)
        if process.wait() != 0:
            raise RuntimeError('容器日志读取失败，保留原容器')
        verify = hashlib.sha256()
        with gzip.open(str(temporary), 'rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                verify.update(block)
        if verify.digest() != digest.digest():
            raise RuntimeError('日志归档校验失败，保留原容器')
        temporary.replace(target)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()
        process.stdout.close()
    return dict(file=target.name, uncompressed_sha256=digest.hexdigest())


def cleanup(rows, tags, folder, apply=False, min_age=600):
    now = time.time()
    report = dict(apply=apply, at=now, removed=[], preserved=[], failures=[])
    for row in rows:
        if not eligible(row, tags, now, min_age):
            continue
        ident = row['Id']
        if not apply:
            report['preserved'].append(ident)
            continue
        try:
            logs = archive_logs(row, folder)
            (folder / (ident + '.json')).write_text(json.dumps(
                dict(container=metadata(row), logs=logs), ensure_ascii=False, indent=2))
            # 归档后再核状态。按不可变ID操作，不强杀、不删除卷。
            current = inspect_many([ident])[0]
            if not eligible(current, tags, time.time(), min_age):
                report['preserved'].append(ident)
                continue
            docker('rm', ident)
            report['removed'].append(ident)
        except Exception as error:
            report['failures'].append(dict(id=ident, error=type(error).__name__))
        (folder / 'result.json').write_text(json.dumps(report, indent=2))
    (folder / 'result.json').write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--min-age-seconds', type=int, default=600)
    parser.add_argument('--archive-root', type=Path, default=Path('/data/zhiyongli/maintenance/containers'))
    args = parser.parse_args()
    if args.min_age_seconds < 0:
        parser.error('保留时长不能为负数')
    os.umask(0o077)
    args.archive_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (args.archive_root / 'cleanup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ids = docker('ps', '-aq').decode().split()
        rows = inspect_many(ids)
        image_ids = sorted({row['Image'] for row in rows})
        tags = {}
        for start in range(0, len(image_ids), 50):
            images = json.loads(docker('image', 'inspect', *image_ids[start:start + 50]))
            tags.update({image['Id']: image.get('RepoTags') or [] for image in images})
        folder = args.archive_root / (str(time.time_ns()) if hasattr(time, 'time_ns') else str(int(time.time() * 1000000)))
        folder.mkdir(mode=0o700)
        candidates = [row for row in rows if eligible(row, tags, time.time(), args.min_age_seconds)]
        (folder / 'inventory.json').write_text(json.dumps([metadata(row) for row in candidates], indent=2))
        report = cleanup(rows, tags, folder, args.apply, args.min_age_seconds)
        print(json.dumps(dict(candidates=len(candidates), removed=len(report['removed']),
                              failures=len(report['failures']), archive=str(folder))))
        if report['failures']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
