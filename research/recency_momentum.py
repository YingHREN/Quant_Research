"""Causal, interpretable recency-weighted momentum features."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd


DECAY_HEADS = {
    "decay_mom_1_3": (1, 3, 2.0),
    "decay_mom_4_10": (4, 10, 4.0),
    "decay_mom_11_20": (11, 20, 7.0),
    "decay_mom_21_60": (21, 60, 20.0),
    "decay_mom_1_20": (1, 20, 7.0),
}
RECENCY_FEATURE_COLUMNS = (
    *DECAY_HEADS,
    "decay_volume_confirmation_1_20",
    "decay_close_location_pressure_1_20",
    "decay_excess_qqq_1_20",
    "decay_excess_sector_1_20",
    "decay_market_agreement_1_20",
)
REQUIRED_COLUMNS = ("High", "Low", "Close", "Volume")


def build_recency_momentum_frame(
    histories: Mapping[str, pd.DataFrame],
    *,
    benchmark_by_ticker=None,
    market_ticker="QQQ",
) -> pd.DataFrame:
    """Return causal temporal features for every supplied ticker and date.

    Lag 1 is the return ending on the observation date. Expected volume is a
    shifted 20-session median, so the current session never defines its own
    baseline.
    """
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    market_ticker = str(market_ticker).strip().upper()
    benchmark_by_ticker = {
        str(ticker).strip().upper(): str(benchmark).strip().upper()
        for ticker, benchmark in (benchmark_by_ticker or {}).items()
        if str(ticker).strip() and str(benchmark).strip()
    }
    validated = {
        str(ticker).strip().upper(): _validated_history(str(ticker), history)
        for ticker, history in histories.items()
    }
    atomic = {
        ticker: _atomic_features(history)
        for ticker, history in validated.items()
    }
    rows = []
    market_momentum = _momentum_head(atomic.get(market_ticker))
    for ticker, features in atomic.items():
        result = features.loc[:, tuple(DECAY_HEADS)].copy()
        result["decay_volume_confirmation_1_20"] = _decayed(
            features["volume_confirmed_return"],
            1,
            20,
            7.0,
        )
        result["decay_close_location_pressure_1_20"] = _decayed(
            features["close_location_pressure"],
            1,
            20,
            7.0,
        )
        stock_momentum = features["decay_mom_1_20"]
        qqq = (
            market_momentum.reindex(result.index)
            if market_momentum is not None
            else pd.Series(np.nan, index=result.index)
        )
        sector_ticker = benchmark_by_ticker.get(ticker)
        sector_momentum = _momentum_head(atomic.get(sector_ticker))
        sector = (
            sector_momentum.reindex(result.index)
            if sector_momentum is not None
            else pd.Series(np.nan, index=result.index)
        )
        result["decay_excess_qqq_1_20"] = stock_momentum - qqq
        result["decay_excess_sector_1_20"] = stock_momentum - sector
        agreement = (
            np.sign(stock_momentum) * np.sign(qqq)
            + np.sign(stock_momentum) * np.sign(sector)
        ) / 2.0
        result["decay_market_agreement_1_20"] = (
            stock_momentum.abs() * agreement
        )
        result["ticker"] = ticker
        result["observation_date"] = result.index
        rows.append(result.reset_index(drop=True))
    if not rows:
        index = pd.MultiIndex.from_arrays(
            (pd.Index([], dtype=object), pd.DatetimeIndex([])),
            names=("ticker", "observation_date"),
        )
        return pd.DataFrame(columns=RECENCY_FEATURE_COLUMNS, index=index)
    combined = pd.concat(rows, ignore_index=True)
    combined = combined.set_index(["ticker", "observation_date"]).sort_index()
    combined = combined.loc[:, RECENCY_FEATURE_COLUMNS].astype(float)
    return combined.where(np.isfinite(combined), np.nan)


def _atomic_features(history):
    close = history["Close"].astype(float)
    log_return = np.log(close / close.shift(1))
    result = pd.DataFrame(index=history.index)
    for name, (start, end, half_life) in DECAY_HEADS.items():
        result[name] = _decayed(log_return, start, end, half_life)
    expected_volume = (
        history["Volume"]
        .astype(float)
        .rolling(20, min_periods=10)
        .median()
        .shift(1)
        .replace(0.0, np.nan)
    )
    volume_ratio = history["Volume"].astype(float) / expected_volume
    result["volume_confirmed_return"] = log_return * volume_ratio
    day_range = (
        history["High"].astype(float) - history["Low"].astype(float)
    ).replace(0.0, np.nan)
    close_location = (
        2.0 * close
        - history["High"].astype(float)
        - history["Low"].astype(float)
    ) / day_range
    result["close_location_pressure"] = close_location * volume_ratio
    return result


def _momentum_head(features):
    if features is None:
        return None
    return features["decay_mom_1_20"]


def _decayed(series, start, end, half_life):
    offsets = range(int(start) - 1, int(end))
    values = pd.concat(
        [series.shift(offset) for offset in offsets],
        axis=1,
    )
    weights = np.power(
        0.5,
        np.arange(len(tuple(offsets)), dtype=float) / float(half_life),
    )
    weighted = values.mul(weights, axis="columns").sum(axis=1)
    result = weighted / float(weights.sum())
    return result.where(values.notna().all(axis=1))


def _validated_history(ticker, history):
    if not isinstance(history, pd.DataFrame):
        raise TypeError(f"history for {ticker} must be a DataFrame")
    missing = [column for column in REQUIRED_COLUMNS if column not in history]
    if missing:
        raise ValueError(f"history for {ticker} is missing columns: {missing}")
    result = history.loc[:, REQUIRED_COLUMNS].copy(deep=True)
    result.index = pd.DatetimeIndex(result.index).tz_localize(None)
    if result.index.has_duplicates:
        raise ValueError(f"history for {ticker} has duplicate dates")
    result = result.sort_index()
    result = result.apply(pd.to_numeric, errors="coerce")
    return result.where(np.isfinite(result), np.nan)
