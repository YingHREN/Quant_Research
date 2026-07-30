"""Leakage-safe helpers for the offline regime-threshold direction study."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

from research.market_direction_model import (
    attach_next_open_targets,
    direction_labels,
)


HORIZON = 5


class RegimeThresholdDataUnavailable(RuntimeError):
    """Raised when required aligned research inputs are unavailable."""


def attach_absolute_and_qqq_relative_targets(
    frame,
    histories,
    horizon=HORIZON,
):
    """Attach aligned absolute, QQQ-relative, and path-MAE targets."""
    if int(horizon) != HORIZON or isinstance(horizon, bool):
        raise ValueError("only the frozen five-session horizon is supported")
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    qqq = _prepared_history(histories.get("QQQ"))
    if qqq is None:
        raise RegimeThresholdDataUnavailable(
            "QQQ history is required for relative targets"
        )

    attached = attach_next_open_targets(
        frame,
        histories,
        horizons=(HORIZON,),
    )
    result = attached.copy(deep=True)
    result["absolute_return_5"] = result["executable_return_5"]
    result["entry_date_5"] = result["executable_entry_date_5"]
    result["label_end_date_5"] = result["executable_label_end_date_5"]
    result["absolute_direction_5"] = _nullable_directions(
        result["absolute_return_5"]
    )
    result["qqq_relative_return_5"] = np.nan
    result["qqq_relative_direction_5"] = pd.Series(
        np.nan,
        index=result.index,
        dtype=object,
    )
    result["maximum_adverse_excursion_5"] = np.nan

    qqq_target, qqq_entry, qqq_end = _executable_series(qqq)
    tickers = result.index.get_level_values("ticker").unique()
    for raw_ticker in tickers:
        ticker = str(raw_ticker)
        history = _prepared_history(histories.get(ticker))
        if history is None:
            continue
        observation_dates = result.loc[ticker].index
        keys = pd.MultiIndex.from_product(
            ((raw_ticker,), observation_dates),
            names=result.index.names,
        )
        absolute = pd.to_numeric(
            result.loc[keys, "absolute_return_5"],
            errors="coerce",
        )
        mature = absolute.notna()
        if mature.any():
            qqq_values = qqq_target.reindex(observation_dates)
            expected_entry = pd.to_datetime(
                result.loc[keys, "entry_date_5"]
            )
            expected_end = pd.to_datetime(
                result.loc[keys, "label_end_date_5"]
            )
            aligned = (
                qqq_values.notna().to_numpy()
                & (
                    qqq_entry.reindex(observation_dates).to_numpy()
                    == expected_entry.to_numpy()
                )
                & (
                    qqq_end.reindex(observation_dates).to_numpy()
                    == expected_end.to_numpy()
                )
            )
            if not bool(np.all(aligned[mature.to_numpy()])):
                raise RegimeThresholdDataUnavailable(
                    f"QQQ sessions are not aligned for {ticker}"
                )
            relative = (
                absolute.to_numpy(dtype=float)
                - qqq_values.to_numpy(dtype=float)
            )
            result.loc[keys, "qqq_relative_return_5"] = relative
            result.loc[keys, "qqq_relative_direction_5"] = (
                _nullable_directions(
                    pd.Series(relative, index=keys)
                ).to_numpy()
            )
        mae = _maximum_adverse_excursion(history)
        result.loc[keys, "maximum_adverse_excursion_5"] = (
            mae.reindex(observation_dates).to_numpy(dtype=float)
        )
    return result


def _prepared_history(history):
    if not isinstance(history, pd.DataFrame) or history.empty:
        return None
    required = {"Open", "Low", "Close"}
    if not required.issubset(history.columns):
        return None
    result = history.copy(deep=True).sort_index()
    index = pd.DatetimeIndex(
        pd.to_datetime(result.index, errors="coerce")
    )
    if index.isna().any() or index.has_duplicates:
        return None
    if index.tz is not None:
        index = index.tz_localize(None)
    result.index = index.normalize()
    return result


def _executable_series(history):
    entry_open = pd.to_numeric(
        history["Open"],
        errors="coerce",
    ).shift(-1)
    exit_close = pd.to_numeric(
        history["Close"],
        errors="coerce",
    ).shift(-HORIZON)
    target = exit_close / entry_open.where(entry_open > 0.0) - 1.0
    dates = pd.Series(history.index, index=history.index)
    return target, dates.shift(-1), dates.shift(-HORIZON)


def _maximum_adverse_excursion(history):
    entry_open = pd.to_numeric(
        history["Open"],
        errors="coerce",
    ).shift(-1)
    low = pd.to_numeric(history["Low"], errors="coerce")
    path = pd.concat(
        [low.shift(-step) for step in range(1, HORIZON + 1)],
        axis=1,
    )
    complete = path.notna().all(axis=1)
    minimum = path.min(axis=1).where(complete)
    return minimum / entry_open.where(entry_open > 0.0) - 1.0


def _nullable_directions(returns):
    values = pd.to_numeric(returns, errors="coerce")
    result = pd.Series(np.nan, index=returns.index, dtype=object)
    valid = values.notna() & np.isfinite(values)
    if valid.any():
        result.loc[valid] = direction_labels(
            values.loc[valid],
            HORIZON,
        )
    return result
