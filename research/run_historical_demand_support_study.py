"""Frozen evaluation helpers for historical demand-support research.

The dashboard consumes this model only as advisory evidence.  These helpers
make that boundary explicit: promotion is fail-closed and never changes the
online forecast policy by itself.
"""

from __future__ import annotations

import math

import pandas as pd


BASELINE = "baseline"
CHALLENGER = "baseline_plus_historical_demand"
REQUIRED_METRIC_COLUMNS = frozenset(
    {
        "variant",
        "fold",
        "group",
        "support_hold_rate",
        "max_adverse_excursion",
        "sample_count",
    }
)


def promotion_decision(
    metrics: pd.DataFrame,
    *,
    causal_audit_passed: bool,
) -> dict[str, object]:
    """Apply the written research gate to paired, frozen fold metrics.

    A pass records research evidence only.  It is deliberately not an online
    authority grant; Ridge and the final decision policy remain unchanged.
    """
    _validate_metrics(metrics)
    baseline, challenger = _paired_metrics(metrics)
    if baseline.empty or challenger.empty:
        return _blocked("insufficient_paired_metrics")

    paired = baseline.merge(
        challenger,
        on=["fold", "group"],
        how="inner",
        suffixes=("_baseline", "_challenger"),
        validate="one_to_one",
    )
    if paired.empty:
        return _blocked("insufficient_paired_metrics")
    paired["hold_increment"] = (
        paired["support_hold_rate_challenger"]
        - paired["support_hold_rate_baseline"]
    )
    paired["mae_not_worse"] = (
        paired["max_adverse_excursion_challenger"]
        >= paired["max_adverse_excursion_baseline"]
    )
    fold_summary = paired.groupby("fold", sort=True).agg(
        hold_increment=("hold_increment", "mean"),
        mae_not_worse=("mae_not_worse", "all"),
    )
    stable_fold_wins = int(
        (
            (fold_summary["hold_increment"] > 0.0)
            & fold_summary["mae_not_worse"]
        ).sum()
    )
    group_summary = paired.groupby("group", sort=True)["hold_increment"].mean()
    improved_group_count = int((group_summary > 0.0).sum())
    ablation_increment = float(paired["hold_increment"].mean())
    max_adverse_excursion_not_worse = bool(paired["mae_not_worse"].all())
    reasons = []
    if stable_fold_wins < 3:
        reasons.append("stable_fold_wins_below_three")
    if not max_adverse_excursion_not_worse:
        reasons.append("max_adverse_excursion_worse")
    if improved_group_count < 2:
        reasons.append("improved_group_count_below_two")
    if ablation_increment <= 0.0:
        reasons.append("ablation_increment_not_positive")
    if not causal_audit_passed:
        reasons.append("causal_audit_failed")
    return {
        "eligible": not reasons,
        "stable_fold_wins": stable_fold_wins,
        "max_adverse_excursion_not_worse": max_adverse_excursion_not_worse,
        "improved_group_count": improved_group_count,
        "ablation_increment": ablation_increment,
        "causal_audit_passed": bool(causal_audit_passed),
        "reasons": reasons,
        "authority": "advisory_only",
    }


def _paired_metrics(metrics: pd.DataFrame):
    selected = metrics.loc[
        metrics["variant"].isin((BASELINE, CHALLENGER))
    ].copy()
    for column in (
        "support_hold_rate",
        "max_adverse_excursion",
        "sample_count",
    ):
        selected[column] = pd.to_numeric(selected[column], errors="coerce")
    selected = selected.loc[
        selected[[
            "support_hold_rate", "max_adverse_excursion", "sample_count",
        ]].notna().all(axis=1)
        & (selected["sample_count"] > 0)
    ]
    keys = ["variant", "fold", "group"]
    if selected.duplicated(keys).any():
        raise ValueError("metrics contain duplicate paired rows")
    return (
        selected.loc[selected["variant"] == BASELINE],
        selected.loc[selected["variant"] == CHALLENGER],
    )


def _validate_metrics(metrics):
    if not isinstance(metrics, pd.DataFrame):
        raise TypeError("metrics must be a DataFrame")
    missing = sorted(REQUIRED_METRIC_COLUMNS.difference(metrics.columns))
    if missing:
        raise ValueError(f"metrics are missing required columns: {missing}")


def _blocked(reason: str) -> dict[str, object]:
    return {
        "eligible": False,
        "stable_fold_wins": 0,
        "max_adverse_excursion_not_worse": False,
        "improved_group_count": 0,
        "ablation_increment": math.nan,
        "causal_audit_passed": False,
        "reasons": [reason],
        "authority": "advisory_only",
    }
