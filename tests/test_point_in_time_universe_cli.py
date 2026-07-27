import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from collect_eodhd_point_in_time_universe import collect_snapshot
from data.research_store import ResearchPriceStore
from import_point_in_time_universe import import_snapshot


COMPONENTS = {
    "HistoricalTickerComponents": {
        "0": {
            "Code": "AAPL",
            "Name": "Apple Inc",
            "StartDate": "2010-01-01",
            "EndDate": None,
            "IsActiveNow": 1,
            "IsDelisted": 0,
        },
        "1": {
            "Code": "TWTR",
            "Name": "Twitter Inc",
            "StartDate": "2018-06-07",
            "EndDate": "2022-10-28",
            "IsActiveNow": 0,
            "IsDelisted": 1,
        },
        "2": {
            "Code": "XYZ",
            "Name": "Former Example",
            "StartDate": "2020-01-01",
            "EndDate": "2021-01-01",
            "IsActiveNow": 0,
            "IsDelisted": 1,
        },
    }
}
CHANGES = [
    {
        "exchange": "US",
        "old_symbol": "FB",
        "new_symbol": "META",
        "company_name": "Meta Platforms Inc",
        "effective": "2022-06-09",
    }
]


class PointInTimeUniverseCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.components = self.root / "components-input.json"
        self.changes = self.root / "changes-input.json"
        self.components.write_text(json.dumps(COMPONENTS))
        self.changes.write_text(json.dumps(CHANGES))

    def tearDown(self):
        self.temporary.cleanup()

    def test_offline_collection_writes_atomic_audited_snapshot_without_token(self):
        output = self.root / "raw" / "2026-07-27"

        manifest = collect_snapshot(
            output,
            snapshot_date="2026-07-27",
            collected_at="2026-07-27T10:00:00Z",
            input_components=self.components,
            input_symbol_changes=self.changes,
            token="must-never-be-persisted",
        )

        self.assertEqual(manifest["component_count"], 3)
        self.assertEqual(manifest["symbol_change_count"], 1)
        self.assertEqual(manifest["mode"], "offline")
        self.assertEqual(
            set(path.name for path in output.iterdir()),
            {
                "historical_components.json",
                "symbol_changes.json",
                "manifest.json",
            },
        )
        combined = "".join(path.read_text() for path in output.iterdir())
        self.assertNotIn("must-never-be-persisted", combined)
        self.assertFalse(any(output.glob("*.tmp")))

    def test_import_builds_historical_membership_and_price_coverage_audit(self):
        raw = self.root / "raw" / "2026-07-27"
        collect_snapshot(
            raw,
            snapshot_date="2026-07-27",
            collected_at="2026-07-27T10:00:00Z",
            input_components=self.components,
            input_symbol_changes=self.changes,
        )
        database = self.root / "research.db"
        with sqlite3.connect(database) as connection:
            store = ResearchPriceStore(connection)
            store.initialize()
            connection.execute(
                """
                INSERT INTO security_master
                    (ticker, name, security_type, active, observed_at, provider)
                VALUES ('AAPL', 'Apple Inc', 'Common Stock', 1,
                        '2026-07-27', 'fixture')
                """
            )
            connection.executemany(
                """
                INSERT INTO daily_prices
                    (ticker, date, raw_open, raw_high, raw_low, raw_close,
                     adjusted_open, adjusted_high, adjusted_low, adjusted_close,
                     adjustment_factor, volume, segment_id, provider,
                     snapshot_date, imported_at, adjustment_method)
                VALUES ('AAPL', ?, 100, 101, 99, 100, 100, 101, 99, 100,
                        1, 1000000, 1, 'fixture', '2026-07-27',
                        '2026-07-27T10:00:00Z', 'fixture')
                """,
                (
                    ("2018-12-28",),
                    ("2020-12-31",),
                    ("2022-12-30",),
                    ("2026-07-24",),
                ),
            )
        report_json = self.root / "report.json"
        report_md = self.root / "report.md"

        report = import_snapshot(
            database,
            raw,
            report_json=report_json,
            report_markdown=report_md,
            imported_at="2026-07-27T10:30:00Z",
        )

        self.assertEqual(report["membership_intervals"], 3)
        self.assertEqual(report["unique_tickers"], 3)
        self.assertEqual(report["delisted_tickers"], 2)
        self.assertEqual(report["symbol_changes"], 1)
        by_date = {
            row["observation_date"]: row for row in report["coverage_by_date"]
        }
        self.assertEqual(by_date["2018-12-31"]["member_count"], 2)
        self.assertEqual(by_date["2020-12-31"]["member_count"], 3)
        self.assertEqual(by_date["2022-12-31"]["member_count"], 1)
        self.assertEqual(by_date["2026-07-27"]["member_count"], 1)
        self.assertEqual(by_date["2020-12-31"]["price_covered"], 1)
        self.assertEqual(by_date["2020-12-31"]["missing_price_count"], 2)
        self.assertTrue(report_json.exists())
        self.assertIn("历史点时股票池覆盖审计", report_md.read_text())

        with sqlite3.connect(database) as connection:
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM universe_memberships
                    WHERE universe_key = 'sp500_historical_eodhd_v1'
                    """
                ).fetchone()[0],
                3,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM security_symbol_changes"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
