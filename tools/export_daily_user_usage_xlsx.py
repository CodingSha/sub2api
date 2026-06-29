#!/usr/bin/env python3
"""Export daily per-user usage totals to an XLSX file.

The script intentionally avoids third-party Python packages so it can run on a
server with only Python 3, PostgreSQL's psql client, or Docker Compose.
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Dict, IO, Iterable, List, Optional, Set, Tuple
from xml.sax.saxutils import escape


ROW_LIMIT = 1_048_576
EXPORT_COLUMNS = [
    ("usage_date", "日期"),
    ("user_id", "用户ID"),
    ("email", "邮箱"),
    ("username", "用户名"),
    ("role", "角色"),
    ("status", "状态"),
    ("deleted_at", "删除时间"),
    ("request_count", "请求次数"),
    ("input_tokens", "输入Tokens"),
    ("output_tokens", "输出Tokens"),
    ("cache_creation_tokens", "缓存写入Tokens"),
    ("cache_read_tokens", "缓存读取Tokens"),
    ("cache_creation_5m_tokens", "缓存写入5m Tokens"),
    ("cache_creation_1h_tokens", "缓存写入1h Tokens"),
    ("image_output_tokens", "图片输出Tokens"),
    ("total_tokens", "总Tokens"),
    ("image_count", "图片数量"),
    ("total_cost", "原始成本"),
    ("actual_cost", "用户实际扣费"),
]

INTEGER_COLUMNS = {
    "user_id",
    "request_count",
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "cache_creation_5m_tokens",
    "cache_creation_1h_tokens",
    "image_output_tokens",
    "total_tokens",
    "image_count",
}
DECIMAL_COLUMNS = {"total_cost", "actual_cost"}


class QueryRunner:
    def __init__(self, command_prefix, env):
        # type: (List[str], Dict[str, str]) -> None
        self.command_prefix = command_prefix
        self.env = env

    def copy_command(self, sql):
        # type: (str) -> List[str]
        copy_sql = f"COPY ({sql}) TO STDOUT WITH CSV HEADER"
        return [*self.command_prefix, "-v", "ON_ERROR_STOP=1", "-c", copy_sql]

    def run_csv_capture(self, sql):
        # type: (str) -> List[Dict[str, str]]
        proc = subprocess.run(
            self.copy_command(sql),
            env=self.env,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "psql command failed")
        return list(csv.DictReader(proc.stdout.splitlines()))

    def open_csv_stream(self, sql):
        # type: (str) -> subprocess.Popen
        return subprocess.Popen(
            self.copy_command(sql),
            env=self.env,
            universal_newlines=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export all users' daily token usage and request counts to an XLSX file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--start", help="Start date, inclusive, in YYYY-MM-DD. Uses all history when omitted.")
    parser.add_argument("--end", help="End date, inclusive, in YYYY-MM-DD. Uses all history when omitted.")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="Timezone used to group created_at into days.")
    parser.add_argument("--output", help="Output .xlsx path.")
    parser.add_argument(
        "--include-zero",
        action="store_true",
        help="Include every user for every day in the range, even when request_count is 0.",
    )
    parser.add_argument(
        "--active-users-only",
        action="store_true",
        help="Only export users whose deleted_at is NULL. By default soft-deleted users are kept.",
    )
    parser.add_argument("--env-file", help="Path to .env file. Defaults to deploy/.env, then .env when present.")
    parser.add_argument("--database-url", help="PostgreSQL connection URL. Overrides DATABASE_URL.")
    parser.add_argument("--database-host", help="PostgreSQL host. Overrides DATABASE_HOST.")
    parser.add_argument("--database-port", type=int, help="PostgreSQL port. Overrides DATABASE_PORT.")
    parser.add_argument("--database-user", help="PostgreSQL user. Overrides DATABASE_USER/POSTGRES_USER.")
    parser.add_argument("--database-password", help="PostgreSQL password. Overrides DATABASE_PASSWORD/POSTGRES_PASSWORD.")
    parser.add_argument("--database-name", help="PostgreSQL database. Overrides DATABASE_DBNAME/POSTGRES_DB.")
    parser.add_argument("--database-sslmode", help="PostgreSQL sslmode. Overrides DATABASE_SSLMODE.")
    parser.add_argument(
        "--use-docker",
        choices=("auto", "always", "never"),
        default="auto",
        help="Run psql inside the Docker Compose postgres service.",
    )
    parser.add_argument("--compose-file", default="deploy/docker-compose.yml", help="Docker Compose file.")
    parser.add_argument("--compose-service", default="postgres", help="Docker Compose PostgreSQL service name.")
    return parser.parse_args()


def validate_date(value, name):
    # type: (Optional[str], str) -> Optional[date]
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"{name} must be YYYY-MM-DD, got {value!r}") from exc


def load_env_file(path):
    # type: (Optional[Path]) -> Dict[str, str]
    if path is None or not path.exists():
        return {}

    env = {}  # type: Dict[str, str]
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        env[key] = value
    return env


def default_env_file():
    # type: () -> Optional[Path]
    for candidate in (Path("deploy/.env"), Path(".env")):
        if candidate.exists():
            return candidate
    return None


def merged_env(args, env_file):
    # type: (argparse.Namespace, Optional[Path]) -> Dict[str, str]
    env = load_env_file(env_file)
    env.update(os.environ)

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
            env[key] = value
    if args.database_user:
        env["POSTGRES_USER"] = args.database_user
    if args.database_password:
        env["POSTGRES_PASSWORD"] = args.database_password
    if args.database_name:
        env["POSTGRES_DB"] = args.database_name
    return env


def env_value(env, *keys, **kwargs):
    # type: (Dict[str, str], *str, **str) -> str
    default = kwargs.get("default", "")
    for key in keys:
        value = env.get(key)
        if value:
            return value
    return default


def should_use_docker(args, env):
    # type: (argparse.Namespace, Dict[str, str]) -> bool
    if args.use_docker == "always":
        return True
    if args.use_docker == "never":
        return False

    psql_path = shutil.which("psql")
    compose_path = shutil.which("docker")
    compose_file_exists = Path(args.compose_file).exists()
    host = env_value(env, "DATABASE_HOST", default="")
    has_url = bool(env.get("DATABASE_URL"))

    if psql_path and (has_url or (host and host not in {"postgres", "db"})):
        return False
    if compose_path and compose_file_exists:
        return True
    return False


def build_runner(args, env_file, env):
    # type: (argparse.Namespace, Optional[Path], Dict[str, str]) -> QueryRunner
    command_env = os.environ.copy()
    command_env.update(env)

    if should_use_docker(args, env):
        compose_cmd = ["docker", "compose"]
        if env_file and env_file.exists():
            compose_cmd.extend(["--env-file", str(env_file)])
        compose_cmd.extend(["-f", args.compose_file, "exec", "-T", args.compose_service])
        user = env_value(env, "POSTGRES_USER", "DATABASE_USER", default="sub2api")
        dbname = env_value(env, "POSTGRES_DB", "DATABASE_DBNAME", default="sub2api")
        return QueryRunner([*compose_cmd, "psql", "-U", user, "-d", dbname], command_env)

    if not shutil.which("psql"):
        raise SystemExit("psql not found. Install PostgreSQL client, or rerun with --use-docker always.")

    if env.get("DATABASE_URL"):
        return QueryRunner(["psql", "-d", env["DATABASE_URL"]], command_env)

    host = env_value(env, "DATABASE_HOST", default="localhost")
    port = env_value(env, "DATABASE_PORT", default="5432")
    user = env_value(env, "DATABASE_USER", "POSTGRES_USER", default="sub2api")
    dbname = env_value(env, "DATABASE_DBNAME", "POSTGRES_DB", default="sub2api")
    password = env_value(env, "DATABASE_PASSWORD", "POSTGRES_PASSWORD", default="")
    if password:
        command_env["PGPASSWORD"] = password
    return QueryRunner(["psql", "-h", host, "-p", port, "-U", user, "-d", dbname], command_env)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def get_columns(runner, table):
    # type: (QueryRunner, str) -> Set[str]
    rows = runner.run_csv_capture(
        "SELECT column_name "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'public' AND table_name = {sql_literal(table)} "
        "ORDER BY ordinal_position"
    )
    return {row["column_name"] for row in rows}


def sum_expr(usage_columns, column):
    # type: (Set[str], str) -> str
    if column in usage_columns:
        return f"COALESCE(SUM(ul.{sql_identifier(column)}), 0)"
    return "0"


def user_expr(user_columns, column, fallback="''"):
    # type: (Set[str], str, str) -> str
    if column in user_columns:
        return f"COALESCE(u.{sql_identifier(column)}::text, '')"
    return fallback


def resolve_date_range(
    runner: QueryRunner,
    start,
    end,
    timezone,
):
    # type: (QueryRunner, Optional[date], Optional[date], str) -> Tuple[Optional[date], Optional[date]]
    if start and end:
        return start, end

    tz = sql_literal(timezone)
    rows = runner.run_csv_capture(
        "SELECT "
        f"MIN((created_at AT TIME ZONE {tz})::date)::text AS min_date, "
        f"MAX((created_at AT TIME ZONE {tz})::date)::text AS max_date "
        "FROM usage_logs"
    )
    min_date = rows[0].get("min_date") if rows else ""
    max_date = rows[0].get("max_date") if rows else ""
    if not min_date or not max_date:
        return start, end

    return start or datetime.strptime(min_date, "%Y-%m-%d").date(), end or datetime.strptime(max_date, "%Y-%m-%d").date()


def build_usage_query(
    usage_columns,
    user_columns,
    start,
    end,
    timezone,
    include_zero,
    active_users_only,
):
    # type: (Set[str], Set[str], Optional[date], Optional[date], str, bool, bool) -> str
    tz = sql_literal(timezone)
    where_parts = []
    join_time_parts = []
    if start:
        start_sql = f"(DATE {sql_literal(start.isoformat())}::timestamp AT TIME ZONE {tz})"
        where_parts.append(f"ul.created_at >= {start_sql}")
        join_time_parts.append(f"ul.created_at >= {start_sql}")
    if end:
        end_sql = f"((DATE {sql_literal(end.isoformat())} + INTERVAL '1 day')::timestamp AT TIME ZONE {tz})"
        where_parts.append(f"ul.created_at < {end_sql}")
        join_time_parts.append(f"ul.created_at < {end_sql}")

    user_where = ""
    if active_users_only and "deleted_at" in user_columns:
        user_where = "WHERE u.deleted_at IS NULL"

    total_tokens = " + ".join(
        [
            sum_expr(usage_columns, "input_tokens"),
            sum_expr(usage_columns, "output_tokens"),
            sum_expr(usage_columns, "cache_creation_tokens"),
            sum_expr(usage_columns, "cache_read_tokens"),
            sum_expr(usage_columns, "image_output_tokens"),
        ]
    )

    select_metrics = f"""
        COUNT(ul.id) AS request_count,
        {sum_expr(usage_columns, "input_tokens")} AS input_tokens,
        {sum_expr(usage_columns, "output_tokens")} AS output_tokens,
        {sum_expr(usage_columns, "cache_creation_tokens")} AS cache_creation_tokens,
        {sum_expr(usage_columns, "cache_read_tokens")} AS cache_read_tokens,
        {sum_expr(usage_columns, "cache_creation_5m_tokens")} AS cache_creation_5m_tokens,
        {sum_expr(usage_columns, "cache_creation_1h_tokens")} AS cache_creation_1h_tokens,
        {sum_expr(usage_columns, "image_output_tokens")} AS image_output_tokens,
        ({total_tokens}) AS total_tokens,
        {sum_expr(usage_columns, "image_count")} AS image_count,
        {sum_expr(usage_columns, "total_cost")} AS total_cost,
        {sum_expr(usage_columns, "actual_cost")} AS actual_cost
    """

    deleted_at_expr = user_expr(user_columns, "deleted_at")

    if include_zero:
        if not start or not end:
            raise SystemExit("--include-zero requires a resolvable date range. Add --start and --end if usage_logs is empty.")
        join_conditions = [
            "ul.user_id = u.id",
            f"(ul.created_at AT TIME ZONE {tz})::date = d.usage_date",
            *join_time_parts,
        ]
        return f"""
WITH days AS (
    SELECT generate_series(DATE {sql_literal(start.isoformat())}, DATE {sql_literal(end.isoformat())}, INTERVAL '1 day')::date AS usage_date
),
users_base AS (
    SELECT u.id, u.email, u.username, u.role, u.status{', u.deleted_at' if 'deleted_at' in user_columns else ''}
    FROM users u
    {user_where}
)
SELECT
    d.usage_date::text AS usage_date,
    u.id AS user_id,
    COALESCE(u.email::text, '') AS email,
    {user_expr(user_columns, "username")} AS username,
    {user_expr(user_columns, "role")} AS role,
    {user_expr(user_columns, "status")} AS status,
    {deleted_at_expr} AS deleted_at,
    {select_metrics}
FROM days d
CROSS JOIN users_base u
LEFT JOIN usage_logs ul ON {" AND ".join(join_conditions)}
GROUP BY d.usage_date, u.id, u.email, u.username, u.role, u.status{', u.deleted_at' if 'deleted_at' in user_columns else ''}
ORDER BY d.usage_date ASC, u.id ASC
""".strip()

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    if user_where:
        where_clause = (where_clause + " AND u.deleted_at IS NULL") if where_clause else "WHERE u.deleted_at IS NULL"
    return f"""
SELECT
    (ul.created_at AT TIME ZONE {tz})::date::text AS usage_date,
    u.id AS user_id,
    COALESCE(u.email::text, '') AS email,
    {user_expr(user_columns, "username")} AS username,
    {user_expr(user_columns, "role")} AS role,
    {user_expr(user_columns, "status")} AS status,
    {deleted_at_expr} AS deleted_at,
    {select_metrics}
FROM usage_logs ul
JOIN users u ON u.id = ul.user_id
{where_clause}
GROUP BY (ul.created_at AT TIME ZONE {tz})::date, u.id, u.email, u.username, u.role, u.status{', u.deleted_at' if 'deleted_at' in user_columns else ''}
ORDER BY usage_date ASC, u.id ASC
""".strip()


def column_letter(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def write_string_cell(out: IO[str], row: int, col: int, value: str, style: int | None = None) -> None:
    ref = f"{column_letter(col)}{row}"
    style_attr = f' s="{style}"' if style is not None else ""
    out.write(f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{escape(value)}</t></is></c>')


def write_number_cell(out: IO[str], row: int, col: int, value: str, style: int | None = None) -> None:
    ref = f"{column_letter(col)}{row}"
    style_attr = f' s="{style}"' if style is not None else ""
    out.write(f'<c r="{ref}"{style_attr}><v>{escape(value)}</v></c>')


def write_row(out, row_num, values, columns=None, header=False):
    # type: (IO[str], int, Iterable[str], Optional[List[str]], bool) -> None
    out.write(f'<row r="{row_num}">')
    for col_num, value in enumerate(values, start=1):
        value = "" if value is None else str(value)
        if header:
            write_string_cell(out, row_num, col_num, value, style=1)
            continue
        column_name = columns[col_num - 1] if columns else ""
        if value != "" and column_name in INTEGER_COLUMNS and re.fullmatch(r"-?\d+", value):
            write_number_cell(out, row_num, col_num, value, style=2)
        elif value != "" and column_name in DECIMAL_COLUMNS and re.fullmatch(r"-?\d+(\.\d+)?", value):
            write_number_cell(out, row_num, col_num, value, style=3)
        else:
            write_string_cell(out, row_num, col_num, value)
    out.write("</row>")


def start_sheet(path, headers):
    # type: (Path, List[str]) -> IO[str]
    out = path.open("w", encoding="utf-8", newline="")
    out.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
    out.write('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">')
    out.write("<cols>")
    widths = [12, 10, 28, 18, 12, 12, 20, 12, 14, 14, 18, 18, 20, 20, 18, 14, 12, 14, 14]
    for idx, width in enumerate(widths[: len(headers)], start=1):
        out.write(f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>')
    out.write("</cols><sheetData>")
    write_row(out, 1, headers, header=True)
    return out


def finish_sheet(out: IO[str]) -> None:
    out.write("</sheetData><autoFilter ref=\"A1:S1\"/></worksheet>")
    out.close()


def workbook_xml(sheet_count: int) -> str:
    sheets = "".join(
        f'<sheet name="DailyUsage{idx}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheets}</sheets></workbook>"
    )


def workbook_rels_xml(sheet_count: int) -> str:
    rels = "".join(
        '<Relationship '
        f'Id="rId{idx}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{idx}.xml"/>'
        for idx in range(1, sheet_count + 1)
    )
    rels += (
        '<Relationship Id="rIdStyles" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


def content_types_xml(sheet_count: int) -> str:
    sheets = "".join(
        '<Override '
        f'PartName="/xl/worksheets/sheet{idx}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for idx in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        f"{sheets}</Types>"
    )


def styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="4">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        "</styleSheet>"
    )


def package_xlsx(output_path, sheet_paths):
    # type: (Path, List[Path]) -> None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml(len(sheet_paths)))
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
            "</Relationships>",
        )
        zf.writestr("xl/workbook.xml", workbook_xml(len(sheet_paths)))
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheet_paths)))
        zf.writestr("xl/styles.xml", styles_xml())
        zf.writestr(
            "docProps/core.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:title>Daily User Usage Export</dc:title>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>'
            "</cp:coreProperties>",
        )
        zf.writestr(
            "docProps/app.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            "<Application>sub2api export script</Application></Properties>",
        )
        for idx, sheet_path in enumerate(sheet_paths, start=1):
            zf.write(sheet_path, f"xl/worksheets/sheet{idx}.xml")


def export_xlsx(runner: QueryRunner, sql: str, output_path: Path) -> int:
    headers = [label for _, label in EXPORT_COLUMNS]
    column_names = [name for name, _ in EXPORT_COLUMNS]
    total_rows = 0
    sheet_paths = []  # type: List[Path]

    with tempfile.TemporaryDirectory(prefix="sub2api_usage_export_") as temp_dir:
        temp_base = Path(temp_dir)
        sheet_idx = 1
        sheet_row = 1
        sheet_path = temp_base / f"sheet{sheet_idx}.xml"
        sheet_out = start_sheet(sheet_path, headers)
        sheet_paths.append(sheet_path)

        proc = runner.open_csv_stream(sql)
        assert proc.stdout is not None
        reader = csv.DictReader(proc.stdout)
        for row in reader:
            if sheet_row >= ROW_LIMIT:
                finish_sheet(sheet_out)
                sheet_idx += 1
                sheet_row = 1
                sheet_path = temp_base / f"sheet{sheet_idx}.xml"
                sheet_out = start_sheet(sheet_path, headers)
                sheet_paths.append(sheet_path)
            sheet_row += 1
            values = [row.get(name, "") for name in column_names]
            write_row(sheet_out, sheet_row, values, columns=column_names)
            total_rows += 1
            if total_rows % 10000 == 0:
                print(f"exported {total_rows} rows...", file=sys.stderr)

        stderr = proc.stderr.read() if proc.stderr is not None else ""
        return_code = proc.wait()
        finish_sheet(sheet_out)
        if return_code != 0:
            raise RuntimeError(stderr.strip() or "psql export query failed")

        package_xlsx(output_path, sheet_paths)
    return total_rows


def default_output_path(start, end):
    # type: (Optional[date], Optional[date]) -> Path
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if start and end:
        name = f"daily_user_usage_{start.isoformat()}_to_{end.isoformat()}_{stamp}.xlsx"
    else:
        name = f"daily_user_usage_{stamp}.xlsx"
    return Path(name)


def main() -> int:
    args = parse_args()
    start = validate_date(args.start, "--start")
    end = validate_date(args.end, "--end")
    if start and end and start > end:
        raise SystemExit("--start must be earlier than or equal to --end")

    env_file = Path(args.env_file) if args.env_file else default_env_file()
    env = merged_env(args, env_file)
    runner = build_runner(args, env_file, env)

    print("checking database schema...", file=sys.stderr)
    usage_columns = get_columns(runner, "usage_logs")
    user_columns = get_columns(runner, "users")
    if not usage_columns:
        raise SystemExit("table usage_logs not found")
    if not user_columns:
        raise SystemExit("table users not found")

    start, end = resolve_date_range(runner, start, end, args.timezone)
    if start and end and start > end:
        raise SystemExit("resolved start date is later than end date")

    output_path = Path(args.output) if args.output else default_output_path(start, end)
    sql = build_usage_query(
        usage_columns=usage_columns,
        user_columns=user_columns,
        start=start,
        end=end,
        timezone=args.timezone,
        include_zero=args.include_zero,
        active_users_only=args.active_users_only,
    )

    date_text = f"{start or 'beginning'} to {end or 'now'}"
    zero_text = "including zero-usage user-days" if args.include_zero else "usage days only"
    print(f"exporting {date_text} ({args.timezone}, {zero_text})...", file=sys.stderr)
    total_rows = export_xlsx(runner, sql, output_path)
    print(f"done: {output_path} ({total_rows} rows)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
