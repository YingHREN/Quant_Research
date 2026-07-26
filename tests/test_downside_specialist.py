import unittest

import numpy as np
import pandas as pd

from research.downside_specialist import attach_next_open_mae_targets


def feature_frame(dates):
    index = pd.MultiIndex.from_product(
        (("AAA",), dates),
        names=("ticker", "observation_date"),
    )
    return pd.DataFrame({"feature": np.arange(len(index))}, index=index)


def history(dates, *, opens=None, lows=None, closes=None):
    size = len(dates)
    open_values = np.full(size, 100.0) if opens is None else np.asarray(opens)
    low_values = np.full(size, 99.0) if lows is None else np.asarray(lows)
    close_values = (
        np.full(size, 100.0) if closes is None else np.asarray(closes)
    )
    return pd.DataFrame(
        {
            "Open": open_values,
            "High": np.maximum(open_values, close_values) + 1.0,
            "Low": low_values,
            "Close": close_values,
            "Volume": 1_000_000.0,
        },
        index=dates,
    )


class DownsideSpecialistTest(unittest.TestCase):
    def test_path_risk_uses_next_open_even_when_terminal_close_recovers(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        lows = [99.0, 99.0, 98.0, 94.0, 97.0, 99.0, 99.0, 99.0]
        closes = [100.0, 99.0, 98.0, 96.0, 101.0, 105.0, 104.0, 103.0]

        result = attach_next_open_mae_targets(
            feature_frame(dates),
            {"AAA": history(dates, lows=lows, closes=closes)},
            horizons=(5,),
        )
        first = result.loc[("AAA", dates[0])]

        self.assertAlmostEqual(first["executable_mae_5"], -0.06)
        self.assertEqual(first["downside_event_5"], 1.0)
        self.assertEqual(first["downside_label_end_date_5"], dates[5])
        self.assertGreater(closes[5] / 100.0 - 1.0, 0.0)

    def test_incomplete_future_path_keeps_tail_labels_missing(self):
        dates = pd.bdate_range("2026-01-02", periods=8)

        result = attach_next_open_mae_targets(
            feature_frame(dates),
            {"AAA": history(dates)},
            horizons=(5,),
        )

        self.assertTrue(result["executable_mae_5"].iloc[-5:].isna().all())
        self.assertTrue(result["downside_event_5"].iloc[-5:].isna().all())
        self.assertTrue(
            result["downside_label_end_date_5"].iloc[-5:].isna().all()
        )

    def test_missing_low_inside_path_rejects_label(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        lows = np.full(8, 99.0)
        lows[3] = np.nan

        result = attach_next_open_mae_targets(
            feature_frame(dates),
            {"AAA": history(dates, lows=lows)},
            horizons=(5,),
        )

        first = result.loc[("AAA", dates[0])]
        self.assertTrue(pd.isna(first["executable_mae_5"]))
        self.assertTrue(pd.isna(first["downside_event_5"]))
        self.assertTrue(pd.isna(first["downside_label_end_date_5"]))

    def test_five_and_twenty_day_thresholds_are_distinct(self):
        dates = pd.bdate_range("2026-01-02", periods=25)
        lows = np.full(25, 99.0)
        lows[4] = 94.0

        result = attach_next_open_mae_targets(
            feature_frame(dates),
            {"AAA": history(dates, lows=lows)},
            horizons=(5, 20),
        )
        first = result.loc[("AAA", dates[0])]

        self.assertEqual(first["downside_event_5"], 1.0)
        self.assertEqual(first["downside_event_20"], 0.0)


if __name__ == "__main__":
    unittest.main()
