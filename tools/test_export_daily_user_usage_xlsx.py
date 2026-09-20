import io
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

from tools import export_daily_user_usage_xlsx as usage_export


class FakeProcess:
    def __init__(self, csv_text):
        self.stdout = io.StringIO(csv_text)
        self.stderr = io.StringIO("")

    def wait(self):
        return 0


class FakeRunner:
    def __init__(self, csv_results):
        self.csv_results = iter(csv_results)

    def open_csv_stream(self, _sql):
        return FakeProcess(next(self.csv_results))


class DailyUsageExportTest(unittest.TestCase):
    def test_model_query_uses_requested_model_with_historical_fallback(self):
        usage_columns = {
            "id",
            "created_at",
            "requested_model",
            "model",
            "input_tokens",
            "output_tokens",
        }

        sql = usage_export.build_model_usage_query(
            usage_columns,
            {"id", "deleted_at"},
            date(2026, 7, 13),
            date(2026, 7, 26),
            "Asia/Shanghai",
            True,
        )

        resolved_model = (
            "COALESCE(NULLIF(TRIM(ul.requested_model::text), ''), "
            "NULLIF(TRIM(ul.model::text), ''), 'unknown')"
        )
        self.assertIn(f'{resolved_model} AS "model"', sql)
        self.assertIn(f"GROUP BY (ul.created_at AT TIME ZONE 'Asia/Shanghai')::date, {resolved_model}", sql)
        self.assertIn("u.deleted_at IS NULL", sql)
        self.assertIn("ul.created_at >=", sql)
        self.assertIn("ul.created_at <", sql)

        self.assertIn('ORDER BY usage_date ASC, "model" ASC', sql)

    def test_user_agent_query_groups_blank_values_as_unknown(self):
        sql = usage_export.build_user_agent_usage_query(
            {"id", "created_at", "user_agent", "input_tokens", "output_tokens"},
            {"id", "deleted_at"},
            date(2026, 7, 13),
            date(2026, 7, 26),
            "Asia/Shanghai",
            True,
        )

        resolved_user_agent = "COALESCE(NULLIF(TRIM(ul.user_agent::text), ''), 'unknown')"
        self.assertIn(f'{resolved_user_agent} AS "user_agent"', sql)
        self.assertIn(f"GROUP BY (ul.created_at AT TIME ZONE 'Asia/Shanghai')::date, {resolved_user_agent}", sql)
        self.assertIn('ORDER BY usage_date ASC, "user_agent" ASC', sql)
        self.assertIn("u.deleted_at IS NULL", sql)

    def test_api_key_query_filters_jarvis_without_exporting_secret(self):
        sql = usage_export.build_api_key_usage_query(
            {"id", "created_at", "api_key_id", "input_tokens", "output_tokens"},
            {"id", "email", "deleted_at"},
            {"id", "name", "key"},
            date(2026, 7, 13),
            date(2026, 7, 26),
            "Asia/Shanghai",
            True,
        )

        self.assertIn("LOWER(u.email::text) = LOWER('Jarvis@apsat.com')", sql)
        self.assertIn("ul.api_key_id AS api_key_id", sql)
        self.assertIn("COALESCE(NULLIF(TRIM(ak.name::text), ''), 'unnamed') AS api_key_name", sql)
        self.assertIn("LEFT JOIN api_keys ak ON ak.id = ul.api_key_id", sql)
        self.assertIn("u.deleted_at IS NULL", sql)
        self.assertNotIn("ak.key", sql)

    def test_export_contains_all_daily_dimension_sheets(self):
        user_header = ",".join(name for name, _ in usage_export.EXPORT_COLUMNS)
        user_row = [
            "2026-07-13",
            "7",
            "user@example.com",
            "User",
            "user",
            "active",
            "",
            "2",
            "100",
            "40",
            "10",
            "5",
            "10",
            "0",
            "0",
            "155",
            "0",
            "0.01",
            "0.02",
        ]
        model_header = ",".join(name for name, _ in usage_export.MODEL_EXPORT_COLUMNS)
        model_row = [
            "2026-07-13",
            "claude-sonnet-4",
            "2",
            "100",
            "40",
            "10",
            "5",
            "10",
            "0",
            "0",
            "155",
            "0",
            "0.01",
            "0.02",
        ]
        user_agent_header = ",".join(name for name, _ in usage_export.USER_AGENT_EXPORT_COLUMNS)
        user_agent_row = [
            "2026-07-13",
            "codex-cli/1.2.3",
            "2",
            "100",
            "40",
            "10",
            "5",
            "10",
            "0",
            "0",
            "155",
            "0",
            "0.01",
            "0.02",
        ]
        api_key_header = ",".join(name for name, _ in usage_export.API_KEY_EXPORT_COLUMNS)
        api_key_row = [
            "2026-07-13",
            "42",
            "Jarvis primary",
            "2",
            "100",
            "40",
            "10",
            "5",
            "10",
            "0",
            "0",
            "155",
            "0",
            "0.01",
            "0.02",
        ]
        runner = FakeRunner(
            [
                user_header + "\n" + ",".join(user_row) + "\n",
                model_header + "\n" + ",".join(model_row) + "\n",
                user_agent_header + "\n" + ",".join(user_agent_row) + "\n",
                api_key_header + "\n" + ",".join(api_key_row) + "\n",
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "daily_usage.xlsx"
            counts = usage_export.export_xlsx(
                runner,
                "user sql",
                "model sql",
                "user-agent sql",
                "api-key sql",
                output,
            )

            self.assertEqual((1, 1, 1, 1), counts)
            with zipfile.ZipFile(output) as archive:
                workbook_root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
                namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                sheet_names = [
                    node.attrib["name"] for node in workbook_root.findall("x:sheets/x:sheet", namespace)
                ]
                self.assertEqual(
                    [
                        "DailyUsage1",
                        "DailyModelUsage1",
                        "DailyUserAgentUsage1",
                        "JarvisAPIKeyUsage1",
                    ],
                    sheet_names,
                )

                user_sheet = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
                model_sheet = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
                user_agent_sheet = archive.read("xl/worksheets/sheet3.xml").decode("utf-8")
                api_key_sheet = archive.read("xl/worksheets/sheet4.xml").decode("utf-8")
                self.assertIn('autoFilter ref="A1:S1"', user_sheet)
                self.assertIn('autoFilter ref="A1:N1"', model_sheet)
                self.assertIn('autoFilter ref="A1:N1"', user_agent_sheet)
                self.assertIn('autoFilter ref="A1:O1"', api_key_sheet)
                self.assertIn("user@example.com", user_sheet)
                self.assertIn("claude-sonnet-4", model_sheet)
                self.assertIn("codex-cli/1.2.3", user_agent_sheet)
                self.assertIn("Jarvis primary", api_key_sheet)


if __name__ == "__main__":
    unittest.main()
