import unittest

import numpy as np
import pandas as pd

from research.report import (
    apply_bh_fdr,
    date_block_bootstrap,
    markdown_table,
    match_controls,
)


class ReportStatisticsTest(unittest.TestCase):
    def test_date_block_bootstrap_is_deterministic(self):
        values = pd.Series(
            np.linspace(-0.02, 0.03, 100),
            index=pd.bdate_range("2020-01-01", periods=100),
        )

        first = date_block_bootstrap(values, block=20, n_boot=200, seed=42)
        second = date_block_bootstrap(values, block=20, n_boot=200, seed=42)

        self.assertEqual(first, second)
        self.assertLess(first[0], first[1])

    def test_bh_adjusted_values_are_monotonic_when_sorted(self):
        adjusted = apply_bh_fdr([0.04, 0.001, 0.02, 0.2])
        order = np.argsort([0.04, 0.001, 0.02, 0.2])

        self.assertTrue(np.all(np.diff(adjusted[order]) >= 0))
        self.assertTrue(np.all((adjusted >= 0) & (adjusted <= 1)))

    def test_controls_share_ticker_and_regime_but_not_event_date(self):
        events = pd.DataFrame(
            {
                "event_id": ["e1"],
                "ticker": ["AAA"],
                "observation_date": [pd.Timestamp("2020-02-10")],
                "market_regime": ["up-low"],
                "distance_to_ma50": [0.02],
            }
        )
        snapshots = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA", "BBB", "AAA"],
                "date": pd.to_datetime(["2020-02-10", "2020-02-07", "2020-02-07", "2020-02-06"]),
                "market_regime": ["up-low", "up-low", "up-low", "down-high"],
                "distance_to_ma50": [0.02, 0.021, 0.02, 0.02],
            }
        )

        matched = match_controls(events, snapshots)

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched.iloc[0].ticker, "AAA")
        self.assertEqual(matched.iloc[0].control_date, pd.Timestamp("2020-02-07"))
        self.assertNotEqual(matched.iloc[0].event_date, matched.iloc[0].control_date)

    def test_markdown_table_needs_no_optional_dependency(self):
        rendered = markdown_table(pd.DataFrame({"name": ["x"], "value": [1.23456789]}))

        self.assertIn("| name | value |", rendered)
        self.assertIn("| x | 1.234568 |", rendered)


if __name__ == "__main__":
    unittest.main()
