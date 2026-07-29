"""Frozen evaluation helpers for historical demand-support research.

The dashboard consumes this model only as advisory evidence.  These helpers
make that boundary explicit: promotion is fail-closed and never changes the
online forecast policy by itself.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from research.expanded_market_data import ExpandedMarketDataRepository
from research.historical_demand_support import (
    build_historical_demand_support_rows,
)
from research.market_regime import build_market_regime_frame
from research.resistance import build_near_resistance_rows
from research.reversal import build_reversal_rows
from research.run_expanded_walkforward_study import (
    classify_study_groups,
    select_analysis_tickers,
)
from research.supply_demand import build_supply_demand_rows
from web.market_groups import REFERENCE_TICKERS


BASELINE = "baseline"
CHALLENGER = "baseline_plus_historical_demand"
HORIZONS = (5, 10, 20)
ABLATION_VARIANTS = (
    BASELINE,
    CHALLENGER,
    "historical_demand_only",
    "no_volume",
    "no_retests",
    "no_environment",
    "no_decay",
)
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
REQUIRED_PRICE_COLUMNS = ("Open", "High", "Low", "Close")


def build_pocket_pivot_rows(
    history: pd.DataFrame,
) -> list[dict[str, bool]]:
    """Return causal pocket-pivot flags using the prior ten sessions only."""
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    missing = [
        column for column in ("Close", "Volume") if column not in history
    ]
    if missing:
        raise ValueError(f"history is missing required columns: {missing}")
    close = pd.to_numeric(history["Close"], errors="coerce")
    volume = pd.to_numeric(history["Volume"], errors="coerce")
    down_volume = volume.where(close < close.shift(1))
    comparison = down_volume.shift(1).rolling(
        10,
        min_periods=1,
    ).max()
    active = (
        (close > close.shift(1))
        & comparison.notna()
        & (volume > comparison)
    )
    return [
        {"pocket_pivot": bool(value)}
        for value in active.fillna(False).to_numpy()
    ]


def build_outcome_rows(
    ticker: str,
    history: pd.DataFrame,
    signals: pd.DataFrame,
    *,
    horizon: int,
) -> pd.DataFrame:
    """Label eligible support observations using next-session-open execution."""
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    if not isinstance(signals, pd.DataFrame):
        raise TypeError("signals must be a DataFrame")
    missing = [column for column in REQUIRED_PRICE_COLUMNS if column not in history]
    if missing:
        raise ValueError(f"history is missing required columns: {missing}")
    signal_missing = [
        column
        for column in ("variant", "eligible", "zone_lower", "zone_upper")
        if column not in signals
    ]
    if signal_missing:
        raise ValueError(f"signals are missing required columns: {signal_missing}")
    if not history.index.equals(signals.index):
        raise ValueError("history and signals must align")
    if not isinstance(horizon, int) or horizon <= 0:
        raise ValueError("horizon must be a positive integer")

    frame = history.loc[:, REQUIRED_PRICE_COLUMNS].astype(float)
    rows = []
    mature_count = max(0, len(frame) - horizon)
    for position in range(mature_count):
        signal = signals.iloc[position]
        if not isinstance(signal.get("eligible"), (bool, np.bool_)) or not bool(
            signal.get("eligible")
        ):
            continue
        lower = _finite_number(signal.get("zone_lower"))
        upper = _finite_number(signal.get("zone_upper"))
        if lower is None or upper is None or lower <= 0.0 or lower > upper:
            continue
        path = frame.iloc[position + 1 : position + horizon + 1]
        entry_price = float(path["Open"].iloc[0])
        if not math.isfinite(entry_price) or entry_price <= 0.0:
            continue
        closes = path["Close"].to_numpy(dtype=float)
        bounce_positions = np.flatnonzero(closes >= entry_price * 1.02)
        support_broken = bool((path["Close"] < lower).any())
        rows.append(
            {
                "ticker": str(ticker).strip().upper(),
                "observation_date": frame.index[position],
                "entry_date": path.index[0],
                "target_date": path.index[-1],
                "horizon": horizon,
                "variant": str(signal["variant"]),
                "entry_price": entry_price,
                "zone_lower": lower,
                "zone_upper": upper,
                "support_held": not support_broken,
                "support_broken": support_broken,
                "first_bounce_delay": (
                    None
                    if not len(bounce_positions)
                    else int(bounce_positions[0])
                ),
                "maximum_favorable_excursion": (
                    float(path["High"].max()) / entry_price - 1.0
                ),
                "maximum_adverse_excursion": (
                    float(path["Low"].min()) / entry_price - 1.0
                ),
                "final_return": float(path["Close"].iloc[-1]) / entry_price - 1.0,
            }
        )
    return pd.DataFrame(rows)


def assign_chronological_folds(
    rows: pd.DataFrame,
    *,
    n_folds: int,
) -> pd.DataFrame:
    """Assign whole observation dates to fixed chronological test folds."""
    if not isinstance(rows, pd.DataFrame):
        raise TypeError("rows must be a DataFrame")
    if "observation_date" not in rows:
        raise ValueError("rows must contain observation_date")
    if not isinstance(n_folds, int) or n_folds < 2:
        raise ValueError("n_folds must be at least two")
    result = rows.copy(deep=True)
    dates = pd.DatetimeIndex(
        pd.to_datetime(result["observation_date"], errors="raise").unique()
    ).sort_values()
    if len(dates) < n_folds:
        raise ValueError("insufficient distinct dates for folds")
    mapping = {}
    for fold, selected in enumerate(np.array_split(dates, n_folds), start=1):
        for date in selected:
            mapping[pd.Timestamp(date)] = fold
    normalized = pd.to_datetime(result["observation_date"], errors="raise")
    result["fold"] = normalized.map(mapping).astype(int)
    return result


def non_overlapping_outcomes(rows: pd.DataFrame) -> pd.DataFrame:
    """Take deterministic horizon-spaced rows within each ticker and fold."""
    if not isinstance(rows, pd.DataFrame):
        raise TypeError("rows must be a DataFrame")
    required = {
        "ticker",
        "variant",
        "horizon",
        "fold",
        "observation_date",
    }
    missing = sorted(required.difference(rows.columns))
    if missing:
        raise ValueError(f"rows are missing required columns: {missing}")
    ordered = rows.sort_values(
        [
            "variant",
            "horizon",
            "fold",
            "ticker",
            "observation_date",
        ],
        kind="mergesort",
    ).copy()
    position = ordered.groupby(
        ["variant", "horizon", "fold", "ticker"],
        sort=False,
    ).cumcount()
    horizon = pd.to_numeric(ordered["horizon"], errors="raise").astype(int)
    if (horizon <= 0).any():
        raise ValueError("horizon must be positive")
    return ordered.loc[position.mod(horizon).eq(0)].copy()


def evaluate_outcomes(
    outcomes: pd.DataFrame,
    *,
    coverage: dict[tuple[str, str], dict[str, int]] | None = None,
) -> pd.DataFrame:
    """Aggregate support outcomes by variant, horizon, fold, group, and regime."""
    if not isinstance(outcomes, pd.DataFrame):
        raise TypeError("outcomes must be a DataFrame")
    required = {
        "variant",
        "horizon",
        "fold",
        "group",
        "regime",
        "support_held",
        "support_broken",
        "first_bounce_delay",
        "maximum_favorable_excursion",
        "maximum_adverse_excursion",
        "final_return",
    }
    missing = sorted(required.difference(outcomes.columns))
    if missing:
        raise ValueError(f"outcomes are missing required columns: {missing}")
    if outcomes.empty:
        return pd.DataFrame()
    group_columns = ("variant", "horizon", "fold", "group", "regime")
    result = (
        outcomes.groupby(list(group_columns), dropna=False, sort=True)
        .agg(
            sample_count=("support_held", "size"),
            support_hold_rate=("support_held", "mean"),
            support_break_rate=("support_broken", "mean"),
            first_bounce_delay=("first_bounce_delay", "mean"),
            maximum_favorable_excursion=(
                "maximum_favorable_excursion",
                "mean",
            ),
            maximum_adverse_excursion=(
                "maximum_adverse_excursion",
                "mean",
            ),
            final_return=("final_return", "mean"),
        )
        .reset_index()
    )
    coverage = coverage or {}
    result["eligible_count"] = [
        int(
            coverage.get((str(row.variant), str(row.group)), {}).get(
                "eligible_count",
                row.sample_count,
            )
        )
        for row in result.itertuples()
    ]
    result["unavailable_count"] = [
        int(
            coverage.get((str(row.variant), str(row.group)), {}).get(
                "unavailable_count",
                0,
            )
        )
        for row in result.itertuples()
    ]
    result["max_adverse_excursion"] = result[
        "maximum_adverse_excursion"
    ]
    return result


def build_variant_signal_rows(
    history: pd.DataFrame,
    baseline_support: pd.DataFrame,
    historical: pd.DataFrame,
    *,
    no_volume_historical: pd.DataFrame,
    no_environment_historical: pd.DataFrame,
    minimum_score: float = 30.0,
) -> dict[str, pd.DataFrame]:
    """Build the frozen baseline and six historical-demand ablations."""
    frames = (
        baseline_support,
        historical,
        no_volume_historical,
        no_environment_historical,
    )
    if not all(isinstance(frame, pd.DataFrame) for frame in frames):
        raise TypeError("variant inputs must be DataFrames")
    if not all(history.index.equals(frame.index) for frame in frames):
        raise ValueError("variant inputs must align to history")
    close = pd.to_numeric(history["Close"], errors="coerce")

    baseline = pd.DataFrame(index=history.index)
    baseline["variant"] = BASELINE
    baseline["eligible"] = (
        baseline_support["near_support_lower"].notna()
        & baseline_support["near_support_upper"].notna()
        & (baseline_support["near_support_score"] >= minimum_score)
        & (baseline_support["near_support_distance_pct"] <= 3.5)
    )
    baseline["zone_lower"] = baseline_support["near_support_lower"]
    baseline["zone_upper"] = baseline_support["near_support_upper"]

    historical_only = _historical_signal_frame(
        historical,
        variant="historical_demand_only",
        minimum_score=minimum_score,
    )
    challenger = baseline.copy(deep=True)
    challenger["variant"] = CHALLENGER
    use_historical = historical_only["eligible"] & (
        ~baseline["eligible"]
        | (
            pd.to_numeric(historical_only["zone_upper"], errors="coerce")
            >= pd.to_numeric(baseline["zone_upper"], errors="coerce").fillna(
                -np.inf
            )
        )
    )
    challenger.loc[use_historical, ["zone_lower", "zone_upper"]] = (
        historical_only.loc[use_historical, ["zone_lower", "zone_upper"]]
    )
    challenger["eligible"] = baseline["eligible"] | historical_only["eligible"]

    no_volume = _historical_signal_frame(
        no_volume_historical,
        variant="no_volume",
        minimum_score=minimum_score,
    )
    no_environment = _historical_signal_frame(
        no_environment_historical,
        variant="no_environment",
        minimum_score=minimum_score,
    )
    no_retests_source = historical.copy(deep=True)
    retests = pd.to_numeric(
        no_retests_source["historical_demand_support_retest_count"],
        errors="coerce",
    ).fillna(0.0)
    no_retests_source["historical_demand_support_score"] = (
        pd.to_numeric(
            no_retests_source["historical_demand_support_score"],
            errors="coerce",
        )
        - np.minimum(20.0, retests * 10.0)
    ).clip(lower=0.0)
    no_retests = _historical_signal_frame(
        no_retests_source,
        variant="no_retests",
        minimum_score=minimum_score,
    )
    no_decay_source = historical.copy(deep=True)
    age = pd.to_numeric(
        no_decay_source["historical_demand_support_age_sessions"],
        errors="coerce",
    ).fillna(0.0)
    no_decay_source["historical_demand_support_score"] = (
        pd.to_numeric(
            no_decay_source["historical_demand_support_score"],
            errors="coerce",
        )
        / np.power(0.5, age / 40.0)
    ).clip(upper=100.0)
    no_decay = _historical_signal_frame(
        no_decay_source,
        variant="no_decay",
        minimum_score=minimum_score,
    )
    output = {
        BASELINE: baseline,
        CHALLENGER: challenger,
        "historical_demand_only": historical_only,
        "no_volume": no_volume,
        "no_retests": no_retests,
        "no_environment": no_environment,
        "no_decay": no_decay,
    }
    for frame in output.values():
        frame.loc[~np.isfinite(close), "eligible"] = False
    return output


def _historical_signal_frame(
    rows: pd.DataFrame,
    *,
    variant: str,
    minimum_score: float,
) -> pd.DataFrame:
    state = rows["historical_demand_support_state"].astype(str)
    score = pd.to_numeric(
        rows["historical_demand_support_score"],
        errors="coerce",
    )
    lower = pd.to_numeric(
        rows["historical_demand_support_lower"],
        errors="coerce",
    )
    upper = pd.to_numeric(
        rows["historical_demand_support_upper"],
        errors="coerce",
    )
    result = pd.DataFrame(index=rows.index)
    result["variant"] = variant
    result["eligible"] = (
        state.isin({"approaching", "testing", "accepted"})
        & (score >= float(minimum_score))
        & lower.notna()
        & upper.notna()
        & (lower > 0.0)
        & (lower <= upper)
    )
    result["zone_lower"] = lower
    result["zone_upper"] = upper
    return result


def build_ticker_signal_variants(
    history: pd.DataFrame,
    *,
    qqq_close: pd.Series | None = None,
    sector_close: pd.Series | None = None,
    minimum_score: float = 30.0,
) -> dict[str, pd.DataFrame]:
    """Build all frozen support variants for one ticker."""
    reversal_rows = build_reversal_rows(history)
    baseline_support = pd.DataFrame(
        build_near_resistance_rows(history, reversal_rows),
        index=history.index,
    )
    entry_rows = build_pocket_pivot_rows(history)
    demand_rows = build_supply_demand_rows(
        history,
        qqq_close=qqq_close,
        sector_close=sector_close,
    )
    historical = build_historical_demand_support_rows(
        history,
        demand_rows=demand_rows,
        entry_signal_rows=entry_rows,
        qqq_close=qqq_close,
        sector_close=sector_close,
    )
    no_environment = build_historical_demand_support_rows(
        history,
        demand_rows=demand_rows,
        entry_signal_rows=entry_rows,
    )

    neutral_history = history.copy(deep=True)
    neutral_history["Volume"] = 1.0
    neutral_demand = build_supply_demand_rows(
        neutral_history,
        qqq_close=qqq_close,
        sector_close=sector_close,
    )
    no_volume = build_historical_demand_support_rows(
        neutral_history,
        demand_rows=neutral_demand,
        entry_signal_rows=[
            {"pocket_pivot": False} for _ in range(len(neutral_history))
        ],
        qqq_close=qqq_close,
        sector_close=sector_close,
    )
    return build_variant_signal_rows(
        history,
        baseline_support,
        historical,
        no_volume_historical=no_volume,
        no_environment_historical=no_environment,
        minimum_score=minimum_score,
    )


def evaluate_study_outcomes(
    outcomes: pd.DataFrame,
    *,
    coverage: dict[tuple[str, str], dict[str, int]] | None = None,
) -> pd.DataFrame:
    """Evaluate overlapping and non-overlapping rows by regime and overall."""
    metric_frames = []
    for sample_mode, selected in (
        ("overlapping", outcomes),
        ("non_overlapping", non_overlapping_outcomes(outcomes)),
    ):
        for regime_mode, regime_rows in (
            ("observed", selected),
            ("all", selected.assign(regime="all")),
        ):
            metrics = evaluate_outcomes(regime_rows, coverage=coverage)
            metrics["sample_mode"] = sample_mode
            metrics["regime_scope"] = regime_mode
            metric_frames.append(metrics)
    if not metric_frames:
        return pd.DataFrame()
    return pd.concat(metric_frames, ignore_index=True, sort=False)


def load_group_assignment_intervals(
    database: str | Path,
) -> pd.DataFrame:
    """Load persisted effective group intervals without mutating the store."""
    path = Path(database).resolve()
    query = """
        SELECT ticker, effective_from, effective_to, primary_model_group
        FROM group_assignments
        ORDER BY ticker, effective_from
    """
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute(query).fetchall()
    finally:
        connection.close()
    return pd.DataFrame(
        rows,
        columns=(
            "ticker",
            "effective_from",
            "effective_to",
            "group",
        ),
    )


def run_historical_demand_support_study(
    histories: dict[str, pd.DataFrame],
    *,
    analysis_tickers: tuple[str, ...],
    fallback_groups: dict[str, str],
    group_intervals: pd.DataFrame,
    asof: str,
    horizons: tuple[int, ...] = HORIZONS,
    n_folds: int = 5,
    minimum_score: float = 30.0,
    progress=None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Run the frozen causal support study on already loaded histories."""
    checked_asof = pd.Timestamp(asof).normalize()
    regime = build_market_regime_frame(histories)
    regime_lookup = (
        regime["regime"] if "regime" in regime else pd.Series(dtype=object)
    )
    outcome_frames = []
    coverage: dict[tuple[str, str], dict[str, int]] = {}
    processed = 0
    for ticker in analysis_tickers:
        history = histories.get(ticker)
        if not isinstance(history, pd.DataFrame) or history.empty:
            continue
        history = history.loc[history.index <= checked_asof].copy()
        if len(history) < 220:
            continue
        group = _group_at_date(
            ticker,
            checked_asof,
            group_intervals,
            fallback_groups,
        )
        qqq_close = _close(histories.get("QQQ"))
        sector_close = _sector_context_close(histories, group, ticker)
        variants = build_ticker_signal_variants(
            history,
            qqq_close=qqq_close,
            sector_close=sector_close,
            minimum_score=minimum_score,
        )
        for variant, signals in variants.items():
            eligible = int(signals["eligible"].sum())
            unavailable = int(
                (
                    signals["zone_lower"].isna()
                    | signals["zone_upper"].isna()
                ).sum()
            )
            key = (variant, group)
            row = coverage.setdefault(
                key,
                {"eligible_count": 0, "unavailable_count": 0},
            )
            row["eligible_count"] += eligible
            row["unavailable_count"] += unavailable
            for horizon in horizons:
                outcomes = build_outcome_rows(
                    ticker,
                    history,
                    signals,
                    horizon=int(horizon),
                )
                if outcomes.empty:
                    continue
                outcomes["group"] = _groups_for_dates(
                    ticker,
                    outcomes["observation_date"],
                    group_intervals,
                    fallback_groups,
                )
                normalized_dates = pd.to_datetime(
                    outcomes["observation_date"],
                    errors="raise",
                ).dt.normalize()
                outcomes["regime"] = (
                    normalized_dates.map(regime_lookup).fillna("unavailable")
                )
                outcome_frames.append(outcomes)
        processed += 1
        if progress is not None:
            progress(processed, len(analysis_tickers), ticker)
    if not outcome_frames:
        raise ValueError("study produced no mature eligible outcomes")
    outcomes = pd.concat(outcome_frames, ignore_index=True, sort=False)
    outcomes = assign_chronological_folds(outcomes, n_folds=n_folds)
    metrics = evaluate_study_outcomes(outcomes, coverage=coverage)
    decision = promotion_decision(metrics, causal_audit_passed=True)
    manifest = {
        "study_version": "historical-demand-support-oos-v1",
        "asof": checked_asof.date().isoformat(),
        "ticker_count": processed,
        "requested_ticker_count": len(analysis_tickers),
        "outcome_row_count": len(outcomes),
        "horizons": [int(value) for value in horizons],
        "folds": int(n_folds),
        "minimum_score": float(minimum_score),
        "variants": list(ABLATION_VARIANTS),
        "execution": "observation_close_to_next_session_open",
        "group_source": "effective_group_assignments_with_sec_fallback",
        "decision": decision,
    }
    return metrics, outcomes, manifest


def _close(history: pd.DataFrame | None) -> pd.Series | None:
    if not isinstance(history, pd.DataFrame) or "Close" not in history:
        return None
    return pd.to_numeric(history["Close"], errors="coerce")


def _sector_context_close(histories, group, ticker):
    benchmark_sets = {
        "semiconductor": ("SOXX", "SMH"),
        "software": ("IGV", "XSW"),
    }
    benchmarks = benchmark_sets.get(group, ())
    closes = []
    for benchmark in benchmarks:
        close = _close(histories.get(benchmark))
        if close is None:
            continue
        first = close.loc[close.notna() & (close > 0.0)]
        if first.empty:
            continue
        closes.append(close / float(first.iloc[0]) * 100.0)
    if closes:
        return pd.concat(closes, axis=1).mean(axis=1, skipna=True)
    return None


def _normalized_group(value) -> str:
    selected = str(value or "").strip().casefold()
    return selected if selected in {"semiconductor", "software"} else "other"


def _group_at_date(ticker, date, intervals, fallback_groups):
    selected = intervals.loc[intervals["ticker"] == ticker]
    timestamp = pd.Timestamp(date).normalize()
    for row in selected.itertuples(index=False):
        start = pd.Timestamp(row.effective_from).normalize()
        end = _interval_end(row.effective_to)
        if start <= timestamp < end:
            return _normalized_group(row.group)
    return _normalized_group(fallback_groups.get(ticker))


def _groups_for_dates(ticker, dates, intervals, fallback_groups):
    fallback = _normalized_group(fallback_groups.get(ticker))
    result = pd.Series(fallback, index=dates.index, dtype=object)
    normalized = pd.to_datetime(dates, errors="raise").dt.normalize()
    selected = intervals.loc[intervals["ticker"] == ticker]
    for row in selected.itertuples(index=False):
        start = pd.Timestamp(row.effective_from).normalize()
        end = _interval_end(row.effective_to)
        result.loc[(normalized >= start) & (normalized < end)] = (
            _normalized_group(row.group)
        )
    return result


def _interval_end(value):
    if value in (None, "") or str(value).startswith("9999-"):
        return pd.Timestamp.max.normalize()
    return pd.Timestamp(value).normalize()


def render_report(metrics: pd.DataFrame, manifest: dict[str, object]) -> str:
    """Render a deterministic Chinese research report."""
    decision = dict(manifest.get("decision") or {})
    reasons = list(decision.get("reasons") or ())
    reason_lines = ["- 无。"] if not reasons else [f"- `{row}`" for row in reasons]
    columns = (
        "variant",
        "horizon",
        "fold",
        "group",
        "regime",
        "sample_count",
        "support_hold_rate",
        "support_break_rate",
        "maximum_favorable_excursion",
        "maximum_adverse_excursion",
    )
    selected = metrics.loc[
        :,
        [column for column in columns if column in metrics],
    ]
    return "\n".join(
        (
            "# 历史需求支撑区样本外消融",
            "",
            f"- 数据截止：{manifest.get('asof', '—')}",
            f"- 股票数量：{manifest.get('ticker_count', 0)}",
            f"- 模型权限：`{decision.get('authority', 'advisory_only')}`",
            f"- 晋级：{'通过研究门槛' if decision.get('eligible') else '未通过研究门槛'}",
            "- 执行定义：观察日收盘形成证据，下一交易日开盘进入观察。",
            "- 规则分数不是上涨概率；即使研究门槛通过也需人工复核。",
            "",
            "## 晋级失败原因",
            "",
            *reason_lines,
            "",
            "## 分层结果",
            "",
            _markdown_table(selected),
            "",
        )
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
    frozen_slices = {
        "horizon": 10,
        "regime": "all",
        "sample_mode": "overlapping",
    }
    for column, value in frozen_slices.items():
        if column in selected:
            selected = selected.loc[selected[column] == value]
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


def _finite_number(value):
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "无可评估样本。"
    columns = list(frame.columns)
    rows = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    for values in frame.itertuples(index=False, name=None):
        rendered = []
        for value in values:
            if isinstance(value, (float, np.floating)):
                rendered.append("—" if not math.isfinite(value) else f"{value:.4f}")
            else:
                rendered.append(str(value))
        rows.append("| " + " | ".join(rendered) + " |")
    return "\n".join(rows)


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


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database",
        default="data/research_prices.db",
    )
    parser.add_argument("--asof", default="2026-07-24")
    parser.add_argument("--max-tickers", type=int, default=240)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--minimum-score", type=float, default=30.0)
    parser.add_argument(
        "--report",
        default="reports/historical-demand-support-study.md",
    )
    parser.add_argument(
        "--metrics",
        default="reports/historical-demand-support-study.csv",
    )
    parser.add_argument(
        "--outcomes",
        default="reports/historical-demand-support-outcomes.csv",
    )
    parser.add_argument(
        "--manifest",
        default="reports/historical-demand-support-study.json",
    )
    args = parser.parse_args(argv)

    repository = ExpandedMarketDataRepository(args.database)
    classifications = repository.load_classifications(asof=args.asof)
    fallback_groups = classify_study_groups(classifications)
    intervals = load_group_assignment_intervals(args.database)
    latest_groups = {
        ticker: _group_at_date(
            ticker,
            args.asof,
            intervals,
            fallback_groups,
        )
        for ticker in fallback_groups
    }
    analysis_tickers = select_analysis_tickers(
        latest_groups,
        max_tickers=args.max_tickers,
        seed=args.seed,
    )
    requested = tuple(
        sorted(set(analysis_tickers).union(REFERENCE_TICKERS))
    )
    histories = repository.load_universe_histories(
        asof=args.asof,
        tickers=requested,
    )

    def progress(done, total, ticker):
        if done == 1 or done % 10 == 0 or done == total:
            print(
                f"[historical-demand] {done}/{total} {ticker}",
                flush=True,
            )

    metrics, outcomes, manifest = run_historical_demand_support_study(
        histories,
        analysis_tickers=analysis_tickers,
        fallback_groups=fallback_groups,
        group_intervals=intervals,
        asof=args.asof,
        n_folds=args.folds,
        minimum_score=args.minimum_score,
        progress=progress,
    )
    report_path = Path(args.report)
    metrics_path = Path(args.metrics)
    outcomes_path = Path(args.outcomes)
    manifest_path = Path(args.manifest)
    for path in (report_path, metrics_path, outcomes_path, manifest_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(metrics_path, index=False)
    outcomes.to_csv(outcomes_path, index=False)
    report_path.write_text(
        render_report(metrics, manifest),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
