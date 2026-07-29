from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.run_support_touch_reaction_study import (
    assign_reaction_folds,
    evaluate_touch_reactions,
    select_touch_reaction_cohorts,
    support_reaction_decision,
)


def _groups(count: int) -> dict[str, str]:
    result = {
        "MU": "semiconductor",
        "NBIS": "software",
        "MRVL": "semiconductor",
    }
    labels = ("semiconductor", "software", "other")
    for position in range(count):
        result[f"T{position:04d}"] = labels[position % len(labels)]
    return result


def _metric_row(
    *,
    variant: str,
    date: str,
    label: str,
    touch_type: str | None = "intersection",
    reclaim_delay: float | None = None,
    rebound: float | None = None,
    penetration: float | None = None,
) -> dict[str, object]:
    touched = label != "not_touched"
    return {
        "cohort": "development",
        "ticker": "AAA",
        "observation_date": pd.Timestamp(date),
        "variant": variant,
        "waiting_horizon": 5,
        "fold": 1,
        "group": "software",
        "regime": "correction",
        "distance_bin": "1.0_2.0_atr",
        "touch_status": "touched" if touched else "not_touched",
        "touch_type": touch_type if touched else None,
        "accepted": label == "accepted",
        "failed": label == "failed",
        "ambiguous": label == "ambiguous",
        "reclaim_delay_sessions": reclaim_delay,
        "maximum_rebound_atr": rebound,
        "maximum_penetration_atr": penetration,
        "close_change_from_touch": 0.01 if touched else np.nan,
        "touch_volume_ratio": 1.2 if touched else np.nan,
    }


class TouchReactionCohortTest(unittest.TestCase):
    def test_cohorts_are_deterministic_disjoint_and_focus_preserving(self):
        first = select_touch_reaction_cohorts(_groups(700))
        second = select_touch_reaction_cohorts(_groups(700))

        self.assertEqual(first, second)
        self.assertEqual(len(first["development"]), 240)
        self.assertEqual(len(first["confirmation"]), 240)
        self.assertEqual(len(set(first["development"])), 240)
        self.assertEqual(len(set(first["confirmation"])), 240)
        self.assertTrue(
            set(first["development"]).isdisjoint(first["confirmation"])
        )
        self.assertIn("MU", first["development"])

    def test_confirmation_cohort_gracefully_uses_remaining_tickers(self):
        cohorts = select_touch_reaction_cohorts(
            _groups(260),
            cohort_size=240,
        )

        self.assertEqual(len(cohorts["development"]), 240)
        self.assertEqual(len(cohorts["confirmation"]), 23)
        self.assertTrue(
            set(cohorts["development"]).isdisjoint(cohorts["confirmation"])
        )


class TouchReactionMetricTest(unittest.TestCase):
    def test_whole_observation_dates_receive_one_chronological_fold(self):
        rows = pd.DataFrame(
            {
                "observation_date": np.repeat(
                    pd.bdate_range("2026-01-02", periods=10),
                    2,
                )
            }
        )

        folded = assign_reaction_folds(rows, n_folds=3)

        self.assertEqual(
            folded.groupby("observation_date")["fold"].nunique().max(),
            1,
        )
        ordered = folded.groupby("fold")["observation_date"].agg(["min", "max"])
        self.assertTrue(
            all(
                ordered.iloc[position]["max"]
                < ordered.iloc[position + 1]["min"]
                for position in range(len(ordered) - 1)
            )
        )

    def test_metrics_exclude_not_touched_from_reaction_rate_denominators(self):
        rows = pd.DataFrame(
            [
                _metric_row(
                    variant="baseline",
                    date="2026-01-02",
                    label="not_touched",
                ),
                _metric_row(
                    variant="baseline",
                    date="2026-01-05",
                    label="accepted",
                    reclaim_delay=1.0,
                    rebound=1.0,
                    penetration=0.2,
                ),
                _metric_row(
                    variant="baseline",
                    date="2026-01-06",
                    label="failed",
                    touch_type="gap_through",
                    rebound=3.0,
                    penetration=1.0,
                ),
            ]
        )

        metrics = evaluate_touch_reactions(rows)
        selected = metrics.loc[
            (metrics["comparison_scope"] == "all_eligible")
            & (metrics["group"] == "all")
            & (metrics["regime"] == "all")
            & (metrics["distance_bin"] == "all")
        ].iloc[0]

        self.assertEqual(selected["event_count"], 3)
        self.assertEqual(selected["touch_count"], 2)
        self.assertAlmostEqual(selected["touch_rate"], 2 / 3)
        self.assertAlmostEqual(selected["gap_through_rate"], 0.5)
        self.assertAlmostEqual(selected["accepted_rate"], 0.5)
        self.assertAlmostEqual(selected["failed_rate"], 0.5)
        self.assertAlmostEqual(selected["ambiguous_rate"], 0.0)
        self.assertAlmostEqual(selected["mean_reclaim_delay"], 1.0)
        self.assertAlmostEqual(selected["mean_maximum_rebound_atr"], 2.0)
        self.assertAlmostEqual(
            selected["mean_maximum_penetration_atr"],
            0.6,
        )

    def test_paired_scope_keeps_only_common_baseline_challenger_keys(self):
        rows = pd.DataFrame(
            [
                _metric_row(
                    variant="baseline",
                    date="2026-01-02",
                    label="accepted",
                ),
                _metric_row(
                    variant="baseline",
                    date="2026-01-05",
                    label="failed",
                ),
                _metric_row(
                    variant="baseline_plus_historical_demand",
                    date="2026-01-02",
                    label="accepted",
                ),
                _metric_row(
                    variant="baseline_plus_historical_demand",
                    date="2026-01-06",
                    label="accepted",
                ),
            ]
        )

        metrics = evaluate_touch_reactions(rows)
        paired = metrics.loc[
            (metrics["comparison_scope"] == "paired")
            & (metrics["group"] == "all")
            & (metrics["regime"] == "all")
            & (metrics["distance_bin"] == "all")
        ]

        self.assertEqual(set(paired["variant"]), {
            "baseline",
            "baseline_plus_historical_demand",
        })
        self.assertEqual(set(paired["event_count"]), {1})


class TouchReactionDecisionTest(unittest.TestCase):
    def test_gate_never_promotes_when_group_audit_failed(self):
        decision = support_reaction_decision(
            pd.DataFrame(),
            causal_audit_passed=False,
            future_holdout_passed=True,
        )

        self.assertFalse(decision["eligible"])
        self.assertEqual(decision["authority"], "advisory_only")
        self.assertIn("causal_audit_failed", decision["reasons"])

    def test_preregistered_metrics_do_not_gain_online_authority(self):
        decision = support_reaction_decision(
            pd.DataFrame(),
            causal_audit_passed=True,
            future_holdout_passed=False,
        )

        self.assertFalse(decision["eligible"])
        self.assertEqual(decision["authority"], "advisory_only")
        self.assertIn("future_holdout_required", decision["reasons"])
        self.assertNotIn("online_authority", decision)


if __name__ == "__main__":
    unittest.main()
