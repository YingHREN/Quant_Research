"""Fixed, leakage-safe walk-forward comparison on expanded market data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.expanded_market_data import ExpandedMarketDataRepository
from research.market_regime import build_market_regime_frame
from research.market_direction_model import (
    attach_next_open_targets,
    evaluate_direction_ablation,
    walk_forward_direction_predictions,
    walk_forward_ridge_predictions,
)
from research.recency_momentum import (
    RECENCY_FEATURE_COLUMNS,
    build_recency_momentum_frame,
)
from web.forecasts.dataset import (
    RIDGE_V4_FEATURE_COLUMNS,
    build_feature_frame,
)
from web.market_groups import REFERENCE_TICKERS, SECTOR_ETFS


SEMICONDUCTOR_INDUSTRIES = frozenset(
    {"semiconductors & related devices"}
)
SOFTWARE_INDUSTRIES = frozenset(
    {
        "services-prepackaged software",
        "services-computer programming, data processing, etc.",
        "services-computer processing & data preparation",
        "services-computer integrated systems design",
        "services-computer programming services",
    }
)
DECAY_HEAD_COLUMNS = RECENCY_FEATURE_COLUMNS[:5]
DECAY_VOLUME_COLUMNS = RECENCY_FEATURE_COLUMNS[5:7]
FOCUS_TICKERS = (
    "MU",
    "NBIS",
    "AMD",
    "MRVL",
    "INTC",
    "ADBE",
)
REGIME_LABELS_ZH = {
    "uptrend": "上涨趋势",
    "range_bound": "震荡",
    "under_pressure": "市场承压",
    "correction": "修正",
    "acute_selloff": "急跌",
    "unavailable": "不可用",
}
SCOPE_LABELS_ZH = {
    "all": "全部",
    "semiconductor": "半导体",
    "software": "软件",
    "other": "其他",
}


def classify_study_groups(classifications):
    """Map SEC point-in-time industries to fixed evaluation strata."""
    result = {}
    for raw_ticker, rows in classifications.items():
        ticker = str(raw_ticker).strip().upper()
        sec = rows.get("sec") or {}
        label = str(sec.get("industry_label") or "").strip().casefold()
        if label in SEMICONDUCTOR_INDUSTRIES:
            result[ticker] = "semiconductor"
        elif label in SOFTWARE_INDUSTRIES:
            result[ticker] = "software"
        else:
            result[ticker] = "other"
    return result


def select_analysis_tickers(groups, *, max_tickers=0, seed=20260726):
    """Return a deterministic, focus-preserving research cohort."""
    normalized = {
        str(ticker).strip().upper(): str(group)
        for ticker, group in groups.items()
        if str(ticker).strip()
    }
    limit = int(max_tickers or 0)
    if limit <= 0 or limit >= len(normalized):
        return tuple(sorted(normalized))
    if limit < min(len(normalized), len(set(FOCUS_TICKERS) & set(normalized))):
        raise ValueError("max_tickers is smaller than the named focus cohort")
    selected = [
        ticker for ticker in FOCUS_TICKERS if ticker in normalized
    ]
    remaining = set(normalized).difference(selected)
    priority = {
        group: sorted(
            (
                ticker
                for ticker in remaining
                if normalized[ticker] == group
            ),
            key=lambda ticker: _sample_key(ticker, seed),
        )
        for group in ("semiconductor", "software")
    }
    priority_count = len(selected) + sum(map(len, priority.values()))
    if priority_count <= limit:
        for candidates in priority.values():
            selected.extend(candidates)
            remaining.difference_update(candidates)
    else:
        target = {
            "semiconductor": max(1, int(limit * 0.40)),
            "software": max(1, int(limit * 0.40)),
        }
        for group, candidates in priority.items():
            already = sum(normalized[ticker] == group for ticker in selected)
            take = max(0, min(len(candidates), target[group] - already))
            selected.extend(candidates[:take])
            remaining.difference_update(candidates[:take])
        other_candidates = sorted(
            (
                ticker
                for ticker in remaining
                if normalized[ticker] == "other"
            ),
            key=lambda ticker: _sample_key(ticker, seed),
        )
        take = max(0, min(len(other_candidates), limit - len(selected)))
        selected.extend(other_candidates[:take])
        remaining.difference_update(other_candidates[:take])
    if len(selected) < limit:
        overflow = sorted(
            remaining,
            key=lambda ticker: _sample_key(ticker, seed),
        )
        selected.extend(overflow[: limit - len(selected)])
    return tuple(sorted(selected))


def _sample_key(ticker, seed):
    return hashlib.sha256(
        f"{int(seed)}:{ticker}".encode("utf-8")
    ).hexdigest()


def expanded_feature_sets(base_columns):
    """Return the frozen additive ablation sets in increasing complexity."""
    current = tuple(dict.fromkeys(str(column) for column in base_columns))
    if not current or any(not column for column in current):
        raise ValueError("base_columns must not be empty")
    decay = current + tuple(DECAY_HEAD_COLUMNS)
    volume = decay + tuple(DECAY_VOLUME_COLUMNS)
    market = volume + tuple(
        column
        for column in RECENCY_FEATURE_COLUMNS
        if column not in DECAY_HEAD_COLUMNS + DECAY_VOLUME_COLUMNS
    )
    return {
        "ridge_current": current,
        "ridge_decay_only": decay,
        "ridge_decay_volume": volume,
        "ridge_decay_market": market,
    }


def evaluate_expanded_scope(
    frame,
    *,
    scope,
    base_columns,
    horizons=(5, 20),
    n_folds=5,
    minimum_samples=1_000,
):
    """Evaluate every challenger on identical point-in-time rows and folds."""
    feature_sets = expanded_feature_sets(base_columns)
    metric_frames = []
    prediction_frames = []
    for horizon in horizons:
        direction = walk_forward_direction_predictions(
            frame,
            horizon=horizon,
            feature_sets={
                "logistic_decay_market": feature_sets["ridge_decay_market"],
            },
            n_folds=n_folds,
            minimum_samples=minimum_samples,
        )
        ridge_predictions = []
        for specification, columns in feature_sets.items():
            ridge_predictions.append(
                walk_forward_ridge_predictions(
                    frame,
                    horizon=horizon,
                    feature_columns=columns,
                    n_folds=n_folds,
                    minimum_samples=minimum_samples,
                    specification=specification,
                )
            )
        predictions = pd.concat(
            (direction, *ridge_predictions),
            ignore_index=True,
            sort=False,
        )
        overlapping = _evaluate_prediction_sample(
            predictions,
            sample_mode="overlapping",
        )
        non_overlapping_predictions = _non_overlapping_rows(
            predictions,
            horizon,
        )
        non_overlapping = _evaluate_prediction_sample(
            non_overlapping_predictions,
            sample_mode="non_overlapping",
        )
        metrics = pd.concat(
            (overlapping, non_overlapping),
            ignore_index=True,
        )
        metrics.insert(0, "horizon", int(horizon))
        metrics.insert(0, "scope", str(scope))
        predictions.insert(0, "scope", str(scope))
        metric_frames.append(metrics)
        prediction_frames.append(predictions)
    return (
        pd.concat(metric_frames, ignore_index=True),
        pd.concat(prediction_frames, ignore_index=True),
    )


def evaluate_predictions_by_regime(
    predictions,
    regimes,
    *,
    minimum_fold_samples=30,
):
    """Stratify fixed predictions by causal market state and comparable fold."""
    required = {
        "scope",
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
    if not isinstance(regimes, pd.DataFrame) or "regime" not in regimes:
        raise ValueError("regimes must be a DataFrame with a regime column")
    if not isinstance(regimes.index, pd.DatetimeIndex):
        raise ValueError("regimes index must be a DatetimeIndex")
    if regimes.index.has_duplicates:
        raise ValueError("regimes index must not contain duplicate dates")
    checked_minimum = int(minimum_fold_samples)
    if checked_minimum < 1:
        raise ValueError("minimum_fold_samples must be positive")

    regime_lookup = regimes.copy(deep=True)
    regime_lookup.index = regime_lookup.index.tz_localize(None).normalize()
    attached = predictions.copy(deep=True)
    observation_dates = pd.to_datetime(
        attached["observation_date"],
        errors="raise",
    )
    if observation_dates.dt.tz is not None:
        observation_dates = observation_dates.dt.tz_localize(None)
    attached["observation_date"] = observation_dates.dt.normalize()
    attached["regime"] = (
        attached["observation_date"]
        .map(regime_lookup["regime"])
        .fillna("unavailable")
    )
    if "regime_version" in regime_lookup:
        attached["regime_version"] = (
            attached["observation_date"]
            .map(regime_lookup["regime_version"])
            .fillna("unavailable")
        )
    else:
        attached["regime_version"] = "unavailable"

    metric_frames = []
    for (scope, horizon), horizon_rows in attached.groupby(
        ["scope", "horizon"],
        sort=True,
    ):
        modes = {
            "overlapping": horizon_rows,
            "non_overlapping": _non_overlapping_rows(
                horizon_rows,
                int(horizon),
            ),
        }
        for sample_mode, sample_rows in modes.items():
            for (regime, version), selected in sample_rows.groupby(
                ["regime", "regime_version"],
                sort=True,
                dropna=False,
            ):
                if selected.empty:
                    continue
                metrics = _evaluate_prediction_sample(
                    selected,
                    sample_mode=sample_mode,
                )
                fold_comparison = _regime_fold_comparison(
                    selected,
                    minimum_fold_samples=checked_minimum,
                )
                metrics["comparable_fold_count"] = metrics[
                    "specification"
                ].map(
                    lambda specification: fold_comparison.get(
                        str(specification),
                        (0, np.nan),
                    )[0]
                )
                metrics["fold_win_rate_vs_ridge_current"] = metrics[
                    "specification"
                ].map(
                    lambda specification: fold_comparison.get(
                        str(specification),
                        (0, np.nan),
                    )[1]
                )
                metrics.insert(
                    0,
                    "regime_end",
                    selected["observation_date"].max(),
                )
                metrics.insert(
                    0,
                    "regime_start",
                    selected["observation_date"].min(),
                )
                metrics.insert(0, "regime_version", str(version))
                metrics.insert(0, "regime", str(regime))
                metrics.insert(0, "horizon", int(horizon))
                metrics.insert(0, "scope", str(scope))
                metric_frames.append(metrics)
    if not metric_frames:
        return pd.DataFrame()
    return pd.concat(metric_frames, ignore_index=True, sort=False)


def summarize_market_regimes(regimes):
    """Summarize dates and shares for every observed market state."""
    if not isinstance(regimes, pd.DataFrame) or "regime" not in regimes:
        raise ValueError("regimes must be a DataFrame with a regime column")
    if not isinstance(regimes.index, pd.DatetimeIndex):
        raise ValueError("regimes index must be a DatetimeIndex")
    if regimes.empty:
        return pd.DataFrame(
            columns=(
                "regime",
                "session_count",
                "start_date",
                "end_date",
                "session_share",
            )
        )
    total = len(regimes)
    rows = []
    for regime, selected in regimes.groupby("regime", sort=True):
        rows.append(
            {
                "regime": str(regime),
                "session_count": len(selected),
                "start_date": selected.index.min(),
                "end_date": selected.index.max(),
                "session_share": len(selected) / total,
            }
        )
    return pd.DataFrame(rows)


def render_market_regime_report(metrics, coverage, manifest):
    """Render the frozen market-regime diagnosis without promotion claims."""
    coverage_display = coverage.copy(deep=True)
    if not coverage_display.empty:
        coverage_display.insert(
            1,
            "阶段",
            coverage_display["regime"].map(REGIME_LABELS_ZH).fillna(
                coverage_display["regime"]
            ),
        )
    selected_columns = (
        "scope",
        "horizon",
        "regime",
        "sample_mode",
        "specification",
        "sample_count",
        "balanced_accuracy",
        "macro_f1",
        "down_precision",
        "down_recall",
        "up_precision",
        "up_recall",
        "return_mae",
        "rank_ic",
        "comparable_fold_count",
        "fold_win_rate_vs_ridge_current",
    )
    metric_display = metrics.copy(deep=True)
    metric_display = metric_display.loc[
        metric_display["specification"].isin(
            ("ridge_current", "logistic_decay_market")
        )
    ].copy()
    for column in selected_columns:
        if column not in metric_display:
            metric_display[column] = np.nan
    metric_display = metric_display.loc[:, selected_columns]
    metric_display.insert(
        1,
        "股票组",
        metric_display["scope"].map(SCOPE_LABELS_ZH).fillna(
            metric_display["scope"]
        ),
    )
    metric_display.insert(
        4,
        "市场阶段",
        metric_display["regime"].map(REGIME_LABELS_ZH).fillna(
            metric_display["regime"]
        ),
    )
    metric_display["证据状态"] = np.where(
        (
            metric_display["specification"] == "ridge_current"
        ),
        "基线",
        np.where(
            pd.to_numeric(
                metric_display["comparable_fold_count"],
                errors="coerce",
            ).fillna(0)
            >= 2,
            "可比较",
            "证据不足",
        ),
    )
    semiconductor = metric_display.loc[
        (metric_display["scope"] == "semiconductor")
        & (metric_display["horizon"] == 5)
        & (metric_display["sample_mode"] == "overlapping")
    ]
    diagnosis = _semiconductor_regime_diagnosis(semiconductor)
    return "\n".join(
        (
            "# 市场阶段分层走步诊断",
            "",
            f"- 数据截止：{manifest['latest_date']}",
            f"- 研究股票：{manifest['ticker_count']} 只",
            f"- 样本起点：{manifest['start_date']}",
            f"- 走步折数：{manifest['folds']}",
            "- 状态规则：`market_regime_v1`，只使用观察日及此前 QQQ/SPY。",
            "- 执行口径：观察日收盘后生成信号，下一交易日开盘进入。",
            "- 结果只用于诊断，不修改 Ridge、Logistic 或线上决策。",
            "",
            "## 阶段覆盖",
            "",
            _markdown_table(coverage_display),
            "",
            "## Logistic 相对 Ridge 的半导体诊断",
            "",
            diagnosis,
            "",
            "## 分层指标",
            "",
            _markdown_table(metric_display),
            "",
            "## 判读限制",
            "",
            "- `comparable_fold_count` 少于 2 的阶段标为证据不足，不据此判断模型优劣。",
            "- 当前股票池和 SEC 分类仍存在幸存者偏差；阶段拆分不能消除该偏差。",
            "- 阶段规则在查看本轮结果前已经冻结，不根据已知案例回调阈值。",
            "",
        )
    )


def _semiconductor_regime_diagnosis(rows):
    if rows.empty:
        return "- 没有成熟的半导体五日重叠样本。"
    lines = []
    for regime, selected in rows.groupby("regime", sort=True):
        indexed = selected.set_index("specification")
        if not {
            "ridge_current",
            "logistic_decay_market",
        }.issubset(indexed.index):
            continue
        ridge = indexed.loc["ridge_current"]
        logistic = indexed.loc["logistic_decay_market"]
        label = REGIME_LABELS_ZH.get(regime, regime)
        fold_count = int(logistic["comparable_fold_count"] or 0)
        win_rate = logistic["fold_win_rate_vs_ridge_current"]
        if fold_count < 2 or pd.isna(win_rate):
            conclusion = "证据不足"
        elif win_rate > 0.5:
            conclusion = "Logistic 跨折占优"
        elif win_rate < 0.5:
            conclusion = "Ridge 跨折占优"
        else:
            conclusion = "两者持平"
        lines.append(
            f"- {label}：{conclusion}；可比较 {fold_count} 折，"
            + (
                "胜率不可用。"
                if pd.isna(win_rate)
                else f"Logistic 相对 Ridge 折次胜率 {win_rate:.1%}。"
            )
            + " 平衡准确率差 "
            f"{logistic['balanced_accuracy'] - ridge['balanced_accuracy']:+.3f}；"
            + "下跌召回差 "
            f"{logistic['down_recall'] - ridge['down_recall']:+.3f}。"
        )
    return "\n".join(lines)


def _regime_fold_comparison(predictions, *, minimum_fold_samples):
    baseline = "ridge_current"
    by_specification = {
        str(specification): selected
        for specification, selected in predictions.groupby(
            "specification",
            sort=False,
        )
    }
    baseline_rows = by_specification.get(baseline)
    if baseline_rows is None:
        return {
            specification: (0, np.nan)
            for specification in by_specification
        }
    result = {baseline: (0, np.nan)}
    for specification, challenger_rows in by_specification.items():
        if specification == baseline:
            continue
        wins = []
        common_folds = sorted(
            set(baseline_rows["fold"]).intersection(challenger_rows["fold"])
        )
        for fold in common_folds:
            current = baseline_rows.loc[baseline_rows["fold"] == fold]
            challenger = challenger_rows.loc[
                challenger_rows["fold"] == fold
            ]
            if (
                len(current) < minimum_fold_samples
                or len(challenger) < minimum_fold_samples
                or current["actual_direction"].nunique() < 2
                or challenger["actual_direction"].nunique() < 2
            ):
                continue
            current_score = _balanced_accuracy_rows(current)
            challenger_score = _balanced_accuracy_rows(challenger)
            delta = challenger_score - current_score
            wins.append(
                1.0 if delta > 1e-12 else 0.0 if delta < -1e-12 else 0.5
            )
        result[specification] = (
            len(wins),
            np.nan if not wins else float(np.mean(wins)),
        )
    return result


def _balanced_accuracy_rows(rows):
    actual = rows["actual_direction"].astype(str).to_numpy()
    predicted = rows["predicted_direction"].astype(str).to_numpy()
    return float(
        np.mean(
            [
                np.mean(predicted[actual == label] == label)
                for label in np.unique(actual)
            ]
        )
    )


def prepare_expanded_frame(
    histories,
    *,
    analysis_tickers,
    classifications,
    start_date=None,
    sector_mode="none",
):
    """Build current and challenger features before applying study filters."""
    checked_mode = str(sector_mode)
    if checked_mode not in {"none", "sec_snapshot"}:
        raise ValueError("sector_mode must be none or sec_snapshot")
    benchmark_by_ticker = {}
    if checked_mode == "sec_snapshot":
        for ticker in analysis_tickers:
            sec = (classifications.get(ticker) or {}).get("sec") or {}
            benchmark = SECTOR_ETFS.get(sec.get("sector_key"))
            if benchmark:
                benchmark_by_ticker[ticker] = benchmark
    base = build_feature_frame(histories)
    recency = build_recency_momentum_frame(
        histories,
        benchmark_by_ticker=benchmark_by_ticker,
    )
    combined = base.join(recency, how="left")
    labeled = attach_next_open_targets(
        combined,
        histories,
        horizons=(5, 20),
    )
    selected = labeled.index.get_level_values("ticker").isin(
        set(analysis_tickers)
    )
    if start_date is not None:
        start = pd.Timestamp(start_date)
        if pd.isna(start):
            raise ValueError("start_date must be valid")
        if start.tz is not None:
            start = start.tz_localize(None)
        selected &= (
            labeled.index.get_level_values("observation_date")
            >= start.normalize()
        )
    return labeled.loc[selected].sort_index()


def run_expanded_study(
    histories,
    *,
    classifications,
    analysis_tickers,
    start_date="2018-01-01",
    sector_mode="none",
    horizons=(5, 20),
    n_folds=5,
    minimum_samples=1_000,
):
    """Run fixed comparisons for all, semiconductor, software, and other."""
    groups = classify_study_groups(classifications)
    frame = prepare_expanded_frame(
        histories,
        analysis_tickers=analysis_tickers,
        classifications=classifications,
        start_date=start_date,
        sector_mode=sector_mode,
    )
    metrics = []
    predictions = []
    scope_masks = {
        "all": np.ones(len(frame), dtype=bool),
        **{
            group: frame.index.get_level_values("ticker").map(
                lambda ticker, selected=group: groups.get(ticker) == selected
            )
            for group in ("semiconductor", "software", "other")
        },
    }
    for scope, mask in scope_masks.items():
        selected = frame.loc[np.asarray(mask, dtype=bool)]
        if selected.empty:
            continue
        scope_minimum = (
            int(minimum_samples)
            if scope == "all"
            else max(100, int(minimum_samples) // 5)
        )
        scope_metrics, scope_predictions = evaluate_expanded_scope(
            selected,
            scope=scope,
            base_columns=RIDGE_V4_FEATURE_COLUMNS,
            horizons=horizons,
            n_folds=n_folds,
            minimum_samples=scope_minimum,
        )
        metrics.append(scope_metrics)
        predictions.append(scope_predictions)
    return (
        pd.concat(metrics, ignore_index=True),
        pd.concat(predictions, ignore_index=True),
        frame,
        groups,
    )


def research_promotion_decision(metrics):
    """Apply the metric gate, then block production on cohort-time leakage."""
    primary = metrics.loc[
        (metrics["scope"] == "all")
        & (metrics["horizon"] == 5)
        & (metrics["sample_mode"].isin(("overlapping", "non_overlapping")))
    ]
    ridge_reasons = []
    direction_reasons = []
    for sample_mode in ("overlapping", "non_overlapping"):
        rows = primary.loc[
            primary["sample_mode"] == sample_mode
        ].set_index("specification")
        ridge_reasons.extend(
            _candidate_gate_reasons(
                rows,
                "ridge_decay_market",
                sample_mode,
                require_return_fit=True,
            )
        )
        direction_reasons.extend(
            _candidate_gate_reasons(
                rows,
                "logistic_decay_market",
                sample_mode,
                require_return_fit=False,
            )
        )
    for scope in ("semiconductor", "software"):
        for sample_mode in ("overlapping", "non_overlapping"):
            rows = metrics.loc[
                (metrics["scope"] == scope)
                & (metrics["horizon"] == 5)
                & (metrics["sample_mode"] == sample_mode)
            ].set_index("specification")
            required = ("ridge_current", "logistic_decay_market")
            prefix = (
                f"{scope}:{sample_mode}:logistic_decay_market"
            )
            if any(name not in rows.index for name in required):
                direction_reasons.append(f"{prefix}:missing_subgroup")
                continue
            challenger = rows.loc["logistic_decay_market"]
            current = rows.loc["ridge_current"]
            if (
                challenger["balanced_accuracy"] + 0.005
                < current["balanced_accuracy"]
            ):
                direction_reasons.append(
                    f"{prefix}:subgroup_balanced_accuracy_degraded"
                )
            fold_win = challenger.get(
                "fold_win_rate_vs_ridge_current"
            )
            if pd.isna(fold_win) or float(fold_win) <= 0.50:
                direction_reasons.append(
                    f"{prefix}:subgroup_fold_majority_not_won"
                )
    ridge_passed = not ridge_reasons
    direction_passed = not direction_reasons
    return {
        "eligible": False,
        "metric_gate_passed": ridge_passed or direction_passed,
        "ridge_metric_gate_passed": ridge_passed,
        "ridge_metric_gate_reasons": ridge_reasons,
        "direction_metric_gate_passed": direction_passed,
        "direction_metric_gate_reasons": direction_reasons,
        "metric_gate_reasons": (
            ridge_reasons
            if not ridge_passed
            else direction_reasons
        ),
        "reason": (
            "production_blocked_until_point_in_time_universe_and_"
            "classification_history_are_available"
        ),
    }


def _candidate_gate_reasons(
    rows,
    candidate,
    sample_mode,
    *,
    require_return_fit,
):
    required = ("majority_baseline", "ridge_current", candidate)
    if any(name not in rows.index for name in required):
        return [f"{sample_mode}:{candidate}:missing_comparator"]
    challenger = rows.loc[candidate]
    current = rows.loc["ridge_current"]
    majority = rows.loc["majority_baseline"]
    reasons = []
    prefix = f"{sample_mode}:{candidate}"
    if challenger["balanced_accuracy"] <= max(
        current["balanced_accuracy"],
        majority["balanced_accuracy"],
    ):
        reasons.append(f"{prefix}:balanced_accuracy_not_improved")
    if challenger["macro_f1"] <= max(
        current["macro_f1"],
        majority["macro_f1"],
    ):
        reasons.append(f"{prefix}:macro_f1_not_improved")
    if challenger["down_recall"] + 0.02 < current["down_recall"]:
        reasons.append(f"{prefix}:down_recall_degraded")
    if (
        "up_precision" in challenger
        and pd.notna(challenger.get("up_precision"))
        and pd.notna(current.get("up_precision"))
        and challenger["up_precision"] + 0.02 < current["up_precision"]
    ):
        reasons.append(f"{prefix}:up_precision_degraded")
    fold_win = challenger.get("fold_win_rate_vs_ridge_current")
    if pd.isna(fold_win) or float(fold_win) <= 0.50:
        reasons.append(f"{prefix}:fold_majority_not_won")
    if require_return_fit:
        if (
            pd.notna(challenger.get("return_mae"))
            and pd.notna(current.get("return_mae"))
            and challenger["return_mae"] > current["return_mae"]
        ):
            reasons.append(f"{prefix}:return_mae_degraded")
        if (
            pd.isna(challenger.get("rank_ic"))
            or challenger["rank_ic"] <= 0
            or (
                pd.notna(current.get("rank_ic"))
                and challenger["rank_ic"] <= current["rank_ic"]
            )
        ):
            reasons.append(f"{prefix}:rank_ic_not_improved")
    return reasons


def render_expanded_report(metrics, manifest, diagnostics=None):
    decision = manifest["decision"]
    lines = [
        "# 扩展数据固定走步预测实验",
        "",
        f"- 数据截止：{manifest['latest_date']}",
        f"- 研究股票：{manifest['ticker_count']} 只",
        f"- 样本起点：{manifest['start_date']}",
        f"- 点时价格行：{manifest['row_count']:,}",
        "- 标签：观察日收盘后，下一交易日开盘进入，第 5/20 个未来交易日收盘退出。",
        "- 验证：扩展窗口走步，训练标签结束日必须严格早于测试窗口。",
        "- 同时报告每日重叠样本和按股票/折次抽取的非重叠样本。",
        "",
        "## 结论",
        "",
        "- Ridge 近因衰减门槛："
        + (
            "通过"
            if decision["ridge_metric_gate_passed"]
            else "未通过"
        ),
        "- 直接方向分类门槛："
        + (
            "通过"
            if decision["direction_metric_gate_passed"]
            else "未通过"
        ),
        f"- 生产晋级：禁止（{decision['reason']}）",
        "- 当前扩展库只有最新股票池和最新 SEC 分类快照；这些分类只用于分层报告。",
        "- 默认实验不把最新 SEC 分类映射作为历史模型输入；板块衰减特征因此明确缺失。",
        "",
        "## 对比模型",
        "",
        "- `ridge_current`：当前 Ridge v4 的冻结 24 因子。",
        "- `ridge_decay_only`：增加五个近因衰减动量头。",
        "- `ridge_decay_volume`：再增加量价确认与弱收盘压力。",
        "- `ridge_decay_market`：再增加相对 QQQ/板块与方向一致性。",
        "- `logistic_decay_market`：同一完整特征集的直接三分类挑战模型。",
        "- `majority_baseline`：每个训练折的多数类别无技巧基线。",
        "",
        "## 样本外指标",
        "",
        _markdown_table(metrics),
    ]
    if diagnostics is not None and not diagnostics.empty:
        lines.extend(
            (
                "",
                "## 指定股票事件日期",
                "",
                _markdown_table(diagnostics),
            )
        )
    lines.extend(
        [
            "",
            "## 限制",
            "",
            "- 当前股票池存在幸存者偏差，不能据此晋级生产模型。",
            "- SEC/行为分类缺少历史版本，不能在历史日期假装当时已知。",
            "- 日线成交量只能提供买卖压力代理，不能证明真实机构身份或主动买卖方向。",
            "",
        ]
    )
    return "\n".join(lines)


def _markdown_table(frame):
    if frame.empty:
        return "_无成熟样本。_"
    display = frame.copy()
    for column in display.select_dtypes(include="number"):
        display[column] = display[column].map(
            lambda value: "" if pd.isna(value) else f"{value:.4f}"
        )
    columns = list(display.columns)
    rows = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for values in display.astype(str).itertuples(index=False, name=None):
        rows.append(
            "| "
            + " | ".join(value.replace("|", "\\|") for value in values)
            + " |"
        )
    return "\n".join(rows)


def _evaluate_prediction_sample(predictions, *, sample_mode):
    metrics = evaluate_direction_ablation(predictions)
    metrics = _attach_continuous_metrics(metrics, predictions)
    metrics["sample_mode"] = str(sample_mode)
    fold_scores = _fold_balanced_accuracy(predictions)
    baseline_scores = fold_scores.get("ridge_current", {})
    for specification, selected in predictions.groupby(
        "specification",
        sort=False,
    ):
        actual = selected["actual_direction"].astype(str)
        predicted = selected["predicted_direction"].astype(str)
        location = metrics["specification"] == specification
        metrics.loc[location, "accuracy"] = float(
            (actual == predicted).mean()
        )
        actual_up = actual == "up"
        predicted_up = predicted == "up"
        metrics.loc[location, "up_precision"] = _ratio(
            int((actual_up & predicted_up).sum()),
            int(predicted_up.sum()),
        )
        metrics.loc[location, "up_recall"] = _ratio(
            int((actual_up & predicted_up).sum()),
            int(actual_up.sum()),
        )
        if specification == "ridge_current":
            metrics.loc[
                location,
                "fold_win_rate_vs_ridge_current",
            ] = np.nan
        else:
            common_folds = sorted(
                set(baseline_scores).intersection(
                    fold_scores.get(specification, {})
                )
            )
            deltas = [
                fold_scores[specification][fold] - baseline_scores[fold]
                for fold in common_folds
            ]
            metrics.loc[
                location,
                "fold_win_rate_vs_ridge_current",
            ] = (
                np.nan
                if not deltas
                else float(
                    np.mean(
                        np.select(
                            (
                                np.asarray(deltas) > 1e-12,
                                np.asarray(deltas) < -1e-12,
                            ),
                            (1.0, 0.0),
                            default=0.5,
                        )
                    )
                )
            )
    return metrics


def _fold_balanced_accuracy(predictions):
    result = {}
    for (specification, fold), selected in predictions.groupby(
        ["specification", "fold"],
        sort=False,
    ):
        actual = selected["actual_direction"].astype(str).to_numpy()
        predicted = selected["predicted_direction"].astype(str).to_numpy()
        classes = np.unique(actual)
        result.setdefault(str(specification), {})[int(fold)] = float(
            np.mean(
                [
                    np.mean(predicted[actual == label] == label)
                    for label in classes
                ]
            )
        )
    return result


def _non_overlapping_rows(predictions, horizon):
    ordered = predictions.sort_values(
        ["specification", "fold", "ticker", "observation_date"],
        kind="mergesort",
    ).copy()
    position = ordered.groupby(
        ["specification", "fold", "ticker"],
        sort=False,
    ).cumcount()
    return ordered.loc[position.mod(int(horizon)) == 0].copy()


def _attach_continuous_metrics(metrics, predictions):
    result = metrics.copy(deep=True)
    result["return_mae"] = np.nan
    result["rank_ic"] = np.nan
    for specification, selected in predictions.groupby(
        "specification",
        sort=False,
    ):
        predicted = pd.to_numeric(
            selected.get("predicted_return"),
            errors="coerce",
        )
        actual = pd.to_numeric(selected["actual_return"], errors="coerce")
        valid = predicted.notna() & actual.notna()
        if not valid.any():
            continue
        location = result["specification"] == specification
        result.loc[location, "return_mae"] = float(
            (predicted.loc[valid] - actual.loc[valid]).abs().mean()
        )
        if (
            predicted.loc[valid].nunique() > 1
            and actual.loc[valid].nunique() > 1
        ):
            result.loc[location, "rank_ic"] = float(
                predicted.loc[valid].corr(
                    actual.loc[valid],
                    method="spearman",
                )
            )
    return result


def _ratio(numerator, denominator):
    return np.nan if denominator == 0 else numerator / denominator


def focus_diagnostics(predictions):
    """Return fixed dates discussed before the experiment was evaluated."""
    dates = pd.to_datetime(
        (
            "2026-06-25",
            "2026-06-30",
            "2026-07-01",
            "2026-07-17",
            "2026-07-23",
        )
    )
    selected = predictions.loc[
        (predictions["scope"] == "all")
        & predictions["ticker"].isin(FOCUS_TICKERS)
        & predictions["observation_date"].isin(dates)
    ].copy()
    columns = (
        "ticker",
        "observation_date",
        "horizon",
        "fold",
        "specification",
        "actual_return",
        "actual_direction",
        "predicted_direction",
        "predicted_return",
        "training_samples",
    )
    for column in columns:
        if column not in selected:
            selected[column] = np.nan
    return selected.loc[:, columns].sort_values(
        ["ticker", "observation_date", "horizon", "specification"],
        kind="mergesort",
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        default="data/research_prices.db",
    )
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--max-tickers", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--minimum-samples", type=int, default=1_000)
    parser.add_argument(
        "--sector-mode",
        choices=("none", "sec_snapshot"),
        default="none",
    )
    parser.add_argument(
        "--report",
        default="reports/expanded-walkforward.md",
    )
    parser.add_argument(
        "--metrics",
        default="reports/expanded-walkforward.csv",
    )
    parser.add_argument(
        "--manifest",
        default="reports/expanded-walkforward.json",
    )
    parser.add_argument(
        "--diagnostics",
        default="reports/expanded-walkforward-diagnostics.csv",
    )
    parser.add_argument(
        "--regime-metrics",
        default="reports/expanded-walkforward-regimes.csv",
    )
    parser.add_argument(
        "--regime-report",
        default="reports/expanded-walkforward-regimes.md",
    )
    parser.add_argument("--minimum-regime-fold-samples", type=int, default=30)
    args = parser.parse_args(argv)

    repository = ExpandedMarketDataRepository(args.database)
    classifications = repository.load_classifications()
    groups = classify_study_groups(classifications)
    analysis_tickers = select_analysis_tickers(
        groups,
        max_tickers=args.max_tickers,
        seed=args.seed,
    )
    requested = tuple(
        sorted(set(analysis_tickers).union(REFERENCE_TICKERS))
    )
    histories = repository.load_universe_histories(tickers=requested)
    metrics, predictions, frame, group_map = run_expanded_study(
        histories,
        classifications=classifications,
        analysis_tickers=analysis_tickers,
        start_date=args.start,
        sector_mode=args.sector_mode,
        n_folds=args.folds,
        minimum_samples=args.minimum_samples,
    )
    decision = research_promotion_decision(metrics)
    diagnostics = focus_diagnostics(predictions)
    regimes = build_market_regime_frame(histories)
    start_timestamp = pd.Timestamp(args.start).normalize()
    regimes = regimes.loc[regimes.index >= start_timestamp]
    regime_metrics = evaluate_predictions_by_regime(
        predictions,
        regimes,
        minimum_fold_samples=args.minimum_regime_fold_samples,
    )
    regime_coverage = summarize_market_regimes(regimes)
    latest = frame.index.get_level_values("observation_date").max()
    manifest = {
        "study_version": "expanded-walkforward-v1",
        "database": Path(args.database).name,
        "latest_date": pd.Timestamp(latest).date().isoformat(),
        "start_date": str(args.start),
        "ticker_count": len(analysis_tickers),
        "row_count": len(frame),
        "group_counts": {
            group: sum(
                group_map.get(ticker) == group
                for ticker in analysis_tickers
            )
            for group in ("semiconductor", "software", "other")
        },
        "sector_mode": args.sector_mode,
        "folds": args.folds,
        "minimum_samples": args.minimum_samples,
        "decision": decision,
        "diagnostic_prediction_rows": len(diagnostics),
        "market_regime_version": "market_regime_v1",
        "market_regime_rows": len(regime_metrics),
        "minimum_regime_fold_samples": args.minimum_regime_fold_samples,
    }
    report = render_expanded_report(metrics, manifest, diagnostics)
    regime_report = render_market_regime_report(
        regime_metrics,
        regime_coverage,
        manifest,
    )
    report_path = Path(args.report)
    metrics_path = Path(args.metrics)
    manifest_path = Path(args.manifest)
    diagnostics_path = Path(args.diagnostics)
    regime_metrics_path = Path(args.regime_metrics)
    regime_report_path = Path(args.regime_report)
    for path in (
        report_path,
        metrics_path,
        manifest_path,
        diagnostics_path,
        regime_metrics_path,
        regime_report_path,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    metrics.to_csv(metrics_path, index=False)
    diagnostics.to_csv(diagnostics_path, index=False)
    regime_metrics.to_csv(regime_metrics_path, index=False)
    regime_report_path.write_text(regime_report, encoding="utf-8")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
