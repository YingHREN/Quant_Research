import unittest

import pandas as pd

from research.run_market_direction_study import (
    promotion_decision,
    render_markdown_report,
    study_feature_sets,
)


class RunMarketDirectionStudyTest(unittest.TestCase):
    def test_feature_sets_add_market_then_sector_and_early_evidence(self):
        feature_sets = study_feature_sets()

        self.assertIn("qqq_return_20", feature_sets["stock_qqq"])
        self.assertNotIn(
            "sector_relative_strength_20",
            feature_sets["stock_qqq"],
        )
        self.assertIn(
            "sector_relative_strength_20",
            feature_sets["full_context"],
        )
        self.assertIn(
            "early_current_price_acceptance",
            feature_sets["full_context"],
        )
        self.assertNotIn("early_reversal_score", feature_sets["full_context"])

    def test_promotion_requires_both_metric_improvement_and_down_recall(self):
        metrics = pd.DataFrame(
            [
                {
                    "scope": "all",
                    "horizon": 5,
                    "specification": "majority_baseline",
                    "sample_count": 2_000,
                    "balanced_accuracy": 0.33,
                    "macro_f1": 0.25,
                    "down_recall": 0.00,
                },
                {
                    "scope": "all",
                    "horizon": 5,
                    "specification": "stock_only",
                    "sample_count": 2_000,
                    "balanced_accuracy": 0.40,
                    "macro_f1": 0.38,
                    "down_recall": 0.45,
                },
                {
                    "scope": "all",
                    "horizon": 5,
                    "specification": "full_context",
                    "sample_count": 2_000,
                    "balanced_accuracy": 0.44,
                    "macro_f1": 0.42,
                    "down_recall": 0.46,
                },
            ]
        )

        decision = promotion_decision(metrics)

        self.assertTrue(decision["eligible"])
        degraded = metrics.copy()
        degraded.loc[
            degraded["specification"] == "full_context",
            "down_recall",
        ] = 0.30
        self.assertFalse(promotion_decision(degraded)["eligible"])

    def test_report_states_promotion_decision_and_executable_label(self):
        metrics = pd.DataFrame(
            [
                {
                    "scope": "all",
                    "horizon": 5,
                    "specification": "full_context",
                    "sample_count": 2_000,
                    "coverage": 1.0,
                    "balanced_accuracy": 0.45,
                    "macro_f1": 0.43,
                    "down_precision": 0.41,
                    "down_recall": 0.49,
                    "mean_return_predicted_down": -0.02,
                    "mean_return_predicted_neutral": 0.00,
                    "mean_return_predicted_up": 0.03,
                }
            ]
        )

        report = render_markdown_report(
            metrics,
            {"eligible": False, "reason": "promotion_gate_failed"},
            latest_date="2026-07-23",
            ticker_count=194,
        )

        self.assertIn("next-session open", report)
        self.assertIn("DO NOT PROMOTE", report)
        self.assertIn("2026-07-23", report)


if __name__ == "__main__":
    unittest.main()
