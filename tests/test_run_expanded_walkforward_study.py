import unittest

import numpy as np
import pandas as pd

from research.recency_momentum import RECENCY_FEATURE_COLUMNS
from research.run_expanded_walkforward_study import (
    classify_study_groups,
    evaluate_expanded_scope,
    expanded_feature_sets,
    research_promotion_decision,
    select_analysis_tickers,
)


def labeled_frame(periods=100):
    dates = pd.bdate_range("2025-01-02", periods=periods)
    rows = []
    index = []
    for ticker, phase in (("AAA", 0.0), ("BBB", 1.0), ("CCC", 2.0)):
        for position, date in enumerate(dates):
            base = np.sin(position / 5.0 + phase)
            decay = np.cos(position / 7.0 + phase)
            row = {
                "base": base,
                "executable_return_5": 0.025 * base + 0.02 * decay,
                "executable_label_end_date_5": date + pd.offsets.BDay(5),
            }
            row.update(
                {
                    column: decay + offset / 100.0
                    for offset, column in enumerate(RECENCY_FEATURE_COLUMNS)
                }
            )
            rows.append(row)
            index.append((ticker, date))
    return pd.DataFrame(
        rows,
        index=pd.MultiIndex.from_tuples(
            index,
            names=("ticker", "observation_date"),
        ),
    ).sort_index()


class RunExpandedWalkForwardStudyTest(unittest.TestCase):
    def test_classifies_sec_semiconductor_and_software_industries(self):
        groups = classify_study_groups(
            {
                "CHIP": {
                    "sec": {
                        "industry_label": "Semiconductors & Related Devices",
                    }
                },
                "CLOUD": {
                    "sec": {
                        "industry_label": (
                            "Services-Computer Programming, "
                            "Data Processing, Etc."
                        ),
                    }
                },
                "BANK": {
                    "sec": {"industry_label": "National Commercial Banks"}
                },
            }
        )

        self.assertEqual(groups["CHIP"], "semiconductor")
        self.assertEqual(groups["CLOUD"], "software")
        self.assertEqual(groups["BANK"], "other")

    def test_feature_sets_preserve_current_then_add_causal_heads(self):
        feature_sets = expanded_feature_sets(("base",))

        self.assertEqual(feature_sets["ridge_current"], ("base",))
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
        self.assertIn(
            "decay_excess_sector_1_20",
            feature_sets["ridge_decay_market"],
        )

    def test_sampler_keeps_named_cases_and_is_deterministic(self):
        groups = {
            "MU": "semiconductor",
            "NBIS": "other",
            "ADBE": "software",
            **{f"T{number:03d}": "other" for number in range(40)},
        }

        first = select_analysis_tickers(groups, max_tickers=12, seed=7)
        second = select_analysis_tickers(groups, max_tickers=12, seed=7)

        self.assertEqual(first, second)
        self.assertTrue({"MU", "NBIS", "ADBE"}.issubset(first))
        self.assertEqual(len(first), 12)

    def test_evaluation_compares_fixed_ridge_and_direction_specs(self):
        metrics, predictions = evaluate_expanded_scope(
            labeled_frame(),
            scope="all",
            base_columns=("base",),
            horizons=(5,),
            n_folds=4,
            minimum_samples=30,
        )

        expected = {
            "majority_baseline",
            "ridge_current",
            "ridge_decay_only",
            "ridge_decay_volume",
            "ridge_decay_market",
            "logistic_decay_market",
        }
        self.assertEqual(set(metrics["specification"]), expected)
        self.assertEqual(
            set(metrics["sample_mode"]),
            {"overlapping", "non_overlapping"},
        )
        self.assertEqual(set(predictions["specification"]), expected)
        self.assertIn("fold_win_rate_vs_ridge_current", metrics)
        challenger = metrics.loc[
            metrics["specification"] == "logistic_decay_market",
            "fold_win_rate_vs_ridge_current",
        ]
        self.assertTrue(challenger.notna().all())
        ridge = metrics.loc[
            metrics["specification"].str.startswith("ridge_")
        ]
        self.assertTrue(ridge["return_mae"].notna().all())
        self.assertTrue(ridge["rank_ic"].notna().all())
        classifiers = metrics.loc[
            ~metrics["specification"].str.startswith("ridge_")
        ]
        self.assertTrue(classifiers["return_mae"].isna().all())

    def test_promotion_metric_gate_rejects_worse_return_fit(self):
        rows = []
        for sample_mode in ("overlapping", "non_overlapping"):
            rows.extend(
                (
                    {
                        "scope": "all", "horizon": 5,
                        "sample_mode": sample_mode,
                        "specification": "majority_baseline",
                        "balanced_accuracy": 0.33, "macro_f1": 0.22,
                        "down_recall": 0.0, "return_mae": np.nan,
                        "rank_ic": np.nan,
                    },
                    {
                        "scope": "all", "horizon": 5,
                        "sample_mode": sample_mode,
                        "specification": "ridge_current",
                        "balanced_accuracy": 0.35, "macro_f1": 0.30,
                        "down_recall": 0.30, "return_mae": 0.05,
                        "rank_ic": 0.05,
                    },
                    {
                        "scope": "all", "horizon": 5,
                        "sample_mode": sample_mode,
                        "specification": "ridge_decay_market",
                        "balanced_accuracy": 0.37, "macro_f1": 0.32,
                        "down_recall": 0.32, "return_mae": 0.06,
                        "rank_ic": 0.06,
                    },
                )
            )

        decision = research_promotion_decision(pd.DataFrame(rows))

        self.assertFalse(decision["metric_gate_passed"])
        self.assertTrue(
            any(
                "return_mae_degraded" in reason
                for reason in decision["metric_gate_reasons"]
            )
        )


if __name__ == "__main__":
    unittest.main()
