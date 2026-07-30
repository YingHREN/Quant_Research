"""Frozen evaluation helpers for support first-touch reactions."""

from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from research.expanded_market_data import ExpandedMarketDataRepository
from research.market_regime import build_market_regime_frame
from research.run_expanded_walkforward_study import (
    FOCUS_TICKERS,
    classify_study_groups,
    select_analysis_tickers,
)
from research.run_historical_demand_support_study import (
    ABLATION_VARIANTS,
    BASELINE,
    CHALLENGER,
    _groups_for_dates,
    _sector_context_close,
    build_ticker_signal_variants,
    group_assignment_causal_audit,
    load_group_assignment_intervals,
)
from research.support_touch_reaction import (
    build_support_touch_reaction_rows,
)
from web.market_groups import REFERENCE_TICKERS


HORIZONS = (5, 10, 20)


def latest_point_in_time_groups(
    fallback_groups: dict[str, str],
    group_intervals: pd.DataFrame,
    *,
    asof: str,
) -> dict[str, str]:
    """Resolve the same as-of group map used by the previous cohort."""
    checked_asof = pd.Timestamp(asof).normalize()
    return {
        ticker: _group_for_ticker(
            ticker,
            checked_asof,
            group_intervals,
            fallback_groups,
        )
        for ticker in fallback_groups
    }


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
    development = _select_cohort(
        normalized,
        limit=min(cohort_size, len(normalized)),
        seed=development_seed,
    )
    remaining = {
        ticker: group
        for ticker, group in normalized.items()
        if ticker not in set(development)
    }
    confirmation = _select_cohort(
        remaining,
        limit=min(cohort_size, len(remaining)),
        seed=confirmation_seed,
    )
    return {
        "development": tuple(development),
        "confirmation": tuple(confirmation),
    }


def _select_cohort(
    groups: dict[str, str],
    *,
    limit: int,
    seed: int,
) -> tuple[str, ...]:
    focus = tuple(ticker for ticker in FOCUS_TICKERS if ticker in groups)
    if limit < len(focus):
        return tuple(sorted(focus[:limit]))
    return select_analysis_tickers(groups, max_tickers=limit, seed=seed)


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
    performance = _preregistered_performance_conditions(metrics)
    reasons = []
    if not performance["available"]:
        reasons.append("insufficient_preregistered_metrics")
    else:
        reasons.extend(
            f"performance_condition_failed:{name}"
            for name, passed in performance["conditions"].items()
            if not passed
        )
    if not causal_audit_passed:
        reasons.append("causal_audit_failed")
    if not future_holdout_passed:
        reasons.append("future_holdout_required")
    return {
        "eligible": bool(not reasons),
        "authority": "advisory_only",
        "reasons": reasons,
        "metric_row_count": int(len(metrics)),
        "performance_conditions": performance,
    }


def _preregistered_performance_conditions(
    metrics: pd.DataFrame,
) -> dict[str, object]:
    condition_names = (
        "acceptance_gain_at_least_2pp",
        "failure_rate_not_worse",
        "at_least_3_fold_wins",
        "at_least_2_group_wins",
        "distance_direction_consistent",
        "maximum_penetration_not_worse",
    )
    unavailable = {
        "available": False,
        "acceptance_rate_delta": None,
        "failure_rate_delta": None,
        "maximum_penetration_atr_delta": None,
        "stable_fold_wins": 0,
        "improved_group_count": 0,
        "group_count": 0,
        "consistent_distance_bins": 0,
        "distance_bin_count": 0,
        "conditions": {name: False for name in condition_names},
    }
    required = {
        "cohort",
        "comparison_scope",
        "variant",
        "waiting_horizon",
        "fold",
        "group",
        "regime",
        "distance_bin",
        "touch_count",
        "accepted_rate",
        "failed_rate",
        "mean_maximum_penetration_atr",
    }
    if not isinstance(metrics, pd.DataFrame) or not required.issubset(
        metrics.columns
    ):
        return unavailable
    selected = metrics.loc[
        metrics["cohort"].eq("confirmation")
        & metrics["comparison_scope"].eq("paired")
        & metrics["variant"].isin((BASELINE, CHALLENGER))
        & metrics["waiting_horizon"].eq(10)
        & metrics["regime"].eq("all")
    ].copy()
    if selected.empty:
        return unavailable

    primary = selected.loc[
        selected["group"].eq("all")
        & selected["distance_bin"].eq("all")
    ]
    overall = _weighted_variant_summary(primary, ())
    fold = _paired_delta_frame(
        _weighted_variant_summary(primary, ("fold",))
    )
    groups = _paired_delta_frame(
        _weighted_variant_summary(
            selected.loc[
                selected["group"].ne("all")
                & selected["distance_bin"].eq("all")
            ],
            ("group",),
        )
    )
    distances = _paired_delta_frame(
        _weighted_variant_summary(
            selected.loc[
                selected["group"].eq("all")
                & selected["distance_bin"].ne("all")
            ],
            ("distance_bin",),
        )
    )
    paired_overall = _paired_delta_frame(overall)
    if paired_overall.empty:
        return unavailable
    row = paired_overall.iloc[0]
    acceptance_delta = float(row["accepted_rate_delta"])
    failure_delta = float(row["failed_rate_delta"])
    penetration_delta = float(
        row["mean_maximum_penetration_atr_delta"]
    )
    stable_fold_wins = int((fold["accepted_rate_delta"] > 0.0).sum())
    improved_group_count = int(
        (groups["accepted_rate_delta"] > 0.0).sum()
    )
    group_count = int(len(groups))
    distance_bin_count = int(len(distances))
    consistent_distance_bins = int(
        (distances["accepted_rate_delta"] > 0.0).sum()
    )
    tolerance = 1e-12
    conditions = {
        "acceptance_gain_at_least_2pp": (
            acceptance_delta + tolerance >= 0.02
        ),
        "failure_rate_not_worse": failure_delta <= tolerance,
        "at_least_3_fold_wins": stable_fold_wins >= 3,
        "at_least_2_group_wins": (
            group_count == 3 and improved_group_count >= 2
        ),
        "distance_direction_consistent": (
            distance_bin_count == 4
            and consistent_distance_bins == distance_bin_count
        ),
        "maximum_penetration_not_worse": penetration_delta <= tolerance,
    }
    return {
        "available": True,
        "acceptance_rate_delta": acceptance_delta,
        "failure_rate_delta": failure_delta,
        "maximum_penetration_atr_delta": penetration_delta,
        "stable_fold_wins": stable_fold_wins,
        "improved_group_count": improved_group_count,
        "group_count": group_count,
        "consistent_distance_bins": consistent_distance_bins,
        "distance_bin_count": distance_bin_count,
        "conditions": conditions,
    }


def _weighted_variant_summary(
    rows: pd.DataFrame,
    keys: tuple[str, ...],
) -> pd.DataFrame:
    records = []
    group_keys = (*keys, "variant")
    if rows.empty:
        return pd.DataFrame()
    for values, selected in rows.groupby(list(group_keys), sort=True):
        if not isinstance(values, tuple):
            values = (values,)
        weights = pd.to_numeric(
            selected["touch_count"],
            errors="coerce",
        ).fillna(0.0)
        record = dict(zip(group_keys, values))
        for column in (
            "accepted_rate",
            "failed_rate",
            "mean_maximum_penetration_atr",
        ):
            numeric = pd.to_numeric(selected[column], errors="coerce")
            valid = numeric.notna() & weights.gt(0.0)
            record[column] = (
                float("nan")
                if not valid.any()
                else float(np.average(numeric[valid], weights=weights[valid]))
            )
        records.append(record)
    return pd.DataFrame(records)


def _paired_delta_frame(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()
    keys = [
        column
        for column in summary.columns
        if column
        not in {
            "variant",
            "accepted_rate",
            "failed_rate",
            "mean_maximum_penetration_atr",
        }
    ]
    baseline = summary.loc[summary["variant"].eq(BASELINE)].drop(
        columns="variant"
    )
    challenger = summary.loc[summary["variant"].eq(CHALLENGER)].drop(
        columns="variant"
    )
    if keys:
        paired = baseline.merge(
            challenger,
            on=keys,
            suffixes=("_baseline", "_challenger"),
            validate="one_to_one",
        )
    elif len(baseline) == 1 and len(challenger) == 1:
        paired = pd.DataFrame(
            {
                f"{column}_baseline": [baseline.iloc[0][column]]
                for column in baseline.columns
            }
        )
        for column in challenger.columns:
            paired[f"{column}_challenger"] = challenger.iloc[0][column]
    else:
        return pd.DataFrame()
    for column in (
        "accepted_rate",
        "failed_rate",
        "mean_maximum_penetration_atr",
    ):
        paired[f"{column}_delta"] = (
            paired[f"{column}_challenger"]
            - paired[f"{column}_baseline"]
        )
    return paired


def run_support_touch_reaction_study(
    histories: dict[str, pd.DataFrame],
    *,
    cohorts: dict[str, tuple[str, ...]],
    fallback_groups: dict[str, str],
    group_intervals: pd.DataFrame,
    asof: str,
    start: str = "2018-01-01",
    horizons: tuple[int, ...] = HORIZONS,
    n_folds: int = 5,
    minimum_score: float = 30.0,
    minimum_sessions: int = 220,
    signal_builder=build_ticker_signal_variants,
    progress=None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Run frozen first-touch evaluation on loaded point-in-time histories."""
    checked_asof = pd.Timestamp(asof).normalize()
    checked_start = pd.Timestamp(start).normalize()
    if checked_start > checked_asof:
        raise ValueError("start must not be after asof")
    if not cohorts:
        raise ValueError("cohorts must not be empty")
    flattened = [
        ticker
        for cohort_tickers in cohorts.values()
        for ticker in cohort_tickers
    ]
    if len(flattened) != len(set(flattened)):
        raise ValueError("cohorts must be disjoint")

    regime_frame = build_market_regime_frame(histories)
    regime_lookup = (
        regime_frame["regime"]
        if "regime" in regime_frame
        else pd.Series(dtype=object)
    )
    qqq_close = _close(histories.get("QQQ"))
    event_frames = []
    exclusions = []
    requested_coverage = []
    total = len(flattened)
    done = 0
    for cohort_name, cohort_tickers in cohorts.items():
        for ticker in cohort_tickers:
            history = histories.get(ticker)
            if not isinstance(history, pd.DataFrame) or history.empty:
                exclusions.append(
                    {
                        "cohort": cohort_name,
                        "ticker": ticker,
                        "reason": "missing_history",
                    }
                )
                done += 1
                continue
            history = history.loc[history.index <= checked_asof].copy()
            if len(history) < minimum_sessions:
                exclusions.append(
                    {
                        "cohort": cohort_name,
                        "ticker": ticker,
                        "reason": f"fewer_than_{minimum_sessions}_sessions",
                        "session_count": len(history),
                    }
                )
                done += 1
                continue
            latest_group = _group_for_ticker(
                ticker,
                checked_asof,
                group_intervals,
                fallback_groups,
            )
            variants = signal_builder(
                history,
                qqq_close=qqq_close,
                sector_close=_sector_context_close(
                    histories,
                    latest_group,
                    ticker,
                ),
                minimum_score=minimum_score,
            )
            for variant in ABLATION_VARIANTS:
                signals = variants.get(variant)
                for horizon in horizons:
                    events = (
                        pd.DataFrame()
                        if signals is None
                        else build_support_touch_reaction_rows(
                            ticker,
                            history,
                            signals,
                            waiting_horizon=int(horizon),
                        )
                    )
                    if not events.empty:
                        events = events.loc[
                            pd.to_datetime(
                                events["observation_date"],
                                errors="raise",
                            )
                            >= checked_start
                        ].copy()
                    requested_coverage.append(
                        {
                            "cohort": cohort_name,
                            "ticker": ticker,
                            "variant": variant,
                            "waiting_horizon": int(horizon),
                            "event_count": int(len(events)),
                            "touch_count": (
                                0
                                if events.empty
                                else int(
                                    events["touch_status"].eq("touched").sum()
                                )
                            ),
                        }
                    )
                    if events.empty:
                        continue
                    events["cohort"] = cohort_name
                    events["group"] = _groups_for_dates(
                        ticker,
                        events["observation_date"],
                        group_intervals,
                        fallback_groups,
                    ).to_numpy()
                    dates = pd.to_datetime(
                        events["observation_date"],
                        errors="raise",
                    ).dt.normalize()
                    events["regime"] = dates.map(regime_lookup).fillna(
                        "unavailable"
                    )
                    event_frames.append(events)
            done += 1
            if progress is not None:
                progress(done, total, ticker)
    if not event_frames:
        raise ValueError("study produced no mature eligible episodes")
    outcomes = pd.concat(event_frames, ignore_index=True, sort=False)
    outcomes = assign_reaction_folds(outcomes, n_folds=n_folds)
    performance = evaluate_touch_reactions(outcomes)
    performance["row_type"] = "performance"
    coverage = _coverage_frame(
        requested_coverage,
        cohorts=cohorts,
        horizons=horizons,
    )
    metrics = pd.concat((performance, coverage), ignore_index=True, sort=False)
    causal_audit_passed = group_assignment_causal_audit(
        group_intervals,
        flattened,
    )
    decision = support_reaction_decision(
        performance,
        causal_audit_passed=causal_audit_passed,
        future_holdout_passed=False,
    )
    touched = outcomes["touch_status"].eq("touched")
    reaction_labels = {
        "touched": int(touched.sum()),
        "accepted": int(outcomes["accepted"].sum()),
        "failed": int(outcomes["failed"].sum()),
        "ambiguous": int(outcomes["ambiguous"].sum()),
    }
    if reaction_labels["touched"] != sum(
        reaction_labels[label]
        for label in ("accepted", "failed", "ambiguous")
    ):
        raise ValueError("reaction labels are not mutually exclusive")
    manifest = {
        "study_version": "support-first-touch-reaction-v1",
        "asof": checked_asof.date().isoformat(),
        "start": checked_start.date().isoformat(),
        "cohorts": {
            name: {
                "requested_count": len(tickers),
                "tickers": list(tickers),
            }
            for name, tickers in cohorts.items()
        },
        "horizons": [int(value) for value in horizons],
        "folds": int(n_folds),
        "variants": list(ABLATION_VARIANTS),
        "episode_count": int(len(outcomes)),
        "touch_count": int(outcomes["touch_status"].eq("touched").sum()),
        "reaction_labels": reaction_labels,
        "excluded_tickers": exclusions,
        "group_assignment_causal_audit_passed": causal_audit_passed,
        "decision": decision,
    }
    return metrics, outcomes, manifest


def render_support_touch_reaction_report(
    metrics: pd.DataFrame,
    manifest: dict[str, object],
) -> str:
    """Render a compact deterministic Chinese evidence report."""
    performance = metrics.loc[
        metrics["row_type"].eq("performance")
        & metrics["comparison_scope"].eq("paired")
        & metrics["waiting_horizon"].eq(10)
        & metrics["group"].eq("all")
        & metrics["regime"].eq("all")
        & metrics["distance_bin"].eq("all")
    ].copy()
    coverage = metrics.loc[metrics["row_type"].eq("coverage")].copy()
    zero = coverage.loc[coverage["event_count"].fillna(0).eq(0)]
    decision = dict(manifest.get("decision") or {})
    evidence = dict(decision.get("performance_conditions") or {})
    conditions = dict(evidence.get("conditions") or {})
    reasons = list(decision.get("reasons") or ())
    reason_lines = ["- 无。"] if not reasons else [
        f"- `{reason}`" for reason in reasons
    ]
    zero_lines = ["- 无。"] if zero.empty else [
        f"- {row.cohort} / {row.variant} / {int(row.waiting_horizon)} 日"
        for row in zero.itertuples()
    ]
    columns = (
        "cohort",
        "variant",
        "fold",
        "event_count",
        "touch_rate",
        "accepted_rate",
        "failed_rate",
        "ambiguous_rate",
        "mean_maximum_rebound_atr",
        "mean_maximum_penetration_atr",
    )
    selected = performance.loc[
        :,
        [column for column in columns if column in performance],
    ]
    return "\n".join(
        (
            "# 支撑区首触反应研究",
            "",
            f"- 数据截止：{manifest.get('asof', '—')}",
            f"- 首触事件：{manifest.get('episode_count', 0)}",
            f"- 实际触达：{manifest.get('touch_count', 0)}",
            f"- 模型权限：`{decision.get('authority', 'advisory_only')}`",
            "- 标签：首次触达后 3 个交易日内，失败优先于接受；未触达不进入反应率分母。",
            "- 该研究不修改 Ridge、下行否决、最终决策策略或 UI。",
            "",
            "## 研究门控",
            "",
            *reason_lines,
            "",
            "## 预注册性能条件（确认队列、10 日严格配对）",
            "",
            "- 承接率增量："
            + _metric_text(evidence.get("acceptance_rate_delta"), percent=True),
            "- 失效率变化："
            + _metric_text(evidence.get("failure_rate_delta"), percent=True),
            "- 最大穿透 ATR 变化："
            + _metric_text(
                evidence.get("maximum_penetration_atr_delta")
            ),
            f"- 改善时间折：{evidence.get('stable_fold_wins', 0)}/5",
            "- 改善板块组："
            f"{evidence.get('improved_group_count', 0)}/"
            f"{evidence.get('group_count', 0)}"
            "（要求覆盖 3 组且至少 2 组改善）",
            "- 同向距离分箱："
            f"{evidence.get('consistent_distance_bins', 0)}/"
            f"{evidence.get('distance_bin_count', 0)}",
            "- 条件通过数："
            f"{sum(bool(value) for value in conditions.values())}/"
            f"{len(conditions)}",
            "",
            "## 10 日严格配对（逐折）",
            "",
            _markdown_table(selected),
            "",
            "## 零事件变体",
            "",
            *zero_lines,
            "",
        )
    )


def write_study_outputs(
    metrics: pd.DataFrame,
    manifest: dict[str, object],
    *,
    report_path: str | Path,
    metrics_path: str | Path,
    manifest_path: str | Path,
) -> None:
    """Write the three tracked study artifacts."""
    report = Path(report_path)
    metric_csv = Path(metrics_path)
    manifest_json = Path(manifest_path)
    for path in (report, metric_csv, manifest_json):
        path.parent.mkdir(parents=True, exist_ok=True)
    metric_csv.write_text(metrics.to_csv(index=False), encoding="utf-8")
    report.write_text(
        render_support_touch_reaction_report(metrics, manifest),
        encoding="utf-8",
    )
    manifest_json.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


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
    paired = selected.loc[row_keys.isin(common_index)].copy()
    baseline_bins = paired.loc[
        paired["variant"].eq(BASELINE),
        [*keys, "distance_bin"],
    ].rename(columns={"distance_bin": "_paired_distance_bin"})
    if baseline_bins.duplicated(list(keys)).any():
        raise ValueError("paired baseline contains duplicate event keys")
    paired = paired.merge(
        baseline_bins,
        on=list(keys),
        how="left",
        validate="many_to_one",
    )
    if paired["_paired_distance_bin"].isna().any():
        raise ValueError("paired rows are missing the baseline distance bin")
    paired["distance_bin"] = paired["_paired_distance_bin"]
    return paired.drop(columns="_paired_distance_bin")


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


def _coverage_frame(
    rows: list[dict[str, object]],
    *,
    cohorts: dict[str, tuple[str, ...]],
    horizons: tuple[int, ...],
) -> pd.DataFrame:
    raw = pd.DataFrame(rows)
    if raw.empty:
        grouped = pd.DataFrame(
            columns=(
                "cohort",
                "variant",
                "waiting_horizon",
                "event_count",
                "touch_count",
            )
        )
    else:
        grouped = (
            raw.groupby(
                ["cohort", "variant", "waiting_horizon"],
                sort=True,
                as_index=False,
            )[["event_count", "touch_count"]]
            .sum()
        )
    expected = pd.MultiIndex.from_product(
        (
            tuple(cohorts),
            ABLATION_VARIANTS,
            tuple(int(value) for value in horizons),
        ),
        names=("cohort", "variant", "waiting_horizon"),
    ).to_frame(index=False)
    result = expected.merge(
        grouped,
        on=["cohort", "variant", "waiting_horizon"],
        how="left",
        validate="one_to_one",
    )
    result[["event_count", "touch_count"]] = result[
        ["event_count", "touch_count"]
    ].fillna(0).astype(int)
    result["status"] = np.where(
        result["event_count"].gt(0),
        "available",
        "unavailable_no_events",
    )
    result["row_type"] = "coverage"
    return result


def _close(history: pd.DataFrame | None) -> pd.Series | None:
    if not isinstance(history, pd.DataFrame) or "Close" not in history:
        return None
    return pd.to_numeric(history["Close"], errors="coerce")


def _group_for_ticker(
    ticker: str,
    date: pd.Timestamp,
    intervals: pd.DataFrame,
    fallback_groups: dict[str, str],
) -> str:
    probe = pd.Series([date])
    return str(
        _groups_for_dates(
            ticker,
            probe,
            intervals,
            fallback_groups,
        ).iloc[0]
    )


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "无可评估样本。"
    columns = list(frame.columns)
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for values in frame.itertuples(index=False, name=None):
        rendered = []
        for value in values:
            if isinstance(value, (float, np.floating)):
                rendered.append(
                    "—" if not math.isfinite(value) else f"{value:.4f}"
                )
            else:
                rendered.append(str(value))
        lines.append("| " + " | ".join(rendered) + " |")
    return "\n".join(lines)


def _metric_text(value: object, *, percent: bool = False) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(numeric):
        return "—"
    return f"{numeric * 100.0:+.2f} pp" if percent else f"{numeric:+.4f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument("--asof", default="2026-07-24")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--cohort-size", type=int, default=240)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--minimum-score", type=float, default=30.0)
    parser.add_argument(
        "--report",
        default="reports/support-touch-reaction-study.md",
    )
    parser.add_argument(
        "--metrics",
        default="reports/support-touch-reaction-study.csv",
    )
    parser.add_argument(
        "--manifest",
        default="reports/support-touch-reaction-study.json",
    )
    args = parser.parse_args(argv)

    repository = ExpandedMarketDataRepository(args.database)
    classifications = repository.load_classifications(asof=args.asof)
    fallback_groups = classify_study_groups(classifications)
    intervals = load_group_assignment_intervals(args.database)
    latest_groups = latest_point_in_time_groups(
        fallback_groups,
        intervals,
        asof=args.asof,
    )
    cohorts = select_touch_reaction_cohorts(
        latest_groups,
        cohort_size=args.cohort_size,
    )
    requested_tickers = tuple(
        sorted(
            set(REFERENCE_TICKERS).union(
                ticker
                for tickers in cohorts.values()
                for ticker in tickers
            )
        )
    )
    histories = repository.load_universe_histories(
        asof=args.asof,
        tickers=requested_tickers,
    )

    def progress(done, total, ticker):
        if done == 1 or done % 10 == 0 or done == total:
            print(f"[support-touch] {done}/{total} {ticker}", flush=True)

    metrics, _, manifest = run_support_touch_reaction_study(
        histories,
        cohorts=cohorts,
        fallback_groups=fallback_groups,
        group_intervals=intervals,
        asof=args.asof,
        start=args.start,
        n_folds=args.folds,
        minimum_score=args.minimum_score,
        progress=progress,
    )
    write_study_outputs(
        metrics,
        manifest,
        report_path=args.report,
        metrics_path=args.metrics,
        manifest_path=args.manifest,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
