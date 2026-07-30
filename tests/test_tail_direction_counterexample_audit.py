import unittest

import numpy as np
import pandas as pd

from research.tail_direction_counterexample_audit import (
    AUDIT_FEATURE_TYPES,
    build_audit_population,
    match_extreme_up_to_terminal_down,
)


def _feature_frame(tickers=("AAA",), dates=None):
    if dates is None:
        dates = pd.date_range("2026-01-02", periods=4, freq="B")
    index = pd.MultiIndex.from_product(
        (tickers, dates),
        names=("ticker", "observation_date"),
    )
    return pd.DataFrame(
        {
            feature: (
                np.tile(np.arange(len(dates), dtype=float), len(tickers))
                if kind == "numeric"
                else np.tile(
                    np.arange(len(dates), dtype=int) % 2,
                    len(tickers),
                )
            )
            for feature, kind in AUDIT_FEATURE_TYPES.items()
            if feature
            not in {
                "opening_gap",
                "log_dollar_volume_20",
                "dollar_volume_ratio_20",
                "realized_volatility_change_20",
            }
        },
        index=index,
    )


def _history(dates=None):
    if dates is None:
        dates = pd.date_range("2025-11-03", periods=50, freq="B")
    close = pd.Series(np.linspace(90.0, 110.0, len(dates)), index=dates)
    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]) * 1.01,
            "High": close * 1.02,
            "Low": close * 0.98,
            "Close": close,
            "Volume": np.linspace(1_000_000, 2_000_000, len(dates)),
        },
        index=dates,
    )


class AuditPopulationTest(unittest.TestCase):
    def test_assigns_mutually_exclusive_outcomes_in_frozen_order(self):
        dates = pd.date_range("2026-01-02", periods=4, freq="B")
        predictions = pd.DataFrame(
            {
                "ticker": ["AAA"] * 4,
                "observation_date": dates,
                "fold": [1] * 4,
                "group": ["software"] * 4,
                "regime": ["uptrend"] * 4,
                "calibrated_down_probability": [0.5] * 4,
                "actual_terminal_return": [-0.06, 0.12, 0.02, 0.01],
                "actual_path_mae": [-0.10, -0.20, -0.08, -0.02],
            }
        )
        history = _history(
            pd.date_range("2025-11-03", "2026-01-07", freq="B")
        )

        result = build_audit_population(
            predictions,
            _feature_frame(dates=dates),
            {"AAA": history},
        )

        self.assertEqual(
            result["outcome_state"].tolist(),
            [
                "terminal_down",
                "extreme_up",
                "path_only_stress",
                "other",
            ],
        )
        self.assertFalse(
            result.duplicated(["ticker", "observation_date"]).any()
        )
        self.assertTrue(
            set(AUDIT_FEATURE_TYPES).issubset(result.columns)
        )
        self.assertEqual(
            set(result["earnings_proximity_status"]),
            {"unavailable"},
        )
        self.assertEqual(
            set(result["market_cap_status"]),
            {"unavailable"},
        )

    def test_filters_low_scores_and_rejects_duplicate_keys(self):
        date = pd.Timestamp("2026-01-02")
        predictions = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "observation_date": [date, date],
                "fold": [1, 1],
                "group": ["software", "software"],
                "regime": ["uptrend", "uptrend"],
                "calibrated_down_probability": [0.39, 0.50],
                "actual_terminal_return": [-0.10, -0.10],
                "actual_path_mae": [-0.10, -0.10],
            }
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_audit_population(
                predictions,
                _feature_frame(dates=[date]),
                {"AAA": _history()},
            )

        unique = predictions.iloc[[0]].copy()
        result = build_audit_population(
            unique,
            _feature_frame(dates=[date]),
            {"AAA": _history()},
        )
        self.assertTrue(result.empty)

    def test_appended_future_history_cannot_change_existing_population(self):
        date = pd.Timestamp("2026-01-02")
        predictions = pd.DataFrame(
            {
                "ticker": ["AAA"],
                "observation_date": [date],
                "fold": [1],
                "group": ["software"],
                "regime": ["uptrend"],
                "calibrated_down_probability": [0.50],
                "actual_terminal_return": [-0.10],
                "actual_path_mae": [-0.10],
            }
        )
        base_history = _history(
            pd.date_range("2025-11-03", date, freq="B")
        )
        appended = _history(
            pd.date_range("2026-01-05", "2026-02-02", freq="B")
        )
        appended[["Open", "Close", "Volume"]] *= 50
        future = pd.concat((base_history, appended)).sort_index()

        first = build_audit_population(
            predictions,
            _feature_frame(dates=[date]),
            {"AAA": base_history},
        )
        second = build_audit_population(
            predictions,
            _feature_frame(dates=[date]),
            {"AAA": future},
        )

        pd.testing.assert_frame_equal(first, second)


def _matching_population():
    rows = [
        ("UP1", "2026-03-01", "extreme_up", 1, "software", "uptrend", 1.0),
        ("UP2", "2026-03-02", "extreme_up", 1, "software", "uptrend", 2.0),
        ("DN1", "2026-03-03", "terminal_down", 1, "software", "uptrend", 1.1),
        ("DN2", "2026-03-04", "terminal_down", 1, "software", "uptrend", 2.1),
        ("WRONG_GROUP", "2026-03-01", "terminal_down", 1, "other", "uptrend", 1.0),
        ("BOUND63", "2026-05-03", "terminal_down", 2, "software", "uptrend", 3.0),
        ("CASE63", "2026-03-01", "extreme_up", 2, "software", "uptrend", 3.1),
        ("BOUND64", "2026-05-04", "terminal_down", 3, "software", "uptrend", 4.0),
        ("CASE64", "2026-03-01", "extreme_up", 3, "software", "uptrend", 4.1),
    ]
    frame = pd.DataFrame(
        rows,
        columns=(
            "ticker",
            "observation_date",
            "outcome_state",
            "fold",
            "group",
            "regime",
            "realized_vol_63",
        ),
    )
    frame["observation_date"] = pd.to_datetime(frame["observation_date"])
    for feature, kind in AUDIT_FEATURE_TYPES.items():
        if feature not in frame:
            frame[feature] = 1.0 if kind == "numeric" else False
    return frame


class DeterministicMatchingTest(unittest.TestCase):
    def test_matching_is_exact_bounded_unique_and_deterministic(self):
        population = _matching_population()

        first_pairs, first_coverage = match_extreme_up_to_terminal_down(
            population
        )
        second_pairs, second_coverage = match_extreme_up_to_terminal_down(
            population
        )

        pd.testing.assert_frame_equal(first_pairs, second_pairs)
        pd.testing.assert_frame_equal(first_coverage, second_coverage)
        self.assertTrue(
            (first_pairs["calendar_distance_days"] <= 63).all()
        )
        self.assertFalse(first_pairs["control_key"].duplicated().any())
        self.assertEqual(
            set(first_pairs["case_ticker"]),
            {"UP1", "UP2", "CASE63"},
        )
        self.assertEqual(
            first_pairs.loc[
                first_pairs["case_ticker"] == "CASE63",
                "calendar_distance_days",
            ].item(),
            63,
        )
        self.assertEqual(
            first_coverage.loc[
                first_coverage["scope_type"] == "overall",
                "unmatched_case_count",
            ].item(),
            1,
        )

    def test_missing_volatility_sorts_after_finite_candidate(self):
        population = _matching_population().iloc[:0].copy()
        extra = pd.DataFrame(
            [
                {
                    "ticker": "UP",
                    "observation_date": "2026-03-01",
                    "outcome_state": "extreme_up",
                    "fold": 1,
                    "group": "software",
                    "regime": "uptrend",
                    "realized_vol_63": 1.0,
                },
                {
                    "ticker": "FINITE",
                    "observation_date": "2026-03-02",
                    "outcome_state": "terminal_down",
                    "fold": 1,
                    "group": "software",
                    "regime": "uptrend",
                    "realized_vol_63": 1.2,
                },
                {
                    "ticker": "MISSING",
                    "observation_date": "2026-03-02",
                    "outcome_state": "terminal_down",
                    "fold": 1,
                    "group": "software",
                    "regime": "uptrend",
                    "realized_vol_63": np.nan,
                },
            ]
        )
        extra["observation_date"] = pd.to_datetime(
            extra["observation_date"]
        )
        for feature, kind in AUDIT_FEATURE_TYPES.items():
            if feature not in extra:
                extra[feature] = 1.0 if kind == "numeric" else False

        pairs, _ = match_extreme_up_to_terminal_down(extra)

        self.assertEqual(pairs.loc[0, "control_ticker"], "FINITE")


if __name__ == "__main__":
    unittest.main()
