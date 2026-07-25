"""Causal persistent erosion state for individually mapped stocks."""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.market_pressure import build_pressure_rows
from research.risk_memory import build_risk_memory_state
from web.market_groups import MarketGroup


MINIMUM_SCORE_COVERAGE = 0.80


def build_slow_decline_state(histories, group: MarketGroup) -> pd.DataFrame:
    """Return point-in-time slow-decline scores for explicit group members."""
    if not isinstance(group, MarketGroup):
        raise TypeError("group must be a MarketGroup")
    prepared = _prepare(histories)
    primary = tuple(
        ticker for ticker in group.benchmark_tickers if ticker in prepared
    )
    group_close = _group_composite(
        prepared,
        primary or group.fallback_benchmark_tickers,
    )
    qqq_close = (
        None if "QQQ" not in prepared else prepared["QQQ"]["Close"]
    )
    frames = {}
    members = tuple(
        dict.fromkeys(
            (*group.constituent_tickers, *group.related_tickers)
        )
    )
    for ticker in members:
        history = prepared.get(ticker)
        if history is None:
            continue
        frames[ticker] = _stock_state(history, group_close, qqq_close)
    if not frames:
        return _empty_frame()
    return pd.concat(
        frames,
        names=("ticker", "observation_date"),
    ).sort_index()


def _stock_state(history, group_close, qqq_close):
    close = history["Close"].astype(float)
    ema20 = close.ewm(span=20, adjust=False).mean()
    sma50 = close.rolling(50, min_periods=50).mean()
    pressure = build_pressure_rows(history)
    group_return = _relative_return(close, group_close, 20)
    qqq_return = _relative_return(close, qqq_close, 20)
    result = pd.DataFrame(index=history.index)
    result["return_5"] = close.pct_change(5, fill_method=None)
    result["return_20"] = close.pct_change(20, fill_method=None)
    result["return_60"] = close.pct_change(60, fill_method=None)
    result["close_below_ema20"] = (close < ema20).where(ema20.notna())
    result["ema20_slope_5"] = ema20 / ema20.shift(5) - 1.0
    result["close_below_sma50"] = (close < sma50).where(sma50.notna())
    result["distribution_count_20"] = (
        pressure["distribution_day"]
        .astype(float)
        .rolling(20, min_periods=20)
        .sum()
    )
    result["relative_group_20"] = group_return
    result["relative_qqq_20"] = qqq_return

    rules = (
        _numeric_rule(result["return_20"], 15.0, -0.05, 0.0, "low"),
        _numeric_rule(result["return_60"], 15.0, -0.10, -0.03, "low"),
        _boolean_rule(result["close_below_ema20"], 15.0),
        _numeric_rule(result["ema20_slope_5"], 10.0, -0.005, 0.0, "low"),
        _boolean_rule(result["close_below_sma50"], 15.0),
        _numeric_rule(result["return_5"], 10.0, -0.02, 0.0, "low"),
        _numeric_rule(
            result["distribution_count_20"],
            10.0,
            3.0,
            1.0,
            "high",
        ),
        _numeric_rule(result["relative_group_20"], 5.0, -0.03, 0.0, "low"),
        _numeric_rule(result["relative_qqq_20"], 5.0, -0.03, 0.0, "low"),
    )
    points = pd.Series(0.0, index=result.index)
    available = pd.Series(0.0, index=result.index)
    for rule_points, rule_available in rules:
        points += rule_points
        available += rule_available
    result["coverage"] = available / 100.0
    result["raw_score"] = (
        points / available.replace(0.0, np.nan) * 100.0
    ).where(result["coverage"] >= MINIMUM_SCORE_COVERAGE).round(2)
    memory = build_risk_memory_state(result["raw_score"])
    result["state_score"] = memory["state_score"]
    result["state"] = memory["state"]
    result["memory_age_sessions"] = memory["memory_age_sessions"]
    numeric = result.select_dtypes(include=[np.number]).columns
    result.loc[:, numeric] = result.loc[:, numeric].where(
        np.isfinite(result.loc[:, numeric]),
        np.nan,
    )
    return result


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
        missing = [
            key
            for key in ("Open", "High", "Low", "Close", "Volume")
            if key not in source
        ]
        if missing or not isinstance(source.index, pd.DatetimeIndex):
            continue
        history = source.loc[
            :, ("Open", "High", "Low", "Close", "Volume")
        ].copy(deep=True).sort_index()
        history.index = history.index.tz_localize(None)
        if history.index.has_duplicates:
            raise ValueError(f"duplicate observation dates for {ticker}")
        if not np.isfinite(history.to_numpy(dtype=float)).all():
            continue
        result[ticker] = history.astype(float)
    return result


def _group_composite(prepared, tickers):
    returns = {}
    for ticker in tickers:
        history = prepared.get(ticker)
        if history is not None:
            returns[ticker] = history["Close"].pct_change(fill_method=None)
    if not returns:
        return None
    mean_return = pd.concat(returns, axis=1).mean(axis=1, skipna=True)
    return (1.0 + mean_return.fillna(0.0)).cumprod()


def _relative_return(close, benchmark, window):
    if benchmark is None or benchmark.empty:
        return pd.Series(np.nan, index=close.index, dtype=float)
    aligned = benchmark.reindex(close.index, method="ffill")
    return close.pct_change(window, fill_method=None) - aligned.pct_change(
        window,
        fill_method=None,
    )


def _boolean_rule(values, weight):
    numeric = pd.to_numeric(values, errors="coerce")
    available = numeric.notna().astype(float) * weight
    points = (numeric != 0.0).astype(float) * weight
    return points.where(numeric.notna(), 0.0), available


def _numeric_rule(values, weight, met, near, direction):
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
    index = pd.MultiIndex.from_arrays(
        [pd.Index([], dtype=object), pd.DatetimeIndex([])],
        names=("ticker", "observation_date"),
    )
    return pd.DataFrame(
        columns=(
            "return_5",
            "return_20",
            "return_60",
            "close_below_ema20",
            "ema20_slope_5",
            "close_below_sma50",
            "distribution_count_20",
            "relative_group_20",
            "relative_qqq_20",
            "coverage",
            "raw_score",
            "state_score",
            "state",
            "memory_age_sessions",
        ),
        index=index,
    )
