"""Causal, auditable QQQ/SPY market-regime labels for research."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


REGIME_VERSION = "market_regime_v1"
MINIMUM_COMMON_SESSIONS = 200
REGIME_COLUMNS = (
    "regime",
    "regime_version",
    "reason_codes",
    "return_5",
    "return_20",
    "drawdown_from_prior_63_high",
    "atr20_ratio",
    "distribution_days_20",
    "pressure_condition_count",
    "qqq_close",
    "qqq_ema20",
    "qqq_sma50",
    "qqq_sma200",
    "spy_close",
    "spy_sma50",
)


def build_market_regime_frame(histories: Mapping) -> pd.DataFrame:
    """Return fixed market states using information available by each close."""
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    qqq = _prepare_history(histories.get("QQQ"), "QQQ")
    if qqq is None:
        return _empty_frame()
    spy = _prepare_history(histories.get("SPY"), "SPY")
    if spy is None:
        return _unavailable_frame(qqq.index)

    common = qqq.index.intersection(spy.index).sort_values()
    result = _unavailable_frame(qqq.index)
    if common.empty:
        return result

    qqq = qqq.loc[common]
    spy = spy.loc[common]
    close = qqq["Close"]
    previous_close = close.shift(1)
    true_range = pd.concat(
        (
            qqq["High"] - qqq["Low"],
            (qqq["High"] - previous_close).abs(),
            (qqq["Low"] - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    volume_baseline = qqq["Volume"].shift(1).rolling(
        20,
        min_periods=20,
    ).mean()
    daily_return = close.pct_change(fill_method=None)
    distribution_day = (daily_return < 0.0) & (
        qqq["Volume"] > volume_baseline
    )

    evidence = pd.DataFrame(index=common)
    evidence["return_5"] = close.pct_change(5, fill_method=None)
    evidence["return_20"] = close.pct_change(20, fill_method=None)
    prior_63_high = close.shift(1).rolling(63, min_periods=63).max()
    evidence["drawdown_from_prior_63_high"] = close / prior_63_high - 1.0
    evidence["atr20_ratio"] = (
        true_range.rolling(20, min_periods=20).mean() / close
    )
    evidence["distribution_days_20"] = (
        distribution_day.astype(float)
        .where(volume_baseline.notna())
        .rolling(20, min_periods=20)
        .sum()
    )
    evidence["qqq_close"] = close
    evidence["qqq_ema20"] = close.ewm(span=20, adjust=False).mean()
    evidence["qqq_sma50"] = close.rolling(50, min_periods=50).mean()
    evidence["qqq_sma200"] = close.rolling(200, min_periods=200).mean()
    evidence["spy_close"] = spy["Close"]
    evidence["spy_sma50"] = spy["Close"].rolling(
        50,
        min_periods=50,
    ).mean()

    required = (
        "return_5",
        "return_20",
        "drawdown_from_prior_63_high",
        "atr20_ratio",
        "distribution_days_20",
        "qqq_ema20",
        "qqq_sma50",
        "qqq_sma200",
        "spy_sma50",
    )
    available = evidence.loc[:, required].notna().all(axis=1)

    qqq_below_ema20 = close < evidence["qqq_ema20"]
    spy_below_sma50 = evidence["spy_close"] < evidence["spy_sma50"]
    qqq_return_20_negative = evidence["return_20"] < 0.0
    distribution_cluster = evidence["distribution_days_20"] >= 4.0
    pressure_conditions = pd.concat(
        (
            qqq_below_ema20,
            spy_below_sma50,
            qqq_return_20_negative,
            distribution_cluster,
        ),
        axis=1,
    ).sum(axis=1)
    evidence["pressure_condition_count"] = pressure_conditions.astype(float)

    acute = (evidence["return_5"] <= -0.07) | (
        (evidence["return_20"] <= -0.12)
        & (evidence["atr20_ratio"] >= 0.025)
    )
    correction = (close < evidence["qqq_sma50"]) & (
        (evidence["return_20"] <= -0.05)
        | (evidence["drawdown_from_prior_63_high"] <= -0.08)
    )
    under_pressure = pressure_conditions >= 2
    uptrend = (
        (close > evidence["qqq_ema20"])
        & (close > evidence["qqq_sma50"])
        & (close > evidence["qqq_sma200"])
        & (evidence["qqq_ema20"] > evidence["qqq_sma50"])
        & (evidence["spy_close"] > evidence["spy_sma50"])
        & (evidence["return_20"] > 0.0)
    )

    regime = pd.Series("range_bound", index=common, dtype=object)
    regime.loc[uptrend] = "uptrend"
    regime.loc[under_pressure] = "under_pressure"
    regime.loc[correction] = "correction"
    regime.loc[acute] = "acute_selloff"
    regime.loc[~available] = "unavailable"

    evidence["regime"] = regime
    evidence["regime_version"] = REGIME_VERSION
    evidence["reason_codes"] = [
        _reason_codes(
            selected_regime,
            qqq_below_ema20=bool(qqq_below_ema20.loc[date]),
            spy_below_sma50=bool(spy_below_sma50.loc[date]),
            return_20_negative=bool(qqq_return_20_negative.loc[date]),
            distribution_cluster=bool(distribution_cluster.loc[date]),
            acute_return_5=bool(evidence.loc[date, "return_5"] <= -0.07),
            acute_return_20=bool(
                (evidence.loc[date, "return_20"] <= -0.12)
                and (evidence.loc[date, "atr20_ratio"] >= 0.025)
            ),
            correction_return=bool(
                evidence.loc[date, "return_20"] <= -0.05
            ),
            correction_drawdown=bool(
                evidence.loc[date, "drawdown_from_prior_63_high"] <= -0.08
            ),
        )
        for date, selected_regime in regime.items()
    ]
    result.loc[common, :] = evidence.loc[:, REGIME_COLUMNS]
    numeric_columns = result.select_dtypes(include=[np.number]).columns
    result.loc[:, numeric_columns] = result.loc[:, numeric_columns].where(
        np.isfinite(result.loc[:, numeric_columns]),
        np.nan,
    )
    return result


def _prepare_history(source, ticker):
    if source is None:
        return None
    if not isinstance(source, pd.DataFrame):
        raise TypeError(f"{ticker} history must be a DataFrame")
    if source.empty:
        return None
    required = ("Open", "High", "Low", "Close", "Volume")
    try:
        result = source.loc[:, required].copy(deep=True)
    except KeyError as exc:
        raise ValueError(f"{ticker} history is missing OHLCV columns") from exc
    if not isinstance(result.index, pd.DatetimeIndex):
        raise TypeError(f"{ticker} history index must be DatetimeIndex")
    result.index = result.index.tz_localize(None)
    if result.index.has_duplicates:
        raise ValueError(f"{ticker} history contains duplicate dates")
    result = result.sort_index().astype(float)
    if not np.isfinite(result.to_numpy()).all():
        raise ValueError(f"{ticker} history contains non-finite values")
    return result


def _reason_codes(
    regime,
    *,
    qqq_below_ema20,
    spy_below_sma50,
    return_20_negative,
    distribution_cluster,
    acute_return_5,
    acute_return_20,
    correction_return,
    correction_drawdown,
):
    if regime == "unavailable":
        return "insufficient_common_history"
    if regime == "acute_selloff":
        reasons = []
        if acute_return_5:
            reasons.append("qqq_return_5_le_-7pct")
        if acute_return_20:
            reasons.append("qqq_return_20_le_-12pct_with_high_atr")
        return ",".join(reasons)
    if regime == "correction":
        reasons = ["qqq_below_sma50"]
        if correction_return:
            reasons.append("qqq_return_20_le_-5pct")
        if correction_drawdown:
            reasons.append("qqq_drawdown_63_le_-8pct")
        return ",".join(reasons)
    if regime == "under_pressure":
        reasons = []
        if qqq_below_ema20:
            reasons.append("qqq_below_ema20")
        if spy_below_sma50:
            reasons.append("spy_below_sma50")
        if return_20_negative:
            reasons.append("qqq_return_20_negative")
        if distribution_cluster:
            reasons.append("distribution_days_20_ge_4")
        return ",".join(reasons)
    if regime == "uptrend":
        return "qqq_above_trend_stack,spy_above_sma50,qqq_return_20_positive"
    return "no_trend_or_stress_consensus"


def _unavailable_frame(index):
    result = pd.DataFrame(index=pd.DatetimeIndex(index), columns=REGIME_COLUMNS)
    result["regime"] = "unavailable"
    result["regime_version"] = REGIME_VERSION
    result["reason_codes"] = "missing_qqq_or_spy"
    for column in REGIME_COLUMNS[3:]:
        result[column] = np.nan
    return result


def _empty_frame():
    return _unavailable_frame(pd.DatetimeIndex([]))
