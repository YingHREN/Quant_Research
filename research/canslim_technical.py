"""Point-in-time CAN SLIM technical trend gate."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


TECHNICAL_GATE_VERSION = "canslim_technical_gate_v1"
CONDITION_KEYS = (
    "close_above_sma50",
    "ema10_above_ema20",
    "moving_average_slopes_positive",
    "within_20pct_of_52_week_high",
)


def evaluate_technical_gate(history, asof, stale=False):
    """Evaluate four causal technical conditions through one observation date."""
    timestamp = pd.Timestamp(asof).normalize()
    if stale:
        return unavailable_technical_gate(timestamp, "stale_observation")
    reason = _history_error(history, timestamp)
    if reason is not None:
        return unavailable_technical_gate(timestamp, reason)
    frame = history.loc[history.index <= timestamp].copy()
    close = frame["Close"].astype(float)
    ema10 = close.ewm(
        span=10,
        adjust=False,
        min_periods=10,
    ).mean()
    ema20 = close.ewm(
        span=20,
        adjust=False,
        min_periods=20,
    ).mean()
    current_close = float(close.iloc[-1])
    current_sma50 = (
        float(close.iloc[-50:].mean()) if len(close) >= 50 else None
    )
    current_ema10 = _series_value(ema10, -1)
    current_ema20 = _series_value(ema20, -1)
    ema10_prior = _series_value(ema10, -6)
    ema20_prior = _series_value(ema20, -6)
    ema10_slope = _ratio_change(current_ema10, ema10_prior)
    ema20_slope = _ratio_change(current_ema20, ema20_prior)
    high_252 = (
        float(close.iloc[-252:].max()) if len(close) >= 252 else None
    )
    distance = _ratio_change(current_close, high_252)
    cross_date, cross_direction = _latest_cross(ema10, ema20)
    conditions = {
        "close_above_sma50": _comparison_condition(
            current_sma50 is not None,
            current_close > current_sma50 if current_sma50 else False,
            _ratio_change(current_close, current_sma50),
            0.0,
            "insufficient_sma50_history",
        ),
        "ema10_above_ema20": _comparison_condition(
            current_ema10 is not None and current_ema20 is not None,
            (
                current_ema10 > current_ema20
                if current_ema10 is not None
                and current_ema20 is not None
                else False
            ),
            _ratio_change(current_ema10, current_ema20),
            0.0,
            "insufficient_ema_history",
        ),
        "moving_average_slopes_positive": _comparison_condition(
            ema10_slope is not None and ema20_slope is not None,
            (
                ema10_slope > 0.0 and ema20_slope > 0.0
                if ema10_slope is not None and ema20_slope is not None
                else False
            ),
            (
                min(ema10_slope, ema20_slope)
                if ema10_slope is not None and ema20_slope is not None
                else None
            ),
            0.0,
            "insufficient_slope_history",
        ),
        "within_20pct_of_52_week_high": _comparison_condition(
            high_252 is not None,
            distance >= -0.20 if distance is not None else False,
            distance,
            -0.20,
            "insufficient_252_session_history",
        ),
    }
    passed = sum(
        row["state"] == "pass" for row in conditions.values()
    )
    state = (
        "fail"
        if any(row["state"] == "fail" for row in conditions.values())
        else "missing"
        if any(row["state"] == "missing" for row in conditions.values())
        else "pass"
    )
    return {
        "state": state,
        "passed_conditions": passed,
        "condition_count": len(CONDITION_KEYS),
        "asof": timestamp.date().isoformat(),
        "version": TECHNICAL_GATE_VERSION,
        "preferred_within_15pct": (
            distance is not None and distance >= -0.15
        ),
        "values": {
            "close": current_close,
            "sma50": current_sma50,
            "ema10": current_ema10,
            "ema20": current_ema20,
            "ema10_slope_5": ema10_slope,
            "ema20_slope_5": ema20_slope,
            "high_close_252": high_252,
            "distance_from_high_252": distance,
            "last_ema_cross_date": cross_date,
            "last_ema_cross_direction": cross_direction,
        },
        "conditions": conditions,
        "reason_codes": sorted(
            {
                row["reason"]
                for row in conditions.values()
                if row["reason"] is not None
            }
        ),
    }


def unavailable_technical_gate(asof, reason):
    """Return a complete missing payload instead of a partial pass."""
    timestamp = pd.Timestamp(asof).normalize()
    return {
        "state": "missing",
        "passed_conditions": 0,
        "condition_count": len(CONDITION_KEYS),
        "asof": timestamp.date().isoformat(),
        "version": TECHNICAL_GATE_VERSION,
        "preferred_within_15pct": False,
        "values": {
            "close": None,
            "sma50": None,
            "ema10": None,
            "ema20": None,
            "ema10_slope_5": None,
            "ema20_slope_5": None,
            "high_close_252": None,
            "distance_from_high_252": None,
            "last_ema_cross_date": None,
            "last_ema_cross_direction": None,
        },
        "conditions": {
            key: {
                "state": "missing",
                "actual": None,
                "threshold": None,
                "reason": reason,
            }
            for key in CONDITION_KEYS
        },
        "reason_codes": [reason],
    }


def _condition(passed, actual, threshold):
    return {
        "state": "pass" if passed else "fail",
        "actual": _finite(actual),
        "threshold": threshold,
        "reason": None,
    }


def _comparison_condition(
    available,
    passed,
    actual,
    threshold,
    missing_reason,
):
    if not available:
        return {
            "state": "missing",
            "actual": None,
            "threshold": threshold,
            "reason": missing_reason,
        }
    return _condition(passed, actual, threshold)


def _finite(value):
    number = float(value)
    return number if math.isfinite(number) else None


def _series_value(series, position):
    if len(series) < abs(position):
        return None
    value = series.iloc[position]
    return _finite(value) if pd.notna(value) else None


def _ratio_change(current, reference):
    if current is None or reference is None or reference == 0:
        return None
    return _finite(float(current) / float(reference) - 1.0)


def _history_error(history, asof):
    if not isinstance(history, pd.DataFrame):
        return "invalid_history_type"
    required = ("Open", "High", "Low", "Close", "Volume")
    if any(column not in history.columns for column in required):
        return "missing_ohlcv_columns"
    if not isinstance(history.index, pd.DatetimeIndex):
        return "invalid_date_index"
    visible = history.loc[history.index <= asof, list(required)]
    if visible.empty:
        return "no_visible_history"
    if visible.index.has_duplicates:
        return "duplicate_dates"
    if not visible.index.is_monotonic_increasing:
        return "non_monotonic_dates"
    numeric = visible.apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        return "non_finite_ohlcv"
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        return "non_finite_ohlcv"
    if (numeric.loc[:, ("Open", "High", "Low", "Close")] <= 0).any().any():
        return "non_positive_price"
    if (numeric["Volume"] < 0).any():
        return "negative_volume"
    return None


def _latest_cross(ema10, ema20):
    spread = ema10 - ema20
    valid = spread.dropna()
    if valid.empty:
        return None, None
    signs = valid.gt(0)
    changes = signs.ne(signs.shift(1))
    changes.iloc[0] = False
    dates = changes[changes].index
    if len(dates) == 0:
        return None, None
    date = pd.Timestamp(dates[-1])
    return (
        date.date().isoformat(),
        "above" if bool(signs.loc[date]) else "below",
    )
