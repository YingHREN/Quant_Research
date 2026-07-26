"""Causal multi-session supply and terminal-acceleration diagnostics."""

from __future__ import annotations

import numpy as np
import pandas as pd

from research.market_pressure import build_pressure_rows


def build_supply_regime_rows(history: pd.DataFrame) -> pd.DataFrame:
    """Return rolling distribution, Churning, and Climax Run evidence."""
    pressure = build_pressure_rows(history)
    checked = history.loc[
        pressure.index,
        ("Open", "High", "Low", "Close", "Volume"),
    ].astype(float)
    close = checked["Close"]
    prior_close = close.shift(1)
    daily_return = close / prior_close.replace(0.0, np.nan) - 1.0
    true_range = pd.concat(
        (
            checked["High"] - checked["Low"],
            (checked["High"] - prior_close).abs(),
            (checked["Low"] - prior_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    atr5 = true_range.rolling(5, min_periods=5).mean()
    atr20 = true_range.rolling(20, min_periods=20).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()

    result = pd.DataFrame(index=checked.index)
    for window in (5, 10, 20):
        result[f"distribution_count_{window}"] = (
            pressure["distribution_day"]
            .astype(float)
            .rolling(window, min_periods=window)
            .sum()
        )

    result["churning_event"] = (
        pressure["high_volume_non_progress"]
        | (
            (pressure["volume_ratio"] >= 1.5)
            & (daily_return.abs() <= 0.01)
            & (pressure["close_location"] <= -0.2)
        )
    ).where(
        pressure["volume_ratio"].notna()
        & pressure["close_location"].notna()
        & daily_return.notna()
    )
    result["churning_count_10"] = (
        result["churning_event"]
        .astype(float)
        .rolling(10, min_periods=10)
        .sum()
    )
    result["churning_cluster"] = (
        result["churning_count_10"] >= 2.0
    ).where(result["churning_count_10"].notna())
    result["failed_breakout_count_10"] = (
        pressure["failed_breakout"]
        .astype(float)
        .rolling(10, min_periods=10)
        .sum()
    )

    prior_advance = close.pct_change(60, fill_method=None) >= 0.30
    acceleration = close.pct_change(15, fill_method=None) >= 0.25
    large_up_count = (
        (daily_return >= 0.018)
        .astype(float)
        .rolling(10, min_periods=10)
        .sum()
    )
    repeated_large_up = large_up_count >= 3.0
    range_expansion = atr5 / atr20.replace(0.0, np.nan) >= 1.35
    extension = (close - ema20) / atr20.replace(0.0, np.nan) >= 2.5
    abnormal_volume_count = (
        (pressure["volume_ratio"] >= 1.8)
        .astype(float)
        .rolling(10, min_periods=10)
        .sum()
    )
    abnormal_volume = abnormal_volume_count >= 2.0
    gap_up_count = (
        (
            checked["Open"]
            / checked["High"].shift(1).replace(0.0, np.nan)
            - 1.0
            >= 0.015
        )
        .astype(float)
        .rolling(10, min_periods=10)
        .sum()
    )
    repeated_gap_up = gap_up_count >= 2.0
    climax_conditions = {
        "climax_acceleration": acceleration,
        "climax_repeated_large_up_days": repeated_large_up,
        "climax_range_expansion": range_expansion,
        "climax_ema_extension": extension,
        "climax_abnormal_volume": abnormal_volume,
        "climax_repeated_gap_up": repeated_gap_up,
    }
    climax_count = sum(
        condition.fillna(False).astype(int)
        for condition in climax_conditions.values()
    )
    result["climax_run_score"] = (climax_count * 20.0).clip(upper=100.0)
    result["climax_run_candidate"] = (
        prior_advance & (climax_count >= 3)
    ).where(prior_advance.notna())
    result["climax_run_conditions"] = _condition_tuples(
        climax_conditions,
        checked.index,
    )
    return result


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
                if pd.notna(series.loc[date]) and bool(series.loc[date])
            )
            for date in index
        ],
        index=index,
        dtype=object,
    )
