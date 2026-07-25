"""Point-in-time outcome evaluation for high-level distribution states."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def evaluate_high_level_distribution(
    history: pd.DataFrame,
    states: pd.DataFrame,
    *,
    horizons=(5, 10, 20),
    adverse_threshold=-0.08,
):
    """Evaluate current confirmed states against future path drawdowns."""
    close = _close(history)
    checked_states = _states(states, close.index)
    checked_horizons = _horizons(horizons)
    threshold = float(adverse_threshold)
    if not math.isfinite(threshold) or threshold >= 0.0:
        raise ValueError("adverse_threshold must be finite and negative")

    result = {}
    predicted = (
        checked_states["high_level_distribution_raw_state"] == "confirmed"
    )
    for horizon in checked_horizons:
        eligible_count = max(len(close) - horizon, 0)
        eligible = close.index[:eligible_count]
        terminal = pd.Series(np.nan, index=close.index, dtype=float)
        adverse = pd.Series(np.nan, index=close.index, dtype=float)
        for position in range(eligible_count):
            start = float(close.iloc[position])
            path = close.iloc[position + 1 : position + horizon + 1]
            terminal.iloc[position] = float(path.iloc[-1]) / start - 1.0
            adverse.iloc[position] = float(path.min()) / start - 1.0

        event_mask = predicted.loc[eligible]
        label = adverse.loc[eligible] <= threshold
        true_positive = int((event_mask & label).sum())
        event_count = int(event_mask.sum())
        positive_count = int(label.sum())
        event_terminal = terminal.loc[eligible][event_mask]
        event_adverse = adverse.loc[eligible][event_mask]
        result[str(horizon)] = {
            "horizon_sessions": horizon,
            "adverse_threshold": threshold,
            "eligible_observations": eligible_count,
            "confirmed_events": event_count,
            "adverse_outcomes": positive_count,
            "precision": (
                None if event_count == 0 else true_positive / event_count
            ),
            "recall": (
                None if positive_count == 0 else true_positive / positive_count
            ),
            "mean_terminal_return": _mean_or_none(event_terminal),
            "mean_max_adverse_excursion": _mean_or_none(event_adverse),
        }
    return result


def _close(history):
    if not isinstance(history, pd.DataFrame) or "Close" not in history:
        raise ValueError("history requires a Close column")
    if (
        not isinstance(history.index, pd.DatetimeIndex)
        or history.index.has_duplicates
    ):
        raise ValueError("history requires a unique DatetimeIndex")
    close = pd.to_numeric(history["Close"], errors="coerce").sort_index()
    if close.empty or close.isna().any() or (close <= 0.0).any():
        raise ValueError("history close values must be finite and positive")
    return close.astype(float)


def _states(states, index):
    if (
        not isinstance(states, pd.DataFrame)
        or "high_level_distribution_raw_state" not in states
    ):
        raise ValueError("states require high_level_distribution_raw_state")
    aligned = states.reindex(index)
    if aligned["high_level_distribution_raw_state"].isna().any():
        raise ValueError("states must cover every history date")
    return aligned


def _horizons(values):
    checked = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("horizons must contain positive integers")
        if value not in checked:
            checked.append(value)
    if not checked:
        raise ValueError("horizons must not be empty")
    return tuple(checked)


def _mean_or_none(values):
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    return None if numeric.empty else float(numeric.mean())
