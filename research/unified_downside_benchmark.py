"""Leakage-safe primitives for the unified downside walk-forward benchmark."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd


DEFAULT_ADVERSE_THRESHOLDS = {
    5: -0.05,
    10: -0.075,
    20: -0.10,
}
PRICE_COLUMNS = ("Open", "High", "Low", "Close")


def attach_next_open_path_targets(
    frame: pd.DataFrame,
    horizons=(5, 10, 20),
    adverse_thresholds: Mapping[int, float] | None = None,
) -> pd.DataFrame:
    """Attach executable future-path outcomes without dropping the tail."""
    checked = _validate_price_frame(frame)
    checked_horizons = _validate_horizons(horizons)
    thresholds = _validate_thresholds(
        checked_horizons,
        DEFAULT_ADVERSE_THRESHOLDS
        if adverse_thresholds is None
        else adverse_thresholds,
    )
    parts = []
    for ticker, source in checked.groupby(level="ticker", sort=True):
        history = source.droplevel("ticker").sort_index()
        positions = pd.Series(
            np.arange(len(history), dtype=int),
            index=history.index,
        )
        numeric = history.loc[:, PRICE_COLUMNS].apply(
            pd.to_numeric,
            errors="coerce",
        )
        finite_row = pd.Series(
            np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1),
            index=history.index,
        )
        for horizon in checked_horizons:
            entry_open = numeric["Open"].shift(-1)
            terminal_close = numeric["Close"].shift(-horizon)
            future_low = _forward_window(
                numeric["Low"].shift(-1),
                horizon,
                "min",
            )
            future_high = _forward_window(
                numeric["High"].shift(-1),
                horizon,
                "max",
            )
            future_finite = _forward_window(
                finite_row.astype(float).shift(-1),
                horizon,
                "min",
            ).eq(1.0)
            has_window = positions + horizon < len(history)
            mature = (
                has_window
                & future_finite
                & entry_open.notna()
                & np.isfinite(entry_open)
                & entry_open.gt(0.0)
                & terminal_close.notna()
                & np.isfinite(terminal_close)
            )
            result = pd.DataFrame(index=history.index)
            result["ticker"] = ticker
            result["horizon"] = horizon
            result["entry_open"] = entry_open.where(mature)
            result["terminal_return"] = (
                terminal_close / entry_open - 1.0
            ).where(mature)
            result["mae"] = (future_low / entry_open - 1.0).where(mature)
            result["mfe"] = (future_high / entry_open - 1.0).where(mature)
            result["mature"] = mature.astype(bool)
            result["immature"] = (~mature).astype(bool)
            result["unavailable_reason"] = pd.Series(
                np.where(
                    mature,
                    None,
                    np.where(
                        has_window,
                        "invalid_future_path",
                        "immature_future_path",
                    ),
                ),
                index=history.index,
                dtype="object",
            )
            actual = pd.Series(
                pd.NA,
                index=history.index,
                dtype="boolean",
            )
            actual.loc[mature] = (
                result.loc[mature, "mae"] <= thresholds[horizon]
            )
            result["actual_event"] = actual
            result.index.name = "observation_date"
            parts.append(
                result.reset_index().set_index(
                    ["ticker", "observation_date", "horizon"]
                )
            )
    if not parts:
        return _empty_target_frame()
    output = pd.concat(parts).sort_index()
    output.index = output.index.set_names(
        ["ticker", "observation_date", "horizon"]
    )
    return output


def _forward_window(series, window, operation):
    reversed_series = series.iloc[::-1]
    rolling = reversed_series.rolling(window, min_periods=window)
    if operation == "min":
        values = rolling.min()
    elif operation == "max":
        values = rolling.max()
    else:  # pragma: no cover - private caller freezes the operations.
        raise ValueError(f"unsupported operation: {operation}")
    return values.iloc[::-1]


def _validate_price_frame(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    checked = frame.copy(deep=True)
    if not isinstance(checked.index, pd.MultiIndex):
        required = {"ticker", "observation_date"}
        if not required.issubset(checked.columns):
            raise ValueError("frame requires ticker and observation_date")
        checked = checked.set_index(["ticker", "observation_date"])
    checked.index = checked.index.set_names(["ticker", "observation_date"])
    if checked.index.has_duplicates:
        raise ValueError("frame contains duplicate point-in-time keys")
    missing = sorted(set(PRICE_COLUMNS).difference(checked.columns))
    if missing:
        raise ValueError(f"frame is missing price columns: {missing}")
    ticker = (
        checked.index.get_level_values("ticker")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    dates = pd.to_datetime(
        checked.index.get_level_values("observation_date"),
        errors="raise",
    ).tz_localize(None)
    checked.index = pd.MultiIndex.from_arrays(
        [ticker, dates],
        names=["ticker", "observation_date"],
    )
    if checked.index.has_duplicates:
        raise ValueError("frame contains duplicate normalized keys")
    return checked.sort_index()


def _validate_horizons(horizons):
    try:
        values = tuple(horizons)
    except TypeError as exc:
        raise TypeError("horizons must be iterable") from exc
    if not values:
        raise ValueError("horizons must not be empty")
    checked = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
            raise ValueError("horizons must contain positive integers")
        horizon = int(value)
        if horizon <= 0:
            raise ValueError("horizons must contain positive integers")
        checked.append(horizon)
    if len(set(checked)) != len(checked):
        raise ValueError("horizons must be unique")
    return tuple(checked)


def _validate_thresholds(horizons, thresholds):
    if not isinstance(thresholds, Mapping):
        raise TypeError("adverse_thresholds must be a mapping")
    missing = [horizon for horizon in horizons if horizon not in thresholds]
    if missing:
        raise ValueError(f"missing adverse threshold for horizons: {missing}")
    checked = {}
    for horizon in horizons:
        value = thresholds[horizon]
        if isinstance(value, bool):
            raise ValueError("adverse threshold must be finite and negative")
        threshold = float(value)
        if not math.isfinite(threshold) or threshold >= 0.0:
            raise ValueError("adverse threshold must be finite and negative")
        checked[horizon] = threshold
    return checked


def _empty_target_frame():
    index = pd.MultiIndex.from_arrays(
        [[], [], []],
        names=["ticker", "observation_date", "horizon"],
    )
    return pd.DataFrame(
        {
            "entry_open": pd.Series(dtype=float),
            "terminal_return": pd.Series(dtype=float),
            "mae": pd.Series(dtype=float),
            "mfe": pd.Series(dtype=float),
            "mature": pd.Series(dtype=bool),
            "immature": pd.Series(dtype=bool),
            "unavailable_reason": pd.Series(dtype=object),
            "actual_event": pd.Series(dtype="boolean"),
        },
        index=index,
    )
