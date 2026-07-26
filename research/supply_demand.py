"""Causal daily supply-pressure and demand-confirmation rule scores."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from research.market_pressure import build_pressure_rows


MINIMUM_COVERAGE = 0.75
SUPPLY_MODEL_KEY = "supply_pressure_v1"
DEMAND_MODEL_KEY = "demand_confirmation_v1"

SUPPLY_CLOSE_VOLUME_WEIGHTS = {
    "distribution_day": 25.0,
    "high_volume_non_progress": 15.0,
    "negative_signed_volume": 15.0,
    "distribution_cluster": 15.0,
}
SUPPLY_REJECTION_WEIGHTS = {
    "upper_wick_supply": 15.0,
    "failed_breakout": 20.0,
    "repeated_failed_breakout": 10.0,
    "pressure_test_efficiency_decay": 10.0,
}
SUPPLY_STRUCTURE_WEIGHTS = {
    "volume_confirmed_ema20_break": 15.0,
    "relative_strength_breakdown_qqq": 10.0,
    "relative_strength_breakdown_sector": 10.0,
    "weak_rebound_below_ema20": 15.0,
}
DEMAND_PARTICIPATION_WEIGHTS = {
    "positive_signed_volume": 15.0,
    "up_volume_confirmation": 20.0,
    "strong_close": 10.0,
}
DEMAND_ABSORPTION_WEIGHTS = {
    "seller_exhaustion": 15.0,
    "buyer_absorption": 20.0,
    "low_volume_higher_low": 15.0,
}
DEMAND_BREAKOUT_WEIGHTS = {
    "breakout_acceptance": 15.0,
    "breakout_follow_through": 10.0,
    "relative_strength_confirmation_qqq": 10.0,
    "relative_strength_confirmation_sector": 10.0,
}


def build_supply_demand_rows(
    history: pd.DataFrame,
    *,
    qqq_close: pd.Series | None = None,
    sector_close: pd.Series | None = None,
) -> pd.DataFrame:
    """Return point-in-time supply and demand evidence for every history row."""
    pressure = build_pressure_rows(history)
    checked = history.loc[
        pressure.index,
        ("Open", "High", "Low", "Close", "Volume"),
    ].astype(float)
    high = checked["High"]
    low = checked["Low"]
    close = checked["Close"]
    prior_close = close.shift(1)
    daily_return = close / prior_close.replace(0.0, np.nan) - 1.0
    ema20 = close.ewm(span=20, adjust=False).mean()
    prior_low_5 = low.shift(1).rolling(5, min_periods=5).min()
    prior_low_10 = low.shift(1).rolling(10, min_periods=10).min()
    prior_pivot_20 = close.shift(1).rolling(20, min_periods=20).max()

    distribution_available = (
        daily_return.notna()
        & pressure["volume_ratio"].notna()
        & pressure["close_location"].notna()
    )
    distribution = _nullable(
        pressure["distribution_day"],
        distribution_available,
    )
    non_progress = _nullable(
        pressure["high_volume_non_progress"],
        daily_return.notna() & pressure["volume_ratio"].notna(),
    )
    negative_signed = _nullable(
        pressure["signed_volume_proxy"] <= -0.75,
        pressure["signed_volume_proxy"].notna(),
    )
    distribution_count, distribution_count_available = _rolling_count(
        distribution,
        10,
    )
    distribution_cluster = _nullable(
        distribution_count >= 2.0,
        distribution_count_available,
    )

    upper_wick_supply = _nullable(
        (pressure["upper_wick_ratio"] >= 0.35)
        & (pressure["volume_ratio"] >= 1.2),
        pressure["upper_wick_ratio"].notna()
        & pressure["volume_ratio"].notna(),
    )
    failed_breakout = _nullable(
        pressure["failed_breakout"],
        prior_pivot_20.notna(),
    )
    failed_count, failed_count_available = _rolling_count(
        failed_breakout,
        10,
    )
    repeated_failed = _nullable(
        failed_count >= 2.0,
        failed_count_available,
    )
    efficiency_decay = _pressure_test_efficiency_decay(
        high,
        daily_return,
        pressure["volume_ratio"],
        prior_pivot_20,
    )

    ema_break = _nullable(
        (close < ema20)
        & (prior_close >= ema20.shift(1))
        & (pressure["volume_ratio"] >= 1.2),
        prior_close.notna()
        & ema20.shift(1).notna()
        & pressure["volume_ratio"].notna(),
    )
    stock_return_20 = close.pct_change(20, fill_method=None)
    qqq_return_20 = _context_return(qqq_close, close.index, 20)
    sector_return_20 = _context_return(sector_close, close.index, 20)
    rs_break_qqq = _relative_condition(
        stock_return_20,
        qqq_return_20,
        threshold=-0.03,
        direction="low",
    )
    rs_break_sector = _relative_condition(
        stock_return_20,
        sector_return_20,
        threshold=-0.03,
        direction="low",
    )
    prior_break = (
        ema_break.fillna(False)
        .astype(float)
        .shift(1)
        .rolling(5, min_periods=5)
        .max()
    )
    weak_rebound = _nullable(
        (prior_break >= 1.0)
        & (daily_return > 0.0)
        & (pressure["volume_ratio"] <= 0.8)
        & (close < ema20),
        prior_break.notna()
        & daily_return.notna()
        & pressure["volume_ratio"].notna()
        & ema20.notna(),
    )

    positive_signed = _nullable(
        pressure["signed_volume_proxy"] >= 0.75,
        pressure["signed_volume_proxy"].notna(),
    )
    up_volume = _nullable(
        (daily_return > 0.0)
        & (pressure["volume_ratio"] >= 1.2)
        & (pressure["close_location"] >= 0.4),
        daily_return.notna()
        & pressure["volume_ratio"].notna()
        & pressure["close_location"].notna(),
    )
    strong_close = _nullable(
        pressure["close_location"] >= 0.6,
        pressure["close_location"].notna(),
    )
    seller_exhaustion = _nullable(
        (pressure["volume_ratio"] >= 1.8)
        & (low >= prior_low_5 * 0.995)
        & (pressure["close_location"] >= 0.0),
        pressure["volume_ratio"].notna()
        & prior_low_5.notna()
        & pressure["close_location"].notna(),
    )
    buyer_absorption = _nullable(
        (low < prior_low_5)
        & (close > prior_low_5)
        & (pressure["close_location"] >= 0.4)
        & (pressure["volume_ratio"] >= 1.2),
        prior_low_5.notna()
        & pressure["close_location"].notna()
        & pressure["volume_ratio"].notna(),
    )
    low_volume_higher_low = _nullable(
        (daily_return.shift(1) < 0.0)
        & (pressure["volume_ratio"].shift(1) <= 0.8)
        & (low.shift(1) > prior_low_10.shift(1))
        & (close > prior_close),
        daily_return.shift(1).notna()
        & pressure["volume_ratio"].shift(1).notna()
        & prior_low_10.shift(1).notna()
        & prior_close.notna(),
    )

    pct_over_pivot = close / prior_pivot_20.replace(0.0, np.nan) - 1.0
    breakout_acceptance = _nullable(
        (pct_over_pivot > 0.0)
        & (pct_over_pivot <= 0.05)
        & (pressure["volume_ratio"] >= 1.2),
        prior_pivot_20.notna() & pressure["volume_ratio"].notna(),
    )
    breakout_follow = _breakout_follow_through(
        close,
        prior_pivot_20,
        breakout_acceptance,
        distribution,
    )
    rs_confirm_qqq = _relative_condition(
        stock_return_20,
        qqq_return_20,
        threshold=0.02,
        direction="high",
    )
    rs_confirm_sector = _relative_condition(
        stock_return_20,
        sector_return_20,
        threshold=0.02,
        direction="high",
    )

    supply_groups = (
        _score_group(
            {
                "distribution_day": distribution,
                "high_volume_non_progress": non_progress,
                "negative_signed_volume": negative_signed,
                "distribution_cluster": distribution_cluster,
            },
            SUPPLY_CLOSE_VOLUME_WEIGHTS,
            40.0,
        ),
        _score_group(
            {
                "upper_wick_supply": upper_wick_supply,
                "failed_breakout": failed_breakout,
                "repeated_failed_breakout": repeated_failed,
                "pressure_test_efficiency_decay": efficiency_decay,
            },
            SUPPLY_REJECTION_WEIGHTS,
            30.0,
        ),
        _score_group(
            {
                "volume_confirmed_ema20_break": ema_break,
                "relative_strength_breakdown_qqq": rs_break_qqq,
                "relative_strength_breakdown_sector": rs_break_sector,
                "weak_rebound_below_ema20": weak_rebound,
            },
            SUPPLY_STRUCTURE_WEIGHTS,
            30.0,
        ),
    )
    demand_groups = (
        _score_group(
            {
                "positive_signed_volume": positive_signed,
                "up_volume_confirmation": up_volume,
                "strong_close": strong_close,
            },
            DEMAND_PARTICIPATION_WEIGHTS,
            35.0,
        ),
        _score_group(
            {
                "seller_exhaustion": seller_exhaustion,
                "buyer_absorption": buyer_absorption,
                "low_volume_higher_low": low_volume_higher_low,
            },
            DEMAND_ABSORPTION_WEIGHTS,
            35.0,
        ),
        _score_group(
            {
                "breakout_acceptance": breakout_acceptance,
                "breakout_follow_through": breakout_follow,
                "relative_strength_confirmation_qqq": rs_confirm_qqq,
                "relative_strength_confirmation_sector": rs_confirm_sector,
            },
            DEMAND_BREAKOUT_WEIGHTS,
            30.0,
        ),
    )

    supply_score, supply_coverage, supply_conditions = _model_score(
        supply_groups,
        close.index,
    )
    demand_score, demand_coverage, demand_conditions = _model_score(
        demand_groups,
        close.index,
    )
    unavailable_reasons = _unavailable_reasons(
        close.index,
        supply_coverage,
        demand_coverage,
        qqq_close,
        sector_close,
    )

    result = pd.DataFrame(index=close.index)
    atomic = {
        "distribution_day": distribution,
        "high_volume_non_progress": non_progress,
        "negative_signed_volume": negative_signed,
        "distribution_cluster": distribution_cluster,
        "upper_wick_supply": upper_wick_supply,
        "failed_breakout": failed_breakout,
        "repeated_failed_breakout": repeated_failed,
        "pressure_test_efficiency_decay": efficiency_decay,
        "volume_confirmed_ema20_break": ema_break,
        "relative_strength_breakdown_qqq": rs_break_qqq,
        "relative_strength_breakdown_sector": rs_break_sector,
        "weak_rebound_below_ema20": weak_rebound,
        "positive_signed_volume": positive_signed,
        "up_volume_confirmation": up_volume,
        "strong_close": strong_close,
        "seller_exhaustion": seller_exhaustion,
        "buyer_absorption": buyer_absorption,
        "low_volume_higher_low": low_volume_higher_low,
        "breakout_acceptance": breakout_acceptance,
        "breakout_follow_through": breakout_follow,
        "relative_strength_confirmation_qqq": rs_confirm_qqq,
        "relative_strength_confirmation_sector": rs_confirm_sector,
    }
    for key, values in atomic.items():
        result[key] = values
    result["supply_close_volume_score"] = supply_groups[0]["score"]
    result["supply_rejection_score"] = supply_groups[1]["score"]
    result["supply_structure_context_score"] = supply_groups[2]["score"]
    result["supply_pressure_score"] = supply_score
    result["supply_pressure_coverage"] = supply_coverage
    result["supply_pressure_conditions"] = supply_conditions
    result["demand_participation_score"] = demand_groups[0]["score"]
    result["demand_absorption_score"] = demand_groups[1]["score"]
    result["demand_breakout_context_score"] = demand_groups[2]["score"]
    result["demand_confirmation_score"] = demand_score
    result["demand_confirmation_coverage"] = demand_coverage
    result["demand_confirmation_conditions"] = demand_conditions
    result["supply_demand_state"] = _combined_state(
        supply_score,
        demand_score,
    )
    result["unavailable_reasons"] = unavailable_reasons
    numeric = result.select_dtypes(include=[np.number]).columns
    result.loc[:, numeric] = result.loc[:, numeric].where(
        np.isfinite(result.loc[:, numeric]),
        np.nan,
    )
    return result


def _nullable(values, available):
    result = pd.Series(pd.NA, index=values.index, dtype="boolean")
    selected = pd.Series(available, index=values.index).fillna(False).astype(bool)
    result.loc[selected] = (
        pd.Series(values, index=values.index).loc[selected].fillna(False).astype(bool)
    )
    return result


def _rolling_count(values, window):
    available = values.notna().astype(float).rolling(
        window,
        min_periods=window,
    ).sum() >= float(window)
    count = values.fillna(False).astype(float).rolling(
        window,
        min_periods=window,
    ).sum()
    return count.where(available), available


def _pressure_test_efficiency_decay(high, daily_return, volume_ratio, pivot):
    available = (
        pivot.notna()
        & daily_return.notna()
        & volume_ratio.notna()
        & (volume_ratio > 0.0)
    )
    test = (
        available
        & (daily_return > 0.0)
        & ((high / pivot.replace(0.0, np.nan) - 1.0).abs() <= 0.02)
    )
    efficiency = daily_return.clip(lower=0.0) / volume_ratio.replace(0.0, np.nan)
    positions = pd.Series(np.arange(len(high), dtype=float), index=high.index)
    previous_position = positions.where(test).ffill().shift(1)
    previous_efficiency = efficiency.where(test).ffill().shift(1)
    previous_volume = volume_ratio.where(test).ffill().shift(1)
    recent = (positions - previous_position) <= 15.0
    comparison_available = (
        test
        & recent
        & previous_efficiency.notna()
        & (previous_efficiency > 0.0)
        & previous_volume.notna()
    )
    return _nullable(
        (volume_ratio >= previous_volume)
        & (efficiency <= previous_efficiency * 0.70),
        comparison_available,
    )


def _context_return(source, index, window):
    if source is None:
        return pd.Series(np.nan, index=index, dtype=float)
    if not isinstance(source, pd.Series):
        raise TypeError("market context must be a Series or None")
    if not isinstance(source.index, pd.DatetimeIndex) or source.index.has_duplicates:
        raise ValueError("market context requires a unique DatetimeIndex")
    numeric = pd.to_numeric(source, errors="coerce").reindex(index)
    numeric = numeric.where(np.isfinite(numeric))
    return numeric.pct_change(window, fill_method=None)


def _relative_condition(stock_return, context_return, *, threshold, direction):
    difference = stock_return - context_return
    available = stock_return.notna() & context_return.notna()
    if direction == "low":
        values = difference <= threshold
    elif direction == "high":
        values = difference >= threshold
    else:
        raise ValueError("relative direction must be high or low")
    return _nullable(values, available)


def _breakout_follow_through(close, pivot, accepted, distribution):
    positions = pd.Series(np.arange(len(close), dtype=float), index=close.index)
    accepted_bool = accepted.fillna(False).astype(bool)
    frozen_pivot = pivot.where(accepted_bool).ffill().shift(1)
    accepted_position = positions.where(accepted_bool).ffill().shift(1)
    recent = (positions - accepted_position).between(1.0, 3.0)
    available = recent & frozen_pivot.notna() & distribution.notna()
    return _nullable(
        (close > frozen_pivot) & ~distribution.fillna(False).astype(bool),
        available,
    )


def _score_group(
    conditions: Mapping[str, pd.Series],
    weights: Mapping[str, float],
    cap: float,
):
    index = next(iter(conditions.values())).index
    points = pd.Series(0.0, index=index)
    available_weight = pd.Series(0.0, index=index)
    for key, weight in weights.items():
        values = conditions[key]
        points += (
            values.eq(True).fillna(False).astype(float) * float(weight)
        )
        available_weight += values.notna().astype(float) * float(weight)
    return {
        "score": points.clip(upper=float(cap)),
        "available_weight": available_weight,
        "maximum_weight": float(sum(weights.values())),
        "conditions": conditions,
    }


def _model_score(groups, index):
    available_weight = sum(group["available_weight"] for group in groups)
    maximum_weight = sum(group["maximum_weight"] for group in groups)
    coverage = available_weight / float(maximum_weight)
    score = sum(group["score"] for group in groups)
    score = score.where(coverage >= MINIMUM_COVERAGE)
    conditions = pd.Series(
        [
            tuple(
                key
                for group in groups
                for key, values in group["conditions"].items()
                if pd.notna(values.loc[date]) and bool(values.loc[date])
            )
            for date in index
        ],
        index=index,
        dtype=object,
    )
    return score.round(2), coverage.round(4), conditions


def _combined_state(supply, demand):
    available = supply.notna() & demand.notna()
    state = pd.Series("unavailable", index=supply.index, dtype=object)
    state.loc[available] = "mixed"
    state.loc[available & (supply < 40.0) & (demand < 40.0)] = (
        "low_participation"
    )
    state.loc[available & (demand >= 60.0) & (supply < 40.0)] = (
        "healthy_advance"
    )
    state.loc[available & (supply >= 60.0) & (demand < 50.0)] = (
        "distribution_risk"
    )
    state.loc[available & (supply >= 50.0) & (demand >= 50.0)] = (
        "two_way_contest"
    )
    return state


def _unavailable_reasons(
    index,
    supply_coverage,
    demand_coverage,
    qqq_close,
    sector_close,
):
    rows = []
    for position, date in enumerate(index):
        reasons = []
        if position < 20:
            reasons.append("insufficient_history")
        if qqq_close is None:
            reasons.append("missing_qqq_context")
        if sector_close is None:
            reasons.append("missing_sector_context")
        if float(supply_coverage.loc[date]) < MINIMUM_COVERAGE:
            reasons.append("insufficient_supply_coverage")
        if float(demand_coverage.loc[date]) < MINIMUM_COVERAGE:
            reasons.append("insufficient_demand_coverage")
        rows.append(tuple(reasons))
    return pd.Series(rows, index=index, dtype=object)
