#!/usr/bin/env bash
# 智用力在生产主机上的分阶段发布脚本（就地构建方式）。
# 用法：sudo bash zhiyongli-host-deploy.sh <preflight|prepare|backup|build|app|grants|worker|status|rollback>
# 每个阶段可重复执行；密钥只写入私有目录，不回显。回退不删除新增表、任务、回执和状态卷。
set -euo pipefail
cd "$(dirname "$0")"

PROJECT=${ZHIYONGLI_COMPOSE_PROJECT:-deploy}
ROOT=${ZHIYONGLI_ROOT:-/data/zhiyongli}
PRIV=$ROOT/private
STATE=$ROOT/state
BACKUP=$ROOT/backup
EXCLUDED=${ZHIYONGLI_EXCLUDED_USER_IDS:-22}
KEY_ID=${ZHIYONGLI_REVIEW_API_KEY_ID:-}
WORKERS=${ZHIYONGLI_WORKERS:-2}
ROLLBACK_TAG=sub2api-local:rollback-pre-zhiyongli

BASE=(-p "$PROJECT" -f docker-compose.local.yml -f docker-compose.override.yml)
FULL=(-p "$PROJECT" --env-file .env --env-file "$PRIV/compose.env" -f docker-compose.local.yml
  -f docker-compose.override.yml -f docker-compose.zhiyongli.yml -f docker-compose.zhiyongli.host.yml)

container() { docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$1"; }
PG=$(container postgres)
[ -n "$PG" ] || { echo "未找到项目 $PROJECT 的 postgres 容器，停止"; exit 1; }
# SQL 一律走标准输入，口令不进入进程参数。
sql() { docker exec -i "$PG" sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F" | "'; }
secret() { openssl rand -hex "$1"; }
private_file() { install -m 0400 -o 1000 -g 1000 /dev/stdin "$1"; }
wait_healthy() {
  local id=$1 i
  for i in $(seq 1 60); do
    [ "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")" = healthy ] && return 0
    sleep 5
  done
  echo "容器 $id 未在5分钟内恢复健康"; docker logs --tail 30 "$id" 2>&1 | cut -c1-300; return 1
}

case "${1:-}" in
preflight)
  # 以root执行时仓库属主不同，git可能拒绝读取；此处只作提示，不因此中止。
  echo "分支: $(git -C .. -c safe.directory='*' log -1 --format='%D %h' 2>/dev/null || echo 未能读取)"
  docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}} | {{.Image}} | {{.Status}}'
  echo "库 | 未删除账户 | 连接数 | 已有智用力迁移数"
  echo "select current_database(), (select count(*) from users where deleted_at is null), (select count(*) from pg_stat_activity),
    (select count(*) from schema_migrations where filename like '%zhiyongli%')" | sql
  echo "最大的五张表:"
  echo "select relname, pg_size_pretty(pg_total_relation_size(oid)) from pg_class where relkind='r' and relnamespace='public'::regnamespace
    order by pg_total_relation_size(oid) desc limit 5" | sql
  if [ -n "$KEY_ID" ]; then
    echo "评审Key（ID | 账户 | 状态）:"; echo "select id, user_id, status from api_keys where id=$KEY_ID and deleted_at is null" | sql
  fi
  df -h "$(dirname "$ROOT")" | tail -1
  [ -f .env ] && echo ".env 存在" || echo ".env 缺失"
  ;;
prepare)
  [ -n "$KEY_ID" ] || { echo "须设置 ZHIYONGLI_REVIEW_API_KEY_ID"; exit 1; }
  install -d -m 0700 -o 1000 -g 1000 "$PRIV" "$STATE"
  install -d -m 0700 "$BACKUP"
  [ -s "$PRIV/worker.key" ] || secret 32 | private_file "$PRIV/worker.key"
  # 评审专用Key直接从本库读入私有文件，不经过终端。
  echo "select key from api_keys where id=$KEY_ID and deleted_at is null and status='active'" | sql | private_file "$PRIV/model.key"
  [ -s "$PRIV/model.key" ] || { echo "评审Key不存在或未启用"; exit 1; }
  private_file "$PRIV/model.json" <<'JSON'
{"base_url": "http://sub2api:8080", "model": "deepseek-v4.1-flash-local", "api_key_file": "/run/zhiyongli/model.key", "timeout_seconds": 600}
JSON
  [ -s "$PRIV/schedule.json" ] || private_file "$PRIV/schedule.json" < zhiyongli-schedule.example.json
  # 工程限额：并发与线程数一致；累计上限用于防失控，不是服务端配额承诺。
  [ -s "$PRIV/flow.json" ] || private_file "$PRIV/flow.json" <<JSON
{"version": 1, "budget_key": "prod-launch-$(date +%Y%m%d)", "approved_by": "李煜",
 "reason": "2026-11-10已授权全员评分流程所需模型请求；首次生产运行按实际耗时复核",
 "max_concurrency": $WORKERS, "requests_per_minute": 20, "reserved_tokens_per_minute": 2000000,
 "max_requests": 30000, "transient_failure_limit": 3, "cooldown_seconds": 60,
 "max_429_retries": 2, "max_interrupted_retries": 2}
JSON
  private_file "$PRIV/compose.env" <<ENV
ZHIYONGLI_PRIVATE_DIR=$PRIV
ZHIYONGLI_STATE_DIR=$STATE
ZHIYONGLI_EXCLUDED_USER_IDS=$EXCLUDED
ZHIYONGLI_REVIEW_API_KEY_ID=$KEY_ID
ZHIYONGLI_MAX_REQUESTS_PER_JOB=3000
ZHIYONGLI_NEW_REQUESTS_PER_TURN=20
ZHIYONGLI_WORKERS=$WORKERS
ZHIYONGLI_SCHEDULE_MODE=disabled
ENV
  docker compose "${FULL[@]}" --profile zhiyongli config --quiet
  ls -l "$PRIV" | awk '{print $1, $3, $4, $NF}'
  ;;
backup)
  # 三份迁移只新增表；审计正文表体量大且不被改动，备份其结构、不含其数据。
  out=$BACKUP/pre-zhiyongli-$(date +%Y%m%d-%H%M%S).sql.gz
  docker exec "$PG" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --exclude-table-data=llm_audit_logs' | gzip > "$out"
  gzip -t "$out"; ls -lh "$out" | awk '{print $5, $NF}'
  ;;
build)
  [ -f "$PRIV/compose.env" ] || { echo "先执行 prepare"; exit 1; }
  docker image inspect "$ROLLBACK_TAG" >/dev/null 2>&1 || docker tag "$(docker inspect -f '{{.Image}}' "$(container sub2api)")" "$ROLLBACK_TAG"
  docker compose "${FULL[@]}" --profile zhiyongli build sub2api zhiyongli-worker
  docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedSince}}' | grep -E '^(sub2api-local|zhiyongli-worker-local):'
  ;;
app)
  # 回退后 latest 指回旧镜像；此时须重新 build，不能把旧镜像当新版本启动。
  [ "$(docker image inspect -f '{{.Id}}' sub2api-local:latest)" != "$(docker image inspect -f '{{.Id}}' "$ROLLBACK_TAG")" ] \
    || { echo "sub2api-local:latest 仍是发布前镜像，先执行 build"; exit 1; }
  docker compose "${FULL[@]}" up -d --no-build --no-deps sub2api
  wait_healthy "$(container sub2api)"
  echo "select filename from schema_migrations where filename like '%zhiyongli%' order by 1" | sql
  ;;
grants)
  sql < zhiyongli-permissions.sql
  for role in worker source; do
    file=$PRIV/$([ "$role" = worker ] && echo product || echo source).dsn
    [ -s "$file" ] && continue
    pw=$(secret 24)
    echo "ALTER ROLE zhiyongli_$role PASSWORD '$pw'" | sql
    db=$(echo "select current_database()" | sql)
    echo "postgresql://zhiyongli_$role:$pw@postgres:5432/$db" | private_file "$file"
  done
  echo "select rolname, rolcanlogin, rolsuper from pg_roles where rolname like 'zhiyongli_%' order by 1" | sql
  ;;
worker)
  [ -s "$PRIV/product.dsn" ] && [ -s "$PRIV/source.dsn" ] || { echo "先执行 grants"; exit 1; }
  docker compose "${FULL[@]}" --profile zhiyongli up -d --no-build --no-deps zhiyongli-worker
  wait_healthy "$(container zhiyongli-worker)"
  docker logs --tail 10 "$(container zhiyongli-worker)" 2>&1 | cut -c1-300
  ;;
status)
  docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}} | {{.Image}} | {{.Status}}'
  echo "任务状态分布:"; echo "select state, count(*) from zhiyongli_jobs group by 1 order by 1" | sql
  # 默认只给摘要；需要完整运行账目时加参数 full。
  docker exec -i -e FULL="${2:-}" "$(container zhiyongli-worker)" python - <<'PY'
import json, os, subprocess
out = subprocess.run(['python', '/app/tools/zhiyongli/operations.py', '--state', '/state', '--flow-config', '/run/zhiyongli/flow.json'],
                     capture_output=True, text=True, check=True).stdout
if os.environ.get('FULL') == 'full':
    print(out)
else:
    d = json.loads(out); m = d['model']
    print('请求预算', m['request_budget'], '| 模型请求状态', m['states'], '| 熔断', m['circuit'])
    print('到期', {k: d['deadlines'][k] for k in ('pending', 'overdue')}, '| 心跳', d['heartbeat']['state'], '| 告警', d['alerts'])
PY
  ;;
rollback)
  worker=$(container zhiyongli-worker)
  [ -z "$worker" ] || docker compose "${FULL[@]}" --profile zhiyongli stop zhiyongli-worker
  docker tag "$ROLLBACK_TAG" sub2api-local:latest
  docker compose "${BASE[@]}" up -d --no-build --no-deps sub2api
  wait_healthy "$(container sub2api)"
  echo "已回到发布前的应用镜像；新增表、任务、回执和状态卷保留。源码请另行切回原分支。"
  ;;
*)
  sed -n 2,4p "$0"; exit 2
  ;;
esac
