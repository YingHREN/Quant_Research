import unittest

import numpy as np
import pandas as pd

from research.evaluate_high_level_distribution import (
    evaluate_high_level_distribution,
)
from tests.helpers import make_ohlcv


class HighLevelDistributionEvaluationTest(unittest.TestCase):
    def test_reports_only_rows_with_complete_future_window(self):
        history = make_ohlcv([100, 100, 100, 95, 90, 92, 94, 96])
        states = pd.DataFrame(
            {
                "high_level_distribution_raw_state": [
                    "inactive",
                    "confirmed",
                    "inactive",
                    "inactive",
                    "inactive",
                    "confirmed",
                    "inactive",
                    "inactive",
                ]
            },
            index=history.index,
        )

        report = evaluate_high_level_distribution(
            history,
            states,
            horizons=(3,),
            adverse_threshold=-0.08,
        )["3"]

        self.assertEqual(report["eligible_observations"], 5)
        self.assertEqual(report["confirmed_events"], 1)
        self.assertAlmostEqual(report["precision"], 1.0)
        self.assertGreaterEqual(report["recall"], 0.0)
        self.assertAlmostEqual(report["mean_max_adverse_excursion"], -0.10)

    def test_empty_or_single_class_metrics_are_explicitly_unavailable(self):
        history = make_ohlcv(np.linspace(100.0, 110.0, 8))
        states = pd.DataFrame(
            {
                "high_level_distribution_raw_state": ["inactive"] * 8,
            },
            index=history.index,
        )

        report = evaluate_high_level_distribution(
            history,
            states,
            horizons=(3,),
        )["3"]

        self.assertEqual(report["confirmed_events"], 0)
        self.assertIsNone(report["precision"])
        self.assertIsNone(report["recall"])
        self.assertIsNone(report["mean_terminal_return"])


if __name__ == "__main__":
    unittest.main()
