import csv
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from build_delisted_research_db import build_database


CATALOG_HASH = "a" * 64


def daily(date, *, high=12, low=9):
    return {
        "date": date,
        "open": 10,
        "high": high,
        "low": low,
        "close": 11,
        "adjusted_close": 11,
        "volume": 100,
    }


def security(ticker, exchange="NASDAQ"):
    return {
        "ticker": ticker,
        "name": f"{ticker} Company",
        "exchange": exchange,
        "currency": "USD",
        "provider_isin": None,
        "identity_status": "ticker_only",
        "identity_key": None,
        "classification": "accepted_common",
        "backfill_eligible": True,
        "rule_version": "delisted_security_purification_v1",
    }


class BuildDelistedResearchDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.raw_root = self.root / "raw"
        self.candidates_path = self.raw_root / "candidates.json"
        self.audit_path = self.root / "audit.csv"
        self.output_path = self.root / "staging.db"
        self.report_json = self.root / "report.json"
        self.report_markdown = self.root / "report.md"
        securities = [
            security("AAA"),
            security("BBB"),
            security("CCC", "NYSE"),
        ]
        self.raw_root.mkdir()
        self.candidates_path.write_text(
            json.dumps(
                {
                    "schema_version":
                        "delisted_history_backfill_candidates_v1",
                    "backfill_version": "delisted_history_backfill_v1",
                    "catalog_sha256": CATALOG_HASH,
                    "start_date": "2016-01-01",
                    "finish_date": "2026-07-27",
                    "candidate_count": 3,
                    "securities": securities,
                }
            ),
            encoding="utf-8",
        )
        (self.raw_root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version":
                        "delisted_history_backfill_manifest_v1",
                    "catalog_sha256": CATALOG_HASH,
                    "candidate_count": 3,
                    "history_files": 3,
                    "error_count": 0,
                    "completion_status": "complete",
                }
            ),
            encoding="utf-8",
        )
        histories = {
            ("NASDAQ", "AAA"): [
                daily("2020-01-02"),
                daily("2021-01-04"),
            ],
            ("NASDAQ", "BBB"): [],
            ("NYSE", "CCC"): [
                daily("2020-01-02"),
                daily("2020-01-03", high=8),
            ],
        }
        for (exchange, ticker), rows in histories.items():
            directory = self.raw_root / "histories" / exchange
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{ticker}.json").write_text(
                json.dumps(rows),
                encoding="utf-8",
            )
        fields = [
            "ticker",
            "name",
            "exchange",
            "request_status",
            "quality_status",
            "raw_rows",
            "valid_rows",
            "duplicate_dates",
            "invalid_rows",
            "first_date",
            "last_date",
            "raw_bytes",
        ]
        rows = [
            ["AAA", "AAA Company", "NASDAQ", "success", "clean", 2, 2, 0, 0,
             "2020-01-02", "2021-01-04", 1],
            ["BBB", "BBB Company", "NASDAQ", "empty", "no_rows", 0, 0, 0, 0,
             "", "", 2],
            ["CCC", "CCC Company", "NYSE", "success", "warning", 2, 1, 0, 1,
             "2020-01-02", "2020-01-02", 1],
        ]
        with self.audit_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(fields)
            writer.writerows(rows)

    def tearDown(self):
        self.temporary.cleanup()

    def build(self):
        return build_database(
            self.candidates_path,
            self.audit_path,
            self.raw_root,
            self.output_path,
            self.report_json,
            self.report_markdown,
            imported_at="2026-07-27T12:00:00+00:00",
        )

    def test_builds_isolated_audited_database(self):
        result = self.build()
        connection = sqlite3.connect(self.output_path)

        self.assertEqual(result["security_count"], 3)
        self.assertEqual(result["daily_rows"], 3)
        self.assertEqual(result["rejected_rows"], 1)
        self.assertEqual(result["empty_responses"], 1)
        self.assertGreaterEqual(result["duration_seconds"], 0)
        self.assertIn("started_at", result)
        self.assertIn("completed_at", result)
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM security_master "
                "WHERE active=0 AND is_delisted=1"
            ).fetchone()[0],
            3,
        )
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0],
            3,
        )
        self.assertEqual(
            connection.execute(
                "SELECT reason FROM rejected_daily_rows"
            ).fetchall(),
            [("invalid_ohlc",)],
        )
        self.assertEqual(
            connection.execute(
                "SELECT request_status FROM security_audits "
                "WHERE ticker='BBB'"
            ).fetchone()[0],
            "empty",
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM history_segments WHERE ticker='AAA'"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            connection.execute(
                "SELECT identity_status, catalog_sha256 FROM security_master "
                "WHERE ticker='AAA'"
            ).fetchone(),
            ("ticker_only", CATALOG_HASH),
        )
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        connection.close()

    def test_rebuild_is_idempotent(self):
        first = self.build()
        second = self.build()

        self.assertEqual(
            {key: first[key] for key in ("security_count", "daily_rows", "rejected_rows")},
            {key: second[key] for key in ("security_count", "daily_rows", "rejected_rows")},
        )

    def test_failed_rebuild_preserves_existing_database_and_removes_temp(self):
        self.output_path.write_bytes(b"existing database")
        with self.audit_path.open(encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        rows[1][6] = "999"
        with self.audit_path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle, lineterminator="\n").writerows(rows)

        with self.assertRaisesRegex(ValueError, "audit"):
            self.build()

        self.assertEqual(self.output_path.read_bytes(), b"existing database")
        self.assertFalse(
            self.output_path.with_suffix(self.output_path.suffix + ".tmp").exists()
        )


if __name__ == "__main__":
    unittest.main()
