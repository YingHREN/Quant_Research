from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

import build_research_rs
from research.relative_strength import persist_relative_strength_snapshot


class BuildResearchRsTest(unittest.TestCase):
    def test_persistence_replaces_same_snapshot_transactionally(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            first = pd.DataFrame(
                [
                    {
                        "ticker": "AAA",
                        "asof": "2026-07-24",
                        "return_63": 0.1,
                        "return_126": 0.2,
                        "return_189": 0.3,
                        "return_252": 0.4,
                        "composite": 0.2,
                        "rs_rating": 80,
                        "sample_count": 2,
                        "model_version": "cross_sectional_rs_v1",
                    }
                ]
            )
            persist_relative_strength_snapshot(database, first)
            second = first.copy()
            second.loc[0, "rs_rating"] = 91
            persist_relative_strength_snapshot(database, second)

            with sqlite3.connect(database) as connection:
                rows = connection.execute(
                    """
                    SELECT ticker, rs_rating, model_version
                    FROM relative_strength_snapshots
                    """
                ).fetchall()
                integrity = connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]

        self.assertEqual(rows, [("AAA", 91, "cross_sectional_rs_v1")])
        self.assertEqual(integrity, "ok")

    def test_cli_builds_latest_snapshot_from_daily_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            dates = pd.bdate_range(end="2026-07-24", periods=253)
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TABLE daily_prices (
                        ticker TEXT NOT NULL,
                        date TEXT NOT NULL,
                        adjusted_close REAL
                    )
                    """
                )
                connection.executemany(
                    "INSERT INTO daily_prices VALUES (?, ?, ?)",
                    [
                        (
                            ticker,
                            date.date().isoformat(),
                            100.0 + position * multiplier,
                        )
                        for ticker, multiplier in (("AAA", 1.0), ("BBB", 0.2))
                        for position, date in enumerate(dates)
                    ],
                )

            code = build_research_rs.main(
                ["--database", str(database), "--asof", "2026-07-24"]
            )
            with sqlite3.connect(database) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM relative_strength_snapshots"
                ).fetchone()[0]

        self.assertEqual(code, 0)
        self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
