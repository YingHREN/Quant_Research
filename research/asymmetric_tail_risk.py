"""Causal labels and models for asymmetric five-session tail risk."""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Integral

import numpy as np
import pandas as pd


INDEX_NAMES = ("ticker", "observation_date")
DOWN_TERMINAL_THRESHOLD = -0.05
DOWN_PATH_THRESHOLD = -0.07
EXTREME_REBOUND_THRESHOLD = 0.10


def attach_asymmetric_tail_targets(
    frame: pd.DataFrame,
    histories: Mapping[str, pd.DataFrame],
    horizon: int = 5,
) -> pd.DataFrame:
    """Attach exact next-open tail labels without shifting missing sessions."""
    _validate_feature_frame(frame)
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, Integral)
        or int(horizon) != 5
    ):
        raise ValueError("horizon must be the frozen integer value 5")
    checked_horizon = int(horizon)
    suffix = str(checked_horizon)
    output_columns = (
        f"terminal_return_{suffix}",
        f"path_mae_{suffix}",
        f"down_event_{suffix}",
        f"extreme_rebound_{suffix}",
    )
    end_column = f"tail_label_end_date_{suffix}"
    result = frame.copy(deep=True)
    for column in output_columns:
        result[column] = np.nan
    result[end_column] = pd.NaT

    tickers = result.index.get_level_values("ticker").unique()
    for ticker in tickers:
        source = histories.get(str(ticker))
        if source is None or not isinstance(source, pd.DataFrame) or source.empty:
            continue
        missing = [
            column for column in ("Open", "Low", "Close") if column not in source
        ]
        if missing:
            raise ValueError(
                f"history for {ticker} is missing columns: {missing}"
            )
        history = source.loc[:, ("Open", "Low", "Close")].copy(deep=True)
        history.index = pd.DatetimeIndex(history.index).tz_localize(None)
        if history.index.has_duplicates:
            raise ValueError(f"history for {ticker} contains duplicate dates")
        history = history.sort_index().apply(pd.to_numeric, errors="coerce")
        dates = result.loc[str(ticker)].index
        keys = pd.MultiIndex.from_product(
            ((str(ticker),), dates),
            names=INDEX_NAMES,
        )

        entry_open = history["Open"].shift(-1)
        terminal_close = history["Close"].shift(-checked_horizon)
        future_lows = pd.concat(
            [
                history["Low"].shift(-offset)
                for offset in range(1, checked_horizon + 1)
            ],
            axis=1,
        )
        date_series = pd.Series(
            history.index,
            index=history.index,
            dtype="datetime64[ns]",
        )
        label_end = date_series.shift(-checked_horizon)
        complete = (
            entry_open.notna()
            & terminal_close.notna()
            & (entry_open > 0.0)
            & (terminal_close > 0.0)
            & future_lows.notna().all(axis=1)
            & (future_lows > 0.0).all(axis=1)
            & label_end.notna()
        )
        terminal_return = (terminal_close / entry_open - 1.0).where(complete)
        path_mae = (
            future_lows.min(axis=1, skipna=False) / entry_open - 1.0
        ).where(complete)
        down_event = (
            (
                (terminal_return <= DOWN_TERMINAL_THRESHOLD)
                | (path_mae <= DOWN_PATH_THRESHOLD)
            )
            .astype(float)
            .where(complete)
        )
        rebound_event = (
            (terminal_return >= EXTREME_REBOUND_THRESHOLD)
            .astype(float)
            .where(complete)
        )
        values = {
            output_columns[0]: terminal_return,
            output_columns[1]: path_mae,
            output_columns[2]: down_event,
            output_columns[3]: rebound_event,
            end_column: label_end.where(complete),
        }
        for column, series in values.items():
            result.loc[keys, column] = series.reindex(dates).to_numpy()
    return result


def _validate_feature_frame(frame: pd.DataFrame) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    if not isinstance(frame.index, pd.MultiIndex):
        raise ValueError("frame must use a MultiIndex")
    if tuple(frame.index.names) != INDEX_NAMES:
        raise ValueError(f"frame index names must be {INDEX_NAMES}")
    if frame.index.has_duplicates:
        raise ValueError("frame contains duplicate ticker/date keys")
    dates = pd.DatetimeIndex(frame.index.get_level_values("observation_date"))
    if dates.tz is not None:
        raise ValueError("observation dates must be timezone-naive")
