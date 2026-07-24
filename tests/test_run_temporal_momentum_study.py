import unittest

import pandas as pd

from research.run_temporal_momentum_study import (
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


if __name__ == "__main__":
    unittest.main()
