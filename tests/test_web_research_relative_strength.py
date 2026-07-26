from pathlib import Path
import sqlite3
import tempfile
import unittest

from web.services.research_relative_strength import (
    ResearchRelativeStrengthService,
)


class ResearchRelativeStrengthServiceTest(unittest.TestCase):
    def test_reads_only_precomputed_snapshot_and_normalizes_tickers(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TABLE relative_strength_snapshots (
                        asof TEXT NOT NULL,
                        ticker TEXT NOT NULL,
                        return_63 REAL NOT NULL,
                        return_126 REAL NOT NULL,
                        return_189 REAL NOT NULL,
                        return_252 REAL NOT NULL,
                        composite REAL NOT NULL,
                        rs_rating INTEGER NOT NULL,
                        sample_count INTEGER NOT NULL,
                        model_version TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (asof, ticker, model_version)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO relative_strength_snapshots
                    VALUES (
                        '2026-07-24', 'AAA', .1, .2, .3, .4, .2, 91,
                        1000, 'cross_sectional_rs_v1', '2026-07-25T00:00:00Z'
                    )
                    """
                )

            payload = ResearchRelativeStrengthService(database).build(
                [" aaa ", "MISSING"]
            )

        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["asof"], "2026-07-24")
        self.assertEqual(payload["sample_count"], 1000)
        self.assertEqual(payload["model_version"], "cross_sectional_rs_v1")
        self.assertEqual(payload["by_ticker"]["AAA"]["rs_rating"], 91)
        self.assertIsNone(
            payload["by_ticker"]["MISSING"]["rs_rating"]
        )

    def test_missing_table_is_safely_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            sqlite3.connect(database).close()

            payload = ResearchRelativeStrengthService(database).build(["AAA"])

        self.assertEqual(payload["status"], "unavailable")
        self.assertIsNone(payload["by_ticker"]["AAA"]["rs_rating"])


if __name__ == "__main__":
    unittest.main()
