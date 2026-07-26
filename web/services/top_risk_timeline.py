"""Serialize cached TOPRISK state into sparse chart events."""

from __future__ import annotations

from numbers import Real

import pandas as pd


MODEL_KEY = "high_level_distribution_risk_v1"
MODEL_VERSION = "v1"

_REQUIRED_COLUMNS = {
    "high_level_distribution_score",
    "high_level_distribution_raw_score",
    "high_level_distribution_state",
    "high_level_distribution_raw_state",
    "high_level_distribution_age_sessions",
    "top_risk_recovery",
}
_AVAILABLE_STATES = {
    "inactive",
    "low",
    "watch",
    "high",
    "confirmed",
    "fading",
}
_ACTIVE_STATES = {"watch", "high", "confirmed", "fading"}
_EVENT_BY_STATE = {
    "watch": "top_risk_watch",
    "high": "top_risk_high",
    "confirmed": "top_risk_confirmed",
}


def unavailable_top_risk_timeline(reason="not_available"):
    """Return the stable unavailable response used by service boundaries."""
    return {
        "model_key": MODEL_KEY,
        "model_version": MODEL_VERSION,
        "status": "unavailable",
        "unavailable_reason": str(reason),
        "latest": None,
        "events": [],
    }


def build_top_risk_timeline(risk_context, ticker, chart_dates):
    """Return latest TOPRISK state and sparse transitions for one ticker."""
    if not isinstance(risk_context, pd.DataFrame) or risk_context.empty:
        return unavailable_top_risk_timeline()
    if not _REQUIRED_COLUMNS.issubset(risk_context.columns):
        return unavailable_top_risk_timeline()
    if not isinstance(risk_context.index, pd.MultiIndex):
        return unavailable_top_risk_timeline()
    if risk_context.index.has_duplicates:
        raise ValueError("risk context contains duplicate ticker-date rows")
    if tuple(risk_context.index.names) != ("ticker", "observation_date"):
        return unavailable_top_risk_timeline()

    normalized_ticker = str(ticker).strip().upper()
    try:
        ticker_rows = risk_context.xs(normalized_ticker, level="ticker")
    except KeyError:
        return unavailable_top_risk_timeline()
    dates = _chart_dates(chart_dates)
    if dates.empty:
        return unavailable_top_risk_timeline()
    selected = ticker_rows.reindex(dates)
    states = selected["high_level_distribution_state"].map(_state)
    available = selected.loc[states.isin(_AVAILABLE_STATES)].copy()
    if available.empty:
        return unavailable_top_risk_timeline()
    available["_state"] = states.loc[available.index]

    return {
        "model_key": MODEL_KEY,
        "model_version": MODEL_VERSION,
        "status": "available",
        "unavailable_reason": None,
        "latest": _latest_summary(available.index[-1], available.iloc[-1]),
        "events": _transition_events(available),
    }


def _chart_dates(values):
    try:
        dates = pd.DatetimeIndex(pd.to_datetime(tuple(values))).normalize()
    except (TypeError, ValueError):
        return pd.DatetimeIndex([])
    return dates.drop_duplicates().sort_values()


def _transition_events(rows):
    events = []
    prior_state = None
    prior_recovery = False
    for timestamp, row in rows.iterrows():
        state = row["_state"]
        recovery = _boolean(row["top_risk_recovery"])
        event_type = None
        if recovery and not prior_recovery and prior_state in _ACTIVE_STATES:
            event_type = "top_risk_recovery"
        elif state in _EVENT_BY_STATE and state != prior_state:
            event_type = _EVENT_BY_STATE[state]
        if event_type is not None:
            events.append(
                {
                    "time": timestamp.date().isoformat(),
                    "type": event_type,
                    "score": _optional_number(
                        row["high_level_distribution_score"]
                    ),
                    "state": state,
                }
            )
        prior_state = state
        prior_recovery = recovery
    return events


def _latest_summary(timestamp, row):
    return {
        "time": timestamp.date().isoformat(),
        "score": _optional_number(row["high_level_distribution_score"]),
        "raw_score": _optional_number(
            row["high_level_distribution_raw_score"]
        ),
        "state": row["_state"],
        "raw_state": _state(row["high_level_distribution_raw_state"]),
        "memory_age_sessions": _optional_integer(
            row["high_level_distribution_age_sessions"]
        ),
    }


def _state(value):
    return value if isinstance(value, str) else "unavailable"


def _boolean(value):
    return bool(value) if isinstance(value, bool) else False


def _optional_number(value):
    if (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and pd.notna(value)
        and pd.api.types.is_number(value)
    ):
        number = float(value)
        if pd.notna(number) and number not in (float("inf"), float("-inf")):
            return number
    return None


def _optional_integer(value):
    number = _optional_number(value)
    return int(number) if number is not None and number >= 0.0 else None
