from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.run_support_touch_reaction_study import (
    ABLATION_VARIANTS,
    assign_reaction_folds,
    evaluate_touch_reactions,
    latest_point_in_time_groups,
    render_support_touch_reaction_report,
    run_support_touch_reaction_study,
    select_touch_reaction_cohorts,
    support_reaction_decision,
    write_study_outputs,
)
from tests.helpers import make_ohlcv


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
    def test_latest_point_in_time_groups_match_previous_cohort_basis(self):
        intervals = pd.DataFrame(
            [
                {
                    "ticker": "AAA",
                    "effective_from": "2025-01-01",
                    "effective_to": None,
                    "group": "software",
                }
            ]
        )

        groups = latest_point_in_time_groups(
            {"AAA": "other", "BBB": "semiconductor"},
            intervals,
            asof="2026-07-24",
        )

        self.assertEqual(
            groups,
            {"AAA": "software", "BBB": "semiconductor"},
        )

    def test_small_smoke_cohorts_keep_named_focus_without_failing(self):
        groups = _groups(20)
        groups.update(
            {
                "AMD": "semiconductor",
                "INTC": "semiconductor",
                "ADBE": "software",
            }
        )
        cohorts = select_touch_reaction_cohorts(
            groups,
            cohort_size=3,
        )

        self.assertEqual(len(cohorts["development"]), 3)
        self.assertEqual(len(cohorts["confirmation"]), 3)
        self.assertIn("MU", cohorts["development"])
        self.assertTrue(
            set(cohorts["development"]).isdisjoint(cohorts["confirmation"])
        )

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

    def test_paired_distance_slices_use_the_baseline_bin_for_both_variants(self):
        baseline = _metric_row(
            variant="baseline",
            date="2026-01-02",
            label="accepted",
        )
        challenger = _metric_row(
            variant="baseline_plus_historical_demand",
            date="2026-01-02",
            label="accepted",
        )
        baseline["distance_bin"] = "0.5_1.0_atr"
        challenger["distance_bin"] = "2.0_3.5_atr"

        metrics = evaluate_touch_reactions(
            pd.DataFrame([baseline, challenger])
        )
        paired = metrics.loc[
            metrics["comparison_scope"].eq("paired")
            & metrics["group"].eq("all")
            & metrics["regime"].eq("all")
            & metrics["distance_bin"].ne("all")
        ]

        self.assertEqual(set(paired["distance_bin"]), {"0.5_1.0_atr"})
        self.assertEqual(set(paired["event_count"]), {1})
        self.assertEqual(
            set(paired["variant"]),
            {"baseline", "baseline_plus_historical_demand"},
        )


class TouchReactionDecisionTest(unittest.TestCase):
    def test_missing_distance_bin_blocks_consistency_condition(self):
        metrics = _preregistered_metric_rows()
        metrics = metrics.loc[
            metrics["distance_bin"].ne("2.0_3.5_atr")
        ].copy()

        decision = support_reaction_decision(
            metrics,
            causal_audit_passed=True,
            future_holdout_passed=True,
        )

        evidence = decision["performance_conditions"]
        self.assertEqual(evidence["distance_bin_count"], 3)
        self.assertFalse(
            evidence["conditions"]["distance_direction_consistent"]
        )
        self.assertFalse(decision["eligible"])

    def test_decision_records_preregistered_performance_conditions(self):
        decision = support_reaction_decision(
            _preregistered_metric_rows(),
            causal_audit_passed=True,
            future_holdout_passed=True,
        )

        evidence = decision["performance_conditions"]
        self.assertAlmostEqual(evidence["acceptance_rate_delta"], 0.03)
        self.assertAlmostEqual(evidence["failure_rate_delta"], -0.01)
        self.assertAlmostEqual(evidence["maximum_penetration_atr_delta"], -0.05)
        self.assertEqual(evidence["stable_fold_wins"], 5)
        self.assertEqual(evidence["improved_group_count"], 2)
        self.assertEqual(evidence["consistent_distance_bins"], 4)
        self.assertEqual(evidence["distance_bin_count"], 4)
        self.assertTrue(all(evidence["conditions"].values()))
        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["authority"], "advisory_only")

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


class TouchReactionRunnerTest(unittest.TestCase):
    def test_synthetic_runner_emits_both_cohorts_and_typed_coverage(self):
        tickers = ("AAA", "BBB", "CCC", "DDD")
        histories = {
            ticker: make_ohlcv(
                [100.0] * 90,
                highs=[101.0] * 90,
                lows=[99.0] * 90,
                start="2025-01-02",
            )
            for ticker in tickers
        }
        histories["QQQ"] = histories["AAA"].copy()
        groups = {
            "AAA": "semiconductor",
            "BBB": "software",
            "CCC": "semiconductor",
            "DDD": "software",
        }
        intervals = pd.DataFrame(
            [
                {
                    "ticker": ticker,
                    "effective_from": "2020-01-01",
                    "effective_to": None,
                    "group": group,
                    "source": "historical_backfill_assumption",
                    "observed_at": "2026-07-24",
                }
                for ticker, group in groups.items()
            ]
        )

        metrics, outcomes, manifest = run_support_touch_reaction_study(
            histories,
            cohorts={
                "development": ("AAA", "BBB"),
                "confirmation": ("CCC", "DDD"),
            },
            fallback_groups=groups,
            group_intervals=intervals,
            asof="2026-07-24",
            start="2025-01-01",
            n_folds=5,
            minimum_sessions=30,
            signal_builder=_synthetic_signal_builder,
        )

        self.assertEqual(set(outcomes["cohort"]), {
            "development",
            "confirmation",
        })
        self.assertEqual(set(outcomes["waiting_horizon"]), {5, 10, 20})
        self.assertEqual(set(outcomes["fold"]), {1, 2, 3, 4, 5})
        coverage = metrics.loc[metrics["row_type"].eq("coverage")]
        expected = 2 * len(ABLATION_VARIANTS) * 3
        self.assertEqual(len(coverage), expected)
        no_volume = coverage.loc[coverage["variant"].eq("no_volume")]
        self.assertTrue(no_volume["event_count"].eq(0).all())
        self.assertTrue(
            no_volume["status"].eq("unavailable_no_events").all()
        )
        self.assertFalse(manifest["decision"]["eligible"])
        self.assertEqual(
            manifest["decision"]["authority"],
            "advisory_only",
        )
        self.assertNotIn("online_authority", manifest)
        self.assertEqual(
            set(manifest["cohorts"]),
            {"development", "confirmation"},
        )
        self.assertEqual(manifest["reaction_labels"]["touched"], 360)
        self.assertEqual(manifest["reaction_labels"]["accepted"], 360)
        self.assertEqual(manifest["reaction_labels"]["failed"], 0)
        self.assertEqual(manifest["reaction_labels"]["ambiguous"], 0)

        report = render_support_touch_reaction_report(metrics, manifest)
        self.assertIn("# 支撑区首触反应研究", report)
        self.assertIn("预注册性能条件", report)
        self.assertIn("承接率增量", report)
        self.assertIn("零事件变体", report)
        self.assertIn("no_volume", report)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_study_outputs(
                metrics,
                manifest,
                report_path=root / "study.md",
                metrics_path=root / "study.csv",
                manifest_path=root / "study.json",
            )
            self.assertEqual(
                json.loads((root / "study.json").read_text("utf-8")),
                manifest,
            )
            self.assertEqual(
                pd.read_csv(root / "study.csv")["row_type"].notna().all(),
                True,
            )


def _synthetic_signal_builder(
    history: pd.DataFrame,
    **_,
) -> dict[str, pd.DataFrame]:
    result = {}
    active_positions = (20, 30, 40, 50, 60)
    for variant in ABLATION_VARIANTS:
        signals = pd.DataFrame(
            {
                "variant": [variant] * len(history),
                "eligible": [False] * len(history),
                "zone_lower": [98.0] * len(history),
                "zone_upper": [99.5] * len(history),
            },
            index=history.index,
        )
        if variant != "no_volume":
            signals.loc[history.index[list(active_positions)], "eligible"] = True
        result[variant] = signals
    return result


def _preregistered_metric_rows() -> pd.DataFrame:
    rows = []

    def add(
        variant: str,
        *,
        fold: int,
        group: str,
        distance: str,
        accepted: float,
        failed: float,
        penetration: float,
    ) -> None:
        rows.append(
            {
                "cohort": "confirmation",
                "comparison_scope": "paired",
                "variant": variant,
                "waiting_horizon": 10,
                "fold": fold,
                "group": group,
                "regime": "all",
                "distance_bin": distance,
                "event_count": 120,
                "touch_count": 100,
                "accepted_rate": accepted,
                "failed_rate": failed,
                "mean_maximum_penetration_atr": penetration,
            }
        )

    for fold in range(1, 6):
        add(
            "baseline",
            fold=fold,
            group="all",
            distance="all",
            accepted=0.50,
            failed=0.40,
            penetration=0.80,
        )
        add(
            "baseline_plus_historical_demand",
            fold=fold,
            group="all",
            distance="all",
            accepted=0.53,
            failed=0.39,
            penetration=0.75,
        )
    for group, increment in (
        ("semiconductor", 0.03),
        ("software", 0.02),
        ("other", -0.01),
    ):
        add(
            "baseline",
            fold=1,
            group=group,
            distance="all",
            accepted=0.50,
            failed=0.40,
            penetration=0.80,
        )
        add(
            "baseline_plus_historical_demand",
            fold=1,
            group=group,
            distance="all",
            accepted=0.50 + increment,
            failed=0.39,
            penetration=0.75,
        )
    for distance in (
        "0_0.5_atr",
        "0.5_1.0_atr",
        "1.0_2.0_atr",
        "2.0_3.5_atr",
    ):
        add(
            "baseline",
            fold=1,
            group="all",
            distance=distance,
            accepted=0.50,
            failed=0.40,
            penetration=0.80,
        )
        add(
            "baseline_plus_historical_demand",
            fold=1,
            group="all",
            distance=distance,
            accepted=0.51,
            failed=0.39,
            penetration=0.75,
        )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    unittest.main()
