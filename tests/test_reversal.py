from __future__ import annotations

import unittest

import pandas as pd

from research.reversal import _meets_higher_low_threshold, build_reversal_rows


def history(closes):
    index = pd.bdate_range("2026-01-02", periods=len(closes))
    close = pd.Series(closes, index=index, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=index,
    )


class ReversalFeatureTest(unittest.TestCase):
    def test_prior_high_breakout_is_a_crossing_of_prior_twenty_sessions(self):
        frame = history([100.0] * 20 + [99.0, 101.0, 102.0])

        rows = build_reversal_rows(frame)

        self.assertFalse(rows[20]["prior_high_breakout"])
        self.assertTrue(rows[21]["prior_high_breakout"])
        self.assertFalse(rows[22]["prior_high_breakout"])
        self.assertEqual(rows[21]["prior_high_resistance"], 100.0)

    def test_swing_high_is_not_available_until_reversal_confirms_it(self):
        frame = history([100, 104, 108, 110, 109, 107, 104])

        rows = build_reversal_rows(frame)

        self.assertIsNone(rows[4]["latest_confirmed_high_date"])
        self.assertEqual(
            rows[6]["latest_confirmed_high_date"],
            frame.index[3].date().isoformat(),
        )
        self.assertEqual(
            rows[6]["latest_confirmed_high_confirmed_date"],
            frame.index[6].date().isoformat(),
        )

    def test_swing_low_is_available_from_confirmation_and_persists(self):
        frame = history([110, 105, 100, 107, 108])

        rows = build_reversal_rows(frame)

        self.assertIsNone(rows[2]["latest_confirmed_low_date"])
        self.assertEqual(
            rows[3]["latest_confirmed_low_date"],
            frame.index[2].date().isoformat(),
        )
        self.assertEqual(rows[3]["latest_confirmed_low_price"], 100.0)
        self.assertEqual(
            rows[3]["latest_confirmed_low_confirmed_date"],
            frame.index[3].date().isoformat(),
        )
        self.assertEqual(
            rows[4]["latest_confirmed_low_date"],
            frame.index[2].date().isoformat(),
        )
        self.assertEqual(rows[4]["latest_confirmed_low_price"], 100.0)

    def test_descending_trendline_breakout_uses_two_confirmed_highs(self):
        frame = history(
            [100, 106, 110, 106, 102, 105, 108, 104, 100, 101, 102, 106]
        )

        rows = build_reversal_rows(frame)

        self.assertIsNotNone(rows[-1]["descending_trendline"])
        self.assertTrue(rows[-1]["trendline_breakout"])
        self.assertEqual(rows[-1]["trendline_high_1_date"], frame.index[2].date().isoformat())
        self.assertEqual(rows[-1]["trendline_high_2_date"], frame.index[6].date().isoformat())

    def test_newly_confirmed_high_cannot_retrofit_yesterdays_trendline(self):
        frame = history([100, 130, 150, 130, 80, 100, 89])

        rows = build_reversal_rows(frame)

        self.assertIsNotNone(rows[-1]["descending_trendline"])
        self.assertFalse(rows[-1]["trendline_breakout"])

    def test_higher_low_threshold_includes_exact_quarter_atr_boundary(self):
        self.assertTrue(_meets_higher_low_threshold(100.0, 101.0, 4.0))
        self.assertFalse(_meets_higher_low_threshold(100.0, 100.99, 4.0))

    def test_higher_low_is_emitted_on_confirmation_date(self):
        frame = history([110, 105, 100, 105, 109, 104, 102, 110])

        rows = build_reversal_rows(frame)

        self.assertFalse(rows[6]["higher_low_confirmed"])
        self.assertTrue(rows[7]["higher_low_confirmed"])
        self.assertEqual(rows[7]["higher_low_latest_date"], frame.index[6].date().isoformat())
        self.assertEqual(
            rows[7]["higher_low_confirmation_date"],
            frame.index[7].date().isoformat(),
        )

    def test_appending_future_prices_cannot_change_existing_rows(self):
        prefix = history([100, 106, 110, 106, 102, 105, 108, 104, 100, 101])
        extended = history([100, 106, 110, 106, 102, 105, 108, 104, 100, 101, 107, 95])

        expected = build_reversal_rows(prefix)
        actual = build_reversal_rows(extended)[: len(prefix)]

        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
