import unittest

import pandas as pd

from research.run_pressure_downside_study import (
    build_matched_comparison_predictions,
    render_pressure_downside_report,
)


def specialist_predictions():
    dates = pd.bdate_range("2026-01-02", periods=2)
    return pd.DataFrame(
        {
            "ticker": ["AAA", "AAA"],
            "observation_date": dates,
            "horizon": [5, 5],
            "fold": [1, 1],
            "regime": ["correction", "under_pressure"],
            "specification": ["pressure_downside_logistic_v1"] * 2,
            "actual_event": [True, False],
            "actual_mae": [-0.08, -0.01],
            "predicted_event": [True, False],
            "predicted_score": [0.8, 0.2],
        }
    )


def direction_predictions():
    rows = []
    for specification, directions in (
        ("ridge_current", ("down", "up")),
        ("general_logistic", ("down", "down")),
    ):
        for position, row in specialist_predictions().iterrows():
            rows.append(
                {
                    "ticker": row["ticker"],
                    "observation_date": row["observation_date"],
                    "horizon": row["horizon"],
                    "fold": row["fold"],
                    "specification": specification,
                    "predicted_direction": directions[position],
                }
            )
    return pd.DataFrame(rows)


class RunPressureDownsideStudyTest(unittest.TestCase):
    def test_comparison_uses_exact_specialist_rows_for_every_baseline(self):
        matched = build_matched_comparison_predictions(
            specialist_predictions(),
            direction_predictions(),
        )

        self.assertEqual(
            set(matched["specification"]),
            {
                "pressure_downside_logistic_v1",
                "ridge_down",
                "general_logistic_down",
                "negative_baseline",
            },
        )
        counts = matched.groupby("specification").size()
        self.assertTrue((counts == 2).all())
        ridge = matched.loc[matched["specification"] == "ridge_down"]
        self.assertEqual(ridge["predicted_event"].tolist(), [True, False])
        general = matched.loc[
            matched["specification"] == "general_logistic_down"
        ]
        self.assertEqual(general["predicted_event"].tolist(), [True, True])

    def test_report_states_path_label_scope_and_blocked_authority(self):
        metrics = pd.DataFrame(
            [
                {
                    "scope": "all",
                    "horizon": 5,
                    "regime_scope": "all_pressure",
                    "sample_mode": "overlapping",
                    "specification": "pressure_downside_logistic_v1",
                    "sample_count": 100,
                    "event_rate": 0.2,
                    "precision": 0.3,
                    "recall": 0.6,
                    "specificity": 0.7,
                    "balanced_accuracy": 0.65,
                    "roc_auc": 0.68,
                    "pr_auc": 0.31,
                    "brier_score": 0.17,
                    "comparable_fold_count": 4,
                    "fold_win_rate_vs_ridge_down": 0.75,
                }
            ]
        )
        manifest = {
            "latest_date": "2026-07-24",
            "start_date": "2018-01-01",
            "ticker_count": 240,
            "row_count": 447875,
            "decision": {
                "eligible": False,
                "metric_gate_passed": False,
                "reasons": ["5d:non_overlapping:fold_majority_not_won"],
                "production_block_reason": (
                    "survivorship_and_point_in_time_classification_history_missing"
                ),
            },
        }
        rule_reference = pd.DataFrame(
            [{"scope": "all", "source": "unified_policy_high"}]
        )

        report = render_pressure_downside_report(
            metrics,
            manifest,
            rule_reference,
        )

        self.assertIn("# 市场压力阶段向下风险专家", report)
        self.assertIn("路径最大不利波动", report)
        self.assertIn("次日开盘", report)
        self.assertIn("不具备线上否决权", report)
        self.assertIn("38 只", report)


if __name__ == "__main__":
    unittest.main()
