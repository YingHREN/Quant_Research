from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.run_historical_demand_support_study import (
    assign_chronological_folds,
    build_outcome_rows,
    build_pocket_pivot_rows,
    build_variant_signal_rows,
    evaluate_outcomes,
    non_overlapping_outcomes,
    promotion_decision,
    render_report,
)
from tests.helpers import make_ohlcv


def metric_rows(*, adverse_delta: float = 0.0, increment: float = 0.02):
    rows = []
    for fold in range(1, 6):
        for group in ("semiconductor", "software"):
            rows.extend(
                (
                    {
                        "variant": "baseline",
                        "fold": fold,
                        "group": group,
                        "support_hold_rate": 0.50,
                        "max_adverse_excursion": -0.08,
                        "sample_count": 40,
                    },
                    {
                        "variant": "baseline_plus_historical_demand",
                        "fold": fold,
                        "group": group,
                        "support_hold_rate": 0.50 + increment,
                        "max_adverse_excursion": -0.08 + adverse_delta,
                        "sample_count": 40,
                    },
                )
            )
    return pd.DataFrame(rows)


class HistoricalDemandSupportStudyTest(unittest.TestCase):
    def test_outcomes_execute_at_next_open_and_exclude_immature_tail(self):
        history = make_ohlcv(
            [100.0, 101.0, 104.0, 98.0, 102.0, 103.0],
            opens=[99.0, 100.0, 102.0, 103.0, 99.0, 102.0],
            highs=[101.0, 102.0, 106.0, 104.0, 103.0, 105.0],
            lows=[98.0, 99.0, 101.0, 96.0, 97.0, 101.0],
        )
        signals = pd.DataFrame(
            {
                "variant": ["baseline"] * len(history),
                "eligible": [True] * len(history),
                "zone_lower": [97.0] * len(history),
                "zone_upper": [99.0] * len(history),
            },
            index=history.index,
        )

        outcomes = build_outcome_rows(
            "AAA",
            history,
            signals,
            horizon=3,
        )

        self.assertEqual(len(outcomes), 3)
        first = outcomes.iloc[0]
        self.assertEqual(first["entry_date"], history.index[1])
        self.assertEqual(first["entry_price"], 100.0)
        self.assertAlmostEqual(first["maximum_favorable_excursion"], 0.06)
        self.assertAlmostEqual(first["maximum_adverse_excursion"], -0.04)
        self.assertTrue(first["support_held"])
        self.assertEqual(first["first_bounce_delay"], 1)

    def test_non_overlapping_folds_keep_each_date_in_one_chronological_fold(self):
        rows = pd.DataFrame(
            {
                "ticker": ["AAA", "BBB"] * 10,
                "observation_date": np.repeat(
                    pd.bdate_range("2026-01-02", periods=10),
                    2,
                ),
            }
        )

        folded = assign_chronological_folds(rows, n_folds=3)

        self.assertEqual(folded.groupby("observation_date")["fold"].nunique().max(), 1)
        fold_order = (
            folded.groupby("fold")["observation_date"]
            .agg(["min", "max"])
            .sort_index()
        )
        self.assertTrue(
            all(
                fold_order.iloc[position]["max"]
                < fold_order.iloc[position + 1]["min"]
                for position in range(len(fold_order) - 1)
            )
        )

    def test_metrics_report_hold_break_excursions_and_coverage(self):
        outcomes = pd.DataFrame(
            {
                "variant": ["baseline", "baseline", "baseline"],
                "horizon": [5, 5, 5],
                "fold": [1, 1, 1],
                "group": ["software", "software", "software"],
                "regime": ["correction", "correction", "correction"],
                "support_held": [True, False, True],
                "support_broken": [False, True, False],
                "first_bounce_delay": [1, np.nan, 3],
                "maximum_favorable_excursion": [0.08, 0.01, 0.04],
                "maximum_adverse_excursion": [-0.02, -0.10, -0.03],
                "final_return": [0.05, -0.08, 0.02],
            }
        )

        metrics = evaluate_outcomes(
            outcomes,
            coverage={
                ("baseline", "software"): {
                    "eligible_count": 3,
                    "unavailable_count": 2,
                }
            },
        )

        row = metrics.iloc[0]
        self.assertAlmostEqual(row["support_hold_rate"], 2 / 3)
        self.assertAlmostEqual(row["support_break_rate"], 1 / 3)
        self.assertEqual(row["first_bounce_delay"], 2.0)
        self.assertAlmostEqual(row["maximum_adverse_excursion"], -0.05)
        self.assertAlmostEqual(row["max_adverse_excursion"], -0.05)
        self.assertEqual(row["eligible_count"], 3)
        self.assertEqual(row["unavailable_count"], 2)

    def test_variant_signals_include_frozen_ablation_set(self):
        index = pd.bdate_range("2026-01-02", periods=2)
        history = make_ohlcv([100.0, 100.0], start="2026-01-02")
        baseline = pd.DataFrame(
            {
                "near_support_lower": [95.0, 95.0],
                "near_support_upper": [97.0, 97.0],
                "near_support_distance_pct": [3.09, 3.09],
                "near_support_score": [55.0, 55.0],
                "near_support_state": ["above", "above"],
            },
            index=index,
        )
        historical = pd.DataFrame(
            {
                "historical_demand_support_state": ["testing", "testing"],
                "historical_demand_support_lower": [96.0, 96.0],
                "historical_demand_support_upper": [98.0, 98.0],
                "historical_demand_support_score": [40.0, 20.0],
                "historical_demand_support_age_sessions": [0, 40],
                "historical_demand_support_retest_count": [1, 0],
            },
            index=index,
        )
        no_volume = historical.copy(deep=True)
        no_volume.loc[:, "historical_demand_support_state"] = "unavailable"

        signals = build_variant_signal_rows(
            history,
            baseline,
            historical,
            no_volume_historical=no_volume,
            no_environment_historical=historical,
            minimum_score=30.0,
        )

        self.assertEqual(
            set(signals),
            {
                "baseline",
                "baseline_plus_historical_demand",
                "historical_demand_only",
                "no_volume",
                "no_retests",
                "no_environment",
                "no_decay",
            },
        )
        self.assertFalse(signals["historical_demand_only"].iloc[1]["eligible"])
        self.assertTrue(signals["no_decay"].iloc[1]["eligible"])

    def test_pocket_pivot_uses_only_prior_down_volume(self):
        closes = [
            100.0,
            99.0,
            100.0,
            98.0,
            99.0,
            100.0,
            101.0,
            100.0,
            101.0,
            102.0,
            103.0,
            104.0,
        ]
        history = make_ohlcv(closes)
        history["Volume"] = [
            100,
            200,
            100,
            300,
            100,
            100,
            100,
            250,
            100,
            100,
            100,
            350,
        ]

        rows = build_pocket_pivot_rows(history)

        self.assertFalse(rows[10]["pocket_pivot"])
        self.assertTrue(rows[11]["pocket_pivot"])

    def test_non_overlapping_outcomes_use_independent_horizon_steps(self):
        rows = pd.DataFrame(
            {
                "ticker": ["AAA"] * 8 + ["BBB"] * 8,
                "variant": ["baseline"] * 16,
                "horizon": [3] * 16,
                "fold": [1] * 16,
                "observation_date": list(
                    pd.bdate_range("2026-01-02", periods=8)
                )
                * 2,
            }
        )

        selected = non_overlapping_outcomes(rows)

        self.assertEqual(len(selected), 6)
        self.assertEqual(
            selected.groupby("ticker").size().to_dict(),
            {"AAA": 3, "BBB": 3},
        )

    def test_promotion_uses_frozen_ten_day_overlapping_all_regime_slice(self):
        base = metric_rows()
        selected = base.assign(
            horizon=10,
            regime="all",
            sample_mode="overlapping",
        )
        distractor = base.assign(
            horizon=5,
            regime="correction",
            sample_mode="non_overlapping",
            support_hold_rate=0.0,
        )

        decision = promotion_decision(
            pd.concat((selected, distractor), ignore_index=True),
            causal_audit_passed=True,
        )

        self.assertTrue(decision["eligible"])

    def test_report_records_advisory_authority_and_gate_reasons(self):
        report = render_report(
            pd.DataFrame(
                [
                    {
                        "variant": "baseline",
                        "horizon": 5,
                        "fold": 1,
                        "group": "software",
                        "regime": "correction",
                        "sample_count": 10,
                        "support_hold_rate": 0.5,
                        "support_break_rate": 0.5,
                        "maximum_favorable_excursion": 0.03,
                        "maximum_adverse_excursion": -0.08,
                    }
                ]
            ),
            {
                "asof": "2026-07-24",
                "ticker_count": 240,
                "decision": {
                    "eligible": False,
                    "reasons": ["stable_fold_wins_below_three"],
                    "authority": "advisory_only",
                },
            },
        )

        self.assertIn("历史需求支撑区样本外消融", report)
        self.assertIn("2026-07-24", report)
        self.assertIn("advisory_only", report)
        self.assertIn("stable_fold_wins_below_three", report)

    def test_promotion_requires_stable_fold_group_and_ablation_gains(self):
        decision = promotion_decision(
            metric_rows(),
            causal_audit_passed=True,
        )

        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["stable_fold_wins"], 5)
        self.assertEqual(decision["improved_group_count"], 2)

    def test_promotion_fails_closed_when_drawdown_is_worse(self):
        decision = promotion_decision(
            metric_rows(adverse_delta=-0.01),
            causal_audit_passed=True,
        )

        self.assertFalse(decision["eligible"])
        self.assertIn("max_adverse_excursion_worse", decision["reasons"])

    def test_promotion_fails_closed_when_audit_is_missing(self):
        decision = promotion_decision(
            metric_rows(),
            causal_audit_passed=False,
        )

        self.assertFalse(decision["eligible"])
        self.assertIn("causal_audit_failed", decision["reasons"])


if __name__ == "__main__":
    unittest.main()
