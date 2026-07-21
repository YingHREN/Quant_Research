import unittest

import numpy as np
import pandas as pd

from research.momentum import add_cross_sectional_ranks, momentum_features
from tests.helpers import make_ohlcv


def indexed_price_fixture(length):
    returns = 0.001 + np.sin(np.arange(length) / 13) * 0.0005
    close = 100 * np.cumprod(1 + returns)
    return make_ohlcv(close)


class MomentumTest(unittest.TestCase):
    def test_mom_3_1_skips_latest_21_bars(self):
        history = indexed_price_fixture(300)

        got = momentum_features(history, history, history.index[-1])

        expected = history.Close.iloc[-22] / history.Close.iloc[-64] - 1
        self.assertAlmostEqual(got["mom_3_1"], expected)

    def test_short_history_does_not_fake_twelve_month_momentum(self):
        history = indexed_price_fixture(200)

        got = momentum_features(history, history, history.index[-1])

        self.assertIsNone(got["mom_12_1"])
        self.assertTrue(got["mom_12_1_missing"])

    def test_ranks_are_computed_within_the_same_date(self):
        rows = pd.DataFrame(
            {
                "observation_date": ["2020-01-01"] * 3 + ["2020-01-02"] * 3,
                "mom_6_1": [1.0, 2.0, 3.0, 30.0, 20.0, 10.0],
            }
        )

        ranked = add_cross_sectional_ranks(rows, features=("mom_6_1",))

        first = ranked[ranked.observation_date == "2020-01-01"]
        second = ranked[ranked.observation_date == "2020-01-02"]
        self.assertEqual(first.mom_6_1_rank.tolist(), [1 / 3, 2 / 3, 1.0])
        self.assertEqual(second.mom_6_1_rank.tolist(), [1.0, 2 / 3, 1 / 3])


if __name__ == "__main__":
    unittest.main()
