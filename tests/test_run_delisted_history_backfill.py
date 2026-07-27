import csv
from io import StringIO
import hashlib
from pathlib import Path
import json
import tempfile
import unittest
from urllib.error import HTTPError, URLError

from run_delisted_history_backfill import run_backfill


SECRET = "test-secret-that-must-not-be-written"


def security(ticker, exchange):
    return {
        "ticker": ticker,
        "name": f"{ticker} Company",
        "exchange": exchange,
        "currency": "USD",
        "provider_type": "Common Stock",
        "provider_isin": None,
        "identity_status": "ticker_only",
        "identity_key": None,
        "classification": "accepted_common",
        "backfill_eligible": True,
        "rule_version": "delisted_security_purification_v1",
    }


def valid_history(date="2020-01-02"):
    return [
        {
            "date": date,
            "open": 10,
            "high": 12,
            "low": 9,
            "close": 11,
            "adjusted_close": 11,
            "volume": 1000,
        }
    ]


class DelistedHistoryBackfillRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.catalog_path = self.root / "catalog.json"
        self.report_path = self.root / "purification.json"
        self.raw_root = self.root / "raw"
        self.csv_path = self.root / "report.csv"
        self.json_path = self.root / "report.json"
        self.markdown_path = self.root / "report.md"
        catalog = {
            "schema_version": "delisted_security_catalog_v1",
            "rule_version": "delisted_security_purification_v1",
            "input_rows": 4,
            "securities": [
                security("AAA", "NASDAQ"),
                security("BBB", "NASDAQ"),
                security("CCC", "NYSE"),
                security("DDD", "NYSE MKT"),
            ],
        }
        self.catalog_path.write_text(
            json.dumps(catalog, sort_keys=True),
            encoding="utf-8",
        )
        catalog_sha = hashlib.sha256(
            self.catalog_path.read_bytes()
        ).hexdigest()
        self.report_path.write_text(
            json.dumps(
                {
                    "schema_version":
                        "delisted_security_purification_report_v1",
                    "catalog_schema_version":
                        "delisted_security_catalog_v1",
                    "rule_version":
                        "delisted_security_purification_v1",
                    "catalog_sha256": catalog_sha,
                    "summary": {"backfill_eligible_rows": 4},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def run_case(self, fetcher, **overrides):
        arguments = {
            "catalog_path": self.catalog_path,
            "catalog_report_path": self.report_path,
            "raw_root": self.raw_root,
            "report_csv": self.csv_path,
            "report_json": self.json_path,
            "report_markdown": self.markdown_path,
            "token": SECRET,
            "fetcher": fetcher,
            "workers": 2,
            "checkpoint_every": 1,
            "updated_at": "2026-07-27T00:00:00+00:00",
        }
        arguments.update(overrides)
        return run_backfill(**arguments)

    def test_first_run_freezes_before_fetch_and_second_retries_only_retryable(
        self,
    ):
        first_calls = []

        def first_fetcher(ticker, start, finish, token):
            self.assertTrue((self.raw_root / "candidates.json").exists())
            first_calls.append(ticker)
            self.assertEqual(start, "2016-01-01")
            self.assertEqual(finish, "2026-07-27")
            self.assertEqual(token, SECRET)
            if ticker == "AAA":
                return valid_history()
            if ticker == "BBB":
                return []
            if ticker == "CCC":
                raise HTTPError(
                    "https://example.invalid/secret",
                    404,
                    "not found",
                    {},
                    StringIO(""),
                )
            raise URLError("temporary")

        first = self.run_case(first_fetcher)

        self.assertEqual(set(first_calls), {"AAA", "BBB", "CCC", "DDD"})
        self.assertEqual(first["summary"]["usable_histories"], 1)
        self.assertEqual(first["summary"]["empty_responses"], 1)
        self.assertEqual(first["summary"]["permanent_errors"], 1)
        self.assertEqual(first["summary"]["retryable_errors"], 1)
        self.assertEqual(first["summary"]["completion_status"], "partial")
        second_calls = []

        def second_fetcher(ticker, start, finish, token):
            second_calls.append(ticker)
            return valid_history("2020-01-03")

        second = self.run_case(second_fetcher)

        self.assertEqual(second_calls, ["DDD"])
        self.assertEqual(second["summary"]["usable_histories"], 2)
        self.assertEqual(second["summary"]["empty_responses"], 1)
        self.assertEqual(second["summary"]["permanent_errors"], 1)
        self.assertEqual(second["summary"]["retryable_errors"], 0)
        self.assertEqual(second["summary"]["completion_status"], "complete")
        with self.csv_path.open(encoding="utf-8") as handle:
            csv_rows = list(csv.DictReader(handle))
        self.assertEqual(len(csv_rows), 4)
        self.assertEqual(
            {row["ticker"] for row in csv_rows},
            {"AAA", "BBB", "CCC", "DDD"},
        )

    def test_damaged_cache_is_downloaded_again(self):
        calls = []

        def fetcher(ticker, start, finish, token):
            calls.append(ticker)
            return valid_history()

        self.run_case(fetcher)
        calls.clear()
        history_path = self.raw_root / "histories" / "NASDAQ" / "AAA.json"
        history_path.write_text("{damaged", encoding="utf-8")

        self.run_case(fetcher)

        self.assertEqual(calls, ["AAA"])
        self.assertEqual(json.loads(history_path.read_text()), valid_history())

    def test_rejects_catalog_hash_or_frozen_window_mismatch(self):
        def fetcher(ticker, start, finish, token):
            return []

        self.run_case(fetcher)
        report = json.loads(self.report_path.read_text())
        report["catalog_sha256"] = "b" * 64
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash"):
            self.run_case(fetcher)

        report["catalog_sha256"] = hashlib.sha256(
            self.catalog_path.read_bytes()
        ).hexdigest()
        self.report_path.write_text(json.dumps(report), encoding="utf-8")
        candidates_path = self.raw_root / "candidates.json"
        candidates = json.loads(candidates_path.read_text())
        candidates["finish_date"] = "2026-07-26"
        candidates_path.write_text(json.dumps(candidates), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.run_case(fetcher)

    def test_outputs_exclude_token_and_leave_no_temporary_files(self):
        def fetcher(ticker, start, finish, token):
            if ticker == "CCC":
                raise HTTPError(
                    f"https://example.invalid/?api_token={token}",
                    403,
                    "forbidden",
                    {},
                    StringIO(""),
                )
            return []

        self.run_case(fetcher)

        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET, path.read_text(encoding="utf-8"))
                self.assertFalse(path.name.endswith(".tmp"))
        report = json.loads(self.json_path.read_text())
        self.assertNotIn("audits", report)
        self.assertEqual(report["summary"]["candidate_count"], 4)


if __name__ == "__main__":
    unittest.main()
