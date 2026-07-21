import unittest

import pandas as pd

from research.market_data import bar_on, next_bar
from tests.helpers import make_ohlcv


class MarketDataTest(unittest.TestCase):
    def test_bar_on_does_not_forward_fill_missing_date(self):
        frame = make_ohlcv([10, 11, 12]).drop(pd.Timestamp("2020-01-02"))

        self.assertIsNone(bar_on(frame, pd.Timestamp("2020-01-02")))

    def test_next_bar_returns_actual_later_bar_and_open(self):
        frame = make_ohlcv([10, 11, 12], opens=[9, 10.5, 11.5])

        date, bar = next_bar(frame, pd.Timestamp("2020-01-01"))

        self.assertEqual(date, pd.Timestamp("2020-01-02"))
        self.assertEqual(float(bar["Open"]), 10.5)


if __name__ == "__main__":
    unittest.main()
