"""Leakage-safe event labels and metrics for causal bottom states."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from research.bottom_state import POSITIVE_STATES, STATE_RANK


HORIZONS = (5, 10, 20)
STRUCTURE_STATES = frozenset(
    ("bullish_structure_confirmed", "breakout_retest_confirmed")
)
TERMINAL_FAILURE = "bottom_failed"
REQUIRED_PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
REQUIRED_STATE_COLUMNS = (
    "bottom_state",
    "bottom_state_transition",
    "bottom_score",
    "bottom_coverage",
    "bottom_state_age_sessions",
)
OUTPUT_COLUMNS = (
    "event_id",
    "ticker",
    "observation_date",
    "event_end_date",
    "observation_state",
    "observation_rank",
    "event_role",
    "scope",
    "horizon",
    "observation_close",
    "drawdown_63",
    "drawdown_bin",
    "forward_return",
    "positive_return",
    "maximum_favorable_excursion",
    "maximum_adverse_excursion",
    "confirmed_within_horizon",
    "failed_within_horizon",
    "first_terminal_state",
    "sessions_to_confirmation",
    "sessions_to_failure",
    "state_maintained",
    "bottom_score",
    "bottom_coverage",
    "bottom_state_age_sessions",
)


def build_bottom_transition_events(
    ticker: str,
    history: pd.DataFrame,
    states: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = HORIZONS,
    non_overlap_sessions: int = 20,
) -> pd.DataFrame:
    """Return mature bottom-state outcomes for both event scopes."""
    checked_horizons = _validate_inputs(
        history,
        states,
        horizons=horizons,
        non_overlap_sessions=non_overlap_sessions,
    )
    frame = history.loc[:, REQUIRED_PRICE_COLUMNS].astype(float)
    state_frame = states.reindex(frame.index)
    normalized_ticker = str(ticker).strip().upper()
    if not normalized_ticker:
        raise ValueError("ticker must be non-empty")
    drawdown = frame["Close"] / frame["Close"].rolling(63).max() - 1.0
    candidates = _candidate_positions(state_frame)
    scope_positions = {
        "all_transitions": candidates,
        "non_overlapping": _non_overlapping_positions(
            candidates,
            state_frame,
            non_overlap_sessions=non_overlap_sessions,
        ),
    }
    rows = []
    for scope, positions in scope_positions.items():
        for position in positions:
            for horizon in checked_horizons:
                if position + horizon >= len(frame):
                    continue
                rows.append(
                    _event_row(
                        normalized_ticker,
                        frame,
                        state_frame,
                        drawdown,
                        position=position,
                        horizon=horizon,
                        scope=scope,
                    )
                )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def _candidate_positions(states: pd.DataFrame) -> list[int]:
    positions = []
    for position, row in enumerate(states.itertuples(index=False)):
        state = str(row.bottom_state)
        transition = bool(row.bottom_state_transition)
        if state == "downtrend_continuation":
            positions.append(position)
        elif transition and (
            state in POSITIVE_STATES or state == TERMINAL_FAILURE
        ):
            positions.append(position)
    return positions


def _non_overlapping_positions(
    positions: list[int],
    states: pd.DataFrame,
    *,
    non_overlap_sessions: int,
) -> list[int]:
    selected = []
    active_until = -1
    for position in positions:
        state = str(states.iloc[position]["bottom_state"])
        if state == "downtrend_continuation":
            selected.append(position)
            continue
        if state == TERMINAL_FAILURE:
            selected.append(position)
            active_until = -1
            continue
        if position < active_until:
            continue
        selected.append(position)
        active_until = position + non_overlap_sessions
    return selected


def _event_row(
    ticker: str,
    history: pd.DataFrame,
    states: pd.DataFrame,
    drawdown: pd.Series,
    *,
    position: int,
    horizon: int,
    scope: str,
) -> dict[str, object]:
    observation = states.iloc[position]
    observation_state = str(observation["bottom_state"])
    observation_close = float(history["Close"].iloc[position])
    future = history.iloc[position + 1 : position + horizon + 1]
    terminal = states.iloc[position + 1 : position + horizon + 1]
    confirmation_delay = (
        0 if observation_state in STRUCTURE_STATES else None
    )
    failure_delay = 0 if observation_state == TERMINAL_FAILURE else None
    if confirmation_delay is None or failure_delay is None:
        for delay, (_, state_row) in enumerate(
            terminal.iterrows(),
            start=1,
        ):
            state = str(state_row["bottom_state"])
            raw_state = str(state_row.get("bottom_raw_state") or state)
            if failure_delay is None and state == TERMINAL_FAILURE:
                failure_delay = delay
            if (
                confirmation_delay is None
                and (state in STRUCTURE_STATES or raw_state in STRUCTURE_STATES)
            ):
                confirmation_delay = delay
    first_terminal = _first_terminal_state(
        confirmation_delay,
        failure_delay,
    )
    terminal_state = str(terminal.iloc[-1]["bottom_state"])
    maintained = bool(
        observation_state in POSITIVE_STATES
        and failure_delay is None
        and terminal_state in POSITIVE_STATES
        and STATE_RANK[terminal_state] >= STATE_RANK[observation_state]
    )
    observation_drawdown = float(drawdown.iloc[position])
    return {
        "event_id": (
            f"{ticker}:{history.index[position].date().isoformat()}:"
            f"{observation_state}:{scope}:{horizon}"
        ),
        "ticker": ticker,
        "observation_date": history.index[position],
        "event_end_date": history.index[position + horizon],
        "observation_state": observation_state,
        "observation_rank": STATE_RANK[observation_state],
        "event_role": (
            "baseline"
            if observation_state == "downtrend_continuation"
            else "event"
        ),
        "scope": scope,
        "horizon": horizon,
        "observation_close": observation_close,
        "drawdown_63": observation_drawdown,
        "drawdown_bin": _drawdown_bin(observation_drawdown),
        "forward_return": (
            float(future["Close"].iloc[-1]) / observation_close - 1.0
        ),
        "positive_return": bool(
            float(future["Close"].iloc[-1]) > observation_close
        ),
        "maximum_favorable_excursion": (
            float(future["High"].max()) / observation_close - 1.0
        ),
        "maximum_adverse_excursion": (
            float(future["Low"].min()) / observation_close - 1.0
        ),
        "confirmed_within_horizon": confirmation_delay is not None,
        "failed_within_horizon": failure_delay is not None,
        "first_terminal_state": first_terminal,
        "sessions_to_confirmation": confirmation_delay,
        "sessions_to_failure": failure_delay,
        "state_maintained": maintained,
        "bottom_score": _optional_float(observation["bottom_score"]),
        "bottom_coverage": _optional_float(
            observation["bottom_coverage"]
        ),
        "bottom_state_age_sessions": _optional_integer(
            observation["bottom_state_age_sessions"]
        ),
    }


def _first_terminal_state(
    confirmation_delay: int | None,
    failure_delay: int | None,
) -> str | None:
    if failure_delay is not None and (
        confirmation_delay is None or failure_delay <= confirmation_delay
    ):
        return "failed"
    if confirmation_delay is not None:
        return "confirmed"
    return None


def _drawdown_bin(value: float) -> str:
    if not math.isfinite(value):
        return "unavailable"
    if value >= -0.15:
        return "0_-15"
    if value >= -0.25:
        return "-15_-25"
    if value >= -0.40:
        return "-25_-40"
    return "below_-40"


def _validate_inputs(
    history: pd.DataFrame,
    states: pd.DataFrame,
    *,
    horizons: tuple[int, ...],
    non_overlap_sessions: int,
) -> tuple[int, ...]:
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    if not isinstance(states, pd.DataFrame):
        raise TypeError("states must be a DataFrame")
    missing_prices = [
        column for column in REQUIRED_PRICE_COLUMNS if column not in history
    ]
    if missing_prices:
        raise ValueError(f"history is missing required columns: {missing_prices}")
    missing_states = [
        column for column in REQUIRED_STATE_COLUMNS if column not in states
    ]
    if missing_states:
        raise ValueError(f"states are missing required columns: {missing_states}")
    if not history.index.equals(states.index):
        raise ValueError("history and states must align")
    if history.index.has_duplicates or not history.index.is_monotonic_increasing:
        raise ValueError("history dates must be unique and increasing")
    values = history.loc[:, REQUIRED_PRICE_COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("history OHLCV values must be finite")
    if (values[:, :4] <= 0.0).any() or (values[:, 4] < 0.0).any():
        raise ValueError("history OHLC prices must be positive and volume nonnegative")
    if (
        not horizons
        or any(
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, np.integer))
            for value in horizons
        )
    ):
        raise ValueError("horizons must contain unique positive integers")
    checked = tuple(int(value) for value in horizons)
    if len(checked) != len(set(checked)) or any(
        value <= 0 for value in checked
    ):
        raise ValueError("horizons must contain unique positive integers")
    if (
        isinstance(non_overlap_sessions, bool)
        or not isinstance(non_overlap_sessions, int)
        or non_overlap_sessions <= 0
    ):
        raise ValueError("non_overlap_sessions must be a positive integer")
    unknown = set(states["bottom_state"].dropna().astype(str)) - set(
        STATE_RANK
    )
    if unknown:
        raise ValueError(f"unknown bottom states: {sorted(unknown)}")
    return checked


def _optional_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_integer(value: object) -> int | None:
    number = _optional_float(value)
    return None if number is None else int(number)
