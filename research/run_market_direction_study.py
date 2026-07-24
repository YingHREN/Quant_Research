"""Run the fixed market-context direction-model ablation."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from research.market_direction_model import (
    attach_next_open_targets,
    evaluate_direction_ablation,
    walk_forward_boosted_predictions,
    walk_forward_direction_predictions,
    walk_forward_ridge_predictions,
)
from web.forecasts.dataset import FEATURE_COLUMNS, build_feature_frame
from web.market_groups import market_group
from web.services.market_data import MarketDataRepository


QQQ_FEATURES = (
    "qqq_trend_state",
    "qqq_close_vs_ema20_pct",
    "qqq_return_5",
    "qqq_return_20",
    "qqq_volume_ratio",
)
SECTOR_FEATURES = (
    "sector_trend_state",
    "sector_relative_strength_20",
    "stock_sector_relative_strength_20",
)
EARLY_REVERSAL_FEATURES = (
    "early_prior_session_selloff",
    "early_current_price_acceptance",
    "early_descending_trendline_proximity",
    "early_current_volume_support",
)


def study_feature_sets():
    excluded = set(QQQ_FEATURES + SECTOR_FEATURES + EARLY_REVERSAL_FEATURES)
    stock = tuple(column for column in FEATURE_COLUMNS if column not in excluded)
    return {
        "stock_only": stock,
        "stock_qqq": stock + QQQ_FEATURES,
        "stock_qqq_early": stock + QQQ_FEATURES + EARLY_REVERSAL_FEATURES,
        "full_context": (
            stock + QQQ_FEATURES + SECTOR_FEATURES + EARLY_REVERSAL_FEATURES
        ),
    }


def evaluate_scope(
    frame,
    *,
    scope,
    horizons=(5, 20),
    n_folds=5,
    minimum_samples=1_000,
):
    feature_sets = study_feature_sets()
    metric_frames = []
    prediction_frames = []
    for horizon in horizons:
        logistic = walk_forward_direction_predictions(
            frame,
            horizon=horizon,
            feature_sets=feature_sets,
            n_folds=n_folds,
            minimum_samples=minimum_samples,
        )
        ridge = walk_forward_ridge_predictions(
            frame,
            horizon=horizon,
            feature_columns=tuple(FEATURE_COLUMNS),
            n_folds=n_folds,
            minimum_samples=minimum_samples,
        )
        boosted = walk_forward_boosted_predictions(
            frame,
            horizon=horizon,
            feature_columns=feature_sets["full_context"],
            n_folds=n_folds,
            minimum_samples=minimum_samples,
        )
        predictions = pd.concat((logistic, ridge, boosted), ignore_index=True)
        metrics = evaluate_direction_ablation(predictions)
        metrics.insert(0, "horizon", horizon)
        metrics.insert(0, "scope", scope)
        metric_frames.append(metrics)
        predictions.insert(0, "scope", scope)
        prediction_frames.append(predictions)
    return (
        pd.concat(metric_frames, ignore_index=True),
        pd.concat(prediction_frames, ignore_index=True),
    )


def promotion_decision(metrics):
    """Apply the written all-universe production promotion gate."""
    all_rows = metrics.loc[metrics["scope"] == "all"]
    horizons = sorted(all_rows["horizon"].unique())
    if not horizons:
        return {"eligible": False, "reason": "missing_all_universe_metrics"}
    candidates = [
        name
        for name in ("full_context", "boosted_full_context")
        if name in set(all_rows["specification"])
    ]
    if not candidates:
        return {"eligible": False, "reason": "missing_context_challenger"}
    assessments = {}
    for candidate in candidates:
        reasons = []
        for horizon in horizons:
            rows = all_rows.loc[all_rows["horizon"] == horizon].set_index(
                "specification"
            )
            required = ("majority_baseline", "stock_only", candidate)
            if any(name not in rows.index for name in required):
                reasons.append(f"{horizon}d_missing_comparator")
                continue
            challenger = rows.loc[candidate]
            stock = rows.loc["stock_only"]
            majority = rows.loc["majority_baseline"]
            if int(challenger["sample_count"]) < 1_000:
                reasons.append(f"{horizon}d_insufficient_samples")
            if not (
                challenger["balanced_accuracy"]
                > max(stock["balanced_accuracy"], majority["balanced_accuracy"])
            ):
                reasons.append(f"{horizon}d_balanced_accuracy_not_improved")
            if not (
                challenger["macro_f1"]
                > max(stock["macro_f1"], majority["macro_f1"])
            ):
                reasons.append(f"{horizon}d_macro_f1_not_improved")
            if challenger["down_recall"] < stock["down_recall"]:
                reasons.append(f"{horizon}d_down_recall_degraded")
        assessments[candidate] = reasons
    eligible = [name for name, reasons in assessments.items() if not reasons]
    if eligible:
        selected = max(
            eligible,
            key=lambda name: all_rows.loc[
                all_rows["specification"] == name,
                "balanced_accuracy",
            ].mean(),
        )
        return {
            "eligible": True,
            "selected": selected,
            "reason": f"promotion_gate_passed: {selected}",
        }
    selected = max(
        candidates,
        key=lambda name: all_rows.loc[
            all_rows["specification"] == name,
            "balanced_accuracy",
        ].mean(),
    )
    return {
        "eligible": False,
        "selected": selected,
        "reason": f"{selected}: " + ", ".join(assessments[selected]),
    }


def render_markdown_report(
    metrics,
    decision,
    *,
    latest_date,
    ticker_count,
    diagnostics=None,
):
    status = "PROMOTE" if decision["eligible"] else "DO NOT PROMOTE"
    lines = [
        "# Market-confirmed direction ablation",
        "",
        f"- Data through: {latest_date}",
        f"- Local universe: {ticker_count} tickers",
        "- Execution label: enter at the next-session open and exit at the "
        "5th/20th future session close.",
        "- Validation: expanding chronological folds with exact label-end purging.",
        "- NBIS and AMD are diagnostics only and are not used to select parameters.",
        "",
        "## Promotion decision",
        "",
        f"**{status}** — {decision['reason']}",
        "",
        "## Out-of-sample metrics",
        "",
        _markdown_table(metrics),
    ]
    if diagnostics is not None and not diagnostics.empty:
        lines.extend(
            (
                "",
                "## NBIS and AMD diagnostics",
                "",
                _markdown_table(diagnostics),
            )
        )
    lines.extend(
        (
            "",
            "## Interpretation",
            "",
            "Balanced accuracy and macro F1 are the primary anti-bias metrics. "
            "Down recall measures whether the model actually identifies falling "
            "periods rather than defaulting to the market's positive base rate. "
            "Sector evidence is valid only for the semiconductor and AI "
            "infrastructure subgroup.",
            "",
        )
    )
    return "\n".join(lines)


def run_study(histories, *, n_folds=5):
    features = build_feature_frame(histories)
    labeled = attach_next_open_targets(features, histories, horizons=(5, 20))
    all_metrics, all_predictions = evaluate_scope(
        labeled,
        scope="all",
        n_folds=n_folds,
        minimum_samples=1_000,
    )
    group = market_group("semiconductor")
    focus = frozenset((*group.constituent_tickers, *group.related_tickers))
    subgroup = labeled.loc[
        labeled.index.get_level_values("ticker").isin(focus)
    ]
    subgroup_metrics, subgroup_predictions = evaluate_scope(
        subgroup,
        scope="semiconductor_ai",
        n_folds=n_folds,
        minimum_samples=200,
    )
    metrics = pd.concat((all_metrics, subgroup_metrics), ignore_index=True)
    predictions = pd.concat(
        (all_predictions, subgroup_predictions),
        ignore_index=True,
    )
    diagnostic_dates = {
        "NBIS": pd.Timestamp("2026-07-01"),
        "AMD": pd.Timestamp("2026-07-01"),
    }
    diagnostics = []
    for ticker, target_date in diagnostic_dates.items():
        selected = predictions.loc[
            (predictions["scope"] == "all")
            & (predictions["ticker"] == ticker)
            & (predictions["observation_date"] == target_date)
        ].copy()
        diagnostics.append(selected)
    diagnostics = (
        pd.concat(diagnostics, ignore_index=True)
        if diagnostics
        else pd.DataFrame()
    )
    return metrics, predictions, diagnostics


def _markdown_table(frame):
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
        lines.append("| " + " | ".join(value.replace("|", "\\|") for value in values) + " |")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/prices.db")
    parser.add_argument(
        "--report",
        default="docs/research/market-direction-ablation-2026-07-25.md",
    )
    parser.add_argument(
        "--metrics",
        default="docs/research/market-direction-ablation-2026-07-25.csv",
    )
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args(argv)

    histories = MarketDataRepository(args.database).load_universe_histories()
    metrics, _, diagnostics = run_study(histories, n_folds=args.folds)
    decision = promotion_decision(metrics)
    latest = max(frame.index.max() for frame in histories.values())
    report = render_markdown_report(
        metrics,
        decision,
        latest_date=pd.Timestamp(latest).date().isoformat(),
        ticker_count=len(histories),
        diagnostics=diagnostics,
    )
    report_path = Path(args.report)
    metrics_path = Path(args.metrics)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    metrics.to_csv(metrics_path, index=False)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
