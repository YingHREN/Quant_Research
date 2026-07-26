import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research.evaluate_unified_risk import (
    binary_metrics,
    build_evaluation_frame,
    evaluation_rows,
    evaluation_rows_by_scope,
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

    def test_outcomes_use_next_session_open_as_executable_entry(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        close = np.arange(100.0, 108.0)
        source = pd.DataFrame(
            {
                "Open": close + 10.0,
                "High": close + 12.0,
                "Low": close + 8.0,
                "Close": close,
                "Volume": 1_000_000.0,
            },
            index=dates,
        )
        index = pd.MultiIndex.from_product(
            (("MU",), dates),
            names=("ticker", "observation_date"),
        )
        context = pd.DataFrame(
            {
                "individual_risk_score": 0.0,
                "group_risk_score": 0.0,
                "slow_decline_risk_score": 0.0,
            },
            index=index,
        )

        result = build_evaluation_frame(
            {"MU": source},
            context=context,
            horizon=5,
        )

        first = result.iloc[0]
        self.assertAlmostEqual(first["future_return"], 105.0 / 111.0 - 1.0)
        self.assertAlmostEqual(first["future_mae"], 109.0 / 111.0 - 1.0)

    def test_stratified_metrics_separate_semiconductor_and_software(self):
        index = pd.MultiIndex.from_tuples(
            (("MU", pd.Timestamp("2026-01-02")),
             ("ADBE", pd.Timestamp("2026-01-02"))),
            names=("ticker", "observation_date"),
        )
        frame = pd.DataFrame(
            {
                "individual_risk_score": [40.0, 10.0],
                "group_risk_score": [70.0, 20.0],
                "slow_decline_risk_score": [20.0, 80.0],
                "policy_high": [True, True],
                "future_return": [-0.10, -0.08],
                "future_mae": [-0.12, -0.11],
            },
            index=index,
        )

        metrics = evaluation_rows_by_scope(frame, adverse_threshold=-0.05)

        self.assertEqual(
            set(metrics["scope"]),
            {"all", "semiconductor", "software"},
        )

    def test_missing_source_score_is_excluded_not_counted_as_low_risk(self):
        frame = pd.DataFrame(
            {
                "individual_risk_score": [40.0, np.nan],
                "group_risk_score": [np.nan, np.nan],
                "slow_decline_risk_score": [np.nan, np.nan],
                "policy_high": [True, False],
                "future_return": [-0.10, -0.10],
                "future_mae": [-0.12, -0.12],
            }
        )

        metrics = evaluation_rows(frame)

        self.assertEqual(metrics.loc["individual", "sample_count"], 1)
        self.assertEqual(metrics.loc["individual", "coverage"], 0.5)
        self.assertEqual(metrics.loc["group", "sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
