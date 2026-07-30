"""Offline runner for the fixed recency and hierarchical direction study."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from research.expanded_market_data import ExpandedMarketDataRepository
from research.hierarchical_direction import walk_forward_hierarchical_predictions
from research.market_direction_model import (
    attach_next_open_targets,
    evaluate_direction_ablation,
    walk_forward_direction_predictions,
    walk_forward_ridge_predictions,
)
from research.market_regime import build_market_regime_frame
from research.run_expanded_walkforward_study import (
    classify_study_groups,
    evaluate_predictions_by_regime,
    prepare_expanded_frame,
    select_analysis_tickers,
)
from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS
from web.market_groups import REFERENCE_TICKERS


PRIMARY_HORIZON = 5
SAMPLE_MODES = ("overlapping", "non_overlapping")
EVALUATION_SCOPES = ("all", "semiconductor", "software", "other")
CHALLENGER = "logistic_time_group_ticker"
COMPARATORS = ("majority_baseline", "ridge_current", "logistic_global")
HIERARCHICAL_SPECIFICATIONS = (
    "logistic_global",
    "logistic_time",
    "logistic_group",
    "logistic_time_group",
    CHALLENGER,
)


def aggregate_direction_metrics(predictions, group_map):
    """Evaluate all specifications on identical overall and subgroup rows."""
    required = {
        "ticker",
        "observation_date",
        "horizon",
        "fold",
        "specification",
        "actual_return",
        "actual_direction",
        "predicted_direction",
    }
    missing = sorted(required.difference(predictions.columns))
    if missing:
        raise ValueError(f"predictions are missing columns: {missing}")
    normalized_groups = {
        str(ticker).strip().upper(): str(group)
        for ticker, group in group_map.items()
    }
    source = predictions.copy(deep=True)
    source["_scope"] = source["ticker"].map(
        lambda value: normalized_groups.get(
            str(value).strip().upper(),
            "other",
        )
    )
    outputs = []
    for scope in EVALUATION_SCOPES:
        scoped = (
            source
            if scope == "all"
            else source.loc[source["_scope"] == scope]
        )
        if scoped.empty:
            continue
        for horizon, horizon_rows in scoped.groupby("horizon", sort=True):
            samples = {
                "overlapping": horizon_rows,
                "non_overlapping": _non_overlapping_rows(
                    horizon_rows,
                    int(horizon),
                ),
            }
            for sample_mode, sample in samples.items():
                if sample.empty:
                    continue
                aggregate = evaluate_direction_ablation(sample)
                aggregate.insert(0, "fold", 0)
                aggregate.insert(0, "sample_mode", sample_mode)
                aggregate.insert(0, "horizon", int(horizon))
                aggregate.insert(0, "scope", scope)
                outputs.append(aggregate)
                for fold, fold_rows in sample.groupby("fold", sort=True):
                    metrics = evaluate_direction_ablation(fold_rows)
                    metrics.insert(0, "fold", int(fold))
                    metrics.insert(0, "sample_mode", sample_mode)
                    metrics.insert(0, "horizon", int(horizon))
                    metrics.insert(0, "scope", scope)
                    outputs.append(metrics)
    if not outputs:
        return pd.DataFrame()
    return pd.concat(outputs, ignore_index=True, sort=False)


def hierarchical_promotion_decision(metrics, regime_metrics):
    """Apply frozen metric gates while withholding all online authority."""
    reasons = []
    primary = metrics.loc[
        (metrics["scope"] == "all")
        & (metrics["horizon"] == PRIMARY_HORIZON)
    ]
    for sample_mode in SAMPLE_MODES:
        rows = primary.loc[
            (primary["sample_mode"] == sample_mode)
            & (primary["fold"] == 0)
        ].set_index("specification")
        required = set(COMPARATORS + (CHALLENGER,))
        if not required.issubset(rows.index):
            reasons.append(f"{sample_mode}:missing_primary_metrics")
            continue
        candidate = rows.loc[CHALLENGER]
        best_accuracy = max(
            float(rows.loc[name, "balanced_accuracy"])
            for name in COMPARATORS
        )
        best_f1 = max(
            float(rows.loc[name, "macro_f1"])
            for name in COMPARATORS
        )
        if float(candidate["balanced_accuracy"]) < best_accuracy + 0.01:
            reasons.append(f"{sample_mode}:balanced_accuracy_gain_below_one_point")
        if float(candidate["macro_f1"]) < best_f1 + 0.01:
            reasons.append(f"{sample_mode}:macro_f1_gain_below_one_point")
        global_row = rows.loc["logistic_global"]
        if float(candidate["down_recall"]) < float(global_row["down_recall"]) - 0.02:
            reasons.append(f"{sample_mode}:down_recall_degraded")
        returns = [
            candidate["mean_return_predicted_down"],
            candidate["mean_return_predicted_neutral"],
            candidate["mean_return_predicted_up"],
        ]
        if (
            any(pd.isna(value) for value in returns)
            or not float(returns[0]) < float(returns[1]) < float(returns[2])
            or not float(returns[0]) < 0.0
        ):
            reasons.append(f"{sample_mode}:return_ordering_failed")
        fold_rows = primary.loc[
            (primary["sample_mode"] == sample_mode)
            & (primary["fold"] > 0)
        ]
        wins = 0
        comparable = 0
        for _, selected in fold_rows.groupby("fold", sort=True):
            indexed = selected.set_index("specification")
            if not required.issubset(indexed.index):
                continue
            comparable += 1
            candidate_score = float(
                indexed.loc[CHALLENGER, "balanced_accuracy"]
            )
            best_score = max(
                float(indexed.loc[name, "balanced_accuracy"])
                for name in COMPARATORS
            )
            wins += candidate_score > best_score
        if comparable != 5:
            reasons.append(f"{sample_mode}:comparable_fold_count_not_five")
        elif wins < 4:
            reasons.append(f"{sample_mode}:fold_wins_below_four")

    subgroup_improvements = 0
    for scope in ("semiconductor", "software", "other"):
        rows = metrics.loc[
            (metrics["scope"] == scope)
            & (metrics["horizon"] == PRIMARY_HORIZON)
            & (metrics["sample_mode"] == "overlapping")
            & (metrics["fold"] == 0)
        ].set_index("specification")
        if not {"logistic_global", CHALLENGER}.issubset(rows.index):
            reasons.append(f"{scope}:missing_subgroup")
            continue
        delta = (
            float(rows.loc[CHALLENGER, "balanced_accuracy"])
            - float(rows.loc["logistic_global", "balanced_accuracy"])
        )
        if delta < -0.01:
            reasons.append(f"{scope}:subgroup_degraded")
        if delta > 0.0:
            subgroup_improvements += 1
    if subgroup_improvements < 2:
        reasons.append("subgroup_improvements_below_two")

    for regime in ("under_pressure", "correction", "acute_selloff"):
        rows = regime_metrics.loc[
            (regime_metrics["scope"] == "all")
            & (regime_metrics["horizon"] == PRIMARY_HORIZON)
            & (regime_metrics["sample_mode"] == "overlapping")
            & (regime_metrics["regime"] == regime)
        ].set_index("specification")
        if not {"logistic_global", CHALLENGER}.issubset(rows.index):
            reasons.append(f"{regime}:missing_regime")
            continue
        candidate = rows.loc[CHALLENGER]
        baseline = rows.loc["logistic_global"]
        if (
            float(candidate["balanced_accuracy"])
            < float(baseline["balanced_accuracy"]) - 0.02
        ):
            reasons.append(f"{regime}:balanced_accuracy_degraded")
        if float(candidate["down_recall"]) <= float(baseline["down_recall"]):
            reasons.append(f"{regime}:down_recall_not_improved")

    ablation = primary.loc[
        (primary["sample_mode"] == "overlapping")
        & (primary["fold"] == 0)
    ].set_index("specification")
    increments = []
    for parent, child in (
        ("logistic_global", "logistic_time"),
        ("logistic_time", "logistic_time_group"),
        ("logistic_time_group", CHALLENGER),
    ):
        if {parent, child}.issubset(ablation.index):
            increments.append(
                float(ablation.loc[child, "balanced_accuracy"])
                - float(ablation.loc[parent, "balanced_accuracy"])
            )
    if not increments or max(increments) <= 0.0:
        reasons.append("ablation_has_no_positive_increment")

    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "metric_gate_passed": not unique_reasons,
        "metric_gate_reasons": unique_reasons,
        "eligible": False,
        "online_authority": "none",
        "reason": (
            "point_in_time universe membership and classifications are not "
            "complete; this challenger remains offline even if metric gates pass"
        ),
    }


def render_report(metrics, manifest):
    """Render a compact Chinese audit report for the offline challenger."""
    decision = manifest.get("decision") or {}
    primary = metrics.loc[
        (metrics["scope"] == "all")
        & (metrics["fold"] == 0)
    ].copy()
    selected_columns = [
        "horizon",
        "sample_mode",
        "specification",
        "sample_count",
        "balanced_accuracy",
        "macro_f1",
        "down_recall",
        "mean_return_predicted_down",
        "mean_return_predicted_neutral",
        "mean_return_predicted_up",
    ]
    for column in selected_columns:
        if column not in primary:
            primary[column] = np.nan
    table = _markdown_table(primary.loc[:, selected_columns])
    gate = "通过" if decision.get("metric_gate_passed") else "未通过"
    reasons = decision.get("metric_gate_reasons") or ["无"]
    return "\n".join(
        (
            "# 时间衰减与层级方向挑战模型",
            "",
            f"- 指标门槛：{gate}",
            "- 线上权限：无；本实验不修改线上 Ridge。",
            "- 限制：股票池与分类尚不具备完整 point-in-time 历史。",
            f"- 门槛原因：{', '.join(map(str, reasons))}",
            "",
            "## 全体样本指标",
            "",
            table,
            "",
        )
    )


def publish_reports(prefix, metrics, manifest, report):
    """Atomically publish JSON manifest, metric CSV, and Markdown report."""
    checked_prefix = Path(prefix)
    checked_prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": checked_prefix.with_suffix(".json"),
        "csv": checked_prefix.with_suffix(".csv"),
        "md": checked_prefix.with_suffix(".md"),
    }
    payloads = {
        "json": json.dumps(
            _json_safe(manifest),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "csv": metrics.to_csv(index=False),
        "md": str(report),
    }
    for name, destination in paths.items():
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payloads[name])
                handle.flush()
                os.fsync(handle.fileno())
            Path(temporary_name).replace(destination)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise
    return paths


def _non_overlapping_rows(predictions, horizon):
    ordered = predictions.sort_values(
        ["specification", "fold", "ticker", "observation_date"]
    ).copy()
    positions = ordered.groupby(
        ["specification", "fold", "ticker"],
        sort=False,
    ).cumcount()
    return ordered.loc[(positions % int(horizon)) == 0]


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _markdown_table(frame):
    if frame.empty:
        return "无可用样本。"
    columns = [str(column) for column in frame.columns]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for values in frame.itertuples(index=False, name=None):
        cells = []
        for value in values:
            if pd.isna(value):
                cells.append("—")
            elif isinstance(value, float):
                cells.append(f"{value:.4f}")
            else:
                cells.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument("--start-date", default="2018-01-01")
    parser.add_argument("--max-tickers", type=int, default=240)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--minimum-samples", type=int, default=1_000)
    parser.add_argument(
        "--output-prefix",
        default="reports/hierarchical-recency-direction",
    )
    args = parser.parse_args(argv)

    repository = ExpandedMarketDataRepository(args.database)
    classifications = repository.load_classifications()
    groups = classify_study_groups(classifications)
    analysis_tickers = select_analysis_tickers(
        groups,
        max_tickers=args.max_tickers,
        seed=args.seed,
    )
    requested = tuple(sorted(set(analysis_tickers) | set(REFERENCE_TICKERS)))
    histories = repository.load_universe_histories(tickers=requested)
    frame = prepare_expanded_frame(
        histories,
        analysis_tickers=analysis_tickers,
        classifications=classifications,
        start_date=args.start_date,
        sector_mode="none",
    )
    frame = attach_next_open_targets(
        frame,
        histories,
        horizons=(5, 20, 60),
    )
    predictions = []
    diagnostics = []
    for horizon in (5, 20, 60):
        hierarchical, weights, fold_groups = (
            walk_forward_hierarchical_predictions(
                frame,
                histories,
                horizon=horizon,
                feature_columns=RIDGE_V4_FEATURE_COLUMNS,
                n_test_folds=5,
                minimum_samples=args.minimum_samples,
            )
        )
        if hierarchical.empty:
            continue
        keys = [
            "ticker",
            "observation_date",
            "horizon",
            "fold",
        ]
        expected_keys = hierarchical.loc[
            hierarchical["specification"] == HIERARCHICAL_SPECIFICATIONS[0],
            keys,
        ]
        ridge = walk_forward_ridge_predictions(
            frame,
            horizon=horizon,
            feature_columns=RIDGE_V4_FEATURE_COLUMNS,
            n_folds=6,
            minimum_samples=args.minimum_samples,
            specification="ridge_current",
        )
        baseline = walk_forward_direction_predictions(
            frame,
            horizon=horizon,
            feature_sets={"logistic_global_reference": RIDGE_V4_FEATURE_COLUMNS},
            n_folds=6,
            minimum_samples=args.minimum_samples,
        )
        baseline = baseline.loc[
            baseline["specification"] == "majority_baseline"
        ]
        for name, comparator in (
            ("ridge_current", ridge),
            ("majority_baseline", baseline),
        ):
            _require_same_test_keys(expected_keys, comparator[keys], name)
        predictions.extend((hierarchical, ridge, baseline))
        diagnostics.append(
            {
                "horizon": horizon,
                "weights": weights.to_dict(orient="records"),
                "groups": fold_groups.to_dict(orient="records"),
            }
        )
    if not predictions:
        raise RuntimeError("no comparable walk-forward predictions were produced")
    prediction_frame = pd.concat(predictions, ignore_index=True, sort=False)
    metrics = aggregate_direction_metrics(prediction_frame, groups)

    market_history = histories.get("QQQ")
    if market_history is None or market_history.empty:
        raise RuntimeError("QQQ history is required for causal regime checks")
    regimes = build_market_regime_frame(histories)
    regime_input = prediction_frame.copy()
    regime_input["scope"] = "all"
    regime_metrics = evaluate_predictions_by_regime(
        regime_input,
        regimes,
        minimum_fold_samples=30,
    )
    decision = hierarchical_promotion_decision(metrics, regime_metrics)
    latest_date = max(
        pd.Timestamp(frame.index.get_level_values("observation_date").max()),
        pd.Timestamp(args.start_date),
    )
    manifest = {
        "study_version": "hierarchical_recency_direction_v1",
        "latest_date": latest_date.date().isoformat(),
        "start_date": str(args.start_date),
        "ticker_count": len(analysis_tickers),
        "folds": 5,
        "horizons": [5, 20, 60],
        "online_authority": "none",
        "decision": decision,
        "diagnostics": diagnostics,
    }
    report = render_report(metrics, manifest)
    paths = publish_reports(args.output_prefix, metrics, manifest, report)
    print(json.dumps({key: str(value) for key, value in paths.items()}))
    return 0


def _require_same_test_keys(expected, actual, specification):
    expected_rows = expected.sort_values(list(expected.columns)).reset_index(
        drop=True
    )
    actual_rows = actual.sort_values(list(actual.columns)).reset_index(drop=True)
    if not expected_rows.equals(actual_rows):
        raise RuntimeError(
            f"{specification} does not share identical walk-forward test keys"
        )


if __name__ == "__main__":
    raise SystemExit(main())
