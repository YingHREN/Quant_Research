import unittest

import numpy as np
import pandas as pd

from research.evaluate_toprisk_comparison import (
    SIGNAL_KEYS,
    build_comparison_frame,
    evaluate_signals,
)


def _history(values):
    index = pd.bdate_range("2026-07-01", periods=len(values))
    close = np.asarray(values, dtype=float)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=index,
    )


def _context(tickers, dates):
    index = pd.MultiIndex.from_product(
        [tickers, dates],
        names=["ticker", "observation_date"],
    )
    frame = pd.DataFrame(index=index)
    frame["individual_risk_score"] = 0.0
    frame["persistent_risk_score"] = 0.0
    frame["high_level_distribution_raw_state"] = "low"
    frame["high_level_distribution_state"] = "low"
    return frame


class TopRiskComparisonTest(unittest.TestCase):
    def test_signal_definitions_and_missing_ridge_are_explicit(self):
        history = _history([100, 100, 90, 100, 100, 100])
        context = _context(["AAA"], history.index)
        context.loc[("AAA", history.index[0]), "individual_risk_score"] = 35.0
        context.loc[
            ("AAA", history.index[1]),
            "high_level_distribution_raw_state",
        ] = "confirmed"
        context.loc[
            ("AAA", history.index[1]),
            "high_level_distribution_state",
        ] = "fading"
        forecasts = pd.DataFrame(
            {
                "ticker": ["AAA"],
                "observation_date": [history.index[0]],
                "raw_direction": ["down"],
                "bearish_turn_score": [80.0],
            }
        )

        frame = build_comparison_frame(
            {"AAA": history},
            forecasts=forecasts,
            context=context,
        )

        self.assertEqual(set(SIGNAL_KEYS), {
            "ridge_down",
            "immediate_8",
            "memory_12",
            "toprisk_confirmed",
            "toprisk_stateful",
            "ridge_plus_toprisk",
        })
        first = frame.loc[("AAA", history.index[0])]
        self.assertTrue(first["signal_ridge_down"])
        self.assertTrue(first["signal_immediate_8"])
        self.assertTrue(first["signal_memory_12"])
        second = frame.loc[("AAA", history.index[1])]
        self.assertTrue(second["signal_toprisk_confirmed"])
        self.assertTrue(second["signal_toprisk_stateful"])

        missing = build_comparison_frame(
            {"AAA": history},
            forecasts=None,
            context=context,
        )
        self.assertTrue(missing["signal_ridge_down"].isna().all())
        self.assertTrue(missing["signal_ridge_plus_toprisk"].isna().all())

    def test_metrics_exclude_mature_tail_and_are_grouped(self):
        histories = {
            "AAA": _history([100, 100, 90, 100, 100, 100]),
            "BBB": _history([100, 100, 100, 100, 100, 100]),
        }
        dates = histories["AAA"].index
        context = _context(histories, dates)
        forecasts = pd.DataFrame(
            [
                {
                    "ticker": ticker,
                    "observation_date": date,
                    "raw_direction": (
                        "down"
                        if ticker == "AAA" and date == dates[0]
                        else "up"
                    ),
                    "bearish_turn_score": (
                        80.0
                        if ticker == "AAA" and date == dates[0]
                        else 0.0
                    ),
                }
                for ticker in histories
                for date in dates
            ]
        )
        frame = build_comparison_frame(
            histories,
            forecasts=forecasts,
            context=context,
        )

        rows = evaluate_signals(
            frame,
            horizons=(2,),
            adverse_threshold=-0.05,
            groups={"AAA": "software", "BBB": "semiconductor"},
        )

        all_immediate = next(
            row for row in rows
            if row["group"] == "all"
            and row["signal"] == "immediate_8"
        )
        self.assertEqual(all_immediate["sample_count"], 8)
        self.assertEqual(all_immediate["signal_count"], 1)
        self.assertEqual(all_immediate["precision"], 1.0)
        self.assertEqual(all_immediate["recall"], 0.5)
        self.assertEqual(all_immediate["specificity"], 1.0)
        self.assertEqual(all_immediate["balanced_accuracy"], 0.75)
        self.assertEqual(all_immediate["mean_lead_sessions"], 2.0)
        self.assertEqual(
            {row["group"] for row in rows},
            {"all", "software", "semiconductor", "other"},
        )

    def test_appending_future_rows_does_not_change_existing_signals(self):
        base = _history([100, 101, 102, 103, 104, 105])
        context = _context(["AAA"], base.index)
        first = build_comparison_frame(
            {"AAA": base},
            context=context,
        )
        extended = _history([100, 101, 102, 103, 104, 105, 50, 40])
        extended_context = _context(["AAA"], extended.index)
        second = build_comparison_frame(
            {"AAA": extended},
            context=extended_context,
        )

        pd.testing.assert_frame_equal(
            first.loc[:, [f"signal_{key}" for key in SIGNAL_KEYS]],
            second.loc[first.index, [f"signal_{key}" for key in SIGNAL_KEYS]],
        )

    def test_immediate_signal_can_be_rebuilt_from_point_in_time_features(self):
        history = _history([100, 99, 98])
        context = _context(["AAA"], history.index)
        features = pd.DataFrame(
            {
                "pressure_distribution_day": [1.0, 0.0, np.nan],
                "close_vs_ema20_pct": [-1.0, 1.0, np.nan],
                "volume_ratio": [1.6, 1.0, np.nan],
                "volume_change": [0.6, 0.0, np.nan],
                "pressure_close_location": [-0.8, 0.0, np.nan],
                "pressure_signed_volume_proxy": [-1.2, 0.0, np.nan],
                "stock_sector_relative_strength_20": [-0.06, 0.0, np.nan],
                "pressure_failed_breakout": [1.0, 0.0, np.nan],
                "pivot_distance_pct": [-12.0, 0.0, np.nan],
            },
            index=context.index,
        )

        frame = build_comparison_frame(
            {"AAA": history},
            context=context,
            feature_frame=features,
        )

        self.assertTrue(frame.iloc[0]["signal_immediate_8"])
        self.assertFalse(frame.iloc[1]["signal_immediate_8"])
        self.assertTrue(pd.isna(frame.iloc[2]["signal_immediate_8"]))


if __name__ == "__main__":
    unittest.main()
