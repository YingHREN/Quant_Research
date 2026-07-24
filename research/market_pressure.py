"""Causal daily OHLCV pressure proxies for market research."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping

import numpy as np
import pandas as pd


EvidenceState = Literal["met", "near", "unmet", "unavailable"]
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


@dataclass(frozen=True)
class Evidence:
    key: str
    value: float | bool | None
    threshold: float | None
    state: EvidenceState
    points: float
    max_points: float
    window: str
    unavailable_reason: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )
        if self.state == "unavailable" and not self.unavailable_reason:
            raise ValueError("unavailable evidence requires a reason")
        if not 0.0 <= float(self.points) <= float(self.max_points):
            raise ValueError("evidence points must be within max_points")


def build_pressure_rows(history: pd.DataFrame) -> pd.DataFrame:
    """Return point-in-time daily supply/demand proxies for every input row."""
    checked = _history(history)
    high, low, close, volume = (
        checked[name].astype(float)
        for name in ("High", "Low", "Close", "Volume")
    )
    spread = (high - low).replace(0.0, np.nan)
    prior_close = close.shift(1)
    prior_pivot = close.shift(1).rolling(20, min_periods=20).max()
    volume_ma20 = volume.shift(1).rolling(20, min_periods=20).mean()
    volume_ratio = volume / volume_ma20.replace(0.0, np.nan)
    close_location = ((close - low) - (high - close)) / spread
    upper_wick = (
        high - pd.concat([checked["Open"], close], axis=1).max(axis=1)
    ) / spread
    lower_wick = (
        pd.concat([checked["Open"], close], axis=1).min(axis=1) - low
    ) / spread
    daily_return = close / prior_close.replace(0.0, np.nan) - 1.0
    efficiency = daily_return.abs() / volume_ratio.replace(0.0, np.nan)

    result = pd.DataFrame(index=checked.index)
    result["close_location"] = close_location
    result["upper_wick_ratio"] = upper_wick
    result["lower_wick_ratio"] = lower_wick
    result["volume_ratio"] = volume_ratio
    result["signed_volume_proxy"] = close_location * volume_ratio
    result["price_progress_efficiency"] = efficiency
    result["distribution_day"] = (
        (daily_return < 0.0)
        & (volume_ratio >= 1.2)
        & (close_location <= -0.4)
    )
    result["high_volume_non_progress"] = (
        (volume_ratio >= 1.5) & (daily_return.abs() <= 0.005)
    )
    result["failed_breakout"] = (
        prior_pivot.notna() & (high > prior_pivot) & (close <= prior_pivot)
    )
    prior_distress = (
        (daily_return.shift(1) < 0.0)
        & (volume_ratio.shift(1) >= 1.5)
        & (close_location.shift(1) <= -0.5)
    )
    result["capitulation_recovery"] = (
        prior_distress & (close > prior_close) & (close_location >= 0.4)
    )
    numeric = result.select_dtypes(include=[np.number]).columns
    result.loc[:, numeric] = result.loc[:, numeric].where(
        np.isfinite(result.loc[:, numeric]),
        np.nan,
    )
    return result


def _history(source: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(source, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    missing = [name for name in REQUIRED_COLUMNS if name not in source]
    if missing:
        raise ValueError(f"history is missing columns: {missing}")
    result = source.loc[:, REQUIRED_COLUMNS].copy(deep=True).sort_index()
    if (
        not isinstance(result.index, pd.DatetimeIndex)
        or result.index.has_duplicates
    ):
        raise ValueError("history requires a unique DatetimeIndex")
    values = result.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("history values must be finite")
    if (
        result["High"]
        < result[["Open", "Close", "Low"]].max(axis=1)
    ).any():
        raise ValueError("history high is inconsistent")
    if (
        result["Low"]
        > result[["Open", "Close", "High"]].min(axis=1)
    ).any():
        raise ValueError("history low is inconsistent")
    return result.astype(float)
