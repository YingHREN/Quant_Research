import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research.evaluate_unified_risk import (
    binary_metrics,
    build_evaluation_frame,
)


class UnifiedRiskEvaluationTest(unittest.TestCase):
    def test_binary_metrics_reports_balanced_accuracy(self):
        metrics = binary_metrics(
            pd.Series([True, True, False, False]),
            pd.Series([True, False, True, False]),
        )

        self.assertEqual(metrics["sample_count"], 4)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(metrics["specificity"], 0.5)
        self.assertEqual(metrics["balanced_accuracy"], 0.5)

    def test_appending_future_prices_does_not_change_earlier_scores(self):
        periods = 100
        dates = pd.bdate_range(end="2026-07-23", periods=periods)

        def history(close):
            values = np.asarray(close, dtype=float)
            return pd.DataFrame(
                {
                    "Open": values,
                    "High": values + 1.0,
                    "Low": values - 1.0,
                    "Close": values,
                    "Volume": np.full(len(values), 1_000_000.0),
                },
                index=dates,
            )

        histories = {
            "QQQ": history(np.linspace(100.0, 120.0, periods)),
            "SOXX": history(np.linspace(100.0, 125.0, periods)),
            "SMH": history(np.linspace(100.0, 126.0, periods)),
            "MU": history(np.linspace(100.0, 130.0, periods)),
        }
        before = build_evaluation_frame(histories)
        extended = {}
        future_dates = pd.bdate_range(dates[-1], periods=3)[1:]
        for ticker, source in histories.items():
            future = pd.DataFrame(
                {
                    "Open": [10.0, 500.0],
                    "High": [11.0, 501.0],
                    "Low": [9.0, 499.0],
                    "Close": [10.0, 500.0],
                    "Volume": [9_000_000.0, 9_000_000.0],
                },
                index=future_dates,
            )
            extended[ticker] = pd.concat([source, future])

        after = build_evaluation_frame(extended)
        score_columns = [
            column
            for column in before.columns
            if column not in {"future_return", "future_mae"}
        ]

        assert_frame_equal(
            after.loc[before.index, score_columns],
            before.loc[:, score_columns],
        )


if __name__ == "__main__":
    unittest.main()
