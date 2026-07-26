"""Executable path-risk labels and pressure-regime specialist research."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd


DOWNSIDE_THRESHOLDS = {5: -0.05, 20: -0.10}
INDEX_NAMES = ("ticker", "observation_date")


def attach_next_open_mae_targets(
    frame: pd.DataFrame,
    histories: Mapping[str, pd.DataFrame],
    horizons: Sequence[int] = (5, 20),
) -> pd.DataFrame:
    """Attach next-open maximum adverse excursion and binary path events."""
    _validate_frame(frame)
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    checked_horizons = _validate_horizons(horizons)
    result = frame.copy(deep=True)
    for horizon in checked_horizons:
        result[f"executable_mae_{horizon}"] = np.nan
        result[f"downside_event_{horizon}"] = np.nan
        result[f"downside_label_end_date_{horizon}"] = pd.NaT

    for ticker in result.index.get_level_values("ticker").unique():
        source = histories.get(str(ticker))
        if source is None or not isinstance(source, pd.DataFrame) or source.empty:
            continue
        missing = [column for column in ("Open", "Low") if column not in source]
        if missing:
            raise ValueError(
                f"history for {ticker} is missing columns: {missing}"
            )
        history = source.loc[:, ("Open", "Low")].copy(deep=True)
        history.index = pd.DatetimeIndex(history.index).tz_localize(None)
        if history.index.has_duplicates:
            raise ValueError(f"history for {ticker} contains duplicate dates")
        history = history.sort_index().apply(pd.to_numeric, errors="coerce")
        group_dates = result.loc[str(ticker)].index
        keys = pd.MultiIndex.from_product(
            ((str(ticker),), group_dates),
            names=INDEX_NAMES,
        )
        entry_open = history["Open"].shift(-1).replace(0.0, np.nan)
        date_series = pd.Series(
            history.index,
            index=history.index,
            dtype="datetime64[ns]",
        )
        for horizon in checked_horizons:
            future_lows = pd.concat(
                [
                    history["Low"].shift(-offset)
                    for offset in range(1, horizon + 1)
                ],
                axis=1,
            )
            minimum_low = future_lows.min(axis=1, skipna=False)
            label_end = date_series.shift(-horizon)
            complete = (
                entry_open.notna()
                & (entry_open > 0.0)
                & future_lows.notna().all(axis=1)
                & label_end.notna()
            )
            mae = (minimum_low / entry_open - 1.0).where(complete)
            event = (
                (mae <= DOWNSIDE_THRESHOLDS[horizon])
                .astype(float)
                .where(complete)
            )
            result.loc[keys, f"executable_mae_{horizon}"] = (
                mae.reindex(group_dates).to_numpy()
            )
            result.loc[keys, f"downside_event_{horizon}"] = (
                event.reindex(group_dates).to_numpy()
            )
            result.loc[keys, f"downside_label_end_date_{horizon}"] = (
                label_end.where(complete).reindex(group_dates).to_numpy()
            )
    return result


def _validate_frame(frame):
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


def _validate_horizons(horizons):
    checked = []
    for raw_horizon in horizons:
        if (
            isinstance(raw_horizon, bool)
            or not isinstance(raw_horizon, (int, np.integer))
        ):
            raise ValueError("unsupported downside horizon")
        horizon = int(raw_horizon)
        if horizon not in DOWNSIDE_THRESHOLDS:
            raise ValueError("unsupported downside horizon")
        if horizon not in checked:
            checked.append(horizon)
    if not checked:
        raise ValueError("horizons must not be empty")
    return tuple(checked)
