#!/usr/bin/env bash
# 安装定向收尾定时器；不重启应用、Worker、数据库或Docker。
set -euo pipefail
cd "$(dirname "$0")"
install -d -m 0755 /data/zhiyongli/ops
install -m 0755 zhiyongli_containers.py /data/zhiyongli/ops/zhiyongli_containers.py
install -m 0644 zhiyongli-container-cleanup.service /etc/systemd/system/
install -m 0644 zhiyongli-container-cleanup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now zhiyongli-container-cleanup.timer
systemctl is-active zhiyongli-container-cleanup.timer
