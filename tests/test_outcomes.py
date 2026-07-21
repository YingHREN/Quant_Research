import unittest

import numpy as np
import pandas as pd

from research.outcomes import barrier_outcome, forward_outcomes
from tests.helpers import make_ohlcv


def gap_fixture():
    frame = make_ohlcv(np.linspace(100, 130, 40), start="2020-01-01")
    frame.loc[frame.index[21], "Open"] = 111.25
    return frame


def benchmark_fixture():
    return make_ohlcv(np.linspace(200, 220, 40), start="2020-01-01")


def double_touch_fixture():
    frame = make_ohlcv(
        np.full(30, 100.0),
        highs=np.full(30, 100.5),
        lows=np.full(30, 99.5),
        opens=np.full(30, 100.0),
        start="2020-01-01",
    )
    future_date = frame.index[21]
    frame.loc[future_date, ["Open", "High", "Low", "Close"]] = [100.0, 103.0, 98.0, 100.0]
    return frame


class OutcomeTest(unittest.TestCase):
    def test_forward_return_enters_at_next_open(self):
        history = gap_fixture()
        observation_date = history.index[20]

        got = forward_outcomes(
            history, benchmark_fixture(), observation_date, horizons=(20,)
        )

        self.assertEqual(got["entry_date"], history.index[21])
        self.assertEqual(got["entry_price"], 111.25)

    def test_same_bar_double_touch_is_ambiguous(self):
        history = double_touch_fixture()
        got = barrier_outcome(history, history.index[20], horizon=5)

        self.assertEqual(got["barrier_label"], "ambiguous")

    def test_missing_next_bar_has_no_executable_outcome(self):
        history = gap_fixture().iloc[:21]
        observation_date = history.index[-1]

        got = forward_outcomes(
            history, benchmark_fixture(), observation_date, horizons=(20,)
        )

        self.assertTrue(got["missing_entry_bar"])
        self.assertIsNone(got["ret_20"])


if __name__ == "__main__":
    unittest.main()
