import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from run_delisted_history_pilot import run_pilot


def catalog_row(code, exchange):
    return {
        "Code": code,
        "Name": f"{code} Company",
        "Country": "USA",
        "Exchange": exchange,
        "Currency": "USD",
        "Type": "Common Stock",
        "Isin": None,
    }


def valid_history(ticker):
    price = 10 + len(ticker)
    return [
        {
            "date": "2020-01-02",
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price,
            "adjusted_close": price,
            "volume": 100000,
        }
    ]


class RunDelistedHistoryPilotTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog = self.root / "catalog-input.json"
        self.catalog.write_text(
            json.dumps(
                [
                    catalog_row("N1", "NASDAQ"),
                    catalog_row("N2", "NASDAQ"),
                    catalog_row("Y1", "NYSE"),
                    catalog_row("Y2", "NYSE"),
                    catalog_row("A1", "NYSE MKT"),
                ]
            )
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _paths(self):
        return {
            "raw_root": self.root / "raw",
            "report_csv": self.root / "report.csv",
            "report_json": self.root / "report.json",
            "report_markdown": self.root / "report.md",
        }

    def test_offline_fetcher_writes_reports_and_never_persists_token(self):
        calls = []

        def fetcher(ticker, start, finish, token):
            calls.append((ticker, start, finish, token))
            return valid_history(ticker)

        result = run_pilot(
            self.catalog,
            quotas={"NASDAQ": 1, "NYSE": 1, "NYSE MKT": 1},
            fetcher=fetcher,
            token="must-never-be-persisted",
            workers=1,
            collected_at="2026-07-27T12:00:00Z",
            **self._paths(),
        )

        self.assertEqual(result["summary"]["sample_count"], 3)
        self.assertEqual(len(calls), 3)
        self.assertTrue(self._paths()["report_csv"].exists())
        self.assertIn(
            "退市普通股历史日线分层试验",
            self._paths()["report_markdown"].read_text(),
        )
        markdown = self._paths()["report_markdown"].read_text()
        self.assertIn("可用历史：3/3（100.0%）", markdown)
        self.assertIn("质量警告响应：0；非法行情行：0；重复日期：0", markdown)
        self.assertIn("疑似非普通股标签：0", markdown)
        self.assertIn("预计有效日线：5 行", markdown)
        persisted = "".join(
            path.read_text()
            for path in (
                self._paths()["raw_root"] / "catalog.json",
                self._paths()["raw_root"] / "sample.json",
                self._paths()["raw_root"] / "errors.json",
                self._paths()["raw_root"] / "manifest.json",
                self._paths()["report_json"],
                self._paths()["report_markdown"],
            )
        )
        self.assertNotIn("must-never-be-persisted", persisted)
        self.assertFalse(
            list(self._paths()["raw_root"].rglob("*.tmp"))
        )

    def test_cached_histories_are_reused_and_errors_do_not_replace_sample(self):
        first_calls = []

        def first_fetcher(ticker, start, finish, token):
            first_calls.append(ticker)
            if ticker.startswith("Y"):
                raise HTTPError(
                    "https://example.invalid",
                    404,
                    "not found",
                    {},
                    None,
                )
            return valid_history(ticker)

        first = run_pilot(
            self.catalog,
            quotas={"NASDAQ": 1, "NYSE": 1, "NYSE MKT": 1},
            fetcher=first_fetcher,
            workers=1,
            collected_at="2026-07-27T12:00:00Z",
            **self._paths(),
        )

        def forbidden_fetcher(*args):
            raise AssertionError("valid cache or recorded error must be reused")

        second = run_pilot(
            self.catalog,
            quotas={"NASDAQ": 1, "NYSE": 1, "NYSE MKT": 1},
            fetcher=forbidden_fetcher,
            workers=1,
            collected_at="2026-07-27T12:30:00Z",
            **self._paths(),
        )

        self.assertEqual(len(first_calls), 3)
        self.assertEqual(first["sample"], second["sample"])
        self.assertEqual(len(second["sample"]), 3)
        error_rows = [
            row
            for row in second["audits"]
            if row["request_status"] == "http_error"
        ]
        self.assertEqual(len(error_rows), 1)
        self.assertTrue(error_rows[0]["ticker"].startswith("Y"))


if __name__ == "__main__":
    unittest.main()
