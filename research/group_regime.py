"""Causal breadth and volume stress state for explicit market groups."""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.market_pressure import build_pressure_rows
from research.risk_memory import build_risk_memory_state
from web.market_groups import MarketGroup


MINIMUM_GROUP_MEMBERS = 3
MINIMUM_SCORE_COVERAGE = 0.80
GROUP_STRESS_RULES_V1 = {
    "relative_return_5": (20.0, -0.03, -0.015, "low"),
    "breadth_above_ema20": (20.0, 0.45, 0.60, "low"),
    "down_volume_breadth": (15.0, 0.60, 0.40, "high"),
    "distribution_breadth": (20.0, 0.30, 0.15, "high"),
    "new_20_low_breadth": (15.0, 0.20, 0.10, "high"),
    "mean_volume_ratio": (10.0, 1.30, 1.10, "high"),
}


def build_group_regime_state(
    histories,
    group: MarketGroup,
    *,
    membership_intervals=None,
) -> pd.DataFrame:
    """Return an equal-weighted, point-in-time group stress history."""
    if not isinstance(group, MarketGroup):
        raise TypeError("group must be a MarketGroup")
    prepared = _prepare(histories)
    members = tuple(
        ticker
        for ticker in dict.fromkeys(
            (*group.constituent_tickers, *group.related_tickers)
        )
        if ticker in prepared
    )
    if not prepared:
        return _empty_frame()

    member_parts = {
        ticker: _membership_parts(
            prepared[ticker],
            None
            if membership_intervals is None
            else membership_intervals.get(ticker),
        )
        for ticker in members
    }
    union_index = pd.DatetimeIndex(
        sorted(
            set().union(
                *(
                    set(part.index)
                    for parts in member_parts.values()
                    for part in parts
                )
            )
        )
    ) if members else pd.DatetimeIndex([])
    if union_index.empty:
        return _empty_frame()

    metrics = {
        "return_5": {},
        "above_ema20": {},
        "down_volume": {},
        "distribution": {},
        "new_20_low": {},
        "volume_ratio": {},
    }
    for ticker in members:
        ticker_metrics = {key: [] for key in metrics}
        for history in member_parts[ticker]:
            close = history["Close"]
            pressure = build_pressure_rows(history)
            daily_return = close.pct_change(fill_method=None)
            ema20 = close.ewm(span=20, adjust=False).mean()
            prior_low = close.shift(1).rolling(20, min_periods=20).min()
            ticker_metrics["return_5"].append(
                close.pct_change(5, fill_method=None)
            )
            ticker_metrics["above_ema20"].append(
                (close > ema20).where(ema20.notna())
            )
            ticker_metrics["down_volume"].append(
                (
                    (daily_return < 0.0)
                    & (pressure["volume_ratio"] >= 1.2)
                ).where(
                    daily_return.notna()
                    & pressure["volume_ratio"].notna()
                )
            )
            ticker_metrics["distribution"].append(
                pressure["distribution_day"].where(
                    pressure["volume_ratio"].notna()
                )
            )
            ticker_metrics["new_20_low"].append(
                (close < prior_low).where(prior_low.notna())
            )
            ticker_metrics["volume_ratio"].append(
                pressure["volume_ratio"]
            )
        for key, parts in ticker_metrics.items():
            if parts:
                metrics[key][ticker] = pd.concat(parts).sort_index()

    result = pd.DataFrame(index=union_index)
    result["member_count"] = float(len(members))
    result["group_return_5"] = _mean_metric(metrics["return_5"], union_index)
    qqq_return = _series_metric(prepared.get("QQQ"), union_index, 5)
    result["relative_return_5"] = result["group_return_5"] - qqq_return
    result["breadth_above_ema20"] = _mean_metric(
        metrics["above_ema20"],
        union_index,
    )
    result["down_volume_breadth"] = _mean_metric(
        metrics["down_volume"],
        union_index,
    )
    result["distribution_breadth"] = _mean_metric(
        metrics["distribution"],
        union_index,
    )
    result["new_20_low_breadth"] = _mean_metric(
        metrics["new_20_low"],
        union_index,
    )
    result["mean_volume_ratio"] = _mean_metric(
        metrics["volume_ratio"],
        union_index,
    )

    points = pd.Series(0.0, index=union_index)
    available = pd.Series(0.0, index=union_index)
    for key, (weight, met, near, direction) in GROUP_STRESS_RULES_V1.items():
        rule_points, rule_available = _numeric_rule(
            result[key],
            weight,
            met,
            near,
            direction=direction,
        )
        points += rule_points
        available += rule_available
    result["coverage"] = available / 100.0
    enough_members = len(members) >= MINIMUM_GROUP_MEMBERS
    result["raw_score"] = (
        points / available.replace(0.0, np.nan) * 100.0
    ).where(enough_members & (result["coverage"] >= MINIMUM_SCORE_COVERAGE))
    result["raw_score"] = result["raw_score"].round(2)
    memory = build_risk_memory_state(result["raw_score"])
    result["state_score"] = memory["state_score"]
    result["state"] = memory["state"]
    result["memory_age_sessions"] = memory["memory_age_sessions"]
    numeric_columns = result.select_dtypes(include=[np.number]).columns
    result.loc[:, numeric_columns] = result.loc[:, numeric_columns].where(
        np.isfinite(result.loc[:, numeric_columns]),
        np.nan,
    )
    return result


def _membership_parts(history, intervals):
    if intervals is None:
        return (history,)
    parts = tuple(
        history.loc[
            (history.index >= effective_from)
            & (
                True
                if effective_to is None
                else history.index < effective_to
            )
        ]
        for effective_from, effective_to in intervals
    )
    return tuple(part for part in parts if not part.empty)


def _prepare(histories):
    try:
        sources = dict(histories)
    except (TypeError, ValueError) as exc:
        raise TypeError("histories must be a mapping") from exc
    result = {}
    for raw_ticker, source in sources.items():
        ticker = str(raw_ticker).strip().upper()
        if not ticker or not isinstance(source, pd.DataFrame) or source.empty:
            continue
        try:
            checked = source.loc[
                :, ("Open", "High", "Low", "Close", "Volume")
            ].copy(deep=True)
        except KeyError:
            continue
        if not isinstance(checked.index, pd.DatetimeIndex):
            continue
        checked.index = checked.index.tz_localize(None)
        if checked.index.has_duplicates:
            raise ValueError(f"duplicate observation dates for {ticker}")
        values = checked.to_numpy(dtype=float)
        if not np.isfinite(values).all():
            continue
        result[ticker] = checked.sort_index().astype(float)
    return result


def _mean_metric(values, index):
    if not values:
        return pd.Series(np.nan, index=index, dtype=float)
    frame = pd.concat(values, axis=1).reindex(index)
    return frame.astype(float).mean(axis=1, skipna=True).where(
        frame.notna().sum(axis=1) >= MINIMUM_GROUP_MEMBERS
    )


def _series_metric(history, index, window):
    if history is None:
        return pd.Series(np.nan, index=index, dtype=float)
    values = history["Close"].pct_change(window, fill_method=None)
    return values.reindex(index, method="ffill")


def _numeric_rule(values, weight, met, near, *, direction):
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric.notna() & np.isfinite(numeric)
    if direction == "high":
        hit = numeric >= met
        close = (numeric < met) & (numeric >= near)
    else:
        hit = numeric <= met
        close = (numeric > met) & (numeric <= near)
    points = pd.Series(0.0, index=numeric.index)
    points.loc[close & finite] = weight / 2.0
    points.loc[hit & finite] = weight
    return points, finite.astype(float) * weight


def _empty_frame():
    columns = (
        "member_count",
        "group_return_5",
        "relative_return_5",
        "breadth_above_ema20",
        "down_volume_breadth",
        "distribution_breadth",
        "new_20_low_breadth",
        "mean_volume_ratio",
        "coverage",
        "raw_score",
        "state_score",
        "state",
        "memory_age_sessions",
    )
    return pd.DataFrame(columns=columns, index=pd.DatetimeIndex([]))
