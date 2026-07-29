from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from data.eodhd_main_database import rebuild_from_eodhd
from data.research_store import ADJUSTMENT_METHOD, ResearchPriceStore


class EODHDMainDatabaseTest(unittest.TestCase):
    def test_rebuild_writes_adjusted_ohlcv_and_eodhd_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research = root / "research.db"
            output = root / "prices.db"
            self._research_database(research)

            summary = rebuild_from_eodhd(
                research,
                output,
                tickers=("AAA",),
                fetched_at=datetime(2026, 7, 29, tzinfo=timezone.utc),
            )

            connection = sqlite3.connect(output)
            try:
                rows = connection.execute(
                    """
                    SELECT ticker, date, open, high, low, close, volume
                    FROM prices ORDER BY ticker, date
                    """
                ).fetchall()
                coverage = connection.execute(
                    """
                    SELECT ticker, provider, adjustment, first_date, last_date
                    FROM price_coverage
                    """
                ).fetchall()
            finally:
                connection.close()

            self.assertEqual(
                rows,
                [
                    (
                        "AAA",
                        "2026-07-27",
                        45.0,
                        55.0,
                        40.0,
                        50.0,
                        1000.0,
                    ),
                    (
                        "AAA",
                        "2026-07-28",
                        52.5,
                        60.0,
                        50.0,
                        57.5,
                        1100.0,
                    ),
                ],
            )
            self.assertEqual(
                coverage,
                [
                    (
                        "AAA",
                        "eodhd",
                        ADJUSTMENT_METHOD,
                        "2026-07-27",
                        "2026-07-28",
                    )
                ],
            )
            self.assertEqual(summary.requested, 1)
            self.assertEqual(summary.imported, 1)
            self.assertEqual(summary.row_count, 2)
            self.assertEqual(summary.first_date, "2026-07-27")
            self.assertEqual(summary.last_date, "2026-07-28")
            self.assertEqual(summary.integrity, "ok")

    def _research_database(self, path):
        connection = sqlite3.connect(path)
        store = ResearchPriceStore(connection)
        store.initialize()
        with connection:
            for ticker in ("AAA", "BBB"):
                connection.execute(
                    """
                    INSERT INTO security_master
                        (ticker, name, security_type, active, observed_at, provider)
                    VALUES (?, ?, 'Common Stock', 1, '2026-07-28', 'eodhd')
                    """,
                    (ticker, f"{ticker} Corp"),
                )
                connection.execute(
                    """
                    INSERT INTO history_segments
                        (ticker, segment_id, first_date, last_date, row_count,
                         break_before_days, is_current_segment)
                    VALUES (?, 1, '2026-07-27', '2026-07-28', 2, NULL, 1)
                    """,
                    (ticker,),
                )
            rows = (
                (
                    "AAA",
                    "2026-07-27",
                    90.0,
                    110.0,
                    80.0,
                    100.0,
                    45.0,
                    55.0,
                    40.0,
                    50.0,
                    0.5,
                    1000.0,
                ),
                (
                    "AAA",
                    "2026-07-28",
                    105.0,
                    120.0,
                    100.0,
                    115.0,
                    52.5,
                    60.0,
                    50.0,
                    57.5,
                    0.5,
                    1100.0,
                ),
                (
                    "BBB",
                    "2026-07-27",
                    20.0,
                    22.0,
                    19.0,
                    21.0,
                    20.0,
                    22.0,
                    19.0,
                    21.0,
                    1.0,
                    2000.0,
                ),
                (
                    "BBB",
                    "2026-07-28",
                    21.0,
                    23.0,
                    20.0,
                    22.0,
                    21.0,
                    23.0,
                    20.0,
                    22.0,
                    1.0,
                    2100.0,
                ),
            )
            connection.executemany(
                """
                INSERT INTO daily_prices
                    (ticker, date, raw_open, raw_high, raw_low, raw_close,
                     adjusted_open, adjusted_high, adjusted_low, adjusted_close,
                     adjustment_factor, volume, segment_id, provider,
                     snapshot_date, imported_at, adjustment_method)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'eodhd',
                        '2026-07-29', '2026-07-29T00:00:00+00:00', ?)
                """,
                [(*row, ADJUSTMENT_METHOD) for row in rows],
            )
        connection.close()


if __name__ == "__main__":
    unittest.main()
