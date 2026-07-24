import unittest

import numpy as np
import pandas as pd

from research.temporal_momentum import stock_temporal_features


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


if __name__ == "__main__":
    unittest.main()
