"""Attach causal bottoming-state diagnostics to chart rows."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from research.bottom_state import (
    BOTTOM_MODEL_KEY,
    BOTTOM_MODEL_VERSION,
    build_bottom_state_rows,
)
from web.contracts import iso_date


def attach_bottom_state_rows(chart, history):
    """Mutate existing chart rows, aligning state evidence by session date."""
    if not isinstance(chart, list):
        raise TypeError("chart must be a list")
    for row in chart:
        if not isinstance(row, dict):
            raise TypeError("chart rows must be dictionaries")
        row.update(_unavailable_defaults())
        row["bottom_conditions"] = []
        row["bottom_counter_conditions"] = []
    if (
        not chart
        or not isinstance(history, pd.DataFrame)
        or history.empty
    ):
        return

    evidence = pd.DataFrame(
        [_evidence_row(row) for row in chart],
        index=pd.to_datetime([row.get("time") for row in chart]),
    )
    try:
        model = build_bottom_state_rows(history, evidence)
    except (TypeError, ValueError, KeyError):
        for row in chart:
            row["bottom_unavailable_reason"] = "model_input_invalid"
        return
    by_date = {
        iso_date(observation_date): _serialized_row(row)
        for observation_date, row in model.iterrows()
    }
    for chart_row in chart:
        selected = by_date.get(chart_row.get("time"))
        if selected is not None:
            chart_row.update(selected)


def _evidence_row(row):
    market_gate = row.get("market_regime_gate")
    if not isinstance(market_gate, dict):
        market_gate = {}
    return {
        "near_support_lower": row.get("near_support_lower"),
        "near_support_upper": row.get("near_support_upper"),
        "near_support_score": row.get("near_support_score"),
        "near_support_state": row.get("near_support_state"),
        "historical_demand_support_state": row.get(
            "historical_demand_support_state"
        ),
        "historical_demand_support_score": row.get(
            "historical_demand_support_score"
        ),
        "historical_demand_support_invalidation_level": row.get(
            "historical_demand_support_invalidation_level"
        ),
        "demand_confirmation_score": row.get(
            "demand_confirmation_score"
        ),
        "demand_confirmation_coverage": row.get(
            "demand_confirmation_coverage"
        ),
        "demand_confirmation_conditions": list(
            row.get("demand_confirmation_conditions") or ()
        ),
        "supply_pressure_score": row.get("supply_pressure_score"),
        "supply_pressure_conditions": list(
            row.get("supply_pressure_conditions") or ()
        ),
        "early_reversal_score": row.get("early_reversal_score"),
        "early_reversal_watch": row.get("early_reversal_watch"),
        "prior_high_breakout": row.get("prior_high_breakout"),
        "trendline_breakout": row.get("trendline_breakout"),
        "higher_low_confirmed": row.get("higher_low_confirmed"),
        "market_regime_state": market_gate.get("market_state"),
    }


def _unavailable_defaults():
    return {
        "bottom_model_key": BOTTOM_MODEL_KEY,
        "bottom_model_version": BOTTOM_MODEL_VERSION,
        "bottom_state": "unavailable",
        "bottom_raw_state": "unavailable",
        "bottom_score": None,
        "bottom_coverage": 0.0,
        "bottom_state_age_sessions": None,
        "bottom_state_transition": False,
        "bottom_location_score": None,
        "bottom_exhaustion_score": None,
        "bottom_demand_score": None,
        "bottom_structure_score": None,
        "bottom_environment_score": None,
        "bottom_conditions": [],
        "bottom_counter_conditions": [],
        "bottom_invalidation_level": None,
        "bottom_unavailable_reason": "model_data_unavailable",
    }


def _serialized_row(row):
    return {
        "bottom_model_key": str(
            row.get("bottom_model_key") or BOTTOM_MODEL_KEY
        ),
        "bottom_model_version": str(
            row.get("bottom_model_version") or BOTTOM_MODEL_VERSION
        ),
        "bottom_state": str(row.get("bottom_state") or "unavailable"),
        "bottom_raw_state": str(
            row.get("bottom_raw_state") or "unavailable"
        ),
        "bottom_score": _optional_number(row.get("bottom_score")),
        "bottom_coverage": _optional_number(row.get("bottom_coverage")),
        "bottom_state_age_sessions": _optional_integer(
            row.get("bottom_state_age_sessions")
        ),
        "bottom_state_transition": bool(
            row.get("bottom_state_transition")
        ),
        "bottom_location_score": _optional_number(
            row.get("bottom_location_score")
        ),
        "bottom_exhaustion_score": _optional_number(
            row.get("bottom_exhaustion_score")
        ),
        "bottom_demand_score": _optional_number(
            row.get("bottom_demand_score")
        ),
        "bottom_structure_score": _optional_number(
            row.get("bottom_structure_score")
        ),
        "bottom_environment_score": _optional_number(
            row.get("bottom_environment_score")
        ),
        "bottom_conditions": list(row.get("bottom_conditions") or ()),
        "bottom_counter_conditions": list(
            row.get("bottom_counter_conditions") or ()
        ),
        "bottom_invalidation_level": _optional_number(
            row.get("bottom_invalidation_level")
        ),
        "bottom_unavailable_reason": (
            None
            if _is_missing(row.get("bottom_unavailable_reason"))
            else str(row.get("bottom_unavailable_reason"))
        ),
    }


def _optional_number(value):
    if (
        isinstance(value, (int, float, np.integer, np.floating))
        and not isinstance(value, (bool, np.bool_))
        and math.isfinite(float(value))
    ):
        return float(value)
    return None


def _optional_integer(value):
    number = _optional_number(value)
    return None if number is None else int(number)


def _is_missing(value):
    return value is None or (
        isinstance(value, (float, np.floating)) and not math.isfinite(value)
    )
