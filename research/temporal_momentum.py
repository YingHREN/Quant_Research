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
TEMPORAL_STOCK_COLUMNS = tuple(DECAY_WINDOWS)


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
    lagged = pd.concat(
        [log_return.shift(int(lag) - 1) for lag in lags],
        axis=1,
    )
    weighted = lagged.mul(weights, axis="columns")
    result = weighted.sum(axis=1, min_count=len(lags)) / float(weights.sum())
    return result.astype(float)


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
    return features.loc[:, TEMPORAL_STOCK_COLUMNS]
