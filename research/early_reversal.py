"""Causal end-of-session early-reversal observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

import pandas as pd

from research.market_pressure import build_pressure_rows
from research.market_data import next_bar
from research.reversal import build_reversal_rows


CONDITION_CODES = (
    "prior_session_selloff",
    "current_price_acceptance",
    "descending_trendline_proximity",
    "current_volume_support",
)


def _entry_outcome(
    history: pd.DataFrame,
    signal_date: pd.Timestamp,
    horizons: Sequence[int],
) -> dict[str, object]:
    signal = pd.Timestamp(signal_date)
    entry = next_bar(history, signal)
    result: dict[str, object] = {
        "signal_date": signal.date().isoformat(),
        "entry_date": None,
        "entry_price": None,
        "returns": {str(horizon): None for horizon in horizons},
        "matured": {str(horizon): False for horizon in horizons},
        "as_of_return": None,
    }
    if entry is None:
        return result
    entry_date, entry_bar = entry
    entry_price = float(entry_bar["Open"])
    result["entry_date"] = pd.Timestamp(entry_date).date().isoformat()
    result["entry_price"] = entry_price
    future = history.loc[history.index > signal]
    for horizon in horizons:
        if len(future) < horizon:
            continue
        result["returns"][str(horizon)] = (
            float(future["Close"].iloc[horizon - 1]) / entry_price - 1.0
        )
        result["matured"][str(horizon)] = True
    if entry_price and history.index[-1] >= entry_date:
        result["as_of_return"] = float(history["Close"].iloc[-1]) / entry_price - 1.0
    return result


def compare_next_open_entries(
    history: pd.DataFrame,
    early_observation_date: pd.Timestamp,
    confirmation_date: pd.Timestamp,
    horizons: Sequence[int] = (5, 20),
) -> dict[str, object]:
    """Compare early and confirmed signals using executable next-session opens."""
    if not horizons or any(
        not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 0
        for horizon in horizons
    ):
        raise ValueError("horizons must be positive integers")
    ordered = history.sort_index().copy(deep=True)
    early = _entry_outcome(ordered, pd.Timestamp(early_observation_date), horizons)
    confirmed = _entry_outcome(ordered, pd.Timestamp(confirmation_date), horizons)
    delay = None
    premium = None
    if early["entry_date"] is not None and confirmed["entry_date"] is not None:
        early_position = ordered.index.get_loc(pd.Timestamp(early["entry_date"]))
        confirmed_position = ordered.index.get_loc(pd.Timestamp(confirmed["entry_date"]))
        delay = int(confirmed_position - early_position)
        if early["entry_price"]:
            premium = float(confirmed["entry_price"]) / float(early["entry_price"]) - 1.0
    return {
        "as_of_date": ordered.index[-1].date().isoformat() if len(ordered) else None,
        "early": early,
        "confirmed": confirmed,
        "confirmation_delay_sessions": delay,
        "confirmation_entry_premium": premium,
    }


def _empty_row() -> dict[str, object]:
    return {
        "early_reversal_score": 0,
        "early_reversal_watch": False,
        "early_reversal_conditions": [],
        "early_prior_session_selloff": False,
        "early_current_price_acceptance": False,
        "early_descending_trendline_proximity": False,
        "early_current_volume_support": False,
    }


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def build_early_reversal_rows(
    history: pd.DataFrame,
    reversal_rows: Sequence[Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    """Return one point-in-time early-watch row per OHLCV session."""
    pressure = build_pressure_rows(history)
    ordered = history.loc[pressure.index].copy(deep=True)
    reversal = (
        build_reversal_rows(ordered)
        if reversal_rows is None
        else [dict(row) for row in reversal_rows]
    )
    if len(reversal) != len(ordered):
        raise ValueError("reversal rows must align one-to-one with history")

    close = ordered["Close"].astype(float)
    daily_return = close.pct_change()
    rows: list[dict[str, object]] = []
    for position in range(len(ordered)):
        row = _empty_row()
        if position == 0:
            rows.append(row)
            continue

        prior_pressure = pressure.iloc[position - 1]
        current_pressure = pressure.iloc[position]
        prior_selloff = bool(
            _finite(daily_return.iloc[position - 1])
            and float(daily_return.iloc[position - 1]) <= -0.05
            and _finite(prior_pressure["volume_ratio"])
            and float(prior_pressure["volume_ratio"]) >= 1.2
            and _finite(prior_pressure["close_location"])
            and float(prior_pressure["close_location"]) <= -0.5
        )
        price_acceptance = bool(
            close.iloc[position] > close.iloc[position - 1]
            and _finite(current_pressure["close_location"])
            and float(current_pressure["close_location"]) >= 0.0
        )

        trendline = reversal[position].get("descending_trendline")
        trendline_proximity = bool(
            _finite(trendline)
            and float(trendline) > 0.0
            and close.iloc[position] <= float(trendline)
            and close.iloc[position] / float(trendline) >= 0.99
        )
        volume_support = bool(
            _finite(current_pressure["volume_ratio"])
            and float(current_pressure["volume_ratio"]) >= 1.2
        )
        condition_values = (
            prior_selloff,
            price_acceptance,
            trendline_proximity,
            volume_support,
        )
        score = 25 * sum(condition_values)
        row.update(
            {
                "early_reversal_score": score,
                "early_reversal_watch": bool(
                    prior_selloff and price_acceptance and score >= 75
                ),
                "early_reversal_conditions": [
                    code
                    for code, satisfied in zip(CONDITION_CODES, condition_values)
                    if satisfied
                ],
                "early_prior_session_selloff": prior_selloff,
                "early_current_price_acceptance": price_acceptance,
                "early_descending_trendline_proximity": trendline_proximity,
                "early_current_volume_support": volume_support,
            }
        )
        rows.append(row)
    return rows
