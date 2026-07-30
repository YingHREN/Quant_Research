import unittest

import numpy as np
import pandas as pd

from research.asymmetric_tail_risk import (
    attach_asymmetric_tail_targets,
    audit_extreme_counterexamples,
    evaluate_tail_predictions,
    fit_oof_isotonic,
    select_tail_boundary,
    tail_promotion_decision,
    walk_forward_asymmetric_tail_predictions,
)


def _feature_frame(dates):
    return pd.DataFrame(
        {"feature": np.arange(len(dates), dtype=float)},
        index=pd.MultiIndex.from_product(
            (("AAA",), dates),
            names=("ticker", "observation_date"),
        ),
    )


def _history(dates, *, opens=None, lows=None, closes=None):
    size = len(dates)
    open_values = (
        np.full(size, 100.0)
        if opens is None
        else np.asarray(opens, dtype=float)
    )
    low_values = (
        np.full(size, 99.0)
        if lows is None
        else np.asarray(lows, dtype=float)
    )
    close_values = (
        np.full(size, 100.0)
        if closes is None
        else np.asarray(closes, dtype=float)
    )
    return pd.DataFrame(
        {
            "Open": open_values,
            "High": np.maximum(open_values, close_values) + 1.0,
            "Low": low_values,
            "Close": close_values,
            "Volume": 1_000_000.0,
        },
        index=dates,
    )


class AsymmetricTailTargetTest(unittest.TestCase):
    def test_uses_next_open_terminal_close_and_complete_future_low_path(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        opens = [90.0, 100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0]
        lows = [89.0, 99.0, 96.0, 92.0, 93.0, 94.0, 94.0, 93.0]
        closes = [91.0, 99.0, 97.0, 94.0, 96.0, 94.0, 95.0, 93.0]

        result = attach_asymmetric_tail_targets(
            _feature_frame(dates),
            {
                "AAA": _history(
                    dates,
                    opens=opens,
                    lows=lows,
                    closes=closes,
                )
            },
        )
        first = result.loc[("AAA", dates[0])]

        self.assertAlmostEqual(first["terminal_return_5"], -0.06)
        self.assertAlmostEqual(first["path_mae_5"], -0.08)
        self.assertEqual(first["down_event_5"], 1.0)
        self.assertEqual(first["extreme_rebound_5"], 0.0)
        self.assertEqual(first["tail_label_end_date_5"], dates[5])

    def test_terminal_loss_alone_can_trigger_down_event(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        lows = np.full(8, 98.0)
        closes = np.full(8, 100.0)
        closes[5] = 95.0

        first = attach_asymmetric_tail_targets(
            _feature_frame(dates),
            {"AAA": _history(dates, lows=lows, closes=closes)},
        ).loc[("AAA", dates[0])]

        self.assertAlmostEqual(first["path_mae_5"], -0.02)
        self.assertAlmostEqual(first["terminal_return_5"], -0.05)
        self.assertEqual(first["down_event_5"], 1.0)

    def test_extreme_rebound_is_separate_from_downside_path(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        lows = np.full(8, 99.0)
        lows[2] = 92.0
        closes = np.full(8, 100.0)
        closes[5] = 112.0

        first = attach_asymmetric_tail_targets(
            _feature_frame(dates),
            {"AAA": _history(dates, lows=lows, closes=closes)},
        ).loc[("AAA", dates[0])]

        self.assertEqual(first["down_event_5"], 1.0)
        self.assertEqual(first["extreme_rebound_5"], 1.0)

    def test_missing_path_or_immature_tail_remains_missing(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        lows = np.full(8, 99.0)
        lows[3] = np.nan

        result = attach_asymmetric_tail_targets(
            _feature_frame(dates),
            {"AAA": _history(dates, lows=lows)},
        )
        target_columns = [
            "terminal_return_5",
            "path_mae_5",
            "down_event_5",
            "extreme_rebound_5",
            "tail_label_end_date_5",
        ]

        self.assertTrue(
            result.loc[("AAA", dates[0]), target_columns].isna().all()
        )
        self.assertTrue(result[target_columns].iloc[-5:].isna().all().all())

    def test_rejects_duplicate_history_dates_and_invalid_horizon(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        duplicate = _history(dates)
        duplicate = pd.concat((duplicate, duplicate.iloc[[0]]))

        with self.assertRaisesRegex(ValueError, "duplicate"):
            attach_asymmetric_tail_targets(
                _feature_frame(dates),
                {"AAA": duplicate},
            )
        with self.assertRaisesRegex(ValueError, "horizon"):
            attach_asymmetric_tail_targets(
                _feature_frame(dates),
                {"AAA": _history(dates)},
                horizon=True,
            )


class OofCalibrationTest(unittest.TestCase):
    def test_calibrated_probabilities_are_monotonic_bounded_and_immutable(self):
        scores = np.array([0.1, 0.2, 0.4, 0.6, 0.8, 0.9])
        outcomes = np.array([0, 0, 1, 0, 1, 1])

        fitted = fit_oof_isotonic(
            scores,
            outcomes,
            minimum_rows=6,
            minimum_class_rows=2,
        )
        calibrated = fitted.transform(np.array([0.0, 0.3, 0.7, 1.0]))

        self.assertEqual(fitted.status, "available")
        self.assertIsNone(fitted.reason)
        self.assertTrue(np.all(np.diff(calibrated) >= 0.0))
        self.assertTrue(np.all((calibrated >= 0.0) & (calibrated <= 1.0)))
        calibrated[0] = 99.0
        self.assertLessEqual(fitted.transform(np.array([0.0]))[0], 1.0)

    def test_one_class_or_constant_scores_fail_closed(self):
        one_class = fit_oof_isotonic(
            np.linspace(0.1, 0.9, 6),
            np.zeros(6),
            minimum_rows=6,
            minimum_class_rows=1,
        )
        constant = fit_oof_isotonic(
            np.full(6, 0.5),
            np.array([0, 0, 0, 1, 1, 1]),
            minimum_rows=6,
            minimum_class_rows=2,
        )

        for fitted in (one_class, constant):
            self.assertEqual(fitted.status, "unavailable")
            self.assertEqual(fitted.reason, "calibration_unavailable")
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                fitted.transform(np.array([0.5]))

    def test_nonfinite_scores_and_invalid_minimums_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            fit_oof_isotonic(
                np.array([0.1, np.nan, 0.9]),
                np.array([0, 0, 1]),
                minimum_rows=3,
                minimum_class_rows=1,
            )
        with self.assertRaisesRegex(ValueError, "minimum_rows"):
            fit_oof_isotonic(
                np.array([0.1, 0.9]),
                np.array([0, 1]),
                minimum_rows=True,
                minimum_class_rows=1,
            )


class TailBoundaryTest(unittest.TestCase):
    @staticmethod
    def _rows():
        return pd.DataFrame(
            {
                "calibrated_down_probability": [
                    0.80, 0.70, 0.65, 0.55, 0.45, 0.20
                ],
                "predicted_median_return": [
                    -0.02, -0.01, -0.01, -0.01, -0.01, 0.01
                ],
                "predicted_lower_quantile_return": [
                    -0.09, -0.08, -0.07, -0.06, -0.06, -0.01
                ],
                "calibrated_rebound_probability": [
                    0.10, 0.15, 0.25, 0.10, 0.10, 0.05
                ],
                "actual_down_event": [1, 1, 0, 0, 1, 0],
                "actual_terminal_return": [
                    -0.08, -0.06, 0.03, 0.01, -0.05, 0.02
                ],
            }
        )

    def test_selects_highest_precision_then_stricter_boundary(self):
        selected = select_tail_boundary(
            self._rows(),
            down_thresholds=(0.4, 0.6),
            rebound_caps=(0.2, 0.3),
            minimum_rows=2,
            minimum_coverage=0.2,
            maximum_coverage=0.8,
        )

        self.assertEqual(selected.status, "available")
        self.assertEqual(selected.down_threshold, 0.6)
        self.assertEqual(selected.rebound_cap, 0.2)
        self.assertAlmostEqual(selected.down_precision, 1.0)

    def test_nonnegative_raw_return_or_low_coverage_fails_closed(self):
        rows = self._rows()
        rows.loc[:, "actual_terminal_return"] = 0.01

        selected = select_tail_boundary(
            rows,
            down_thresholds=(0.6,),
            rebound_caps=(0.2,),
            minimum_rows=2,
            minimum_coverage=0.2,
            maximum_coverage=0.8,
        )

        self.assertEqual(selected.status, "unavailable")
        self.assertEqual(selected.reason, "tail_boundary_unavailable")
        self.assertIsNone(selected.down_threshold)


def _model_frame(periods=180):
    dates = pd.bdate_range("2024-01-02", periods=periods)
    rows = []
    keys = []
    for ticker, phase in (("AAA", 0), ("BBB", 2), ("CCC", 4)):
        for position, date in enumerate(dates):
            feature = np.sin((position + phase) / 5.0)
            terminal = -0.08 if feature < -0.15 else (
                0.12 if feature > 0.75 else 0.01
            )
            path_mae = min(terminal, -0.08 if feature < -0.15 else -0.01)
            rows.append(
                {
                    "feature": feature,
                    "terminal_return_5": terminal,
                    "path_mae_5": path_mae,
                    "down_event_5": float(
                        terminal <= -0.05 or path_mae <= -0.07
                    ),
                    "extreme_rebound_5": float(terminal >= 0.10),
                    "tail_label_end_date_5": date + pd.offsets.BDay(5),
                }
            )
            keys.append((ticker, date))
    return pd.DataFrame(
        rows,
        index=pd.MultiIndex.from_tuples(
            keys,
            names=("ticker", "observation_date"),
        ),
    ).sort_index()


class NestedTailWalkForwardTest(unittest.TestCase):
    @staticmethod
    def _predict(frame):
        return walk_forward_asymmetric_tail_predictions(
            frame,
            feature_columns=("feature",),
            n_test_folds=4,
            minimum_samples=60,
            minimum_calibration_rows=30,
            minimum_class_rows=5,
            minimum_boundary_rows=5,
        )

    def test_emits_four_semantically_distinct_heads_on_causal_outer_folds(self):
        predictions = self._predict(_model_frame())

        self.assertFalse(predictions.empty)
        self.assertTrue(
            predictions["calibrated_down_probability"].between(0, 1).all()
        )
        self.assertTrue(
            predictions["calibrated_rebound_probability"].between(0, 1).all()
        )
        self.assertTrue(
            (
                predictions["predicted_lower_quantile_return"]
                <= predictions["predicted_median_return"]
            ).all()
        )
        self.assertTrue(
            (
                pd.to_datetime(predictions["training_label_end_max"])
                < pd.to_datetime(predictions["test_start"])
            ).all()
        )
        self.assertEqual(
            set(predictions["model_status"]),
            {"available"},
        )

    def test_outer_outcomes_cannot_change_same_fold_predictions_or_boundary(self):
        frame = _model_frame()
        original = self._predict(frame)
        first_fold = int(original["fold"].min())
        first_start = pd.Timestamp(
            original.loc[original["fold"] == first_fold, "test_start"].iloc[0]
        )
        changed = frame.copy()
        outer = (
            changed.index.get_level_values("observation_date") >= first_start
        )
        changed.loc[outer, "terminal_return_5"] *= -10.0
        changed.loc[outer, "path_mae_5"] *= -10.0
        changed.loc[outer, "down_event_5"] = 1.0
        changed.loc[outer, "extreme_rebound_5"] = 0.0
        rerun = self._predict(changed)
        comparable = [
            "ticker",
            "observation_date",
            "raw_down_probability",
            "calibrated_down_probability",
            "raw_rebound_probability",
            "calibrated_rebound_probability",
            "raw_predicted_median_return",
            "raw_predicted_lower_quantile_return",
            "predicted_median_return",
            "predicted_lower_quantile_return",
            "boundary_status",
            "down_threshold",
            "rebound_cap",
            "predicted_tail_risk",
        ]

        pd.testing.assert_frame_equal(
            original.loc[original["fold"] == first_fold, comparable]
            .reset_index(drop=True),
            rerun.loc[rerun["fold"] == first_fold, comparable]
            .reset_index(drop=True),
        )

    def test_missing_rebound_class_fails_closed_without_partial_heads(self):
        frame = _model_frame()
        frame.loc[:, "extreme_rebound_5"] = 0.0

        predictions = self._predict(frame)

        self.assertTrue(predictions.empty)
        self.assertEqual(
            predictions.attrs["reason"],
            "calibration_unavailable",
        )


def _evaluation_predictions():
    rows = []
    dates = pd.bdate_range("2026-01-02", periods=10)
    returns = (-0.10, 1.00, -0.06, 0.02, -0.08, 0.01, -0.07, 0.03, -0.09, 0.04)
    for position, date in enumerate(dates):
        risk = position < 5
        rows.append(
            {
                "ticker": "AAA" if position % 2 == 0 else "BBB",
                "observation_date": date,
                "fold": 1 if position < 5 else 2,
                "regime": "under_pressure",
                "group": (
                    "semiconductor" if position % 2 == 0 else "software"
                ),
                "boundary_status": "available",
                "predicted_tail_risk": risk,
                "calibrated_down_probability": 0.8 if risk else 0.2,
                "calibrated_rebound_probability": 0.1,
                "actual_down_event": returns[position] <= -0.05,
                "actual_rebound_event": returns[position] >= 0.10,
                "actual_terminal_return": returns[position],
                "actual_path_mae": min(returns[position], -0.01),
                "baseline_predicted_down": position in (0, 2, 6),
                "opening_gap": 0.02 * position,
                "realized_volatility": 0.30,
                "dollar_volume": 5_000_000.0,
                "earnings_proximity": None,
            }
        )
    return pd.DataFrame(rows)


class TailEvaluationTest(unittest.TestCase):
    def test_metrics_keep_extreme_winner_in_untrimmed_risk_mean(self):
        predictions = _evaluation_predictions()

        metrics = evaluate_tail_predictions(
            predictions,
            group_map={"AAA": "semiconductor", "BBB": "software"},
        )
        overall = metrics.loc[
            (metrics["sample_mode"] == "overlapping")
            & (metrics["scope_type"] == "overall")
        ].iloc[0]

        self.assertEqual(overall["risk_count"], 5)
        self.assertAlmostEqual(
            overall["mean_terminal_return"],
            (-0.10 + 1.00 - 0.06 + 0.02 - 0.08) / 5,
        )
        self.assertGreater(overall["mean_terminal_return"], 0.0)
        spaced = metrics.loc[
            (metrics["sample_mode"] == "non_overlapping")
            & (metrics["scope_type"] == "overall")
        ].iloc[0]
        self.assertEqual(spaced["row_count"], 2)

    def test_counterexample_audit_keeps_only_risk_flagged_extreme_winners(self):
        audited = audit_extreme_counterexamples(_evaluation_predictions())

        self.assertEqual(len(audited), 1)
        self.assertEqual(audited.iloc[0]["ticker"], "BBB")
        self.assertGreaterEqual(
            audited.iloc[0]["actual_terminal_return"],
            0.10,
        )
        self.assertIn("opening_gap", audited)
        self.assertIn("earnings_proximity", audited)

    def test_gate_fails_when_required_large_group_is_missing(self):
        metrics = pd.DataFrame(
            [
                {
                    "sample_mode": "non_overlapping",
                    "scope_type": "overall",
                    "scope_name": "all",
                    "fold": None,
                    "row_count": 100,
                    "risk_count": 10,
                    "coverage": 0.10,
                    "down_precision_gain": 0.05,
                    "mean_terminal_return": -0.02,
                    "risk_rebound_rate": 0.02,
                    "all_rebound_rate": 0.05,
                },
                *[
                    {
                        "sample_mode": "non_overlapping",
                        "scope_type": "fold",
                        "scope_name": str(fold),
                        "fold": fold,
                        "row_count": 20,
                        "risk_count": 2,
                        "coverage": 0.10,
                        "down_precision_gain": 0.05,
                        "mean_terminal_return": -0.01,
                        "risk_rebound_rate": 0.02,
                        "all_rebound_rate": 0.05,
                    }
                    for fold in range(1, 6)
                ],
                {
                    "sample_mode": "non_overlapping",
                    "scope_type": "group",
                    "scope_name": "semiconductor",
                    "fold": None,
                    "row_count": 40,
                    "risk_count": 4,
                    "coverage": 0.10,
                    "down_precision_gain": 0.05,
                    "mean_terminal_return": -0.02,
                    "risk_rebound_rate": 0.02,
                    "all_rebound_rate": 0.05,
                },
            ]
        )

        decision = tail_promotion_decision(
            metrics,
            {"passed": True},
            minimum_group_risk_rows=2,
        )

        self.assertFalse(decision["promoted"])
        self.assertIn("software_group_unavailable", decision["reasons"])
        self.assertEqual(decision["online_authority"], "none")

    def test_passing_research_gate_still_has_no_online_authority(self):
        base = {
            "sample_mode": "non_overlapping",
            "row_count": 100,
            "risk_count": 10,
            "coverage": 0.10,
            "down_precision_gain": 0.05,
            "mean_terminal_return": -0.02,
            "risk_rebound_rate": 0.02,
            "all_rebound_rate": 0.05,
        }
        rows = [
            {**base, "scope_type": "overall", "scope_name": "all", "fold": None},
            *[
                {
                    **base,
                    "scope_type": "fold",
                    "scope_name": str(fold),
                    "fold": fold,
                }
                for fold in range(1, 6)
            ],
            {
                **base,
                "scope_type": "group",
                "scope_name": "semiconductor",
                "fold": None,
            },
            {
                **base,
                "scope_type": "group",
                "scope_name": "software",
                "fold": None,
            },
        ]

        decision = tail_promotion_decision(
            pd.DataFrame(rows),
            {"passed": True},
            minimum_group_risk_rows=10,
        )

        self.assertTrue(decision["promoted"])
        self.assertEqual(decision["status"], "passed")
        self.assertEqual(decision["online_authority"], "none")


if __name__ == "__main__":
    unittest.main()
