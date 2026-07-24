import unittest

import numpy as np
import pandas as pd

from research.temporal_momentum import (
    stock_temporal_features,
    temporal_feature_frame,
)


def ohlcv(close, volume=None):
    values = np.asarray(close, dtype=float)
    dates = pd.bdate_range("2026-01-02", periods=len(values))
    supplied_volume = (
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
            "Volume": supplied_volume,
        },
        index=dates,
    )


class TemporalMomentumTest(unittest.TestCase):
    def test_recent_equal_return_has_more_weight_than_older_return(self):
        recent = np.full(70, 100.0)
        recent[-1] = 110.0
        older = np.full(70, 100.0)
        older[-3:] = 110.0

        recent_value = stock_temporal_features(ohlcv(recent))[
            "decay_mom_1_3"
        ].iloc[-1]
        older_value = stock_temporal_features(ohlcv(older))[
            "decay_mom_1_3"
        ].iloc[-1]

        self.assertGreater(recent_value, older_value)
        self.assertGreater(older_value, 0.0)

    def test_future_observations_cannot_change_prior_decay_features(self):
        history = ohlcv(np.linspace(100.0, 130.0, 70))
        cutoff = history.index[-1]
        future = ohlcv([1_000.0, 10.0]).set_axis(
            pd.bdate_range(cutoff + pd.Timedelta(days=1), periods=2)
        )

        before = stock_temporal_features(history).loc[cutoff]
        after = stock_temporal_features(pd.concat((history, future))).loc[cutoff]

        pd.testing.assert_series_equal(after, before)

    def test_high_volume_increases_recent_price_progress_confirmation(self):
        close = np.full(70, 100.0)
        close[-1] = 110.0
        normal = ohlcv(close)
        high_volume = normal.copy()
        high_volume.loc[high_volume.index[-1], "Volume"] = 5_000_000.0

        normal_value = stock_temporal_features(normal)[
            "decay_volume_confirmation_1_20"
        ].iloc[-1]
        high_value = stock_temporal_features(high_volume)[
            "decay_volume_confirmation_1_20"
        ].iloc[-1]

        self.assertGreater(high_value, normal_value)
        self.assertGreater(normal_value, 0.0)

    def test_high_volume_weak_close_has_negative_close_location_pressure(self):
        history = ohlcv(np.full(70, 100.0))
        latest = history.index[-1]
        history.loc[latest, ["High", "Low", "Close", "Volume"]] = (
            110.0,
            90.0,
            92.0,
            5_000_000.0,
        )

        value = stock_temporal_features(history)[
            "decay_close_location_pressure_1_20"
        ].iloc[-1]

        self.assertLess(value, 0.0)

    def test_cross_market_context_requires_an_exact_session_match(self):
        stock = ohlcv(np.linspace(100.0, 140.0, 70))
        qqq = ohlcv(np.linspace(100.0, 120.0, 70)).iloc[:-1]
        sector = ohlcv(np.linspace(100.0, 130.0, 70))

        frame = temporal_feature_frame(
            {"AAA": stock, "QQQ": qqq, "SOXX": sector},
            {"AAA": "SOXX"},
        )

        latest = frame.loc[("AAA", stock.index[-1])]
        prior = frame.loc[("AAA", stock.index[-2])]
        self.assertTrue(np.isnan(latest["decay_excess_qqq_1_20"]))
        self.assertTrue(np.isnan(latest["decay_market_agreement_1_20"]))
        self.assertTrue(np.isfinite(prior["decay_excess_qqq_1_20"]))

    def test_future_market_spike_cannot_change_prior_context(self):
        stock = ohlcv(np.linspace(100.0, 140.0, 70))
        qqq = ohlcv(np.linspace(100.0, 120.0, 70))
        sector = ohlcv(np.linspace(100.0, 130.0, 70))
        cutoff = stock.index[-1]
        future_dates = pd.bdate_range(cutoff + pd.Timedelta(days=1), periods=2)
        future_qqq = ohlcv([1_000.0, 10.0]).set_axis(future_dates)

        before = temporal_feature_frame(
            {"AAA": stock, "QQQ": qqq, "SOXX": sector},
            {"AAA": "SOXX"},
        ).loc[("AAA", cutoff)]
        after = temporal_feature_frame(
            {
                "AAA": stock,
                "QQQ": pd.concat((qqq, future_qqq)),
                "SOXX": sector,
            },
            {"AAA": "SOXX"},
        ).loc[("AAA", cutoff)]

        pd.testing.assert_series_equal(after, before)


if __name__ == "__main__":
    unittest.main()
