import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research.market_regime import build_market_regime_frame


def history(close, *, volume=None, index=None):
    values = np.asarray(close, dtype=float)
    dates = (
        pd.bdate_range("2025-01-02", periods=len(values))
        if index is None
        else pd.DatetimeIndex(index)
    )
    volumes = (
        np.full(len(values), 1_000_000.0)
        if volume is None
        else np.asarray(volume, dtype=float)
    )
    return pd.DataFrame(
        {
            "Open": values,
            "High": values * 1.01,
            "Low": values * 0.99,
            "Close": values,
            "Volume": volumes,
        },
        index=dates,
    )


def histories_with_tail(tail):
    prefix = np.linspace(100.0, 140.0, 220 - len(tail))
    qqq = np.concatenate((prefix, np.asarray(tail, dtype=float)))
    spy = np.linspace(100.0, 145.0, len(qqq))
    return {"QQQ": history(qqq), "SPY": history(spy)}


class MarketRegimeTest(unittest.TestCase):
    def test_steady_broad_advance_is_uptrend(self):
        close = np.linspace(100.0, 160.0, 220)

        frame = build_market_regime_frame(
            {"QQQ": history(close), "SPY": history(close * 1.01)}
        )

        self.assertEqual(frame.iloc[-1]["regime"], "uptrend")
        self.assertEqual(
            frame.iloc[-1]["regime_version"],
            "market_regime_v1",
        )
        self.assertIn("qqq_above_trend_stack", frame.iloc[-1]["reason_codes"])

    def test_flat_market_is_range_bound(self):
        close = np.full(220, 100.0)

        frame = build_market_regime_frame(
            {"QQQ": history(close), "SPY": history(close)}
        )

        self.assertEqual(frame.iloc[-1]["regime"], "range_bound")

    def test_two_mild_pressure_conditions_create_under_pressure(self):
        tail = np.linspace(140.0, 136.0, 20)

        frame = build_market_regime_frame(histories_with_tail(tail))

        latest = frame.iloc[-1]
        self.assertEqual(latest["regime"], "under_pressure")
        self.assertGreaterEqual(latest["pressure_condition_count"], 2.0)
        self.assertGreater(latest["return_20"], -0.05)

    def test_correction_precedes_pressure(self):
        tail = np.linspace(140.0, 131.0, 20)

        frame = build_market_regime_frame(histories_with_tail(tail))

        latest = frame.iloc[-1]
        self.assertEqual(latest["regime"], "correction")
        self.assertGreater(latest["return_5"], -0.07)
        self.assertLessEqual(latest["return_20"], -0.05)

    def test_acute_selloff_precedes_correction(self):
        tail = np.concatenate(
            (np.linspace(140.0, 139.0, 14), np.linspace(139.0, 126.0, 6))
        )

        frame = build_market_regime_frame(histories_with_tail(tail))

        latest = frame.iloc[-1]
        self.assertEqual(latest["regime"], "acute_selloff")
        self.assertLessEqual(latest["return_5"], -0.07)

    def test_first_199_common_sessions_are_unavailable(self):
        close = np.linspace(100.0, 140.0, 220)

        frame = build_market_regime_frame(
            {"QQQ": history(close), "SPY": history(close)}
        )

        self.assertTrue((frame.iloc[:199]["regime"] == "unavailable").all())
        self.assertNotEqual(frame.iloc[199]["regime"], "unavailable")

    def test_appending_future_prices_does_not_change_prior_rows(self):
        sources = histories_with_tail(np.linspace(140.0, 136.0, 20))
        before = build_market_regime_frame(sources)
        future_dates = pd.bdate_range(before.index[-1], periods=6)[1:]
        extended = {}
        for ticker, source in sources.items():
            future = history(
                [80.0, 200.0, 60.0, 220.0, 50.0],
                volume=[9_000_000.0] * 5,
                index=future_dates,
            )
            extended[ticker] = pd.concat((source, future))

        after = build_market_regime_frame(extended)

        assert_frame_equal(after.loc[before.index], before)

    def test_missing_benchmark_returns_explicit_unavailable_rows(self):
        close = np.linspace(100.0, 140.0, 220)

        frame = build_market_regime_frame({"QQQ": history(close)})

        self.assertEqual(len(frame), 220)
        self.assertTrue((frame["regime"] == "unavailable").all())
        self.assertTrue(frame["return_20"].isna().all())


if __name__ == "__main__":
    unittest.main()
