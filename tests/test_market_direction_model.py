import unittest
import warnings

import numpy as np
import pandas as pd

from research.market_direction_model import (
    attach_next_open_targets,
    chronological_purged_folds,
    direction_labels,
    evaluate_direction_ablation,
    training_only_design,
    walk_forward_direction_predictions,
    walk_forward_boosted_predictions,
    walk_forward_ridge_predictions,
)


def history(periods=12, start=100.0):
    dates = pd.bdate_range("2026-01-02", periods=periods)
    close = start + np.arange(periods, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(periods, 1_000_000.0),
        },
        index=dates,
    )


def feature_frame(periods=90):
    dates = pd.bdate_range("2025-01-02", periods=periods)
    rows = []
    index = []
    for ticker, offset in (("AAA", 0.0), ("BBB", 0.4), ("CCC", -0.3)):
        for position, date in enumerate(dates):
            stock = np.sin(position / 4.0) + offset
            market = np.cos(position / 7.0)
            sector = np.nan if ticker == "CCC" else np.sin(position / 9.0)
            future = 0.035 * stock + 0.025 * market + 0.015 * (
                0.0 if np.isnan(sector) else sector
            )
            rows.append(
                {
                    "stock": stock,
                    "market": market,
                    "sector": sector,
                    "executable_return_5": future,
                    "executable_label_end_date_5": date + pd.offsets.BDay(5),
                }
            )
            index.append((ticker, date))
    return pd.DataFrame(
        rows,
        index=pd.MultiIndex.from_tuples(
            index, names=("ticker", "observation_date")
        ),
    ).sort_index()


class MarketDirectionModelTest(unittest.TestCase):
    def test_ten_session_direction_band_is_supported(self):
        values = pd.Series([-0.02, 0.0, 0.02])

        self.assertEqual(
            direction_labels(values, 10).tolist(),
            ["down", "neutral", "up"],
        )

    def test_direction_labels_reject_unsupported_horizon(self):
        with self.assertRaisesRegex(
            ValueError,
            "supported positive session counts",
        ):
            direction_labels(pd.Series([-0.02, 0.0, 0.02]), 7)

    def test_public_direction_labels_match_walk_forward_actual_directions(self):
        frame = feature_frame()

        predictions = walk_forward_direction_predictions(
            frame,
            horizon=5,
            feature_sets={"stock": ("stock",)},
            n_folds=4,
            minimum_samples=30,
        )

        self.assertEqual(
            predictions["actual_direction"].tolist(),
            direction_labels(predictions["actual_return"], 5).tolist(),
        )

    def test_training_design_fit_does_not_depend_on_test_values(self):
        train = pd.DataFrame(
            {
                "stock": [0.0, 1.0, 2.0, 3.0],
                "market": [np.nan, -1.0, 1.0, 2.0],
            }
        )
        ordinary_test = pd.DataFrame(
            {
                "stock": [4.0, 5.0],
                "market": [3.0, np.nan],
            }
        )
        changed_test = pd.DataFrame(
            {
                "stock": [-1.0e300, 1.0e300],
                "market": [np.inf, -np.inf],
            }
        )

        ordinary_train_design, ordinary_test_design = training_only_design(
            train,
            ordinary_test,
            ("stock", "market"),
        )
        changed_train_design, changed_test_design = training_only_design(
            train,
            changed_test,
            ("stock", "market"),
        )

        np.testing.assert_array_equal(
            ordinary_train_design,
            changed_train_design,
        )
        self.assertTrue(np.isfinite(ordinary_test_design).all())
        self.assertTrue(np.isfinite(changed_test_design).all())

    def test_next_open_targets_use_next_open_and_horizon_close(self):
        histories = {"AAA": history(), "BBB": history(start=200.0)}
        index = pd.MultiIndex.from_tuples(
            [
                (ticker, date)
                for ticker, frame in histories.items()
                for date in frame.index
            ],
            names=("ticker", "observation_date"),
        )
        frame = pd.DataFrame({"feature": 1.0}, index=index)

        result = attach_next_open_targets(frame, histories, horizons=(5,))
        aaa = result.xs("AAA", level="ticker")

        self.assertAlmostEqual(
            aaa["executable_return_5"].iloc[2],
            107.0 / 102.5 - 1.0,
        )
        self.assertEqual(
            aaa["executable_entry_date_5"].iloc[2],
            aaa.index[3],
        )
        self.assertEqual(
            aaa["executable_label_end_date_5"].iloc[2],
            aaa.index[7],
        )
        self.assertTrue(aaa["executable_return_5"].iloc[-5:].isna().all())

    def test_chronological_folds_purge_labels_that_end_at_test_start(self):
        frame = feature_frame(periods=60)

        folds = chronological_purged_folds(frame, horizon=5, n_folds=4)

        self.assertGreaterEqual(len(folds), 2)
        for train_index, test_index in folds:
            train = frame.iloc[train_index]
            test = frame.iloc[test_index]
            test_start = test.index.get_level_values("observation_date").min()
            self.assertTrue(
                (train["executable_label_end_date_5"] < test_start).all()
            )

    def test_logistic_ablation_handles_missing_sector_and_reports_down_recall(self):
        frame = feature_frame()
        feature_sets = {
            "stock": ("stock",),
            "stock_market": ("stock", "market"),
            "stock_market_sector": ("stock", "market", "sector"),
        }

        predictions = walk_forward_direction_predictions(
            frame,
            horizon=5,
            feature_sets=feature_sets,
            n_folds=4,
            minimum_samples=30,
        )
        metrics = evaluate_direction_ablation(predictions)

        self.assertEqual(
            set(metrics["specification"]),
            set(feature_sets) | {"majority_baseline"},
        )
        self.assertTrue(
            {
                "balanced_accuracy",
                "macro_f1",
                "down_precision",
                "down_recall",
                "coverage",
            }.issubset(metrics.columns)
        )
        self.assertTrue(np.isfinite(metrics["balanced_accuracy"]).all())
        self.assertTrue(
            predictions.loc[
                predictions["specification"] == "stock_market_sector",
                "predicted_direction",
            ].notna().all()
        )

    def test_ridge_baseline_uses_same_purged_executable_rows(self):
        frame = feature_frame()

        predictions = walk_forward_ridge_predictions(
            frame,
            horizon=5,
            feature_columns=("stock", "market", "sector"),
            n_folds=4,
            minimum_samples=30,
            specification="ridge_decay_market",
        )

        self.assertEqual(
            set(predictions["specification"]),
            {"ridge_decay_market"},
        )
        self.assertTrue(predictions["predicted_direction"].notna().all())
        self.assertTrue(
            (
                predictions["training_samples"]
                < len(frame)
            ).all()
        )

    def test_fold_preprocessing_clips_extreme_finite_values_without_warning(self):
        frame = feature_frame()
        frame.loc[("AAA", frame.loc["AAA"].index[10]), "stock"] = 1e300

        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            predictions = walk_forward_direction_predictions(
                frame,
                horizon=5,
                feature_sets={"extreme": ("stock", "market")},
                n_folds=4,
                minimum_samples=30,
            )

        self.assertFalse(predictions.empty)

    def test_boosted_challenger_learns_non_linear_context_on_purged_folds(self):
        frame = feature_frame()

        predictions = walk_forward_boosted_predictions(
            frame,
            horizon=5,
            feature_columns=("stock", "market", "sector"),
            n_folds=4,
            minimum_samples=30,
        )

        self.assertEqual(
            set(predictions["specification"]),
            {"boosted_full_context"},
        )
        self.assertTrue(predictions["predicted_direction"].notna().all())


if __name__ == "__main__":
    unittest.main()
