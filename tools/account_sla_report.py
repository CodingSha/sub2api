#!/usr/bin/env python3
"""Report Ops SLA metrics grouped by upstream account.

The calculation matches the Ops dashboard:

    SLA = successful requests / (successful requests + non-business errors)

Successful requests come from usage_logs. Errors come from ops_error_logs;
count_tokens probes and business-limited errors are excluded from the SLA scope.
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Dict, List, Optional, Sequence

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # Python 3.8 and earlier
    ZoneInfo = None

    class ZoneInfoNotFoundError(Exception):
        pass


class QueryRunner:
    def __init__(self, command_prefix: Sequence[str], env: Dict[str, str]) -> None:
        self.command_prefix = list(command_prefix)
        self.env = env

    def run_csv(self, sql: str) -> List[Dict[str, str]]:
        copy_sql = f"COPY ({sql}) TO STDOUT WITH CSV HEADER"
        proc = subprocess.run(
            [*self.command_prefix, "-v", "ON_ERROR_STOP=1", "-c", copy_sql],
            env=self.env,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "psql command failed")
        return list(csv.DictReader(proc.stdout.splitlines()))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report account-level SLA using the same scope as the Ops dashboard.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--start",
        help="Inclusive start time (YYYY-MM-DD or ISO-8601). Defaults to 24 hours before --end.",
    )
    parser.add_argument(
        "--end",
        help="Exclusive end time (YYYY-MM-DD or ISO-8601). Defaults to now.",
    )
    parser.add_argument(
        "--timezone",
        default="Asia/Shanghai",
        help="Timezone used for date-only or timezone-less values.",
    )
    parser.add_argument("--platform", help="Only include this platform, for example openai or anthropic.")
    parser.add_argument("--group-id", type=positive_int, help="Only include requests routed through this group ID.")
    parser.add_argument("--account-id", type=positive_int, help="Only include this account ID.")
    parser.add_argument(
        "--account-ids",
        type=parse_account_ids,
        help="Only include these account IDs, separated by commas (for example 12,18,25).",
    )
    parser.add_argument(
        "--min-requests",
        type=non_negative_int,
        default=1,
        help="Minimum total requests required for an account to be shown.",
    )
    parser.add_argument("--output", help="Write CSV to this path instead of printing a table.")

    parser.add_argument("--env-file", help="Path to .env. Defaults to deploy/.env, then .env.")
    parser.add_argument("--database-url", help="PostgreSQL URL. Overrides DATABASE_URL.")
    parser.add_argument("--database-host", help="Overrides DATABASE_HOST.")
    parser.add_argument("--database-port", type=int, help="Overrides DATABASE_PORT.")
    parser.add_argument("--database-user", help="Overrides DATABASE_USER/POSTGRES_USER.")
    parser.add_argument("--database-password", help="Overrides DATABASE_PASSWORD/POSTGRES_PASSWORD.")
    parser.add_argument("--database-name", help="Overrides DATABASE_DBNAME/POSTGRES_DB.")
    parser.add_argument("--database-sslmode", help="Overrides DATABASE_SSLMODE.")
    parser.add_argument(
        "--use-docker",
        choices=("auto", "always", "never"),
        default="auto",
        help="Run psql inside the Docker Compose PostgreSQL service.",
    )
    parser.add_argument("--compose-file", default="deploy/docker-compose.yml")
    parser.add_argument("--compose-service", default="postgres")
    return parser.parse_args()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def parse_account_ids(value: str) -> List[int]:
    raw_ids = [part.strip() for part in value.split(",")]
    if not raw_ids or any(not part for part in raw_ids):
        raise argparse.ArgumentTypeError("must be a comma-separated list of account IDs")
    try:
        account_ids = [positive_int(part) for part in raw_ids]
    except (ValueError, argparse.ArgumentTypeError) as exc:
        raise argparse.ArgumentTypeError(
            "must be a comma-separated list of positive account IDs"
        ) from exc
    return sorted(set(account_ids))


def load_env_file(path: Optional[Path]) -> Dict[str, str]:
    if path is None or not path.exists():
        return {}

    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def find_env_file(explicit: Optional[str]) -> Optional[Path]:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"env file not found: {path}")
        return path
    for candidate in (Path("deploy/.env"), Path(".env")):
        if candidate.is_file():
            return candidate
    return None


def merged_env(args: argparse.Namespace, env_file: Optional[Path]) -> Dict[str, str]:
    values = load_env_file(env_file)
    values.update(os.environ)
    overrides = {
        "DATABASE_URL": args.database_url,
        "DATABASE_HOST": args.database_host,
        "DATABASE_PORT": str(args.database_port) if args.database_port else None,
        "DATABASE_USER": args.database_user,
        "DATABASE_PASSWORD": args.database_password,
        "DATABASE_DBNAME": args.database_name,
        "DATABASE_SSLMODE": args.database_sslmode,
    }
    for key, value in overrides.items():
        if value:
            values[key] = value
    return values


def env_value(values: Dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        if values.get(key):
            return values[key]
    return default


def should_use_docker(args: argparse.Namespace, values: Dict[str, str]) -> bool:
    if args.use_docker == "always":
        return True
    if args.use_docker == "never":
        return False
    local_database = bool(values.get("DATABASE_URL")) or env_value(
        values, "DATABASE_HOST", default=""
    ) not in {"", "postgres", "db"}
    if shutil.which("psql") and local_database:
        return False
    return bool(shutil.which("docker") and Path(args.compose_file).is_file())


def build_runner(
    args: argparse.Namespace, env_file: Optional[Path], values: Dict[str, str]
) -> QueryRunner:
    command_env = os.environ.copy()
    command_env.update(values)

    if should_use_docker(args, values):
        command = ["docker", "compose"]
        if env_file:
            command.extend(["--env-file", str(env_file)])
        command.extend(["-f", args.compose_file, "exec", "-T", args.compose_service, "psql"])
        command.extend(
            [
                "-U",
                env_value(values, "POSTGRES_USER", "DATABASE_USER", default="sub2api"),
                "-d",
                env_value(values, "POSTGRES_DB", "DATABASE_DBNAME", default="sub2api"),
            ]
        )
        return QueryRunner(command, command_env)

    if not shutil.which("psql"):
        raise SystemExit("psql not found; install it or use --use-docker always")
    if values.get("DATABASE_URL"):
        return QueryRunner(["psql", "-d", values["DATABASE_URL"]], command_env)

    password = env_value(values, "DATABASE_PASSWORD", "POSTGRES_PASSWORD")
    if password:
        command_env["PGPASSWORD"] = password
    command = [
        "psql",
        "-h",
        env_value(values, "DATABASE_HOST", default="localhost"),
        "-p",
        env_value(values, "DATABASE_PORT", default="5432"),
        "-U",
        env_value(values, "DATABASE_USER", "POSTGRES_USER", default="sub2api"),
        "-d",
        env_value(values, "DATABASE_DBNAME", "POSTGRES_DB", default="sub2api"),
    ]
    sslmode = env_value(values, "DATABASE_SSLMODE")
    if sslmode:
        command_env["PGSSLMODE"] = sslmode
    return QueryRunner(command, command_env)


def resolve_timezone(name: str) -> tzinfo:
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError:
            pass

    normalized = name.strip().upper()
    fixed_offsets = {
        "ASIA/SHANGHAI": 8 * 60,
        "PRC": 8 * 60,
        "UTC": 0,
        "GMT": 0,
    }
    if normalized in fixed_offsets:
        return timezone(timedelta(minutes=fixed_offsets[normalized]), name)

    offset_text = normalized
    for prefix in ("UTC", "GMT"):
        if offset_text.startswith(prefix):
            offset_text = offset_text[len(prefix):]
            break
    if len(offset_text) == 6 and offset_text[0] in "+-" and offset_text[3] == ":":
        try:
            hours = int(offset_text[1:3])
            minutes = int(offset_text[4:6])
        except ValueError:
            hours = minutes = -1
        if 0 <= hours <= 23 and 0 <= minutes <= 59:
            total_minutes = hours * 60 + minutes
            if offset_text[0] == "-":
                total_minutes = -total_minutes
            return timezone(timedelta(minutes=total_minutes), name)

    if ZoneInfo is None:
        raise SystemExit(
            f"timezone {name!r} requires Python 3.9+ zoneinfo; "
            "use Asia/Shanghai, UTC, or a numeric offset such as UTC+08:00"
        )
    raise SystemExit(f"unknown timezone: {name}")


def parse_time(value: Optional[str], tz: tzinfo, *, default: datetime) -> datetime:
    if not value:
        return default
    try:
        if len(value) == 10:
            parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
            parsed = datetime.combine(parsed_date, time.min)
        else:
            parsed = parse_iso_datetime(value)
    except ValueError as exc:
        raise SystemExit(f"invalid date/time: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(timezone.utc)


def parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip()
    parsed_timezone = None
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1]
        parsed_timezone = timezone.utc
    else:
        offset_match = re.search(r"([+-])(\d{2}):?(\d{2})$", normalized)
        if offset_match and offset_match.start() > 10:
            hours = int(offset_match.group(2))
            minutes = int(offset_match.group(3))
            if hours > 23 or minutes > 59:
                raise ValueError("invalid UTC offset")
            total_minutes = hours * 60 + minutes
            if offset_match.group(1) == "-":
                total_minutes = -total_minutes
            parsed_timezone = timezone(timedelta(minutes=total_minutes))
            normalized = normalized[:offset_match.start()]

    for pattern in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            parsed = datetime.strptime(normalized, pattern)
            return parsed.replace(tzinfo=parsed_timezone) if parsed_timezone else parsed
        except ValueError:
            continue
    raise ValueError("unsupported ISO-8601 date/time")


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_query(args: argparse.Namespace, start: datetime, end: datetime) -> str:
    usage_filters = [
        f"ul.created_at >= {sql_literal(start.isoformat())}::timestamptz",
        f"ul.created_at < {sql_literal(end.isoformat())}::timestamptz",
        "ul.account_id > 0",
    ]
    error_filters = [
        f"oe.created_at >= {sql_literal(start.isoformat())}::timestamptz",
        f"oe.created_at < {sql_literal(end.isoformat())}::timestamptz",
        "oe.account_id IS NOT NULL",
        "oe.account_id > 0",
        "COALESCE(oe.is_count_tokens, FALSE) = FALSE",
        "COALESCE(oe.status_code, 0) >= 400",
    ]
    if args.platform:
        platform = sql_literal(args.platform.strip().lower())
        usage_filters.append(f"LOWER(COALESCE(NULLIF(g.platform, ''), a.platform, '')) = {platform}")
        error_filters.append(f"LOWER(COALESCE(oe.platform, '')) = {platform}")
    if args.group_id:
        usage_filters.append(f"ul.group_id = {args.group_id}")
        error_filters.append(f"oe.group_id = {args.group_id}")
    account_ids = set(args.account_ids or [])
    if args.account_id:
        account_ids.add(args.account_id)
    if account_ids:
        ids_sql = ", ".join(str(account_id) for account_id in sorted(account_ids))
        usage_filters.append(f"ul.account_id IN ({ids_sql})")
        error_filters.append(f"oe.account_id IN ({ids_sql})")

    return f"""
WITH success AS (
    SELECT
        ul.account_id,
        COUNT(*)::bigint AS success_count
    FROM usage_logs ul
    LEFT JOIN groups g ON g.id = ul.group_id
    LEFT JOIN accounts a ON a.id = ul.account_id
    WHERE {' AND '.join(usage_filters)}
    GROUP BY ul.account_id
),
errors AS (
    SELECT
        oe.account_id,
        COUNT(*)::bigint AS error_count_total,
        COUNT(*) FILTER (WHERE COALESCE(oe.is_business_limited, FALSE))::bigint
            AS business_limited_count,
        COUNT(*) FILTER (WHERE NOT COALESCE(oe.is_business_limited, FALSE))::bigint
            AS error_count_sla
    FROM ops_error_logs oe
    WHERE {' AND '.join(error_filters)}
    GROUP BY oe.account_id
),
combined AS (
    SELECT
        COALESCE(s.account_id, e.account_id) AS account_id,
        COALESCE(s.success_count, 0)::bigint AS success_count,
        COALESCE(e.error_count_total, 0)::bigint AS error_count_total,
        COALESCE(e.business_limited_count, 0)::bigint AS business_limited_count,
        COALESCE(e.error_count_sla, 0)::bigint AS error_count_sla
    FROM success s
    FULL OUTER JOIN errors e ON e.account_id = s.account_id
)
SELECT
    c.account_id,
    COALESCE(a.name, '[deleted account]') AS account_name,
    COALESCE(a.platform, '') AS platform,
    COALESCE(a.status, '') AS account_status,
    c.success_count,
    c.error_count_sla,
    c.business_limited_count,
    c.error_count_total,
    (c.success_count + c.error_count_total)::bigint AS request_count_total,
    (c.success_count + c.error_count_sla)::bigint AS request_count_sla,
    CASE
        WHEN c.success_count + c.error_count_sla = 0 THEN NULL
        ELSE ROUND(
            c.success_count::numeric * 100 / (c.success_count + c.error_count_sla),
            4
        )
    END AS sla_percent
FROM combined c
LEFT JOIN accounts a ON a.id = c.account_id
WHERE c.success_count + c.error_count_total >= {args.min_requests}
ORDER BY sla_percent ASC NULLS LAST, request_count_sla DESC, c.account_id ASC
""".strip()


def write_csv(rows: List[Dict[str, str]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0]) if rows else [
        "account_id",
        "account_name",
        "platform",
        "account_status",
        "success_count",
        "error_count_sla",
        "business_limited_count",
        "error_count_total",
        "request_count_total",
        "request_count_sla",
        "sla_percent",
    ]
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def print_table(rows: List[Dict[str, str]]) -> None:
    if not rows:
        print("No account requests found in this time range.")
        return
    columns = list(rows[0])
    widths = {
        column: max(len(column), *(len(row.get(column, "")) for row in rows))
        for column in columns
    }
    print("  ".join(column.ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(row.get(column, "").ljust(widths[column]) for column in columns))


def main() -> int:
    args = parse_args()
    tz = resolve_timezone(args.timezone)

    now = datetime.now(timezone.utc)
    end = parse_time(args.end, tz, default=now)
    start = parse_time(args.start, tz, default=end - timedelta(hours=24))
    if start >= end:
        raise SystemExit("--start must be earlier than --end")

    env_file = find_env_file(args.env_file)
    values = merged_env(args, env_file)
    runner = build_runner(args, env_file, values)
    rows = runner.run_csv(build_query(args, start, end))

    if args.output:
        output = Path(args.output)
        write_csv(rows, output)
        print(f"Wrote {len(rows)} account rows to {output}")
    else:
        print(f"Window (UTC): {start.isoformat()} <= created_at < {end.isoformat()}")
        print_table(rows)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
