import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research.unified_downside_benchmark import (
    EVIDENCE_GROUPS,
    VOLUME_PARTICIPATION_COLUMNS,
    align_model_predictions,
    attach_next_open_path_targets,
    attach_point_in_time_strata,
    build_evidence_ablations,
    compare_folds,
    evaluate_unified_predictions,
    evidence_overlap_matrix,
)


def example_prices(extra_rows=0):
    dates = pd.bdate_range("2026-01-02", periods=5 + extra_rows)
    values = [100.0, 102.0, 104.0, 103.0, 105.0, 106.0, 107.0]
    lows = [99.0, 100.0, 95.0, 101.0, 103.0, 104.0, 105.0]
    highs = [101.0, 103.0, 106.0, 105.0, 107.0, 108.0, 109.0]
    frame = pd.DataFrame(
        {
            "ticker": "AAA",
            "observation_date": dates,
            "Open": values[: len(dates)],
            "High": highs[: len(dates)],
            "Low": lows[: len(dates)],
            "Close": values[: len(dates)],
        }
    )
    return frame.set_index(["ticker", "observation_date"])


class UnifiedDownsidePathTargetTest(unittest.TestCase):
    def test_next_open_targets_use_future_open_and_mark_immature_tail(self):
        labeled = attach_next_open_path_targets(
            example_prices(),
            horizons=(2,),
            adverse_thresholds={2: -0.05},
        )

        row = labeled.loc[("AAA", pd.Timestamp("2026-01-02"), 2)]

        self.assertEqual(row["entry_open"], 102.0)
        self.assertAlmostEqual(row["terminal_return"], 104.0 / 102.0 - 1.0)
        self.assertAlmostEqual(row["mae"], 95.0 / 102.0 - 1.0)
        self.assertAlmostEqual(row["mfe"], 106.0 / 102.0 - 1.0)
        self.assertTrue(row["actual_event"])
        self.assertEqual(int(labeled["immature"].sum()), 2)
        self.assertTrue(labeled.loc[("AAA", slice(None), 2), "mature"].iloc[:-2].all())

    def test_appending_future_rows_does_not_change_mature_prefix_labels(self):
        before = attach_next_open_path_targets(
            example_prices(),
            horizons=(2,),
            adverse_thresholds={2: -0.05},
        )
        after = attach_next_open_path_targets(
            example_prices(extra_rows=2),
            horizons=(2,),
            adverse_thresholds={2: -0.05},
        )
        mature = before.loc[before["mature"]]

        assert_frame_equal(
            mature,
            after.loc[mature.index],
        )

    def test_invalid_prices_and_duplicate_keys_fail_closed(self):
        duplicate = pd.concat([example_prices(), example_prices().iloc[[0]]])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            attach_next_open_path_targets(
                duplicate,
                horizons=(2,),
                adverse_thresholds={2: -0.05},
            )

        invalid = example_prices()
        invalid.loc[("AAA", invalid.index.get_level_values(1)[1]), "Open"] = 0.0
        labeled = attach_next_open_path_targets(
            invalid,
            horizons=(2,),
            adverse_thresholds={2: -0.05},
        )
        first = labeled.loc[("AAA", pd.Timestamp("2026-01-02"), 2)]
        self.assertFalse(first["mature"])
        self.assertEqual(first["unavailable_reason"], "invalid_future_path")
        self.assertTrue(np.isnan(first["mae"]))

    def test_thresholds_and_horizons_are_strictly_validated(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            attach_next_open_path_targets(
                example_prices(),
                horizons=(0,),
                adverse_thresholds={0: -0.05},
            )
        with self.assertRaisesRegex(ValueError, "threshold"):
            attach_next_open_path_targets(
                example_prices(),
                horizons=(2,),
                adverse_thresholds={2: 0.0},
            )
        with self.assertRaisesRegex(ValueError, "missing"):
            attach_next_open_path_targets(
                example_prices(),
                horizons=(2,),
                adverse_thresholds={5: -0.05},
            )


class UnifiedDownsideAlignmentTest(unittest.TestCase):
    def test_alignment_requires_unique_keys_and_keeps_missing_unavailable(self):
        keys = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "observation_date": pd.to_datetime(
                    ["2026-01-02", "2026-01-05"]
                ),
                "horizon": [5, 5],
                "fold": [1, 1],
            }
        )
        ridge = keys.iloc[[0]].assign(
            predicted_event=True,
            predicted_score=pd.NA,
            model_version="ridge_direction_v1",
        )

        aligned = align_model_predictions(keys, {"ridge_down": ridge})

        self.assertEqual(len(aligned), 2)
        available = aligned.loc[
            aligned["observation_date"] == pd.Timestamp("2026-01-02")
        ].iloc[0]
        missing = aligned.loc[
            aligned["observation_date"] == pd.Timestamp("2026-01-05")
        ].iloc[0]
        self.assertEqual(available["status"], "available")
        self.assertTrue(available["predicted_event"])
        self.assertEqual(missing["status"], "unavailable")
        self.assertTrue(pd.isna(missing["predicted_event"]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            align_model_predictions(
                keys,
                {"ridge_down": pd.concat([ridge, ridge], ignore_index=True)},
            )

    def test_alignment_rejects_model_rows_outside_frozen_test_keys(self):
        keys = pd.DataFrame(
            {
                "ticker": ["AAA"],
                "observation_date": [pd.Timestamp("2026-01-02")],
                "horizon": [5],
                "fold": [1],
            }
        )
        outside = keys.assign(
            observation_date=pd.Timestamp("2026-01-05"),
            predicted_event=False,
            predicted_score=0.0,
            model_version="v1",
        )

        with self.assertRaisesRegex(ValueError, "outside"):
            align_model_predictions(keys, {"ridge_down": outside})

    def test_strata_use_half_open_assignment_intervals(self):
        keys = pd.DataFrame(
            {
                "ticker": ["AAA"] * 3,
                "observation_date": pd.to_datetime(
                    ["2026-01-09", "2026-01-10", "2026-02-02"]
                ),
                "horizon": [5] * 3,
                "fold": [1] * 3,
            }
        )
        assignments = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "theme_keys": [
                    ("semiconductor",),
                    ("software_cloud",),
                ],
                "primary_model_group": [
                    "semiconductor",
                    "software_cloud",
                ],
                "classification_state": ["classified", "classified"],
                "effective_from": pd.to_datetime(
                    ["2025-01-01", "2026-02-01"]
                ),
                "effective_to": pd.to_datetime(["2026-01-10", None]),
                "source": ["override", "override"],
            }
        )
        regimes = pd.DataFrame(
            {
                "observation_date": keys["observation_date"],
                "regime": "uptrend",
            }
        )

        stratified = attach_point_in_time_strata(
            keys,
            assignments,
            regimes,
        ).set_index("observation_date")

        self.assertEqual(
            stratified.loc[pd.Timestamp("2026-01-09"), "group_key"],
            "semiconductor",
        )
        self.assertEqual(
            stratified.loc[pd.Timestamp("2026-01-10"), "group_key"],
            "unclassified",
        )
        self.assertEqual(
            stratified.loc[pd.Timestamp("2026-02-02"), "group_key"],
            "software_cloud",
        )
        self.assertEqual(
            stratified.loc[pd.Timestamp("2026-01-09"), "market_regime"],
            "uptrend",
        )

    def test_strata_do_not_bridge_departure_and_reentry(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        keys = pd.DataFrame(
            {
                "ticker": "AAA",
                "observation_date": dates,
                "horizon": 5,
                "fold": 1,
            }
        )
        assignments = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "theme_keys_json": [
                    '["semiconductor"]',
                    '["semiconductor"]',
                ],
                "primary_model_group": [
                    "semiconductor",
                    "semiconductor",
                ],
                "classification_state": ["classified", "classified"],
                "effective_from": [dates[0], dates[3]],
                "effective_to": [dates[2], pd.NaT],
                "source": ["sec", "sec"],
            }
        )

        result = attach_point_in_time_strata(
            keys,
            assignments,
            pd.DataFrame(),
        )

        self.assertEqual(
            result["group_key"].tolist(),
            [
                "semiconductor",
                "semiconductor",
                "unclassified",
                "semiconductor",
                "semiconductor",
            ],
        )
        self.assertEqual(
            result["market_regime"].tolist(),
            ["unavailable"] * 5,
        )


class UnifiedDownsideMetricTest(unittest.TestCase):
    def _prediction_rows(self):
        dates = pd.bdate_range("2026-01-02", periods=6)
        rows = []
        actual = [True, False, True, False, True, False]
        predictions = {
            "ridge_down": [True, False, False, False, True, False],
            "immediate_8": [True, True, False, False, True, False],
        }
        for specification, values in predictions.items():
            for position, (date, event, predicted) in enumerate(
                zip(dates, actual, values)
            ):
                rows.append(
                    {
                        "ticker": "AAA",
                        "observation_date": date,
                        "horizon": 5,
                        "fold": 1 + position // 2,
                        "specification": specification,
                        "predicted_event": predicted,
                        "predicted_score": float(predicted),
                        "status": "available",
                        "mature": True,
                        "actual_event": event,
                        "terminal_return": -0.08 if event else 0.04,
                        "mae": -0.10 if event else -0.01,
                        "mfe": 0.02 if event else 0.08,
                        "group_key": "semiconductor",
                        "market_regime": "correction",
                    }
                )
        return pd.DataFrame(rows)

    def test_binary_models_do_not_fabricate_auc_or_probability_metrics(self):
        metrics = evaluate_unified_predictions(
            self._prediction_rows(),
            minimum_group_samples=2,
        )
        row = metrics.loc[
            (metrics["specification"] == "immediate_8")
            & (metrics["scope"] == "all")
            & (metrics["regime_scope"] == "all")
            & (metrics["sample_mode"] == "overlapping")
            & (metrics["fold"].astype(str) == "all")
        ].iloc[0]

        self.assertTrue(pd.isna(row["roc_auc"]))
        self.assertTrue(pd.isna(row["pr_auc"]))
        self.assertAlmostEqual(row["precision"], 2.0 / 3.0)
        self.assertAlmostEqual(row["recall"], 2.0 / 3.0)
        self.assertEqual(row["status"], "ok")

    def test_metrics_report_group_regime_fold_and_non_overlapping_rows(self):
        metrics = evaluate_unified_predictions(
            self._prediction_rows(),
            minimum_group_samples=2,
        )

        self.assertIn("semiconductor", set(metrics["scope"]))
        self.assertIn("correction", set(metrics["regime_scope"]))
        self.assertEqual(
            set(metrics["sample_mode"]),
            {"overlapping", "non_overlapping"},
        )
        self.assertTrue((metrics["excluded_unavailable_count"] == 0).all())
        self.assertIn("1", set(metrics["fold"].astype(str)))

    def test_fold_comparison_counts_only_paired_sufficient_folds(self):
        metrics = pd.DataFrame(
            {
                "scope": "all",
                "regime_scope": "all",
                "horizon": 5,
                "sample_mode": "non_overlapping",
                "specification": (
                    ["ridge_down"] * 4 + ["toprisk_stateful"] * 4
                ),
                "fold": [1, 2, 3, 4, 1, 2, 3, 4],
                "status": [
                    "ok",
                    "ok",
                    "ok",
                    "insufficient",
                    "ok",
                    "ok",
                    "ok",
                    "ok",
                ],
                "balanced_accuracy": [
                    0.50,
                    0.60,
                    0.55,
                    np.nan,
                    0.60,
                    0.61,
                    0.50,
                    0.80,
                ],
                "sample_count": [100] * 8,
            }
        )

        comparison = compare_folds(metrics, baseline="ridge_down")
        row = comparison.loc[
            comparison["specification"] == "toprisk_stateful"
        ].iloc[0]

        self.assertEqual(row["comparable_fold_count"], 3)
        self.assertEqual(row["fold_win_count"], 2)
        self.assertAlmostEqual(row["fold_win_rate"], 2.0 / 3.0)


class UnifiedDownsideAblationTest(unittest.TestCase):
    def _evidence(self):
        return pd.DataFrame(
            {
                "volume_ratio": [1.5, 0.9, 1.8],
                "volume_change": [0.5, -0.1, 0.8],
                "close_location": [0.1, 0.8, 0.2],
                "signed_volume_proxy": [-1.0, 0.5, -1.2],
                "below_ema20": [True, False, True],
                "failed_breakout": [False, False, True],
                "sector_relative_return": [-0.05, 0.02, -0.08],
                "market_under_pressure": [True, False, True],
                "prior_runup": [0.8, 0.2, 1.0],
                "extended_from_ema20": [0.2, 0.01, 0.3],
            }
        )

    def test_each_ablation_removes_only_one_registered_evidence_group(self):
        evidence = self._evidence()
        seen_columns = {}

        def scorer(frame):
            seen_columns[len(seen_columns)] = tuple(frame.columns)
            return frame.notna().sum(axis=1).astype(float)

        outputs = build_evidence_ablations(evidence, scorer)

        self.assertEqual(
            set(outputs),
            {
                "full",
                *(
                    f"without_{group}"
                    for group in EVIDENCE_GROUPS
                ),
            },
        )
        volume_columns = set(VOLUME_PARTICIPATION_COLUMNS)
        without_volume = set(seen_columns[1])
        self.assertTrue(volume_columns.isdisjoint(without_volume))
        self.assertEqual(
            without_volume,
            set(evidence.columns).difference(volume_columns),
        )

    def test_overlap_matrix_reports_numeric_and_boolean_dependence(self):
        overlaps = evidence_overlap_matrix(self._evidence())
        volume_pair = overlaps.loc[
            (overlaps["evidence_a"] == "volume_ratio")
            & (overlaps["evidence_b"] == "volume_change")
        ].iloc[0]
        boolean_pair = overlaps.loc[
            (overlaps["evidence_a"] == "below_ema20")
            & (overlaps["evidence_b"] == "failed_breakout")
        ].iloc[0]

        self.assertGreater(volume_pair["pearson"], 0.9)
        self.assertTrue(pd.isna(volume_pair["jaccard"]))
        self.assertTrue(pd.isna(boolean_pair["pearson"]))
        self.assertAlmostEqual(boolean_pair["jaccard"], 0.5)


if __name__ == "__main__":
    unittest.main()
