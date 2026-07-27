import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research.market_gate import (
    MARKET_GATE_VERSION,
    build_market_gate_frame,
    latest_market_gate,
)


def history(close, volume=None, index=None):
    close = np.asarray(close, dtype=float)
    dates = (
        pd.bdate_range("2025-01-02", periods=len(close))
        if index is None
        else pd.DatetimeIndex(index)
    )
    volume = (
        np.full(len(close), 1_000_000.0)
        if volume is None
        else np.asarray(volume, dtype=float)
    )
    return pd.DataFrame(
        {
            "Open": close * 0.997,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": volume,
        },
        index=dates,
    )


def broad_histories(qqq_close, qqq_volume=None, spy_close=None, spy_volume=None):
    spy_close = qqq_close if spy_close is None else spy_close
    return {
        "QQQ": history(qqq_close, qqq_volume),
        "SPY": history(spy_close, spy_volume),
        "AAA": history(np.asarray(qqq_close) * 1.01),
        "BBB": history(np.asarray(qqq_close) * 0.99),
    }


class MarketGateTest(unittest.TestCase):
    def test_steady_broad_advance_passes_confirmed_uptrend(self):
        close = np.linspace(100.0, 170.0, 240)

        frame = build_market_gate_frame(
            broad_histories(close),
            minimum_breadth=2,
        )
        latest = latest_market_gate(
            broad_histories(close),
            minimum_breadth=2,
        )

        self.assertEqual(frame.iloc[-1]["market_state"], "confirmed_uptrend")
        self.assertEqual(latest["state"], "pass")
        self.assertEqual(latest["market_state"], "confirmed_uptrend")
        self.assertEqual(latest["version"], MARKET_GATE_VERSION)
        self.assertTrue(latest["point_in_time"])

    def test_correction_starts_rally_and_confirms_only_on_day_four(self):
        prefix = np.linspace(100.0, 160.0, 220)
        selloff = np.array([156.0, 151.0, 146.0, 140.0, 136.0])
        rally = np.array([138.0, 139.0, 140.0, 143.0])
        close = np.concatenate((prefix, selloff, rally))
        volume = np.full(len(close), 1_000_000.0)
        volume[-1] = 1_800_000.0

        frame = build_market_gate_frame(
            broad_histories(close, volume, close * 1.002, volume),
            minimum_breadth=2,
        )

        self.assertEqual(frame.iloc[-5]["market_state"], "market_in_correction")
        self.assertEqual(frame.iloc[-4]["market_state"], "rally_attempt")
        self.assertEqual(frame.iloc[-2]["rally_day_count"], 3)
        self.assertEqual(frame.iloc[-2]["market_state"], "rally_attempt")
        self.assertEqual(frame.iloc[-1]["rally_day_count"], 4)
        self.assertEqual(frame.iloc[-1]["market_state"], "confirmed_uptrend")
        self.assertEqual(
            frame.iloc[-1]["follow_through_date"],
            frame.index[-1].date().isoformat(),
        )

    def test_new_low_resets_rally_attempt(self):
        prefix = np.linspace(100.0, 160.0, 220)
        tail = np.array([150.0, 140.0, 142.0, 143.0, 139.0])
        close = np.concatenate((prefix, tail))

        frame = build_market_gate_frame(
            broad_histories(close),
            minimum_breadth=2,
        )

        self.assertEqual(frame.iloc[-3]["market_state"], "rally_attempt")
        self.assertEqual(frame.iloc[-1]["market_state"], "market_in_correction")
        self.assertEqual(frame.iloc[-1]["rally_day_count"], 0)

    def test_rally_attempt_expires_after_ten_sessions_without_follow_through(self):
        prefix = np.linspace(100.0, 160.0, 220)
        selloff = np.array([154.0, 148.0, 142.0, 136.0])
        weak_rally = np.linspace(137.0, 141.0, 11)
        close = np.concatenate((prefix, selloff, weak_rally))

        frame = build_market_gate_frame(
            broad_histories(close),
            minimum_breadth=2,
        )

        self.assertEqual(frame.iloc[-2]["market_state"], "rally_attempt")
        self.assertEqual(frame.iloc[-2]["rally_day_count"], 10)
        self.assertEqual(frame.iloc[-1]["market_state"], "market_in_correction")
        self.assertEqual(frame.iloc[-1]["rally_day_count"], 0)

    def test_distribution_days_expire_and_are_removed_after_five_percent_gain(self):
        close = np.linspace(100.0, 140.0, 210).tolist()
        volume = [1_000_000.0] * 210
        close.extend([139.0] + [139.0] * 26)
        volume.extend([1_500_000.0] + [1_000_000.0] * 26)
        expired = build_market_gate_frame(
            broad_histories(close, volume),
            minimum_breadth=2,
        )
        self.assertGreaterEqual(expired.iloc[-26]["qqq_distribution_days"], 1)
        self.assertEqual(expired.iloc[-1]["qqq_distribution_days"], 0)

        gained_close = np.linspace(100.0, 140.0, 210).tolist()
        gained_volume = [1_000_000.0] * 210
        gained_close.extend([139.0, 141.0, 143.0, 146.1])
        gained_volume.extend([1_500_000.0, 1_000_000.0, 1_000_000.0, 1_000_000.0])
        gained = build_market_gate_frame(
            broad_histories(gained_close, gained_volume),
            minimum_breadth=2,
        )
        self.assertEqual(gained.iloc[-1]["qqq_distribution_days"], 0)

    def test_pressure_after_confirmation_fails_gate(self):
        close = np.linspace(100.0, 160.0, 220).tolist()
        volume = [1_000_000.0] * 220
        for drop in (0.004, 0.006, 0.005, 0.004):
            close.append(close[-1] * (1.0 - drop))
            volume.append(volume[-1] + 100_000.0)

        latest = latest_market_gate(
            broad_histories(close, volume),
            minimum_breadth=2,
        )

        self.assertEqual(latest["state"], "fail")
        self.assertEqual(latest["market_state"], "uptrend_under_pressure")
        self.assertGreaterEqual(latest["values"]["distribution_days"], 4)

    def test_missing_benchmark_or_breadth_is_missing(self):
        close = np.linspace(100.0, 160.0, 240)

        no_spy = latest_market_gate(
            {"QQQ": history(close)},
            minimum_breadth=2,
        )
        no_breadth = latest_market_gate(
            {"QQQ": history(close), "SPY": history(close)},
            minimum_breadth=2,
        )

        self.assertEqual(no_spy["state"], "missing")
        self.assertEqual(no_breadth["state"], "missing")
        self.assertIn("insufficient_breadth_coverage", no_breadth["reason_codes"])

    def test_future_rows_do_not_change_prior_market_gate(self):
        close = np.linspace(100.0, 160.0, 240)
        sources = broad_histories(close)
        before = build_market_gate_frame(sources, minimum_breadth=2)
        future_dates = pd.bdate_range(before.index[-1], periods=6)[1:]
        extended = {
            ticker: pd.concat(
                (
                    source,
                    history(
                        [80.0, 200.0, 60.0, 220.0, 50.0],
                        [9_000_000.0] * 5,
                        future_dates,
                    ),
                )
            )
            for ticker, source in sources.items()
        }

        after = build_market_gate_frame(extended, minimum_breadth=2)

        assert_frame_equal(after.loc[before.index], before)


if __name__ == "__main__":
    unittest.main()
