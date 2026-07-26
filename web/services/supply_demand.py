"""Attach causal daily supply and demand evidence to chart dates."""

from __future__ import annotations

from collections.abc import Mapping
import math

import numpy as np
import pandas as pd

from research.supply_demand import (
    DEMAND_MODEL_KEY,
    SUPPLY_MODEL_KEY,
    build_supply_demand_rows,
)
from web.contracts import iso_date
from web.market_groups import market_group_for_ticker


def attach_supply_demand_rows(chart, ticker, histories):
    """Mutate existing chart rows only, aligning evidence by ISO session date."""
    if not isinstance(chart, list):
        raise TypeError("chart must be a list")
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    defaults = _unavailable_defaults()
    for row in chart:
        if not isinstance(row, dict):
            raise TypeError("chart rows must be dictionaries")
        row.update(defaults)
        row["supply_pressure_conditions"] = []
        row["demand_confirmation_conditions"] = []
        row["unavailable_reasons"] = ["model_data_unavailable"]
    normalized = str(ticker).strip().upper()
    history = histories.get(normalized)
    if not chart or not isinstance(history, pd.DataFrame):
        return
    try:
        qqq_close = _close(histories.get("QQQ"))
        sector_close = _sector_close(histories, normalized)
        model = build_supply_demand_rows(
            history,
            qqq_close=qqq_close,
            sector_close=sector_close,
        )
    except (TypeError, ValueError, KeyError):
        for row in chart:
            row["unavailable_reasons"] = ["model_input_invalid"]
        return
    by_date = {
        iso_date(observation_date): row
        for observation_date, row in model.iterrows()
    }
    for chart_row in chart:
        selected = by_date.get(chart_row.get("time"))
        if selected is None:
            continue
        chart_row.update(_serialized_row(selected))


def _unavailable_defaults():
    return {
        "supply_pressure_model_key": SUPPLY_MODEL_KEY,
        "demand_confirmation_model_key": DEMAND_MODEL_KEY,
        "supply_close_volume_score": None,
        "supply_rejection_score": None,
        "supply_structure_context_score": None,
        "supply_pressure_score": None,
        "supply_pressure_coverage": 0.0,
        "supply_pressure_conditions": [],
        "demand_participation_score": None,
        "demand_absorption_score": None,
        "demand_breakout_context_score": None,
        "demand_confirmation_score": None,
        "demand_confirmation_coverage": 0.0,
        "demand_confirmation_conditions": [],
        "supply_demand_state": "unavailable",
        "unavailable_reasons": ["model_data_unavailable"],
    }


def _serialized_row(row):
    return {
        "supply_pressure_model_key": SUPPLY_MODEL_KEY,
        "demand_confirmation_model_key": DEMAND_MODEL_KEY,
        "supply_close_volume_score": _optional_number(
            row.get("supply_close_volume_score")
        ),
        "supply_rejection_score": _optional_number(
            row.get("supply_rejection_score")
        ),
        "supply_structure_context_score": _optional_number(
            row.get("supply_structure_context_score")
        ),
        "supply_pressure_score": _optional_number(
            row.get("supply_pressure_score")
        ),
        "supply_pressure_coverage": _optional_number(
            row.get("supply_pressure_coverage")
        ),
        "supply_pressure_conditions": list(
            row.get("supply_pressure_conditions") or ()
        ),
        "demand_participation_score": _optional_number(
            row.get("demand_participation_score")
        ),
        "demand_absorption_score": _optional_number(
            row.get("demand_absorption_score")
        ),
        "demand_breakout_context_score": _optional_number(
            row.get("demand_breakout_context_score")
        ),
        "demand_confirmation_score": _optional_number(
            row.get("demand_confirmation_score")
        ),
        "demand_confirmation_coverage": _optional_number(
            row.get("demand_confirmation_coverage")
        ),
        "demand_confirmation_conditions": list(
            row.get("demand_confirmation_conditions") or ()
        ),
        "supply_demand_state": str(
            row.get("supply_demand_state") or "unavailable"
        ),
        "unavailable_reasons": list(row.get("unavailable_reasons") or ()),
    }


def _optional_number(value):
    if (
        isinstance(value, (int, float, np.integer, np.floating))
        and not isinstance(value, (bool, np.bool_))
        and math.isfinite(float(value))
    ):
        return float(value)
    return None


def _close(history):
    if (
        not isinstance(history, pd.DataFrame)
        or history.empty
        or "Close" not in history
    ):
        return None
    return pd.to_numeric(history["Close"], errors="coerce")


def _sector_close(histories, ticker):
    group = market_group_for_ticker(ticker)
    if group is None:
        return None
    primary = _normalized_closes(histories, group.benchmark_tickers)
    if primary:
        return pd.concat(primary, axis=1).mean(axis=1, skipna=True)
    fallback = _normalized_closes(
        histories,
        group.fallback_benchmark_tickers,
    )
    if not fallback:
        return None
    return pd.concat(fallback, axis=1).mean(axis=1, skipna=True)


def _normalized_closes(histories, tickers):
    result = []
    for ticker in tickers:
        close = _close(histories.get(ticker))
        if close is None:
            continue
        finite = close[np.isfinite(close) & (close > 0.0)]
        if finite.empty:
            continue
        result.append(close / float(finite.iloc[0]) * 100.0)
    return result
