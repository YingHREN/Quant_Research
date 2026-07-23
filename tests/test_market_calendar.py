from __future__ import annotations

import unittest

import pandas as pd

from web.market_calendar import session_offset


class MarketCalendarTest(unittest.TestCase):
    def test_independence_day_observation_is_skipped(self):
        self.assertEqual(
            session_offset(pd.Timestamp("2026-07-02"), 1),
            pd.Timestamp("2026-07-06"),
        )

    def test_good_friday_is_skipped(self):
        self.assertEqual(
            session_offset(pd.Timestamp("2026-04-02"), 1),
            pd.Timestamp("2026-04-06"),
        )

    def test_new_year_on_saturday_does_not_close_preceding_friday(self):
        self.assertEqual(
            session_offset(pd.Timestamp("2027-12-30"), 1),
            pd.Timestamp("2027-12-31"),
        )

    def test_known_history_dates_take_precedence_over_calendar_projection(self):
        history = pd.DatetimeIndex(["2026-07-02", "2026-07-07"])
        self.assertEqual(
            session_offset(pd.Timestamp("2026-07-02"), 1, known_sessions=history),
            pd.Timestamp("2026-07-07"),
        )


if __name__ == "__main__":
    unittest.main()
