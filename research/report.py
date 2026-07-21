from __future__ import annotations

import argparse
from math import erfc, sqrt
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from research.regression import (
    SPECIFICATIONS,
    coefficient_stability,
    chronological_folds,
    design_matrix,
    evaluate_specifications,
    linear_predict,
    walkforward_predictions,
)


def markdown_table(frame: pd.DataFrame) -> str:
    """Render a compact Markdown table without pandas' optional tabulate package."""
    if frame.empty:
        return ""
    columns = [str(column) for column in frame.columns]

    def render(value) -> str:
        if pd.isna(value):
            return "NA"
        if isinstance(value, (float, np.floating)):
            return f"{float(value):.6f}"
        return str(value).replace("|", "\\|")

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame.itertuples(index=False, name=None):
        lines.append("| " + " | ".join(render(value) for value in row) + " |")
    return "\n".join(lines)


def date_block_bootstrap(
    values_by_date: pd.Series,
    block: int = 40,
    n_boot: int = 2000,
    seed: int = 42,
) -> tuple[float, float]:
    """Moving-block bootstrap confidence interval for a date-indexed mean."""
    series = values_by_date.dropna().groupby(level=0).mean().sort_index()
    values = series.to_numpy(dtype=float)
    if len(values) == 0:
        return (np.nan, np.nan)
    block = max(1, min(block, len(values)))
    starts = np.arange(max(1, len(values) - block + 1))
    rng = np.random.RandomState(seed)
    means = []
    blocks_needed = int(np.ceil(len(values) / block))
    for _ in range(n_boot):
        sampled = []
        for start in rng.choice(starts, size=blocks_needed, replace=True):
            sampled.extend(values[start:start + block])
        means.append(float(np.mean(sampled[:len(values)])))
    low, high = np.percentile(means, [2.5, 97.5])
    return float(low), float(high)


def apply_bh_fdr(p_values) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values in original input order."""
    values = np.asarray(p_values, dtype=float)
    order = np.argsort(values)
    ranked = values[order]
    adjusted_ranked = ranked * len(values) / np.arange(1, len(values) + 1)
    adjusted_ranked = np.minimum.accumulate(adjusted_ranked[::-1])[::-1]
    adjusted = np.empty_like(adjusted_ranked)
    adjusted[order] = np.clip(adjusted_ranked, 0, 1)
    return adjusted


def match_controls(events: pd.DataFrame, snapshots: pd.DataFrame) -> pd.DataFrame:
    """Choose a same-ticker, same-regime, nearby non-event control date."""
    snapshots = snapshots.copy()
    snapshots["date"] = pd.to_datetime(snapshots["date"])
    event_dates = {
        (ticker, pd.Timestamp(date))
        for ticker, date in zip(events["ticker"], pd.to_datetime(events["observation_date"]))
    }
    matches = []
    for event in events.itertuples(index=False):
        date = pd.Timestamp(event.observation_date)
        candidates = snapshots[
            (snapshots["ticker"] == event.ticker)
            & (snapshots["market_regime"] == event.market_regime)
            & ((snapshots["date"] - date).abs() <= pd.Timedelta(days=60))
        ].copy()
        candidates = candidates[
            ~candidates.apply(lambda row: (row["ticker"], row["date"]) in event_dates, axis=1)
        ]
        if candidates.empty:
            continue
        candidates["distance_gap"] = (
            candidates["distance_to_ma50"] - float(event.distance_to_ma50)
        ).abs()
        candidates["date_gap"] = (candidates["date"] - date).abs()
        control = candidates.sort_values(["distance_gap", "date_gap", "date"]).iloc[0]
        matches.append(
            {
                "event_id": event.event_id,
                "ticker": event.ticker,
                "event_date": date,
                "control_date": control["date"],
            }
        )
    return pd.DataFrame(matches)


def _normal_mean_pvalue(values: pd.Series) -> float:
    sample = values.dropna().to_numpy(dtype=float)
    if len(sample) < 3 or np.std(sample, ddof=1) == 0:
        return 1.0
    statistic = abs(float(np.mean(sample) / (np.std(sample, ddof=1) / np.sqrt(len(sample)))))
    return float(erfc(statistic / sqrt(2)))


def _continuous_summary(events: pd.DataFrame, target: str) -> pd.DataFrame:
    metrics = evaluate_specifications(events, target=target, horizon=int(target.rsplit("_", 1)[-1]))
    predictions = walkforward_predictions(events, target=target, horizon=int(target.rsplit("_", 1)[-1]))
    rows = []
    for specification in SPECIFICATIONS:
        subset = predictions[predictions["specification"] == specification].copy()
        if subset.empty:
            continue
        subset["improvement"] = np.square(subset["actual"] - subset["train_mean"]) - np.square(
            subset["actual"] - subset["prediction"]
        )
        daily = subset.set_index("observation_date")["improvement"].groupby(level=0).mean()
        ci = date_block_bootstrap(daily)
        fold_metrics = metrics[metrics["specification"] == specification]
        phase_correlations = []
        unique_dates = np.array(sorted(subset.observation_date.unique()))
        horizon = int(target.rsplit("_", 1)[-1])
        for phase in range(min(horizon, len(unique_dates))):
            selected_dates = unique_dates[phase::horizon]
            phase_rows = subset[subset.observation_date.isin(selected_dates)]
            if (
                len(phase_rows) >= 4
                and phase_rows.actual.std() > 0
                and phase_rows.prediction.std() > 0
            ):
                phase_correlations.append(
                    float(phase_rows.actual.corr(phase_rows.prediction))
                )
        rows.append(
            {
                "target": target,
                "specification": specification,
                "n_obs": len(subset),
                "n_dates": subset.observation_date.nunique(),
                "mean_fold_corr": fold_metrics.correlation.mean(),
                "mean_oos_r2": fold_metrics.oos_r2.mean(),
                "positive_folds": int((fold_metrics.correlation > 0).sum()),
                "n_folds": len(fold_metrics),
                "mean_mse_improvement": daily.mean(),
                "ci_low": ci[0],
                "ci_high": ci[1],
                "p_value": _normal_mean_pvalue(daily),
                "positive_phases": int(sum(value > 0 for value in phase_correlations)),
                "n_phases": len(phase_correlations),
                "median_phase_corr": (
                    float(np.median(phase_correlations)) if phase_correlations else np.nan
                ),
            }
        )
    summary = pd.DataFrame(rows)
    if not summary.empty:
        summary["p_fdr"] = apply_bh_fdr(summary["p_value"].to_numpy())
    return summary


def _barrier_summary(events: pd.DataFrame) -> pd.DataFrame:
    data = events[events["barrier_label"].isin(["up", "down"])].copy().reset_index(drop=True)
    if data.empty:
        return pd.DataFrame()
    data["target"] = (data["barrier_label"] == "up").astype(int)
    rows = []
    for fold, (train_index, test_index) in enumerate(
        chronological_folds(data, horizon=40, n_folds=5), start=1
    ):
        train, test = data.iloc[train_index], data.iloc[test_index]
        if train.target.nunique() < 2 or test.target.nunique() < 2:
            continue
        for specification in SPECIFICATIONS:
            x_train, stats = design_matrix(train, specification)
            x_test, _ = design_matrix(test, specification, stats)
            model = LogisticRegression(
                C=1.0, max_iter=2000, solver="liblinear", random_state=42
            )
            model.fit(x_train, train.target)
            score = linear_predict(x_test, model.coef_[0], model.intercept_[0])
            probability = 1.0 / (1.0 + np.exp(-np.clip(score, -35, 35)))
            rows.append(
                {
                    "specification": specification,
                    "fold": fold,
                    "n_obs": len(test),
                    "log_loss": log_loss(test.target, probability),
                    "brier": brier_score_loss(test.target, probability),
                    "roc_auc": roc_auc_score(test.target, probability),
                }
            )
    return pd.DataFrame(rows)


def build_report(events: pd.DataFrame) -> str:
    events = events.copy()
    events["observation_date"] = pd.to_datetime(events["observation_date"])
    summaries = [
        _continuous_summary(events, target)
        for target in ("rel_ret_20", "rel_ret_40", "rel_ret_60")
    ]
    continuous = pd.concat([item for item in summaries if not item.empty], ignore_index=True)
    barrier = _barrier_summary(events)
    coefficients = coefficient_stability(events, target="rel_ret_40", horizon=40)
    coefficient_summary = (
        coefficients.groupby(["specification", "feature"])["coefficient"]
        .agg(
            mean_coefficient="mean",
            median_coefficient="median",
            positive_folds=lambda values: int((values > 0).sum()),
            n_folds="count",
        )
        .reset_index()
    )
    span_days = (events.observation_date.max() - events.observation_date.min()).days
    independent_dates = events.observation_date.nunique()
    underpowered = span_days < 3 * 365 or independent_dates < 252
    verdict = "UNDERPOWERED" if underpowered else "FAIL"

    lines = [
        "# VCP Momentum Research Report v1",
        "",
        "## Coverage",
        "",
        f"- Events: {len(events)}",
        f"- Tickers: {events.ticker.nunique()}",
        f"- Independent observation dates: {independent_dates}",
        f"- Date range: {events.observation_date.min().date()} to {events.observation_date.max().date()}",
        f"- Duplicate event IDs: {int(events.event_id.duplicated().sum())}",
        "",
        "## Continuous walk-forward results",
        "",
        markdown_table(continuous) if not continuous.empty else "No eligible folds.",
        "",
        "## Barrier-model diagnostics",
        "",
        markdown_table(barrier) if not barrier.empty else "No eligible binary folds.",
        "",
        "## Primary-target standardized coefficient stability",
        "",
        markdown_table(coefficient_summary),
        "",
        "## Required gates not available",
        "",
        "- A point-in-time daily snapshot table for same-ticker matched controls is not yet available.",
        "- The local history covers fewer than three years and fewer than 252 independent event dates.",
        "- Sector metadata is unavailable, so sector concentration cannot be adjudicated.",
        "",
        "## Decision",
        "",
        f"- VCP structure family: **{verdict}**",
        f"- Momentum family: **{verdict}**",
        f"- VCP × momentum interactions: **{verdict}**",
        "",
        "A required gate that cannot be run prevents PASS. Secondary outcomes cannot rescue the primary result.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    events = pd.read_csv(args.events)
    report = build_report(events)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(f"events={len(events)} output={output}")


if __name__ == "__main__":
    main()
