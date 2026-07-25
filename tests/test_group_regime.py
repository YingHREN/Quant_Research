import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research.group_regime import build_group_regime_state
from web.market_groups import MarketGroup


GROUP = MarketGroup(
    key="test_group",
    label_key="test.group",
    benchmark_tickers=("BENCH",),
    constituent_tickers=("AAA", "BBB", "CCC", "DDD"),
)


def history(close, volume=None, index=None):
    values = np.asarray(close, dtype=float)
    dates = (
        pd.bdate_range(end="2026-07-23", periods=len(values))
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
            "Open": values * 1.01,
            "High": values * 1.02,
            "Low": values * 0.98,
            "Close": values,
            "Volume": volumes,
        },
        index=dates,
    )


def stressed_histories():
    periods = 100
    index = pd.bdate_range(end="2026-07-23", periods=periods)
    base = np.linspace(100.0, 125.0, periods)
    result = {
        "QQQ": history(np.linspace(100.0, 120.0, periods), index=index),
        "BENCH": history(base, index=index),
    }
    for position, ticker in enumerate(GROUP.constituent_tickers):
        close = base + position
        close[-5:] = close[-6] * np.array([0.97, 0.94, 0.90, 0.87, 0.84])
        volume = np.full(periods, 1_000_000.0)
        volume[-5:] = 2_500_000.0
        result[ticker] = history(close, volume, index)
    return result


class GroupRegimeTest(unittest.TestCase):
    def test_broad_high_volume_selloff_creates_remembered_group_risk(self):
        frame = build_group_regime_state(stressed_histories(), GROUP)

        latest = frame.iloc[-1]
        self.assertGreaterEqual(latest["raw_score"], 60.0)
        self.assertGreaterEqual(latest["state_score"], latest["raw_score"])
        self.assertGreaterEqual(latest["down_volume_breadth"], 0.75)
        self.assertGreaterEqual(latest["new_20_low_breadth"], 0.75)
        self.assertLess(latest["relative_return_5"], -0.03)

    def test_memory_survives_two_quiet_rebound_sessions(self):
        histories = stressed_histories()
        for ticker in GROUP.constituent_tickers:
            source = histories[ticker]
            future_index = pd.bdate_range(source.index[-1], periods=3)[1:]
            last = float(source["Close"].iloc[-1])
            future = history(
                [last * 1.005, last * 1.01],
                [900_000.0, 900_000.0],
                future_index,
            )
            histories[ticker] = pd.concat([source, future])
        frame = build_group_regime_state(histories, GROUP)

        self.assertGreaterEqual(frame.iloc[-1]["state_score"], 20.0)
        self.assertEqual(frame.iloc[-1]["state"], "fading")
        self.assertEqual(frame.iloc[-1]["memory_age_sessions"], 2.0)

    def test_future_append_does_not_change_earlier_state(self):
        histories = stressed_histories()
        before = build_group_regime_state(histories, GROUP)
        extended = {}
        for ticker, source in histories.items():
            future_index = pd.bdate_range(source.index[-1], periods=3)[1:]
            future = history([10.0, 300.0], [9_000_000.0, 9_000_000.0], future_index)
            extended[ticker] = pd.concat([source, future])

        after = build_group_regime_state(extended, GROUP)

        assert_frame_equal(after.loc[before.index], before)

    def test_insufficient_constituents_is_explicitly_unavailable(self):
        histories = stressed_histories()
        histories = {
            ticker: source
            for ticker, source in histories.items()
            if ticker not in {"BBB", "CCC", "DDD"}
        }

        frame = build_group_regime_state(histories, GROUP)

        self.assertTrue(frame["raw_score"].isna().all())
        self.assertTrue((frame["state"] == "unavailable").all())


if __name__ == "__main__":
    unittest.main()
