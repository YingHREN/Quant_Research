import unittest

import pandas as pd

from research.run_temporal_momentum_study import (
    render_temporal_report,
    temporal_feature_sets,
    temporal_promotion_decision,
)


def metric_row(
    specification,
    balanced_accuracy,
    down_recall,
    *,
    fold=0,
    scope="all",
):
    return {
        "scope": scope,
        "horizon": 5,
        "fold": fold,
        "specification": specification,
        "sample_count": 2_000,
        "balanced_accuracy": balanced_accuracy,
        "macro_f1": balanced_accuracy,
        "down_recall": down_recall,
        "mean_return_predicted_down": -0.01,
        "mean_return_predicted_neutral": 0.0,
        "mean_return_predicted_up": 0.01,
    }


class RunTemporalMomentumStudyTest(unittest.TestCase):
    def test_feature_sets_add_decay_then_volume_then_market_context(self):
        feature_sets = temporal_feature_sets()

        self.assertEqual(
            tuple(feature_sets),
            (
                "ridge_current",
                "ridge_decay_only",
                "ridge_decay_volume",
                "ridge_decay_market",
            ),
        )
        self.assertNotIn(
            "decay_mom_1_3",
            feature_sets["ridge_current"],
        )
        self.assertIn(
            "decay_mom_1_3",
            feature_sets["ridge_decay_only"],
        )
        self.assertNotIn(
            "decay_volume_confirmation_1_20",
            feature_sets["ridge_decay_only"],
        )
        self.assertIn(
            "decay_volume_confirmation_1_20",
            feature_sets["ridge_decay_volume"],
        )
        self.assertNotIn(
            "decay_excess_sector_1_20",
            feature_sets["ridge_decay_volume"],
        )
        self.assertIn(
            "decay_excess_sector_1_20",
            feature_sets["ridge_decay_market"],
        )

    def test_promotion_requires_majority_of_folds_and_down_recall(self):
        rows = [
            metric_row("majority_baseline", 0.333, 0.0),
            metric_row("ridge_current", 0.400, 0.40),
            metric_row("ridge_decay_market", 0.450, 0.41),
            metric_row(
                "ridge_current",
                0.400,
                0.40,
                scope="semiconductor_ai",
            ),
            metric_row(
                "ridge_decay_market",
                0.410,
                0.40,
                scope="semiconductor_ai",
            ),
        ]
        current_folds = [0.41, 0.42, 0.43, 0.44, 0.45]
        challenger_folds = [0.42, 0.43, 0.42, 0.43, 0.44]
        for fold, (current, challenger) in enumerate(
            zip(current_folds, challenger_folds),
            start=1,
        ):
            rows.append(metric_row("ridge_current", current, 0.40, fold=fold))
            rows.append(
                metric_row(
                    "ridge_decay_market",
                    challenger,
                    0.41,
                    fold=fold,
                )
            )

        failed = temporal_promotion_decision(pd.DataFrame(rows))

        self.assertFalse(failed["eligible"])
        rows[-1]["balanced_accuracy"] = 0.46
        passed = temporal_promotion_decision(pd.DataFrame(rows))
        self.assertTrue(passed["eligible"])

    def test_promotion_rejects_economically_inverted_down_predictions(self):
        rows = [
            metric_row("majority_baseline", 0.333, 0.0),
            metric_row("ridge_current", 0.400, 0.40),
            metric_row("ridge_decay_market", 0.450, 0.41),
            metric_row(
                "ridge_current",
                0.400,
                0.40,
                scope="semiconductor_ai",
            ),
            metric_row(
                "ridge_decay_market",
                0.410,
                0.41,
                scope="semiconductor_ai",
            ),
        ]
        for fold in range(1, 6):
            rows.append(metric_row("ridge_current", 0.40, 0.40, fold=fold))
            rows.append(
                metric_row(
                    "ridge_decay_market",
                    0.42,
                    0.41,
                    fold=fold,
                )
            )
        rows[2]["mean_return_predicted_down"] = 0.008

        decision = temporal_promotion_decision(pd.DataFrame(rows))

        self.assertFalse(decision["eligible"])
        self.assertIn("predicted_down_return_not_negative", decision["reason"])

    def test_promotion_requires_at_least_one_known_false_bull_correction(self):
        rows = [
            metric_row("majority_baseline", 0.333, 0.0),
            metric_row("ridge_current", 0.400, 0.40),
            metric_row("ridge_decay_market", 0.450, 0.41),
            metric_row(
                "ridge_current",
                0.400,
                0.40,
                scope="semiconductor_ai",
            ),
            metric_row(
                "ridge_decay_market",
                0.410,
                0.41,
                scope="semiconductor_ai",
            ),
        ]
        for fold in range(1, 6):
            rows.append(metric_row("ridge_current", 0.40, 0.40, fold=fold))
            rows.append(
                metric_row(
                    "ridge_decay_market",
                    0.42,
                    0.41,
                    fold=fold,
                )
            )
        diagnostics = pd.DataFrame(
            [
                {
                    "ticker": "MU",
                    "observation_date": pd.Timestamp("2026-06-25"),
                    "specification": "ridge_current",
                    "actual_direction": "down",
                    "predicted_direction": "up",
                },
                {
                    "ticker": "MU",
                    "observation_date": pd.Timestamp("2026-06-25"),
                    "specification": "ridge_decay_market",
                    "actual_direction": "down",
                    "predicted_direction": "up",
                },
            ]
        )

        failed = temporal_promotion_decision(
            pd.DataFrame(rows),
            diagnostics=diagnostics,
        )

        self.assertFalse(failed["eligible"])
        diagnostics.loc[
            diagnostics["specification"] == "ridge_decay_market",
            "predicted_direction",
        ] = "down"
        passed = temporal_promotion_decision(
            pd.DataFrame(rows),
            diagnostics=diagnostics,
        )
        self.assertTrue(passed["eligible"])

    def test_promotion_rejects_non_overlapping_sample_degradation(self):
        rows = [
            metric_row("majority_baseline", 0.333, 0.0),
            metric_row("ridge_current", 0.400, 0.40),
            metric_row("ridge_decay_market", 0.450, 0.41),
            metric_row(
                "ridge_current",
                0.400,
                0.40,
                scope="semiconductor_ai",
            ),
            metric_row(
                "ridge_decay_market",
                0.410,
                0.41,
                scope="semiconductor_ai",
            ),
        ]
        for fold in range(1, 6):
            rows.append(metric_row("ridge_current", 0.40, 0.40, fold=fold))
            rows.append(
                metric_row(
                    "ridge_decay_market",
                    0.42,
                    0.41,
                    fold=fold,
                )
            )
        non_overlapping = [
            metric_row("majority_baseline", 0.333, 0.0),
            metric_row("ridge_current", 0.400, 0.40),
            metric_row("ridge_decay_market", 0.390, 0.41),
        ]
        for row in non_overlapping:
            row["evaluation_mode"] = "non_overlapping"
        for row in rows:
            row["evaluation_mode"] = "overlapping"

        decision = temporal_promotion_decision(pd.DataFrame(rows + non_overlapping))

        self.assertFalse(decision["eligible"])
        self.assertIn("non_overlapping_accuracy_not_improved", decision["reason"])

    def test_report_states_offline_decision_and_diagnostic_scopes(self):
        metrics = pd.DataFrame(
            [
                metric_row("ridge_current", 0.40, 0.40),
                metric_row("ridge_decay_market", 0.44, 0.42),
                metric_row(
                    "ridge_current",
                    0.41,
                    0.40,
                    scope="semiconductor_ai",
                ),
            ]
        )
        diagnostics = pd.DataFrame(
            [
                {
                    "ticker": "MU",
                    "observation_date": "2026-06-25",
                    "specification": "ridge_decay_market",
                    "predicted_direction": "down",
                },
                {
                    "ticker": "NBIS",
                    "observation_date": "2026-07-01",
                    "specification": "ridge_decay_market",
                    "predicted_direction": "down",
                },
            ]
        )

        report = render_temporal_report(
            metrics,
            {"eligible": False, "reason": "fold_majority_not_improved"},
            diagnostics=diagnostics,
            latest_date="2026-07-23",
            ticker_count=194,
        )

        self.assertIn("DO NOT PROMOTE", report)
        self.assertIn("offline", report.lower())
        self.assertIn("next-session open", report)
        self.assertIn("semiconductor_ai", report)
        self.assertIn("MU", report)
        self.assertIn("NBIS", report)


if __name__ == "__main__":
    unittest.main()
