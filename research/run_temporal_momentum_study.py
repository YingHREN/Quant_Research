"""Evaluate recency-weighted momentum without changing production forecasts."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path

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
from web.services.market_data import MarketDataRepository


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


def temporal_promotion_decision(
    metrics: pd.DataFrame,
    *,
    diagnostics: pd.DataFrame | None = None,
) -> dict[str, object]:
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

    primary_metrics = (
        metrics.loc[metrics["evaluation_mode"] == "overlapping"]
        if "evaluation_mode" in metrics
        else metrics
    )
    aggregate = primary_metrics.loc[
        (primary_metrics["scope"] == "all")
        & (primary_metrics["horizon"] == 5)
        & (primary_metrics["fold"] == 0)
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
        predicted_down_return = challenger.get(
            "mean_return_predicted_down",
            float("nan"),
        )
        predicted_up_return = challenger.get(
            "mean_return_predicted_up",
            float("nan"),
        )
        if (
            pd.isna(predicted_down_return)
            or float(predicted_down_return) >= 0.0
        ):
            reasons.append("predicted_down_return_not_negative")
        if (
            pd.isna(predicted_up_return)
            or float(predicted_down_return) >= float(predicted_up_return)
        ):
            reasons.append("predicted_class_returns_not_ordered")

        subgroup = primary_metrics.loc[
            (primary_metrics["scope"] == "semiconductor_ai")
            & (primary_metrics["horizon"] == 5)
            & (primary_metrics["fold"] == 0)
            & primary_metrics["specification"].isin(
                ("ridge_current", candidate)
            )
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

        fold_rows = primary_metrics.loc[
            (primary_metrics["scope"] == "all")
            & (primary_metrics["horizon"] == 5)
            & (primary_metrics["fold"] > 0)
            & primary_metrics["specification"].isin(
                ("ridge_current", candidate)
            )
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
        if "evaluation_mode" in metrics:
            non_overlapping = metrics.loc[
                (metrics["evaluation_mode"] == "non_overlapping")
                & (metrics["scope"] == "all")
                & (metrics["horizon"] == 5)
                & (metrics["fold"] == 0)
                & metrics["specification"].isin(
                    ("majority_baseline", "ridge_current", candidate)
                )
            ].set_index("specification")
            if any(
                name not in non_overlapping.index
                for name in (
                    "majority_baseline",
                    "ridge_current",
                    candidate,
                )
            ):
                reasons.append("non_overlapping_comparator_missing")
            elif non_overlapping.loc[
                candidate,
                "balanced_accuracy",
            ] <= max(
                non_overlapping.loc[
                    "ridge_current",
                    "balanced_accuracy",
                ],
                non_overlapping.loc[
                    "majority_baseline",
                    "balanced_accuracy",
                ],
            ):
                reasons.append("non_overlapping_accuracy_not_improved")
        reasons.extend(
            _diagnostic_reasons(
                diagnostics,
                candidate,
            )
        )
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


def render_temporal_report(
    metrics: pd.DataFrame,
    decision: Mapping[str, object],
    *,
    diagnostics: pd.DataFrame,
    latest_date: str,
    ticker_count: int,
) -> str:
    """Render the offline experiment and its fixed promotion result."""
    status = "PROMOTE" if decision.get("eligible") else "DO NOT PROMOTE"
    lines = [
        "# Recency-weighted momentum ablation",
        "",
        f"- Data through: {latest_date}",
        f"- Local universe: {ticker_count} tickers",
        "- Status: offline research challenger; production forecasts are unchanged.",
        "- Execution label: enter at the next-session open and exit at the "
        "fifth future session close.",
        "- Validation: expanding chronological folds with exact label-end purging.",
        "",
        "## Promotion decision",
        "",
        f"**{status}** — {decision.get('reason', 'missing_reason')}",
        "",
        "## Full-universe and semiconductor_ai metrics",
        "",
        _markdown_table(metrics),
        "",
        "## MU and NBIS diagnostics",
        "",
        _markdown_table(diagnostics),
        "",
        "## Interpretation",
        "",
        "The decay-only, volume-confirmed, and market-confirmed specifications "
        "use identical executable observations and folds. A candidate must "
        "improve aggregate balanced accuracy and macro F1, preserve downside "
        "recall, retain the improvement on non-overlapping outcomes, win a "
        "majority of eligible folds, and avoid material semiconductor "
        "degradation. Predicted-down returns must be negative and ordered below "
        "predicted-up returns. Named event dates are diagnostics only and a "
        "candidate must correct at least one known false-bull case without "
        "adding another.",
        "",
    ]
    return "\n".join(lines)


def run_study(
    histories: Mapping[str, pd.DataFrame],
    *,
    n_folds: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run full-universe and semiconductor temporal momentum ablations."""
    frame = build_temporal_research_frame(histories)
    all_metrics, all_predictions = evaluate_temporal_scope(
        frame,
        scope="all",
        n_folds=n_folds,
        minimum_samples=1_000,
    )
    group = market_group("semiconductor")
    focus = frozenset((*group.constituent_tickers, *group.related_tickers))
    subgroup = frame.loc[
        frame.index.get_level_values("ticker").isin(focus)
    ]
    subgroup_metrics, subgroup_predictions = evaluate_temporal_scope(
        subgroup,
        scope="semiconductor_ai",
        n_folds=n_folds,
        minimum_samples=200,
    )
    metrics = pd.concat(
        (all_metrics, subgroup_metrics),
        ignore_index=True,
    )
    predictions = pd.concat(
        (all_predictions, subgroup_predictions),
        ignore_index=True,
    )
    diagnostic_dates = {
        "MU": (
            pd.Timestamp("2026-06-25"),
            pd.Timestamp("2026-07-01"),
        ),
        "NBIS": (
            pd.Timestamp("2026-07-01"),
            pd.Timestamp("2026-07-17"),
        ),
    }
    diagnostic_rows = []
    for ticker, dates in diagnostic_dates.items():
        diagnostic_rows.append(
            predictions.loc[
                (predictions["scope"] == "all")
                & (predictions["ticker"] == ticker)
                & predictions["observation_date"].isin(dates)
            ].copy()
        )
    diagnostics = (
        pd.concat(diagnostic_rows, ignore_index=True)
        if diagnostic_rows
        else pd.DataFrame()
    )
    return metrics, predictions, diagnostics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/prices.db")
    parser.add_argument(
        "--report",
        default="docs/research/temporal-momentum-ablation-2026-07-25.md",
    )
    parser.add_argument(
        "--metrics",
        default="docs/research/temporal-momentum-ablation-2026-07-25.csv",
    )
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args(argv)

    histories = MarketDataRepository(args.database).load_universe_histories()
    metrics, _, diagnostics = run_study(histories, n_folds=args.folds)
    decision = temporal_promotion_decision(
        metrics,
        diagnostics=diagnostics,
    )
    latest = max(frame.index.max() for frame in histories.values())
    report = render_temporal_report(
        metrics,
        decision,
        diagnostics=diagnostics,
        latest_date=pd.Timestamp(latest).date().isoformat(),
        ticker_count=len(histories),
    )
    report_path = Path(args.report)
    metrics_path = Path(args.metrics)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    metrics.to_csv(metrics_path, index=False)
    print(report)
    return 0


def _aggregate_and_fold_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = [_metric_rows(predictions, "overlapping")]
    unique_dates = pd.DatetimeIndex(
        sorted(pd.to_datetime(predictions["observation_date"]).unique())
    )
    selected_dates = frozenset(unique_dates[::5])
    non_overlapping = predictions.loc[
        pd.to_datetime(predictions["observation_date"]).isin(selected_dates)
    ]
    if not non_overlapping.empty:
        rows.append(_metric_rows(non_overlapping, "non_overlapping"))
    return pd.concat(rows, ignore_index=True)


def _metric_rows(
    predictions: pd.DataFrame,
    evaluation_mode: str,
) -> pd.DataFrame:
    aggregate = evaluate_direction_ablation(predictions)
    aggregate.insert(0, "fold", 0)
    aggregate.insert(0, "evaluation_mode", evaluation_mode)
    rows = [aggregate]
    for fold, group in predictions.groupby("fold", sort=True):
        metrics = evaluate_direction_ablation(group)
        metrics.insert(0, "fold", int(fold))
        metrics.insert(0, "evaluation_mode", evaluation_mode)
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


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No mature observations._"
    display = frame.copy()
    for column in display.select_dtypes(include="number"):
        display[column] = display[column].map(
            lambda value: "" if pd.isna(value) else f"{value:.4f}"
        )
    columns = list(display.columns)
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for values in display.astype(str).itertuples(index=False, name=None):
        lines.append(
            "| "
            + " | ".join(value.replace("|", "\\|") for value in values)
            + " |"
        )
    return "\n".join(lines)


def _diagnostic_reasons(
    diagnostics: pd.DataFrame | None,
    candidate: str,
) -> list[str]:
    if diagnostics is None or diagnostics.empty:
        return []
    required = {
        "ticker",
        "observation_date",
        "specification",
        "actual_direction",
        "predicted_direction",
    }
    if not required.issubset(diagnostics.columns):
        return ["diagnostic_columns_missing"]
    selected = diagnostics.loc[
        diagnostics["specification"].isin(("ridge_current", candidate))
        & (diagnostics["actual_direction"] == "down")
    ]
    if selected.empty:
        return ["known_false_bull_diagnostics_missing"]
    table = selected.pivot_table(
        index=("ticker", "observation_date"),
        columns="specification",
        values="predicted_direction",
        aggfunc="first",
    ).dropna()
    if "ridge_current" not in table or candidate not in table:
        return ["known_false_bull_comparator_missing"]
    current_false = table["ridge_current"] != "down"
    candidate_false = table[candidate] != "down"
    corrections = int((current_false & ~candidate_false).sum())
    if corrections < 1:
        return ["known_false_bull_not_corrected"]
    if int(candidate_false.sum()) > int(current_false.sum()):
        return ["known_false_bull_count_worsened"]
    return []


if __name__ == "__main__":
    raise SystemExit(main())
