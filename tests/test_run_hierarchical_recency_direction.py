from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from research.run_hierarchical_recency_direction import (
    aggregate_direction_metrics,
    hierarchical_promotion_decision,
    publish_reports,
    render_report,
)


def _metric_row(
    specification,
    *,
    scope="all",
    sample_mode="overlapping",
    fold=0,
    balanced_accuracy=0.55,
    macro_f1=0.54,
    down_recall=0.50,
):
    return {
        "scope": scope,
        "horizon": 5,
        "sample_mode": sample_mode,
        "fold": fold,
        "specification": specification,
        "balanced_accuracy": balanced_accuracy,
        "macro_f1": macro_f1,
        "down_recall": down_recall,
        "mean_return_predicted_down": -0.04,
        "mean_return_predicted_neutral": 0.0,
        "mean_return_predicted_up": 0.05,
    }


def _passing_metrics():
    rows = []
    comparators = {
        "majority_baseline": 0.50,
        "ridge_current": 0.51,
        "logistic_global": 0.52,
        "logistic_time": 0.525,
        "logistic_group": 0.526,
        "logistic_time_group": 0.528,
        "logistic_time_group_ticker": 0.54,
    }
    for mode in ("overlapping", "non_overlapping"):
        for specification, score in comparators.items():
            rows.append(
                _metric_row(
                    specification,
                    sample_mode=mode,
                    balanced_accuracy=score,
                    macro_f1=score,
                )
            )
        for fold in range(1, 6):
            for specification, score in comparators.items():
                adjusted = score
                if (
                    specification == "logistic_time_group_ticker"
                    and fold == 5
                ):
                    adjusted = 0.515
                rows.append(
                    _metric_row(
                        specification,
                        sample_mode=mode,
                        fold=fold,
                        balanced_accuracy=adjusted,
                        macro_f1=adjusted,
                    )
                )
    for scope, gain in (
        ("semiconductor", 0.015),
        ("software", 0.012),
        ("other", 0.0),
    ):
        rows.append(
            _metric_row(
                "logistic_global",
                scope=scope,
                balanced_accuracy=0.52,
            )
        )
        rows.append(
            _metric_row(
                "logistic_time_group_ticker",
                scope=scope,
                balanced_accuracy=0.52 + gain,
            )
        )
    return pd.DataFrame(rows)


def _passing_regime_metrics():
    rows = []
    for regime in ("under_pressure", "correction", "acute_selloff"):
        rows.extend(
            [
                {
                    "scope": "all",
                    "horizon": 5,
                    "sample_mode": "overlapping",
                    "regime": regime,
                    "specification": "logistic_global",
                    "balanced_accuracy": 0.50,
                    "down_recall": 0.45,
                },
                {
                    "scope": "all",
                    "horizon": 5,
                    "sample_mode": "overlapping",
                    "regime": regime,
                    "specification": "logistic_time_group_ticker",
                    "balanced_accuracy": 0.52,
                    "down_recall": 0.48,
                },
            ]
        )
    return pd.DataFrame(rows)


class HierarchicalRecencyRunnerTest(unittest.TestCase):
    def test_promotion_gate_requires_every_frozen_condition_but_never_goes_online(self):
        decision = hierarchical_promotion_decision(
            _passing_metrics(),
            _passing_regime_metrics(),
        )

        self.assertTrue(decision["metric_gate_passed"])
        self.assertFalse(decision["eligible"])
        self.assertEqual(decision["online_authority"], "none")
        self.assertEqual(decision["metric_gate_reasons"], [])
        self.assertIn("point_in_time", decision["reason"])

        failed = _passing_metrics()
        failed.loc[
            (failed["scope"] == "all")
            & (failed["sample_mode"] == "overlapping")
            & (failed["fold"] == 4)
            & (
                failed["specification"]
                == "logistic_time_group_ticker"
            ),
            "balanced_accuracy",
        ] = 0.50
        failed_decision = hierarchical_promotion_decision(
            failed,
            _passing_regime_metrics(),
        )
        self.assertFalse(failed_decision["metric_gate_passed"])
        self.assertIn(
            "overlapping:fold_wins_below_four",
            failed_decision["metric_gate_reasons"],
        )

    def test_metrics_cover_scopes_folds_and_non_overlapping_samples(self):
        rows = []
        dates = pd.bdate_range("2026-01-02", periods=30)
        for specification in (
            "ridge_current",
            "logistic_global",
            "logistic_time_group_ticker",
        ):
            for fold in (1, 2):
                for ticker in ("CHIP", "SOFT", "BANK"):
                    for position, date in enumerate(dates):
                        rows.append(
                            {
                                "ticker": ticker,
                                "observation_date": date,
                                "horizon": 5,
                                "fold": fold,
                                "specification": specification,
                                "actual_return": (
                                    -0.03 if position % 3 == 0 else 0.04
                                ),
                                "actual_direction": (
                                    "down" if position % 3 == 0 else "up"
                                ),
                                "predicted_direction": (
                                    "down" if position % 3 == 0 else "up"
                                ),
                            }
                        )

        metrics = aggregate_direction_metrics(
            pd.DataFrame(rows),
            {
                "CHIP": "semiconductor",
                "SOFT": "software",
                "BANK": "other",
            },
        )

        self.assertEqual(
            set(metrics["scope"]),
            {"all", "semiconductor", "software", "other"},
        )
        self.assertEqual(
            set(metrics["sample_mode"]),
            {"overlapping", "non_overlapping"},
        )
        self.assertTrue({0, 1, 2}.issubset(set(metrics["fold"])))

    def test_report_publication_is_atomic_and_states_research_limit(self):
        metrics = _passing_metrics()
        decision = hierarchical_promotion_decision(
            metrics,
            _passing_regime_metrics(),
        )
        manifest = {
            "study_version": "hierarchical_recency_direction_v1",
            "online_authority": "none",
            "decision": decision,
        }
        report = render_report(metrics, manifest)
        self.assertIn("时间衰减与层级方向挑战模型", report)
        self.assertIn("不修改线上 Ridge", report)

        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "study"
            paths = publish_reports(prefix, metrics, manifest, report)

            self.assertEqual(set(paths), {"json", "csv", "md"})
            self.assertEqual(
                json.loads(paths["json"].read_text(encoding="utf-8"))[
                    "online_authority"
                ],
                "none",
            )
            self.assertIn(
                "不修改线上 Ridge",
                paths["md"].read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
