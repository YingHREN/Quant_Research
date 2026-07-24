"""Causal end-of-session early-reversal observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math

import pandas as pd

from research.market_pressure import build_pressure_rows
from research.reversal import build_reversal_rows


CONDITION_CODES = (
    "prior_session_selloff",
    "current_price_acceptance",
    "descending_trendline_proximity",
    "current_volume_support",
)


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
