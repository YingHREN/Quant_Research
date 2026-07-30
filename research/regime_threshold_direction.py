"""Leakage-safe helpers for the offline regime-threshold direction study."""

from __future__ import annotations

from numbers import Integral

import numpy as np
import pandas as pd


INDEX_NAMES = ("ticker", "observation_date")
STUDY_HORIZON = 5


def attach_qqq_relative_targets(
    frame: pd.DataFrame,
    qqq_history: pd.DataFrame,
    *,
    horizon: int = STUDY_HORIZON,
) -> pd.DataFrame:
    """Attach QQQ and stock-minus-QQQ returns over each row's exact dates."""
    checked_horizon = _validate_horizon(horizon)
    _validate_target_frame(frame, checked_horizon)
    qqq = _normalized_qqq_history(qqq_history)

    entry_dates = _normalized_dates(
        frame[f"executable_entry_date_{checked_horizon}"]
    )
    exit_dates = _normalized_dates(
        frame[f"executable_label_end_date_{checked_horizon}"]
    )
    entry_open = qqq["Open"].reindex(entry_dates).to_numpy(dtype=float)
    exit_close = qqq["Close"].reindex(exit_dates).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        benchmark_return = (
            exit_close / np.where(entry_open == 0.0, np.nan, entry_open)
        ) - 1.0
    benchmark_return[~np.isfinite(benchmark_return)] = np.nan

    absolute_return = pd.to_numeric(
        frame[f"executable_return_{checked_horizon}"],
        errors="coerce",
    ).to_numpy(dtype=float)
    result = frame.copy(deep=True)
    result[f"qqq_executable_return_{checked_horizon}"] = benchmark_return
    result[f"qqq_relative_return_{checked_horizon}"] = (
        absolute_return - benchmark_return
    )
    return result


def _validate_horizon(horizon):
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, Integral)
        or int(horizon) != STUDY_HORIZON
    ):
        raise ValueError("the regime-threshold study supports only horizon 5")
    return int(horizon)


def _validate_target_frame(frame, horizon):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    if (
        not isinstance(frame.index, pd.MultiIndex)
        or tuple(frame.index.names) != INDEX_NAMES
    ):
        raise ValueError(
            "frame index must be a MultiIndex named ticker and observation_date"
        )
    if frame.index.has_duplicates:
        raise ValueError("frame index must not contain duplicate keys")
    required = {
        f"executable_return_{horizon}",
        f"executable_entry_date_{horizon}",
        f"executable_label_end_date_{horizon}",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"frame is missing required columns: {missing}")


def _normalized_qqq_history(history):
    if not isinstance(history, pd.DataFrame):
        raise TypeError("qqq_history must be a DataFrame")
    if not isinstance(history.index, pd.DatetimeIndex):
        raise ValueError("qqq_history index must be a DatetimeIndex")
    missing = sorted({"Open", "Close"}.difference(history.columns))
    if missing:
        raise ValueError("qqq_history must contain Open and Close columns")
    normalized_index = history.index
    if normalized_index.tz is not None:
        normalized_index = normalized_index.tz_localize(None)
    normalized_index = normalized_index.normalize()
    if normalized_index.has_duplicates:
        raise ValueError("qqq_history must not contain duplicate dates")
    result = history.loc[:, ["Open", "Close"]].copy(deep=True)
    result.index = normalized_index
    result = result.apply(pd.to_numeric, errors="coerce")
    return result.sort_index()


def _normalized_dates(values):
    dates = pd.DatetimeIndex(pd.to_datetime(values, errors="coerce"))
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    return dates.normalize()
