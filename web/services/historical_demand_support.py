"""Attach remembered daily demand-support diagnostics to chart rows."""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

from research.historical_demand_support import (
    MODEL_KEY,
    MODEL_VERSION,
    build_historical_demand_support_rows,
)
from research.resistance import merge_historical_demand_support
from web.contracts import iso_date
from web.services.supply_demand import _close, _sector_close


def attach_historical_demand_support_rows(chart, ticker, histories):
    """Mutate chart rows with causal demand zones and merged support fields."""
    if not isinstance(chart, list):
        raise TypeError("chart must be a list")
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    for row in chart:
        if not isinstance(row, dict):
            raise TypeError("chart rows must be dictionaries")
        row.update(_unavailable_defaults())

    normalized = str(ticker).strip().upper()
    history = histories.get(normalized)
    if not chart or not isinstance(history, pd.DataFrame) or history.empty:
        return
    try:
        index = pd.to_datetime([row.get("time") for row in chart])
        demand_rows = pd.DataFrame(
            {
                "demand_confirmation_conditions": [
                    list(row.get("demand_confirmation_conditions") or ())
                    for row in chart
                ],
                "supply_pressure_conditions": [
                    list(row.get("supply_pressure_conditions") or ())
                    for row in chart
                ],
            },
            index=index,
        )
        entry_rows = [
            {"pocket_pivot": bool(row.get("pocket_pivot"))}
            for row in chart
        ]
        model = build_historical_demand_support_rows(
            history,
            demand_rows=demand_rows,
            entry_signal_rows=entry_rows,
            qqq_close=_close(histories.get("QQQ")),
            sector_close=_sector_close(histories, normalized),
        )
    except (TypeError, ValueError, KeyError):
        for row in chart:
            row["historical_demand_support_unavailable_reason"] = (
                "model_input_invalid"
            )
        return

    by_date = {
        iso_date(observation_date): _serialized_row(model_row)
        for observation_date, model_row in model.iterrows()
    }
    atr20 = _atr20(history)
    for chart_row in chart:
        selected = by_date.get(chart_row.get("time"))
        if selected is None:
            continue
        chart_row.update(selected)
        date = pd.Timestamp(chart_row["time"])
        value = atr20.get(date)
        if _finite(value):
            chart_row.update(
                merge_historical_demand_support(
                    chart_row,
                    selected,
                    close=chart_row.get("close"),
                    atr20=float(value),
                )
            )


def _atr20(history: pd.DataFrame) -> pd.Series:
    previous = history["Close"].shift(1)
    true_range = pd.concat(
        (
            history["High"] - history["Low"],
            (history["High"] - previous).abs(),
            (history["Low"] - previous).abs(),
        ),
        axis=1,
    ).max(axis=1)
    return true_range.rolling(20, min_periods=20).mean()


def _unavailable_defaults() -> dict[str, object]:
    return {
        "historical_demand_support_model_key": MODEL_KEY,
        "historical_demand_support_model_version": MODEL_VERSION,
        "historical_demand_support_state": "unavailable",
        "historical_demand_support_lower": None,
        "historical_demand_support_upper": None,
        "historical_demand_support_mid": None,
        "historical_demand_support_distance_pct": None,
        "historical_demand_support_score": None,
        "historical_demand_support_first_date": None,
        "historical_demand_support_last_confirmed_date": None,
        "historical_demand_support_age_sessions": None,
        "historical_demand_support_event_types": [],
        "historical_demand_support_event_count": 0,
        "historical_demand_support_retest_count": 0,
        "historical_demand_support_volume_ratio": None,
        "historical_demand_support_invalidation_level": None,
        "historical_demand_support_conditions": [],
        "historical_demand_support_counter_conditions": [],
        "historical_demand_support_coverage": 0.0,
        "historical_demand_support_unavailable_reason": (
            "model_data_unavailable"
        ),
    }


def _serialized_row(row: pd.Series) -> dict[str, object]:
    return {
        "historical_demand_support_model_key": str(
            row.get("historical_demand_support_model_key") or MODEL_KEY
        ),
        "historical_demand_support_model_version": str(
            row.get("historical_demand_support_model_version") or MODEL_VERSION
        ),
        "historical_demand_support_state": str(
            row.get("historical_demand_support_state") or "unavailable"
        ),
        "historical_demand_support_lower": _number(
            row.get("historical_demand_support_lower")
        ),
        "historical_demand_support_upper": _number(
            row.get("historical_demand_support_upper")
        ),
        "historical_demand_support_mid": _number(
            row.get("historical_demand_support_mid")
        ),
        "historical_demand_support_distance_pct": _number(
            row.get("historical_demand_support_distance_pct")
        ),
        "historical_demand_support_score": _number(
            row.get("historical_demand_support_score")
        ),
        "historical_demand_support_first_date": _text(
            row.get("historical_demand_support_first_date")
        ),
        "historical_demand_support_last_confirmed_date": _text(
            row.get("historical_demand_support_last_confirmed_date")
        ),
        "historical_demand_support_age_sessions": _integer(
            row.get("historical_demand_support_age_sessions")
        ),
        "historical_demand_support_event_types": list(
            row.get("historical_demand_support_event_types") or ()
        ),
        "historical_demand_support_event_count": int(
            row.get("historical_demand_support_event_count") or 0
        ),
        "historical_demand_support_retest_count": int(
            row.get("historical_demand_support_retest_count") or 0
        ),
        "historical_demand_support_volume_ratio": _number(
            row.get("historical_demand_support_volume_ratio")
        ),
        "historical_demand_support_invalidation_level": _number(
            row.get("historical_demand_support_invalidation_level")
        ),
        "historical_demand_support_conditions": list(
            row.get("historical_demand_support_conditions") or ()
        ),
        "historical_demand_support_counter_conditions": list(
            row.get("historical_demand_support_counter_conditions") or ()
        ),
        "historical_demand_support_coverage": _number(
            row.get("historical_demand_support_coverage")
        ),
        "historical_demand_support_unavailable_reason": _text(
            row.get("historical_demand_support_unavailable_reason")
        ),
    }


def _finite(value) -> bool:
    return _number(value) is not None


def _number(value):
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _integer(value):
    number = _number(value)
    return None if number is None else int(number)


def _text(value):
    return None if value is None or _is_nan(value) else str(value)


def _is_nan(value) -> bool:
    return isinstance(value, (float, np.floating)) and not math.isfinite(value)
