import unittest

import numpy as np
import pandas as pd

from research.recency_momentum import (
    RECENCY_FEATURE_COLUMNS,
    build_recency_momentum_frame,
)


def history(closes, volumes=None):
    closes = np.asarray(closes, dtype=float)
    dates = pd.bdate_range("2026-01-02", periods=len(closes))
    if volumes is None:
        volumes = np.full(len(closes), 1_000.0)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": np.asarray(volumes, dtype=float),
        },
        index=dates,
    )


class RecencyMomentumTest(unittest.TestCase):
    def setUp(self):
        returns = np.linspace(-0.02, 0.03, 89)
        stock_close = 100.0 * np.exp(np.r_[0.0, returns].cumsum())
        market_close = 100.0 * np.exp(
            np.r_[0.0, np.linspace(-0.01, 0.015, 89)].cumsum()
        )
        sector_close = 100.0 * np.exp(
            np.r_[0.0, np.linspace(-0.015, 0.02, 89)].cumsum()
        )
        self.histories = {
            "AAA": history(stock_close, np.linspace(800, 1_600, 90)),
            "QQQ": history(market_close),
            "XLK": history(sector_close),
        }

    def test_computes_normalized_exponential_decay_heads(self):
        frame = build_recency_momentum_frame(
            self.histories,
            benchmark_by_ticker={"AAA": "XLK"},
        )

        row = frame.loc[("AAA", self.histories["AAA"].index[-1])]
        log_returns = np.log(
            self.histories["AAA"]["Close"]
            / self.histories["AAA"]["Close"].shift(1)
        )
        weights = np.power(0.5, np.arange(3, dtype=float) / 2.0)
        expected = np.average(
            log_returns.iloc[-3:].to_numpy()[::-1],
            weights=weights,
        )
        self.assertAlmostEqual(row["decay_mom_1_3"], expected)
        self.assertTrue(np.isfinite(row["decay_excess_qqq_1_20"]))
        self.assertTrue(np.isfinite(row["decay_excess_sector_1_20"]))
        self.assertEqual(tuple(frame.columns), RECENCY_FEATURE_COLUMNS)

    def test_appending_future_rows_does_not_change_existing_features(self):
        baseline = build_recency_momentum_frame(
            self.histories,
            benchmark_by_ticker={"AAA": "XLK"},
        )
        extended = {}
        for ticker, source in self.histories.items():
            future_dates = pd.bdate_range(
                source.index[-1] + pd.offsets.BDay(1),
                periods=5,
            )
            future_close = np.linspace(
                source["Close"].iloc[-1] * 0.7,
                source["Close"].iloc[-1] * 1.4,
                len(future_dates),
            )
            future = history(future_close)
            future.index = future_dates
            extended[ticker] = pd.concat((source, future))

        contaminated = build_recency_momentum_frame(
            extended,
            benchmark_by_ticker={"AAA": "XLK"},
        )

        pd.testing.assert_frame_equal(
            baseline.loc["AAA"],
            contaminated.loc["AAA"].reindex(baseline.loc["AAA"].index),
        )

    def test_missing_sector_benchmark_remains_missing(self):
        frame = build_recency_momentum_frame(
            {"AAA": self.histories["AAA"], "QQQ": self.histories["QQQ"]},
            benchmark_by_ticker={"AAA": "MISSING"},
        )

        latest = frame.loc[("AAA", self.histories["AAA"].index[-1])]
        self.assertTrue(np.isnan(latest["decay_excess_sector_1_20"]))
        self.assertTrue(np.isnan(latest["decay_market_agreement_1_20"]))


if __name__ == "__main__":
    unittest.main()
