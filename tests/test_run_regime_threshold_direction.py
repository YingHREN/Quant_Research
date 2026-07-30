from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.run_regime_threshold_direction import (
    aggregate_absolute_metrics,
    aggregate_relative_metrics,
    publish_reports,
    regime_threshold_promotion_decision,
    render_report,
    validate_report_payload,
)


def _row(
    specification,
    *,
    scope="all",
    sample_mode="overlapping",
    fold=0,
    balanced_accuracy=0.52,
    down_precision=0.50,
    down_recall=0.50,
    down_coverage=0.10,
    mean_return_predicted_down=-0.02,
):
    return {
        "scope": scope,
        "horizon": 5,
        "sample_mode": sample_mode,
        "fold": fold,
        "specification": specification,
        "balanced_accuracy": balanced_accuracy,
        "down_precision": down_precision,
        "down_recall": down_recall,
        "down_coverage": down_coverage,
        "mean_return_predicted_down": mean_return_predicted_down,
    }


def _passing_metrics():
    rows = []
    for mode in ("overlapping", "non_overlapping"):
        rows.extend(
            (
                _row("logistic_global", sample_mode=mode),
                _row(
                    "logistic_regime_prior",
                    sample_mode=mode,
                    balanced_accuracy=0.525,
                ),
                _row(
                    "logistic_regime_threshold",
                    sample_mode=mode,
                    balanced_accuracy=0.524,
                    down_precision=0.54,
                    down_recall=0.48,
                ),
            )
        )
        for fold in range(1, 6):
            rows.append(
                _row(
                    "logistic_global",
                    sample_mode=mode,
                    fold=fold,
                )
            )
            rows.append(
                _row(
                    "logistic_regime_threshold",
                    sample_mode=mode,
                    fold=fold,
                    down_precision=0.54,
                    down_recall=0.48,
                    mean_return_predicted_down=(
                        0.002 if fold == 5 else -0.02
                    ),
                )
            )
    for scope in ("semiconductor", "software", "other"):
        rows.extend(
            (
                _row("logistic_global", scope=scope),
                _row(
                    "logistic_regime_threshold",
                    scope=scope,
                    balanced_accuracy=0.515,
                    down_precision=0.54,
                    down_recall=0.48,
                ),
            )
        )
    return pd.DataFrame(rows)


def _passing_regimes():
    rows = []
    for regime in (
        "under_pressure",
        "correction",
        "acute_selloff",
    ):
        rows.extend(
            (
                {
                    **_row("logistic_global"),
                    "regime": regime,
                    "mean_return_predicted_down": -0.01,
                },
                {
                    **_row(
                        "logistic_regime_threshold",
                        down_precision=0.55,
                        down_recall=0.47,
                    ),
                    "regime": regime,
                    "mean_return_predicted_down": -0.03,
                },
            )
        )
    rows.extend(
        (
            {
                **_row("logistic_global"),
                "regime": "stressed_combined",
                "mean_return_predicted_down": -0.01,
            },
            {
                **_row(
                    "logistic_regime_threshold",
                    down_precision=0.55,
                    down_recall=0.47,
                ),
                "regime": "stressed_combined",
                "mean_return_predicted_down": -0.03,
            },
        )
    )
    return pd.DataFrame(rows)


class RegimeThresholdRunnerTest(unittest.TestCase):
    def test_absolute_and_relative_metrics_preserve_distinct_semantics(self):
        dates = pd.bdate_range("2026-01-02", periods=15)
        absolute_rows = []
        relative_rows = []
        for position, date in enumerate(dates):
            actual = "down" if position % 3 == 0 else "up"
            predicted = "down" if position % 4 == 0 else "up"
            absolute_rows.append(
                {
                    "ticker": "AAA",
                    "observation_date": date,
                    "horizon": 5,
                    "fold": 1,
                    "specification": "logistic_global",
                    "actual_return": -0.03 if actual == "down" else 0.04,
                    "actual_direction": actual,
                    "predicted_direction": predicted,
                }
            )
            relative_rows.append(
                {
                    "ticker": "AAA",
                    "observation_date": date,
                    "horizon": 5,
                    "fold": 1,
                    "specification": "logistic_qqq_relative",
                    "actual_return": 0.02,
                    "actual_relative_return": (
                        -0.03 if actual == "down" else 0.04
                    ),
                    "actual_relative_direction": actual,
                    "predicted_relative_direction": predicted,
                }
            )

        absolute = aggregate_absolute_metrics(
            pd.DataFrame(absolute_rows),
            {"AAA": "other"},
        )
        relative = aggregate_relative_metrics(
            pd.DataFrame(relative_rows)
        )

        self.assertIn("down_coverage", absolute.columns)
        self.assertIn("down_mean_absolute_return", relative.columns)
        self.assertIn("down_mean_relative_return", relative.columns)
        self.assertNotIn("mean_return_predicted_down", relative.columns)

    def test_frozen_gate_can_pass_but_never_grants_online_authority(self):
        decision = regime_threshold_promotion_decision(
            _passing_metrics(),
            _passing_regimes(),
        )

        self.assertTrue(decision["metric_gate_passed"])
        self.assertFalse(decision["eligible"])
        self.assertEqual(decision["online_authority"], "none")
        self.assertEqual(decision["metric_gate_reasons"], [])

    def test_each_economic_failure_is_reported(self):
        metrics = _passing_metrics()
        selected = (
            (metrics["scope"] == "all")
            & (metrics["sample_mode"] == "overlapping")
            & (metrics["fold"] == 0)
            & (
                metrics["specification"]
                == "logistic_regime_threshold"
            )
        )
        metrics.loc[selected, "mean_return_predicted_down"] = 0.01
        metrics.loc[selected, "down_precision"] = 0.51
        metrics.loc[selected, "down_coverage"] = 0.04

        decision = regime_threshold_promotion_decision(
            metrics,
            _passing_regimes(),
        )

        self.assertFalse(decision["metric_gate_passed"])
        self.assertIn(
            "overlapping:predicted_down_return_not_negative",
            decision["metric_gate_reasons"],
        )
        self.assertIn(
            "overlapping:down_precision_gain_below_0.03",
            decision["metric_gate_reasons"],
        )
        self.assertIn(
            "overlapping:down_coverage_below_0.05",
            decision["metric_gate_reasons"],
        )

    def test_report_payload_rejects_nonfinite_paths_and_secrets(self):
        validate_report_payload(
            {
                "study_version": "regime_threshold_direction_v1",
                "database": "research_prices.db",
                "metric": 0.5,
            }
        )
        invalid = (
            {"metric": float("nan")},
            {"database": str(Path("/tmp/private.db"))},
            {"api_key": "not-allowed"},
            {"note": "token=abcdefghijklmnopqrstuvwxyz123456"},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_report_payload(payload)

        with tempfile.TemporaryDirectory() as directory:
            relative = Path(directory).name
            validate_report_payload({"database": relative})

    def test_atomic_publication_writes_strict_json_and_research_warning(self):
        metrics = _passing_metrics()
        decision = regime_threshold_promotion_decision(
            metrics,
            _passing_regimes(),
        )
        relative = pd.DataFrame(
            [
                {
                    "sample_mode": "overlapping",
                    "specification": "logistic_qqq_relative",
                    "balanced_accuracy": 0.5,
                }
            ]
        )
        manifest = {
            "study_version": "regime_threshold_direction_v1",
            "online_authority": "none",
            "decision": decision,
        }
        report = render_report(metrics, relative, manifest)
        self.assertIn("QQQ 相对方向", report)
        self.assertIn("不修改 Ridge", report)

        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "study"
            paths = publish_reports(prefix, metrics, manifest, report)
            self.assertEqual(set(paths), {"json", "csv", "md"})
            self.assertNotIn(
                "NaN",
                paths["json"].read_text(encoding="utf-8"),
            )
            with self.assertRaises(ValueError):
                publish_reports(
                    prefix,
                    metrics,
                    {**manifest, "bad": np.nan},
                    report,
                )


if __name__ == "__main__":
    unittest.main()
