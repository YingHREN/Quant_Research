"""Offline runner and strict gates for the regime-threshold study."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd
from sklearn.metrics import balanced_accuracy_score, f1_score

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.expanded_market_data import ExpandedMarketDataRepository
from research.market_direction_model import (
    walk_forward_direction_predictions,
    walk_forward_ridge_predictions,
)
from research.market_regime import REGIME_VERSION, build_market_regime_frame
from research.regime_threshold_direction import (
    DOWN_THRESHOLDS,
    REGIME_PRIOR_STRENGTH,
    attach_absolute_and_qqq_relative_targets,
    walk_forward_qqq_relative_predictions,
    walk_forward_regime_threshold_predictions,
)
from research.run_expanded_walkforward_study import (
    classify_study_groups,
    prepare_expanded_frame,
    select_analysis_tickers,
)
from research.run_hierarchical_recency_direction import (
    input_content_fingerprint,
    named_case_diagnostics,
)
from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS
from web.market_groups import REFERENCE_TICKERS


PRIMARY_HORIZON = 5
HORIZON = PRIMARY_HORIZON
SAMPLE_MODES = ("overlapping", "non_overlapping")
CHALLENGER = "logistic_regime_threshold"
PRIOR_MODEL = "logistic_regime_prior"
GLOBAL_MODEL = "logistic_global"
STRESSED_REGIMES = (
    "under_pressure",
    "correction",
    "acute_selloff",
)
EVALUATION_SCOPES = ("all", "semiconductor", "software", "other")
_SECRET_KEY = re.compile(
    r"(api.?key|secret|password|passwd|access.?token|private.?key)",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?:token|secret|api.?key|password)\s*[:=]\s*[A-Za-z0-9_./+-]{12,}",
    re.IGNORECASE,
)


def regime_threshold_promotion_decision(metrics, regime_metrics):
    """Apply frozen historical gates without granting online authority."""
    _require_metric_columns(metrics)
    reasons = []
    primary = metrics.loc[
        (metrics["scope"] == "all")
        & (metrics["horizon"] == PRIMARY_HORIZON)
    ]
    for sample_mode in SAMPLE_MODES:
        aggregate = primary.loc[
            (primary["sample_mode"] == sample_mode)
            & (primary["fold"] == 0)
        ].set_index("specification")
        if not {GLOBAL_MODEL, PRIOR_MODEL, CHALLENGER}.issubset(
            aggregate.index
        ):
            reasons.append(f"{sample_mode}:missing_primary_metrics")
            continue
        baseline = aggregate.loc[GLOBAL_MODEL]
        candidate = aggregate.loc[CHALLENGER]
        if not float(candidate["mean_return_predicted_down"]) < 0.0:
            reasons.append(
                f"{sample_mode}:predicted_down_return_not_negative"
            )
        if (
            float(candidate["down_precision"])
            < float(baseline["down_precision"]) + 0.03
        ):
            reasons.append(
                f"{sample_mode}:down_precision_gain_below_0.03"
            )
        if (
            float(candidate["down_recall"])
            < float(baseline["down_recall"]) - 0.03
        ):
            reasons.append(
                f"{sample_mode}:down_recall_degraded_over_0.03"
            )
        if (
            float(candidate["balanced_accuracy"])
            < float(baseline["balanced_accuracy"]) - 0.005
        ):
            reasons.append(
                f"{sample_mode}:balanced_accuracy_degraded_over_0.005"
            )
        if float(candidate["down_coverage"]) < 0.05:
            reasons.append(f"{sample_mode}:down_coverage_below_0.05")

        folds = primary.loc[
            (primary["sample_mode"] == sample_mode)
            & (primary["fold"] > 0)
            & (primary["specification"] == CHALLENGER)
        ]
        comparable = folds["fold"].nunique()
        negative_folds = folds.loc[
            folds["mean_return_predicted_down"] < 0.0,
            "fold",
        ].nunique()
        if comparable != 5:
            reasons.append(f"{sample_mode}:comparable_fold_count_not_five")
        elif negative_folds < 4:
            reasons.append(
                f"{sample_mode}:negative_return_folds_below_four"
            )

    for scope in ("semiconductor", "software", "other"):
        rows = metrics.loc[
            (metrics["scope"] == scope)
            & (metrics["horizon"] == PRIMARY_HORIZON)
            & (metrics["sample_mode"] == "overlapping")
            & (metrics["fold"] == 0)
        ].set_index("specification")
        if not {GLOBAL_MODEL, CHALLENGER}.issubset(rows.index):
            reasons.append(f"{scope}:missing_subgroup_metrics")
            continue
        baseline = rows.loc[GLOBAL_MODEL]
        candidate = rows.loc[CHALLENGER]
        if not float(candidate["mean_return_predicted_down"]) < 0.0:
            reasons.append(f"{scope}:predicted_down_return_not_negative")
        if (
            float(candidate["balanced_accuracy"])
            < float(baseline["balanced_accuracy"]) - 0.01
        ):
            reasons.append(f"{scope}:balanced_accuracy_degraded_over_0.01")

    _evaluate_stressed_regimes(regime_metrics, reasons)

    ablation = primary.loc[
        (primary["sample_mode"] == "overlapping")
        & (primary["fold"] == 0)
    ].set_index("specification")
    positive_increment = False
    if {GLOBAL_MODEL, PRIOR_MODEL}.issubset(ablation.index):
        positive_increment |= (
            float(ablation.loc[PRIOR_MODEL, "balanced_accuracy"])
            > float(ablation.loc[GLOBAL_MODEL, "balanced_accuracy"])
        )
    if {PRIOR_MODEL, CHALLENGER}.issubset(ablation.index):
        positive_increment |= (
            float(ablation.loc[CHALLENGER, "balanced_accuracy"])
            > float(ablation.loc[PRIOR_MODEL, "balanced_accuracy"])
        )
    if not positive_increment:
        reasons.append("ablation_has_no_positive_increment")

    unique = list(dict.fromkeys(reasons))
    return {
        "metric_gate_passed": not unique,
        "metric_gate_reasons": unique,
        "eligible": False,
        "online_authority": "none",
        "reason": (
            "完整点时股票池审计与至少 60 个冻结后交易日影子验证尚未完成；"
            "即使历史门槛通过也不得进入线上决策。"
        ),
    }


def validate_report_payload(payload):
    """Reject non-finite, absolute-path, and credential-shaped report data."""
    _validate_value(payload, path=())
    return payload


def aggregate_absolute_metrics(predictions, group_map):
    """Aggregate absolute-direction metrics over frozen scopes and samples."""
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
        modes = {
            "overlapping": scoped,
            "non_overlapping": _non_overlapping_rows(scoped, HORIZON),
        }
        for sample_mode, sample in modes.items():
            if sample.empty:
                continue
            outputs.extend(
                _metric_rows(
                    sample,
                    scope=scope,
                    sample_mode=sample_mode,
                    fold=0,
                )
            )
            for fold, fold_rows in sample.groupby("fold", sort=True):
                outputs.extend(
                    _metric_rows(
                        fold_rows,
                        scope=scope,
                        sample_mode=sample_mode,
                        fold=int(fold),
                    )
                )
    return pd.DataFrame(outputs)


def aggregate_relative_metrics(predictions):
    """Aggregate the separately named QQQ-relative diagnostic head."""
    required = {
        "ticker",
        "observation_date",
        "fold",
        "specification",
        "actual_return",
        "actual_relative_return",
        "actual_relative_direction",
        "predicted_relative_direction",
    }
    missing = sorted(required.difference(predictions.columns))
    if missing:
        raise ValueError(
            f"relative predictions are missing columns: {missing}"
        )
    rows = []
    modes = {
        "overlapping": predictions,
        "non_overlapping": _non_overlapping_rows(
            predictions,
            HORIZON,
        ),
    }
    for sample_mode, sample in modes.items():
        if sample.empty:
            continue
        actual = sample["actual_relative_direction"].astype(str)
        predicted = sample["predicted_relative_direction"].astype(str)
        row = {
            "horizon": HORIZON,
            "sample_mode": sample_mode,
            "specification": "logistic_qqq_relative",
            "sample_count": len(sample),
            "balanced_accuracy": float(
                balanced_accuracy_score(actual, predicted)
            ),
            "macro_f1": float(
                f1_score(
                    actual,
                    predicted,
                    labels=("down", "neutral", "up"),
                    average="macro",
                    zero_division=0,
                )
            ),
        }
        for label in ("down", "neutral", "up"):
            selected = predicted == label
            row[f"{label}_coverage"] = float(selected.mean())
            row[f"{label}_mean_absolute_return"] = _finite_mean(
                sample.loc[selected, "actual_return"]
            )
            row[f"{label}_mean_relative_return"] = _finite_mean(
                sample.loc[selected, "actual_relative_return"]
            )
        rows.append(row)
    return pd.DataFrame(rows)


def aggregate_regime_metrics(predictions, regimes):
    """Aggregate absolute predictions for each causal regime and stress union."""
    if not isinstance(regimes, pd.DataFrame) or "regime" not in regimes:
        raise ValueError("regimes must contain a regime column")
    source = predictions.copy(deep=True)
    dates = pd.to_datetime(source["observation_date"]).dt.normalize()
    lookup = regimes["regime"].copy(deep=True)
    lookup.index = pd.DatetimeIndex(lookup.index).tz_localize(None).normalize()
    source["regime"] = dates.map(lookup).fillna("unavailable")
    source["scope"] = "all"
    outputs = []
    groups = list(source.groupby("regime", sort=True))
    stressed = source.loc[source["regime"].isin(STRESSED_REGIMES)]
    if not stressed.empty:
        groups.append(("stressed_combined", stressed))
    for regime, selected in groups:
        for sample_mode, sample in (
            ("overlapping", selected),
            ("non_overlapping", _non_overlapping_rows(selected, HORIZON)),
        ):
            if sample.empty:
                continue
            for row in _metric_rows(
                sample,
                scope="all",
                sample_mode=sample_mode,
                fold=0,
            ):
                row["regime"] = str(regime)
                row["regime_version"] = REGIME_VERSION
                outputs.append(row)
    return pd.DataFrame(outputs)


def render_report(metrics, relative_metrics, manifest):
    """Render the Chinese audit summary for the offline experiment."""
    decision = manifest["decision"]
    gate = "通过" if decision["metric_gate_passed"] else "未通过"
    reasons = decision["metric_gate_reasons"] or ["无"]
    fold_diagnostics = manifest.get("fold_diagnostics") or []
    selected_folds = sum(
        row.get("threshold_status") == "available"
        for row in fold_diagnostics
    )
    primary = metrics.loc[
        (metrics["scope"] == "all")
        & (metrics["fold"] == 0)
    ].copy()
    columns = (
        "sample_mode",
        "specification",
        "sample_count",
        "balanced_accuracy",
        "macro_f1",
        "down_precision",
        "down_recall",
        "down_coverage",
        "mean_return_predicted_down",
    )
    for column in columns:
        if column not in primary:
            primary[column] = None
    return "\n".join(
        (
            "# 市场阶段与经济阈值方向挑战模型",
            "",
            f"- 历史指标门槛：{gate}",
            "- 线上权限：无；不修改 Ridge、风险否决、API 或 UI。",
            f"- 经济阈值可用折：{selected_folds}/{len(fold_diagnostics)}。",
            f"- 未通过原因：{', '.join(map(str, reasons))}",
            "",
            "## 绝对方向",
            "",
            _markdown_table(primary.loc[:, columns]),
            "",
            "## QQQ 相对方向（独立诊断）",
            "",
            _markdown_table(relative_metrics),
            "",
            "## 解释边界",
            "",
            "- 绝对下跌表示股票自身五日可执行收益低于 -1%。",
            "- 相对下跌只表示跑输 QQQ，不得改写为股票绝对下跌。",
            "- 经济阈值只由每个外层训练集内部的净化 OOF 预测选择。",
            "",
        )
    )


def publish_reports(prefix, metrics, manifest, report):
    """Validate then atomically publish strict JSON, CSV, and Markdown."""
    validate_report_payload(manifest)
    payloads = {
        "json": json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        "csv": metrics.to_csv(index=False),
        "md": str(report),
    }
    checked_prefix = Path(prefix)
    checked_prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": checked_prefix.with_suffix(".json"),
        "csv": checked_prefix.with_suffix(".csv"),
        "md": checked_prefix.with_suffix(".md"),
    }
    temporary = {}
    try:
        for name, destination in paths.items():
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=str(destination.parent),
            )
            temporary[name] = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payloads[name])
                handle.flush()
                os.fsync(handle.fileno())
        for name, destination in paths.items():
            temporary[name].replace(destination)
    finally:
        for path in temporary.values():
            path.unlink(missing_ok=True)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument("--start-date", default="2018-01-01")
    parser.add_argument("--max-tickers", type=int, default=240)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--minimum-samples", type=int, default=1_000)
    parser.add_argument(
        "--output-prefix",
        default="reports/regime-threshold-direction",
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
    feature_frame = prepare_expanded_frame(
        histories,
        analysis_tickers=analysis_tickers,
        classifications=classifications,
        start_date=args.start_date,
        sector_mode="none",
    )
    frame = attach_absolute_and_qqq_relative_targets(
        feature_frame,
        histories,
    )
    regimes = build_market_regime_frame(histories)
    challenger, fold_diagnostics = (
        walk_forward_regime_threshold_predictions(
            frame,
            regimes,
            feature_columns=RIDGE_V4_FEATURE_COLUMNS,
            minimum_samples=args.minimum_samples,
        )
    )
    if challenger.empty:
        raise RuntimeError("no absolute challenger predictions were produced")
    relative = walk_forward_qqq_relative_predictions(
        frame,
        feature_columns=RIDGE_V4_FEATURE_COLUMNS,
        minimum_samples=args.minimum_samples,
    )
    expected = challenger.loc[
        challenger["specification"] == GLOBAL_MODEL,
        ["ticker", "observation_date", "horizon", "fold"],
    ]
    ridge = walk_forward_ridge_predictions(
        frame,
        horizon=HORIZON,
        feature_columns=RIDGE_V4_FEATURE_COLUMNS,
        n_folds=6,
        minimum_samples=args.minimum_samples,
        specification="ridge_current",
    )
    baseline = walk_forward_direction_predictions(
        frame,
        horizon=HORIZON,
        feature_sets={
            "logistic_reference": RIDGE_V4_FEATURE_COLUMNS,
        },
        n_folds=6,
        minimum_samples=args.minimum_samples,
    )
    baseline = baseline.loc[
        baseline["specification"] == "majority_baseline"
    ]
    _require_same_keys(expected, ridge, "ridge_current")
    _require_same_keys(expected, baseline, "majority_baseline")
    absolute = pd.concat(
        (challenger, ridge, baseline),
        ignore_index=True,
        sort=False,
    )
    absolute = _attach_mae(absolute, frame)
    metrics = aggregate_absolute_metrics(absolute, groups)
    relative_metrics = aggregate_relative_metrics(relative)
    regime_metrics = aggregate_regime_metrics(absolute, regimes)
    decision = regime_threshold_promotion_decision(
        metrics,
        regime_metrics,
    )
    fingerprint = input_content_fingerprint(
        frame,
        histories,
        analysis_tickers,
    )
    source_commit, dirty_worktree = _git_state()
    manifest = {
        "study_version": "regime_threshold_direction_v1",
        "latest_date": pd.Timestamp(
            frame.index.get_level_values("observation_date").max()
        ).date().isoformat(),
        "start_date": str(args.start_date),
        "ticker_count": len(analysis_tickers),
        "row_count": len(frame),
        "folds": 5,
        "horizon": HORIZON,
        "database": Path(args.database).name,
        "database_content_fingerprint": fingerprint,
        "source_commit": source_commit,
        "dirty_worktree": dirty_worktree,
        "configuration": {
            "cohort_seed": args.seed,
            "maximum_tickers": args.max_tickers,
            "minimum_samples": args.minimum_samples,
            "regime_version": REGIME_VERSION,
            "regime_prior_strength": REGIME_PRIOR_STRENGTH,
            "down_thresholds": list(DOWN_THRESHOLDS),
            "neutral_band": 0.01,
            "entry": "next_session_open",
            "exit": "fifth_future_session_close",
        },
        "online_authority": "none",
        "decision": decision,
        "causal_audit": {
            "identical_comparator_test_keys": True,
            "outer_training_labels_end_before_test_start": bool(
                (
                    pd.to_datetime(
                        challenger["training_label_end_max"]
                    )
                    < pd.to_datetime(challenger["test_start"])
                ).all()
            ),
            "threshold_source": "outer_training_inner_purged_oof_only",
            "relative_direction_serialized_as_absolute": False,
        },
        "fold_diagnostics": _records_for_manifest(fold_diagnostics),
        "absolute_metrics": _records_for_manifest(metrics),
        "qqq_relative_metrics": _records_for_manifest(relative_metrics),
        "regime_metrics": _records_for_manifest(regime_metrics),
        "named_case_diagnostics": _records_for_manifest(
            named_case_diagnostics(absolute)
        ),
    }
    validate_report_payload(manifest)
    report = render_report(metrics, relative_metrics, manifest)
    paths = publish_reports(
        args.output_prefix,
        metrics,
        manifest,
        report,
    )
    print(json.dumps({key: str(value) for key, value in paths.items()}))
    return 0


def _evaluate_stressed_regimes(regime_metrics, reasons):
    required = {
        "regime",
        "specification",
        "down_precision",
        "down_recall",
        "mean_return_predicted_down",
    }
    if not isinstance(regime_metrics, pd.DataFrame) or not required.issubset(
        regime_metrics.columns
    ):
        reasons.append("stressed_regimes:missing_metrics")
        return
    for regime in STRESSED_REGIMES:
        rows = regime_metrics.loc[
            (regime_metrics["regime"] == regime)
            & (regime_metrics["horizon"] == PRIMARY_HORIZON)
            & (regime_metrics["sample_mode"] == "overlapping")
        ].set_index("specification")
        if not {GLOBAL_MODEL, CHALLENGER}.issubset(rows.index):
            reasons.append(f"{regime}:missing_metrics")
            continue
        if (
            float(rows.loc[CHALLENGER, "down_recall"])
            < float(rows.loc[GLOBAL_MODEL, "down_recall"]) - 0.05
        ):
            reasons.append(f"{regime}:down_recall_degraded_over_0.05")
    combined = regime_metrics.loc[
        (regime_metrics["regime"] == "stressed_combined")
        & (regime_metrics["horizon"] == PRIMARY_HORIZON)
        & (regime_metrics["sample_mode"] == "overlapping")
    ].set_index("specification")
    if not {GLOBAL_MODEL, CHALLENGER}.issubset(combined.index):
        reasons.append("stressed_combined:missing_metrics")
        return
    baseline = combined.loc[GLOBAL_MODEL]
    candidate = combined.loc[CHALLENGER]
    if not (
        float(candidate["down_precision"])
        > float(baseline["down_precision"])
    ):
        reasons.append("stressed_combined:down_precision_not_improved")
    if not (
        float(candidate["mean_return_predicted_down"])
        < float(baseline["mean_return_predicted_down"])
    ):
        reasons.append("stressed_combined:down_return_not_improved")


def _require_metric_columns(metrics):
    required = {
        "scope",
        "horizon",
        "sample_mode",
        "fold",
        "specification",
        "balanced_accuracy",
        "down_precision",
        "down_recall",
        "down_coverage",
        "mean_return_predicted_down",
    }
    if not isinstance(metrics, pd.DataFrame):
        raise TypeError("metrics must be a DataFrame")
    missing = sorted(required.difference(metrics.columns))
    if missing:
        raise ValueError(f"metrics are missing columns: {missing}")


def _metric_rows(sample, *, scope, sample_mode, fold):
    rows = []
    for specification, selected in sample.groupby(
        "specification",
        sort=True,
    ):
        actual = selected["actual_direction"].astype(str)
        predicted = selected["predicted_direction"].astype(str)
        actual_down = actual == "down"
        predicted_down = predicted == "down"
        true_down = int((actual_down & predicted_down).sum())
        down_count = int(predicted_down.sum())
        actual_down_count = int(actual_down.sum())
        down_returns = selected.loc[predicted_down, "actual_return"]
        row = {
            "scope": str(scope),
            "horizon": HORIZON,
            "sample_mode": str(sample_mode),
            "fold": int(fold),
            "specification": str(specification),
            "sample_count": len(selected),
            "balanced_accuracy": float(
                balanced_accuracy_score(actual, predicted)
            ),
            "macro_f1": float(
                f1_score(
                    actual,
                    predicted,
                    labels=("down", "neutral", "up"),
                    average="macro",
                    zero_division=0,
                )
            ),
            "down_precision": (
                float(true_down / down_count) if down_count else 0.0
            ),
            "down_recall": (
                float(true_down / actual_down_count)
                if actual_down_count
                else 0.0
            ),
            "down_coverage": float(predicted_down.mean()),
            "mean_return_predicted_down": _finite_mean(down_returns),
            "median_return_predicted_down": _finite_median(down_returns),
        }
        if "maximum_adverse_excursion_5" in selected:
            row["mean_mae_predicted_down"] = _finite_mean(
                selected.loc[
                    predicted_down,
                    "maximum_adverse_excursion_5",
                ]
            )
        rows.append(row)
    return rows


def _non_overlapping_rows(predictions, horizon):
    ordered = predictions.sort_values(
        ["specification", "fold", "ticker", "observation_date"],
        kind="mergesort",
    ).copy()
    positions = ordered.groupby(
        ["specification", "fold", "ticker"],
        sort=False,
    ).cumcount()
    return ordered.loc[positions.mod(int(horizon)) == 0].copy()


def _finite_mean(values):
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric.loc[np.isfinite(numeric)]
    return None if numeric.empty else float(numeric.mean())


def _finite_median(values):
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric.loc[np.isfinite(numeric)]
    return None if numeric.empty else float(numeric.median())


def _require_same_keys(expected, actual, specification):
    columns = ["ticker", "observation_date", "horizon", "fold"]
    expected_rows = expected.loc[:, columns].sort_values(
        columns,
        kind="mergesort",
    ).reset_index(drop=True)
    actual_rows = actual.loc[:, columns].sort_values(
        columns,
        kind="mergesort",
    ).reset_index(drop=True)
    if not expected_rows.equals(actual_rows):
        raise RuntimeError(
            f"{specification} does not share identical outer test keys"
        )


def _git_state():
    repository = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        return commit, bool(status.strip())
    except (OSError, subprocess.SubprocessError):
        return "unavailable", True


def _attach_mae(predictions, frame):
    result = predictions.copy(deep=True)
    keys = pd.MultiIndex.from_arrays(
        (
            result["ticker"].astype(str),
            pd.to_datetime(result["observation_date"]),
        ),
        names=("ticker", "observation_date"),
    )
    result["maximum_adverse_excursion_5"] = frame[
        "maximum_adverse_excursion_5"
    ].reindex(keys).to_numpy(dtype=float)
    return result


def _records_for_manifest(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("manifest records must come from a DataFrame")
    return [
        {
            str(key): _manifest_value(value)
            for key, value in row.items()
        }
        for row in frame.to_dict(orient="records")
    ]


def _manifest_value(value):
    if isinstance(value, Mapping):
        return {
            str(key): _manifest_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_manifest_value(item) for item in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if value is None:
        return None
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    if isinstance(value, np.generic):
        return _manifest_value(value.item())
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
            if value is None or pd.isna(value):
                cells.append("—")
            elif isinstance(value, float):
                cells.append(f"{value:.4f}")
            else:
                cells.append(str(value).replace("|", "\\|"))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _validate_value(value, path):
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            if _SECRET_KEY.search(name):
                raise ValueError(
                    f"credential-shaped key at {'.'.join((*path, name))}"
                )
            _validate_value(item, (*path, name))
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_value(item, (*path, str(index)))
        return
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(float(value)):
            raise ValueError(
                f"non-finite value at {'.'.join(path) or '<root>'}"
            )
        return
    if isinstance(value, (str, os.PathLike)):
        text = os.fspath(value)
        if os.path.isabs(text):
            raise ValueError(
                f"absolute path at {'.'.join(path) or '<root>'}"
            )
        if _SECRET_VALUE.search(text):
            raise ValueError(
                f"credential-shaped value at {'.'.join(path) or '<root>'}"
            )
        return
    if isinstance(value, np.generic):
        _validate_value(value.item(), path)


if __name__ == "__main__":
    raise SystemExit(main())
