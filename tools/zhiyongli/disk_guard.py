"""生产评审写入前保留共享磁盘余量；本地未配置时不启用。"""
import os
from pathlib import Path
import shutil


def require_space(path, pending_bytes=0):
    gib = os.environ.get('ZHIYONGLI_MIN_FREE_GIB')
    percent = os.environ.get('ZHIYONGLI_MIN_FREE_PERCENT')
    if gib is None and percent is None:
        return
    # 配置缺失或非法时明确失败，不悄悄关闭生产保护。
    minimum = int(gib)
    ratio = int(percent)
    if minimum < 1 or not 1 <= ratio < 100:
        raise ValueError('磁盘保护须明确正整数GiB及1至99的剩余比例')
    directory = Path(path)
    while not directory.exists():
        directory = directory.parent
    usage = shutil.disk_usage(directory)
    if usage.free - pending_bytes < max(minimum * 1024**3, usage.total * ratio / 100):
        from pipeline import Paused
        raise Paused('needs_review', '共享磁盘余量不足，已暂停评审并保留原任务及回执；请核对磁盘后恢复')
