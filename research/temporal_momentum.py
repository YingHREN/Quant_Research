"""Causal multi-scale temporal momentum research features."""

from __future__ import annotations

import numpy as np
import pandas as pd


DECAY_WINDOWS = {
    "decay_mom_1_3": (1, 3, 2.0),
    "decay_mom_4_10": (4, 10, 4.0),
    "decay_mom_11_20": (11, 20, 7.0),
    "decay_mom_21_60": (21, 60, 20.0),
    "decay_mom_1_20": (1, 20, 7.0),
}
TEMPORAL_CONFIRMATION_COLUMNS = (
    "decay_volume_confirmation_1_20",
    "decay_close_location_pressure_1_20",
)
TEMPORAL_STOCK_COLUMNS = (*DECAY_WINDOWS, *TEMPORAL_CONFIRMATION_COLUMNS)


def decayed_return(
    close: pd.Series,
    start_lag: int,
    end_lag: int,
    half_life: float,
) -> pd.Series:
    """Return a normalized exponentially decayed mean of causal log returns."""
    if start_lag < 1 or end_lag < start_lag:
        raise ValueError("lags must satisfy 1 <= start_lag <= end_lag")
    if not np.isfinite(half_life) or half_life <= 0.0:
        raise ValueError("half_life must be positive and finite")

    values = pd.to_numeric(close, errors="coerce").astype(float)
    log_return = np.log(values / values.shift(1))
    lags = np.arange(start_lag, end_lag + 1, dtype=int)
    weights = np.exp(-np.log(2.0) * (lags - start_lag) / half_life)
    return _decayed_signal(log_return, lags, weights)


def stock_temporal_features(history: pd.DataFrame) -> pd.DataFrame:
    """Compute stock-only temporal features at every observation date."""
    if "Close" not in history:
        raise ValueError("history is missing Close")
    features = pd.DataFrame(index=history.index)
    for name, (start_lag, end_lag, half_life) in DECAY_WINDOWS.items():
        features[name] = decayed_return(
            history["Close"],
            start_lag,
            end_lag,
            half_life,
        )
    volume = pd.to_numeric(history.get("Volume"), errors="coerce").astype(float)
    expected_volume = volume.rolling(20, min_periods=10).median().shift(1)
    volume_ratio = (volume / expected_volume.replace(0.0, np.nan)).clip(0.0, 5.0)
    close = pd.to_numeric(history["Close"], errors="coerce").astype(float)
    log_return = np.log(close / close.shift(1))
    recent_lags = np.arange(1, 21, dtype=int)
    recent_weights = np.exp(
        -np.log(2.0) * (recent_lags - 1) / 7.0
    )
    features["decay_volume_confirmation_1_20"] = _decayed_signal(
        log_return * volume_ratio,
        recent_lags,
        recent_weights,
    )
    high = pd.to_numeric(history.get("High"), errors="coerce").astype(float)
    low = pd.to_numeric(history.get("Low"), errors="coerce").astype(float)
    bar_range = high - low
    close_location = pd.Series(0.0, index=history.index, dtype=float)
    valid_range = bar_range > 0.0
    close_location.loc[valid_range] = (
        2.0
        * (close.loc[valid_range] - low.loc[valid_range])
        / bar_range.loc[valid_range]
        - 1.0
    )
    close_location.loc[bar_range.isna()] = np.nan
    features["decay_close_location_pressure_1_20"] = _decayed_signal(
        close_location * volume_ratio,
        recent_lags,
        recent_weights,
    )
    return features.loc[:, TEMPORAL_STOCK_COLUMNS]


def _decayed_signal(
    signal: pd.Series,
    lags: np.ndarray,
    weights: np.ndarray,
) -> pd.Series:
    lagged = pd.concat(
        [signal.shift(int(lag) - 1) for lag in lags],
        axis=1,
    )
    weighted = lagged.mul(weights, axis="columns")
    result = weighted.sum(axis=1, min_count=len(lags)) / float(weights.sum())
    return result.astype(float)
