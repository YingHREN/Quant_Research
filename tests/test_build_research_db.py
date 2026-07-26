from __future__ import annotations

import sqlite3
import unittest

from build_research_db import _price_history
from data.research_store import ResearchPriceStore


class BuildResearchDatabaseTest(unittest.TestCase):
    def test_behavior_history_reads_only_the_current_identity_segment(self):
        connection = sqlite3.connect(":memory:")
        ResearchPriceStore(connection).initialize()
        connection.execute(
            """
            INSERT INTO security_master
                (ticker, name, security_type, active, observed_at, provider)
            VALUES ('AAA', 'Example', 'Common Stock', 1, '2026-01-03', 'test')
            """
        )
        for segment_id, date_text, price in (
            (1, "2020-01-02", 10.0),
            (2, "2026-01-02", 20.0),
            (2, "2026-01-03", 21.0),
        ):
            connection.execute(
                """
                INSERT INTO daily_prices
                    (ticker, date, raw_open, raw_high, raw_low, raw_close,
                     adjusted_open, adjusted_high, adjusted_low, adjusted_close,
                     adjustment_factor, volume, segment_id, provider,
                     snapshot_date, imported_at, adjustment_method)
                VALUES ('AAA', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 100, ?, 'test',
                        '2026-01-03', '2026-01-03T00:00:00Z', 'test')
                """,
                (
                    date_text,
                    price,
                    price,
                    price,
                    price,
                    price,
                    price,
                    price,
                    price,
                    segment_id,
                ),
            )
        connection.executemany(
            """
            INSERT INTO history_segments
                (ticker, segment_id, first_date, last_date, row_count,
                 break_before_days, is_current_segment)
            VALUES ('AAA', ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "2020-01-02", "2020-01-02", 1, None, 0),
                (2, "2026-01-02", "2026-01-03", 2, 2192, 1),
            ],
        )

        self.assertEqual(
            _price_history(connection, "AAA", "2026-01-03"),
            [("2026-01-02", 20.0), ("2026-01-03", 21.0)],
        )


if __name__ == "__main__":
    unittest.main()
