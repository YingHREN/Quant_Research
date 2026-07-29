"""Frozen evaluation helpers for support first-touch reactions."""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from research.run_expanded_walkforward_study import select_analysis_tickers
from research.run_historical_demand_support_study import BASELINE, CHALLENGER


def select_touch_reaction_cohorts(
    groups: dict[str, str],
    *,
    cohort_size: int = 240,
    development_seed: int = 20260726,
    confirmation_seed: int = 20260729,
) -> dict[str, tuple[str, ...]]:
    """Return disjoint deterministic development and confirmation cohorts."""
    normalized = {
        str(ticker).strip().upper(): str(group)
        for ticker, group in groups.items()
        if str(ticker).strip()
    }
    if not isinstance(cohort_size, int) or cohort_size <= 0:
        raise ValueError("cohort_size must be a positive integer")
    development = select_analysis_tickers(
        normalized,
        max_tickers=min(cohort_size, len(normalized)),
        seed=development_seed,
    )
    remaining = {
        ticker: group
        for ticker, group in normalized.items()
        if ticker not in set(development)
    }
    confirmation = select_analysis_tickers(
        remaining,
        max_tickers=min(cohort_size, len(remaining)),
        seed=confirmation_seed,
    )
    return {
        "development": tuple(development),
        "confirmation": tuple(confirmation),
    }


def assign_reaction_folds(
    rows: pd.DataFrame,
    *,
    n_folds: int = 5,
) -> pd.DataFrame:
    """Assign whole observation dates to chronological folds."""
    if not isinstance(rows, pd.DataFrame):
        raise TypeError("rows must be a DataFrame")
    if "observation_date" not in rows:
        raise ValueError("rows must contain observation_date")
    if not isinstance(n_folds, int) or n_folds < 2:
        raise ValueError("n_folds must be at least two")
    result = rows.copy(deep=True)
    normalized_dates = pd.to_datetime(
        result["observation_date"],
        errors="raise",
    ).dt.normalize()
    dates = pd.DatetimeIndex(normalized_dates.unique()).sort_values()
    if len(dates) < n_folds:
        raise ValueError("insufficient distinct dates for folds")
    mapping: dict[pd.Timestamp, int] = {}
    for fold, selected in enumerate(np.array_split(dates, n_folds), start=1):
        for date in selected:
            mapping[pd.Timestamp(date)] = fold
    result["fold"] = normalized_dates.map(mapping).astype(int)
    return result


def evaluate_touch_reactions(rows: pd.DataFrame) -> pd.DataFrame:
    """Aggregate event, touch, and reaction metrics."""
    _validate_reaction_rows(rows)
    frames = []
    for comparison_scope, selected in (
        ("all_eligible", rows.copy(deep=True)),
        ("paired", _paired_baseline_challenger(rows)),
    ):
        if selected.empty:
            continue
        for group_all, regime_all, distance_all in itertools.product(
            (False, True),
            repeat=3,
        ):
            scoped = selected.copy(deep=True)
            if group_all:
                scoped["group"] = "all"
            if regime_all:
                scoped["regime"] = "all"
            if distance_all:
                scoped["distance_bin"] = "all"
            metrics = _aggregate_reactions(scoped)
            metrics["comparison_scope"] = comparison_scope
            frames.append(metrics)
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True, sort=False)
        .drop_duplicates()
        .reset_index(drop=True)
    )


def support_reaction_decision(
    metrics: pd.DataFrame,
    *,
    causal_audit_passed: bool,
    future_holdout_passed: bool,
) -> dict[str, object]:
    """Return the fail-closed research-only decision."""
    reasons = []
    if not causal_audit_passed:
        reasons.append("causal_audit_failed")
    if not future_holdout_passed:
        reasons.append("future_holdout_required")
    return {
        "eligible": bool(not reasons),
        "authority": "advisory_only",
        "reasons": reasons,
        "metric_row_count": int(len(metrics)),
    }


def _paired_baseline_challenger(rows: pd.DataFrame) -> pd.DataFrame:
    selected = rows.loc[rows["variant"].isin((BASELINE, CHALLENGER))].copy()
    if selected.empty:
        return selected
    keys = (
        "cohort",
        "ticker",
        "observation_date",
        "waiting_horizon",
        "fold",
    )
    common = (
        selected.groupby(list(keys), sort=False)["variant"].nunique().eq(2)
    )
    common_index = common.loc[common].index
    row_keys = pd.MultiIndex.from_frame(selected.loc[:, keys])
    return selected.loc[row_keys.isin(common_index)].copy()


def _aggregate_reactions(rows: pd.DataFrame) -> pd.DataFrame:
    keys = (
        "cohort",
        "variant",
        "waiting_horizon",
        "fold",
        "group",
        "regime",
        "distance_bin",
    )
    records = []
    for values, group_rows in rows.groupby(list(keys), dropna=False, sort=True):
        touched = group_rows.loc[group_rows["touch_status"].eq("touched")]
        touch_count = len(touched)
        records.append(
            {
                **dict(zip(keys, values)),
                "event_count": int(len(group_rows)),
                "touch_count": int(touch_count),
                "touch_rate": float(touch_count / len(group_rows)),
                "gap_through_rate": _conditional_mean(
                    touched["touch_type"].eq("gap_through")
                ),
                "accepted_rate": _conditional_mean(touched["accepted"]),
                "failed_rate": _conditional_mean(touched["failed"]),
                "ambiguous_rate": _conditional_mean(touched["ambiguous"]),
                "mean_reclaim_delay": _numeric_mean(
                    touched["reclaim_delay_sessions"]
                ),
                "mean_maximum_rebound_atr": _numeric_mean(
                    touched["maximum_rebound_atr"]
                ),
                "mean_maximum_penetration_atr": _numeric_mean(
                    touched["maximum_penetration_atr"]
                ),
                "mean_close_change_from_touch": _numeric_mean(
                    touched["close_change_from_touch"]
                ),
                "mean_touch_volume_ratio": _numeric_mean(
                    touched["touch_volume_ratio"]
                ),
            }
        )
    return pd.DataFrame(records)


def _conditional_mean(values: pd.Series) -> float:
    if values.empty:
        return float("nan")
    return float(values.astype(float).mean())


def _numeric_mean(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce")
    if not numeric.notna().any():
        return float("nan")
    return float(numeric.mean())


def _validate_reaction_rows(rows: pd.DataFrame) -> None:
    if not isinstance(rows, pd.DataFrame):
        raise TypeError("rows must be a DataFrame")
    required = {
        "cohort",
        "ticker",
        "observation_date",
        "variant",
        "waiting_horizon",
        "fold",
        "group",
        "regime",
        "distance_bin",
        "touch_status",
        "touch_type",
        "accepted",
        "failed",
        "ambiguous",
        "reclaim_delay_sessions",
        "maximum_rebound_atr",
        "maximum_penetration_atr",
        "close_change_from_touch",
        "touch_volume_ratio",
    }
    missing = sorted(required.difference(rows.columns))
    if missing:
        raise ValueError(f"rows are missing required columns: {missing}")
