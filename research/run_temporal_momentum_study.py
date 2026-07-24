"""Evaluate recency-weighted momentum without changing production forecasts."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from research.market_direction_model import (
    attach_next_open_targets,
    evaluate_direction_ablation,
    walk_forward_direction_predictions,
    walk_forward_ridge_predictions,
)
from research.temporal_momentum import (
    DECAY_WINDOWS,
    TEMPORAL_CONFIRMATION_COLUMNS,
    TEMPORAL_MARKET_COLUMNS,
    temporal_feature_frame,
)
from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS, build_feature_frame
from web.market_groups import market_group


def temporal_feature_sets() -> dict[str, tuple[str, ...]]:
    """Return fixed, nested Ridge ablations for the temporal experiment."""
    current = tuple(RIDGE_V4_FEATURE_COLUMNS)
    decay = tuple(DECAY_WINDOWS)
    volume = tuple(TEMPORAL_CONFIRMATION_COLUMNS)
    market = tuple(TEMPORAL_MARKET_COLUMNS)
    return {
        "ridge_current": current,
        "ridge_decay_only": current + decay,
        "ridge_decay_volume": current + decay + volume,
        "ridge_decay_market": current + decay + volume + market,
    }


def build_temporal_research_frame(
    histories: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Join production-frozen features to offline temporal challengers."""
    base = build_feature_frame(histories)
    temporal = temporal_feature_frame(
        histories,
        _sector_members(histories),
    )
    joined = base.join(temporal, how="left")
    return attach_next_open_targets(joined, histories, horizons=(5,))


def evaluate_temporal_scope(
    frame: pd.DataFrame,
    *,
    scope: str,
    n_folds: int = 5,
    minimum_samples: int = 1_000,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate fixed temporal ablations on identical purged folds."""
    sets = temporal_feature_sets()
    prediction_frames = []
    for name, columns in sets.items():
        predictions = walk_forward_ridge_predictions(
            frame,
            horizon=5,
            feature_columns=columns,
            n_folds=n_folds,
            minimum_samples=minimum_samples,
        )
        if predictions.empty:
            continue
        predictions["specification"] = name
        prediction_frames.append(predictions)

    direct = walk_forward_direction_predictions(
        frame,
        horizon=5,
        feature_sets={
            "logistic_decay_market": sets["ridge_decay_market"],
        },
        n_folds=n_folds,
        minimum_samples=minimum_samples,
    )
    if not direct.empty:
        prediction_frames.append(direct)
    if not prediction_frames:
        return pd.DataFrame(), pd.DataFrame()

    predictions = pd.concat(prediction_frames, ignore_index=True)
    predictions.insert(0, "scope", scope)
    metrics = _aggregate_and_fold_metrics(predictions)
    metrics.insert(0, "horizon", 5)
    metrics.insert(0, "scope", scope)
    return metrics, predictions


def temporal_promotion_decision(metrics: pd.DataFrame) -> dict[str, object]:
    """Apply the fixed aggregate, subgroup, and fold-stability gate."""
    required = {
        "scope",
        "horizon",
        "fold",
        "specification",
        "balanced_accuracy",
        "macro_f1",
        "down_recall",
    }
    missing = sorted(required.difference(metrics.columns))
    if missing:
        return {"eligible": False, "reason": f"missing_metrics: {missing}"}

    aggregate = metrics.loc[
        (metrics["scope"] == "all")
        & (metrics["horizon"] == 5)
        & (metrics["fold"] == 0)
    ].set_index("specification")
    candidates = [
        name
        for name in (
            "ridge_decay_only",
            "ridge_decay_volume",
            "ridge_decay_market",
            "logistic_decay_market",
        )
        if name in aggregate.index
    ]
    if (
        "ridge_current" not in aggregate.index
        or "majority_baseline" not in aggregate.index
        or not candidates
    ):
        return {"eligible": False, "reason": "missing_primary_comparators"}

    current = aggregate.loc["ridge_current"]
    majority = aggregate.loc["majority_baseline"]
    assessments: dict[str, list[str]] = {}
    for candidate in candidates:
        reasons = []
        challenger = aggregate.loc[candidate]
        if challenger["balanced_accuracy"] <= max(
            current["balanced_accuracy"],
            majority["balanced_accuracy"],
        ):
            reasons.append("aggregate_balanced_accuracy_not_improved")
        if challenger["macro_f1"] <= max(
            current["macro_f1"],
            majority["macro_f1"],
        ):
            reasons.append("aggregate_macro_f1_not_improved")
        if challenger["down_recall"] < current["down_recall"]:
            reasons.append("down_recall_degraded")

        subgroup = metrics.loc[
            (metrics["scope"] == "semiconductor_ai")
            & (metrics["horizon"] == 5)
            & (metrics["fold"] == 0)
            & metrics["specification"].isin(("ridge_current", candidate))
        ].set_index("specification")
        if (
            "ridge_current" not in subgroup.index
            or candidate not in subgroup.index
        ):
            reasons.append("missing_semiconductor_comparator")
        elif (
            subgroup.loc[candidate, "balanced_accuracy"]
            < subgroup.loc["ridge_current", "balanced_accuracy"] - 0.01
        ):
            reasons.append("semiconductor_balanced_accuracy_degraded")

        fold_rows = metrics.loc[
            (metrics["scope"] == "all")
            & (metrics["horizon"] == 5)
            & (metrics["fold"] > 0)
            & metrics["specification"].isin(("ridge_current", candidate))
        ]
        fold_table = fold_rows.pivot(
            index="fold",
            columns="specification",
            values="balanced_accuracy",
        ).dropna()
        if len(fold_table) < 3:
            reasons.append("insufficient_fold_comparisons")
        else:
            wins = int(
                (
                    fold_table[candidate]
                    > fold_table["ridge_current"]
                ).sum()
            )
            if wins <= len(fold_table) // 2:
                reasons.append("fold_majority_not_improved")
        assessments[candidate] = reasons

    eligible = [name for name, reasons in assessments.items() if not reasons]
    selected_pool = eligible or candidates
    selected = max(
        selected_pool,
        key=lambda name: float(aggregate.loc[name, "balanced_accuracy"]),
    )
    if eligible:
        return {
            "eligible": True,
            "selected": selected,
            "reason": f"promotion_gate_passed: {selected}",
        }
    return {
        "eligible": False,
        "selected": selected,
        "reason": f"{selected}: " + ", ".join(assessments[selected]),
    }


def _aggregate_and_fold_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    aggregate = evaluate_direction_ablation(predictions)
    aggregate.insert(0, "fold", 0)
    rows = [aggregate]
    for fold, group in predictions.groupby("fold", sort=True):
        metrics = evaluate_direction_ablation(group)
        metrics.insert(0, "fold", int(fold))
        rows.append(metrics)
    return pd.concat(rows, ignore_index=True)


def _sector_members(
    histories: Mapping[str, pd.DataFrame],
) -> dict[str, str | None]:
    group = market_group("semiconductor")
    sector_tickers = frozenset(
        (*group.constituent_tickers, *group.related_tickers)
    )
    benchmark = next(
        (
            ticker
            for ticker in group.benchmark_tickers
            if ticker in histories
        ),
        None,
    )
    return {
        str(ticker): benchmark if str(ticker) in sector_tickers else None
        for ticker in histories
    }
