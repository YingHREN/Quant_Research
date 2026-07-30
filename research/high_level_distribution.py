"""Causal high-level distribution and bearish top-turn risk state."""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.market_pressure import build_pressure_rows
from research.risk_memory import build_risk_memory_state
from research.supply_regime import build_supply_regime_rows


MINIMUM_CONTEXT_COVERAGE = 0.75
HIGH_CONTEXT_THRESHOLD = 60.0
SUPPLY_WATCH_THRESHOLD = 40.0
SUPPLY_HIGH_THRESHOLD = 60.0
STRUCTURE_CONFIRM_THRESHOLD = 40.0


def build_high_level_distribution_state(
    history: pd.DataFrame,
    sector_close: pd.Series | None = None,
    qqq_history: pd.DataFrame | None = None,
    group_supply: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return point-in-time high-level supply and structure-risk rows."""
    checked = _history(history)
    close = checked["Close"]
    volume = checked["Volume"]
    pressure = build_pressure_rows(checked)
    supply_regime = build_supply_regime_rows(checked)
    ema20 = close.ewm(span=20, adjust=False).mean()
    sma50 = close.rolling(50, min_periods=50).mean()
    prior_high_252 = close.shift(1).rolling(252, min_periods=126).max()
    atr20 = _true_range(checked).rolling(20, min_periods=20).mean()

    context_rules = (
        _numeric_rule(close.pct_change(60, fill_method=None), 30.0, 0.20, "high"),
        _numeric_rule(
            close / prior_high_252.replace(0.0, np.nan) - 1.0,
            25.0,
            -0.15,
            "high",
        ),
        _boolean_rule((close > sma50).where(sma50.notna()), 20.0),
        _numeric_rule(
            (close - ema20) / atr20.replace(0.0, np.nan),
            25.0,
            1.0,
            "high",
        ),
    )
    context_raw_score, context_coverage = _score_rules(
        context_rules,
        checked.index,
    )
    context_score = context_raw_score.rolling(
        10,
        min_periods=1,
    ).max()

    close_volume_points = pd.Series(0.0, index=checked.index)
    close_volume_points += pressure["distribution_day"].astype(float) * 25.0
    close_volume_points += (
        pressure["high_volume_non_progress"].astype(float) * 15.0
    )
    close_volume_points += (
        (pressure["signed_volume_proxy"] <= -0.75).astype(float) * 15.0
    ).where(pressure["signed_volume_proxy"].notna(), 0.0)
    close_volume_points += (
        (pressure["close_location"] <= -0.5).astype(float) * 10.0
    ).where(pressure["close_location"].notna(), 0.0)
    close_volume_points += (
        supply_regime["churning_cluster"].eq(True).astype(float) * 15.0
    )
    close_volume_points += (
        (supply_regime["distribution_count_10"] >= 2.0)
        .fillna(False)
        .astype(float)
        * 15.0
    )
    close_volume_score = close_volume_points.clip(upper=40.0)

    upper_wick_supply = (
        (pressure["upper_wick_ratio"] >= 0.35)
        & (pressure["volume_ratio"] >= 1.2)
    ).where(
        pressure["upper_wick_ratio"].notna()
        & pressure["volume_ratio"].notna()
    )
    rejection_points = (
        upper_wick_supply.astype(float).fillna(0.0) * 15.0
        + pressure["failed_breakout"].astype(float) * 20.0
        + (
            (supply_regime["failed_breakout_count_10"] >= 2.0)
            .fillna(False)
            .astype(float)
            * 10.0
        )
    )
    rejection_score = rejection_points.clip(upper=30.0)

    relative_score, relative_conditions = _relative_supply(
        checked,
        sector_close,
        qqq_history,
        group_supply,
    )
    supply_score = close_volume_score + rejection_score + relative_score

    structure_rules = (
        _boolean_rule((close < ema20).where(ema20.notna()), 30.0),
        _numeric_rule(ema20 / ema20.shift(5) - 1.0, 20.0, 0.0, "low"),
        _boolean_rule((close < sma50).where(sma50.notna()), 25.0),
        _numeric_rule(
            close / close.shift(1).rolling(20, min_periods=20).max() - 1.0,
            25.0,
            -0.10,
            "low",
        ),
    )
    structure_score, structure_coverage = _score_rules(
        structure_rules,
        checked.index,
    )

    raw_score = (
        context_score * 0.35
        + supply_score * 0.40
        + structure_score * 0.25
    )
    raw_state = _raw_states(
        context_score,
        supply_score,
        structure_score,
    )
    memory_input = raw_score.where(
        raw_state.isin(("watch", "high", "confirmed")),
        0.0,
    )
    memory_input = memory_input.where(context_score.notna())
    prior_high_20 = close.shift(1).rolling(20, min_periods=20).max()
    strong_reclaim = (
        (close > prior_high_20)
        & (close > ema20)
        & (pressure["signed_volume_proxy"] >= 0.5)
        & (pressure["volume_ratio"] >= 1.2)
    ).where(
        prior_high_20.notna()
        & pressure["signed_volume_proxy"].notna()
        & pressure["volume_ratio"].notna()
    )
    risk_recovery = strong_reclaim.eq(True)
    memory = _memory_with_resets(memory_input, risk_recovery)
    state = _semantic_states(raw_state, memory["state"])

    result = pd.DataFrame(index=checked.index)
    result["high_level_context_raw_score"] = context_raw_score.round(2)
    result["high_level_context_score"] = context_score.round(2)
    result["high_level_context_coverage"] = context_coverage.round(4)
    result["close_volume_supply_score"] = close_volume_score.round(2)
    result["rejection_supply_score"] = rejection_score.round(2)
    result["relative_supply_score"] = relative_score.round(2)
    result["distribution_pressure_score"] = supply_score.round(2)
    for field in (
        "distribution_count_5",
        "distribution_count_10",
        "distribution_count_20",
        "churning_count_10",
        "churning_cluster",
        "failed_breakout_count_10",
        "climax_run_score",
        "climax_run_candidate",
        "climax_run_conditions",
    ):
        result[field] = supply_regime[field]
    result["structure_damage_score"] = structure_score.round(2)
    result["structure_damage_coverage"] = structure_coverage.round(4)
    result["high_level_distribution_raw_score"] = raw_score.round(2)
    result["high_level_distribution_raw_state"] = raw_state
    result["high_level_distribution_state_score"] = memory[
        "state_score"
    ].round(2)
    result["high_level_distribution_state"] = state
    result["high_level_distribution_memory_age_sessions"] = memory[
        "memory_age_sessions"
    ]
    result["risk_recovery"] = risk_recovery.astype(bool)
    result["risk_recovery_conditions"] = _condition_tuples(
        {"strong_reclaim": strong_reclaim},
        checked.index,
    )
    result["high_level_context_conditions"] = _condition_tuples(
        {
            "prior_60_session_advance": close.pct_change(
                60,
                fill_method=None,
            ) >= 0.20,
            "near_252_session_high": (
                close / prior_high_252.replace(0.0, np.nan) - 1.0
            ) >= -0.15,
            "above_sma50": close > sma50,
            "extended_above_ema20": (
                (close - ema20) / atr20.replace(0.0, np.nan)
            ) >= 1.0,
        },
        checked.index,
    )
    result["distribution_pressure_conditions"] = _condition_tuples(
        {
            "distribution_day": pressure["distribution_day"],
            "high_volume_non_progress": pressure[
                "high_volume_non_progress"
            ],
            "negative_signed_volume": (
                pressure["signed_volume_proxy"] <= -0.75
            ),
            "weak_close": pressure["close_location"] <= -0.5,
            "upper_wick_supply": upper_wick_supply,
            "failed_breakout": pressure["failed_breakout"],
            "multi_session_churning": supply_regime["churning_cluster"],
            "distribution_cluster": (
                supply_regime["distribution_count_10"] >= 2.0
            ),
            "repeated_failed_breakout": (
                supply_regime["failed_breakout_count_10"] >= 2.0
            ),
            **relative_conditions,
        },
        checked.index,
    )
    result["structure_damage_conditions"] = _condition_tuples(
        {
            "below_ema20": close < ema20,
            "ema20_slope_negative": ema20 / ema20.shift(5) - 1.0 < 0.0,
            "below_sma50": close < sma50,
            "drawdown_from_20_session_high": (
                close
                / close.shift(1).rolling(20, min_periods=20).max()
                - 1.0
            ) <= -0.10,
        },
        checked.index,
    )
    result["unavailable_reason"] = np.where(
        context_score.isna(),
        "insufficient_high_level_context",
        None,
    )
    return result


def _relative_supply(history, sector_close, qqq_history, group_supply):
    index = history.index
    score = pd.Series(0.0, index=index)
    conditions: dict[str, pd.Series] = {}
    if isinstance(sector_close, pd.Series):
        aligned = pd.to_numeric(sector_close, errors="coerce").reindex(index)
        stock_return = history["Close"].pct_change(20, fill_method=None)
        sector_return = aligned.pct_change(20, fill_method=None)
        weak = stock_return - sector_return <= -0.05
        score += weak.astype(float).fillna(0.0) * 15.0
        conditions["underperforming_sector"] = weak
    if isinstance(qqq_history, pd.DataFrame) and not qqq_history.empty:
        qqq = build_pressure_rows(qqq_history).reindex(index)
        count = (
            qqq["distribution_day"]
            .astype(float)
            .rolling(20, min_periods=20)
            .sum()
        )
        stressed = count >= 4.0
        score += stressed.astype(float).fillna(0.0) * 15.0
        conditions["qqq_distribution_cluster"] = stressed
    if isinstance(group_supply, pd.DataFrame) and not group_supply.empty:
        aligned = group_supply.reindex(index)
        group_rules = (
            (
                "group_distribution_breadth",
                "distribution_breadth",
                0.30,
                15.0,
            ),
            (
                "group_down_volume_breadth",
                "down_volume_breadth",
                0.60,
                10.0,
            ),
            (
                "group_volume_expansion",
                "mean_volume_ratio",
                1.30,
                5.0,
            ),
        )
        for condition_name, column, threshold, points in group_rules:
            if column not in aligned:
                continue
            values = pd.to_numeric(aligned[column], errors="coerce")
            met = (values >= threshold).where(values.notna())
            score += met.eq(True).astype(float) * points
            conditions[condition_name] = met
    return score.clip(upper=30.0), conditions


def _memory_with_resets(raw_score, resets):
    frames = []
    start = 0
    reset_values = resets.reindex(raw_score.index).eq(True)
    for position, reset in enumerate(reset_values):
        if not reset:
            continue
        if position > start:
            frames.append(build_risk_memory_state(raw_score.iloc[start:position]))
        reset_index = raw_score.index[position : position + 1]
        frames.append(
            pd.DataFrame(
                {
                    "raw_score": raw_score.iloc[position : position + 1],
                    "state_score": 0.0,
                    "state": "inactive",
                    "memory_age_sessions": 0.0,
                },
                index=reset_index,
            )
        )
        start = position + 1
    if start < len(raw_score):
        frames.append(build_risk_memory_state(raw_score.iloc[start:]))
    if not frames:
        return build_risk_memory_state(raw_score)
    return pd.concat(frames).reindex(raw_score.index)


def _raw_states(context, supply, structure):
    states = pd.Series("inactive", index=context.index, dtype=object)
    states.loc[context.isna()] = "unavailable"
    states.loc[context < HIGH_CONTEXT_THRESHOLD] = "low"
    high_context = context >= HIGH_CONTEXT_THRESHOLD
    states.loc[high_context & (supply >= SUPPLY_WATCH_THRESHOLD)] = "watch"
    states.loc[high_context & (supply >= SUPPLY_HIGH_THRESHOLD)] = "high"
    states.loc[
        high_context
        & (supply >= SUPPLY_WATCH_THRESHOLD)
        & (structure >= STRUCTURE_CONFIRM_THRESHOLD)
    ] = "confirmed"
    return states


def _semantic_states(raw_state, memory_state):
    states = raw_state.copy()
    fading = (
        ~raw_state.isin(("watch", "high", "confirmed"))
        & (memory_state == "fading")
    )
    states.loc[fading] = "fading"
    return states


def _score_rules(rules, index):
    points = pd.Series(0.0, index=index)
    available = pd.Series(0.0, index=index)
    maximum = 0.0
    for rule_points, rule_available, weight in rules:
        points += rule_points
        available += rule_available
        maximum += weight
    coverage = available / maximum
    score = (
        points / available.replace(0.0, np.nan) * 100.0
    ).where(coverage >= MINIMUM_CONTEXT_COVERAGE)
    return score, coverage


def _boolean_rule(values, weight):
    numeric = pd.to_numeric(values, errors="coerce")
    available = numeric.notna().astype(float) * weight
    points = numeric.fillna(0.0).astype(float) * weight
    return points, available, weight


def _numeric_rule(values, weight, threshold, direction):
    numeric = pd.to_numeric(values, errors="coerce")
    if direction == "high":
        met = numeric >= threshold
    elif direction == "low":
        met = numeric < threshold
    else:
        raise ValueError("direction must be high or low")
    available = numeric.notna().astype(float) * weight
    points = met.fillna(False).astype(float) * weight
    return points, available, weight


def _condition_tuples(values, index):
    aligned = {
        key: series.reindex(index)
        for key, series in values.items()
    }
    return pd.Series(
        [
            tuple(
                key
                for key, series in aligned.items()
                if pd.notna(series.loc[observation_date])
                and bool(series.loc[observation_date])
            )
            for observation_date in index
        ],
        index=index,
        dtype=object,
    )


def _true_range(history):
    prior_close = history["Close"].shift(1)
    return pd.concat(
        (
            history["High"] - history["Low"],
            (history["High"] - prior_close).abs(),
            (history["Low"] - prior_close).abs(),
        ),
        axis=1,
    ).max(axis=1)


def _history(source):
    build_pressure_rows(source)
    result = source.loc[:, ("Open", "High", "Low", "Close", "Volume")].copy()
    return result.sort_index().astype(float)
