import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research.slow_decline import build_slow_decline_state
from web.market_groups import MarketGroup


GROUP = MarketGroup(
    key="software_test",
    label_key="test.software",
    benchmark_tickers=("IGV", "XSW"),
    constituent_tickers=("ADBE", "CRM"),
)


def price_history(close, index=None):
    values = np.asarray(close, dtype=float)
    dates = (
        pd.bdate_range(end="2026-07-23", periods=len(values))
        if index is None
        else pd.DatetimeIndex(index)
    )
    return pd.DataFrame(
        {
            "Open": values * 1.002,
            "High": values * 1.008,
            "Low": values * 0.992,
            "Close": values,
            "Volume": np.full(len(values), 1_000_000.0),
        },
        index=dates,
    )


def histories():
    periods = 140
    index = pd.bdate_range(end="2026-07-23", periods=periods)
    return {
        "QQQ": price_history(np.linspace(100.0, 135.0, periods), index),
        "IGV": price_history(np.linspace(100.0, 96.0, periods), index),
        "XSW": price_history(np.linspace(100.0, 94.0, periods), index),
        "ADBE": price_history(np.linspace(150.0, 95.0, periods), index),
        "CRM": price_history(np.linspace(100.0, 125.0, periods), index),
    }


class SlowDeclineTest(unittest.TestCase):
    def test_persistent_erosion_scores_high_without_one_day_crash(self):
        frame = build_slow_decline_state(histories(), GROUP)
        adbe = frame.loc["ADBE"].iloc[-1]
        crm = frame.loc["CRM"].iloc[-1]

        self.assertGreaterEqual(adbe["raw_score"], 70.0)
        self.assertGreaterEqual(adbe["state_score"], 70.0)
        self.assertLess(adbe["return_20"], -0.05)
        self.assertLess(adbe["relative_qqq_20"], -0.05)
        self.assertLess(crm["raw_score"], adbe["raw_score"])

    def test_state_is_strictly_point_in_time(self):
        source = histories()
        before = build_slow_decline_state(source, GROUP)
        extended = {}
        for ticker, frame in source.items():
            future_index = pd.bdate_range(frame.index[-1], periods=3)[1:]
            future = price_history([10.0, 1_000.0], future_index)
            extended[ticker] = pd.concat([frame, future])

        after = build_slow_decline_state(extended, GROUP)

        assert_frame_equal(after.loc[before.index], before)

    def test_unmapped_ticker_is_not_assigned_software_risk(self):
        source = histories()
        source["AAPL"] = price_history(
            np.linspace(120.0, 80.0, 140),
            next(iter(source.values())).index,
        )

        frame = build_slow_decline_state(source, GROUP)

        self.assertNotIn("AAPL", frame.index.get_level_values("ticker"))


if __name__ == "__main__":
    unittest.main()
