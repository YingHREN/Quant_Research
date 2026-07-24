import unittest

import numpy as np
import pandas as pd

from research.market_direction_model import (
    attach_next_open_targets,
    chronological_purged_folds,
    evaluate_direction_ablation,
    walk_forward_direction_predictions,
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


if __name__ == "__main__":
    unittest.main()
