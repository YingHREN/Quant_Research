"""Point-in-time monetary-policy and liquidity context."""

from __future__ import annotations

import math

import pandas as pd

POLICY_MODEL_KEY = "macro_policy_context_v1"
POLICY_MODEL_VERSION = "v1"

POLICY_SERIES_IDS = (
    "DFEDTARL",
    "DFEDTARU",
    "WALCL",
    "WSHOSHO",
    "WSHOMCB",
    "WRESBAL",
    "WTREGEN",
    "RRPONTSYD",
    "DFII10",
    "PCEPI",
    "PCEPILFE",
)

MINIMUM_POLICY_COVERAGE = 0.70

_DIMENSION_WEIGHTS = {
    "policy_rate": 25.0,
    "liquidity": 25.0,
    "reserves": 15.0,
    "real_rate": 15.0,
    "pce": 10.0,
    "core_pce": 10.0,
}

_DIMENSION_SERIES = {
    "policy_rate": ("DFEDTARL", "DFEDTARU"),
    "liquidity": ("WALCL",),
    "reserves": ("WRESBAL",),
    "real_rate": ("DFII10",),
    "pce": ("PCEPI",),
    "core_pce": ("PCEPILFE",),
}


def build_policy_context(observations, asof):
    """Describe policy and liquidity using only observations known by ``asof``."""
    cutoff = _cutoff(asof)
    frame = _available_observations(
        _prepare_observations(observations),
        cutoff,
    )
    dimensions = {
        "policy_rate": _policy_rate_dimension(frame),
        "liquidity": _change_dimension(
            frame,
            "WALCL",
            key="liquidity",
            days=91,
            threshold=0.01,
            change_kind="percent",
        ),
        "reserves": _change_dimension(
            frame,
            "WRESBAL",
            key="reserves",
            days=91,
            threshold=0.01,
            change_kind="percent",
        ),
        "real_rate": _change_dimension(
            frame,
            "DFII10",
            key="real_rate",
            days=63,
            threshold=0.25,
            change_kind="absolute",
        ),
        "pce": _inflation_dimension(frame, "PCEPI", key="pce"),
        "core_pce": _inflation_dimension(
            frame,
            "PCEPILFE",
            key="core_pce",
        ),
    }
    coverage = _coverage(dimensions)
    available = coverage >= MINIMUM_POLICY_COVERAGE
    return {
        "model_key": POLICY_MODEL_KEY,
        "model_version": POLICY_MODEL_VERSION,
        "asof": cutoff.isoformat(),
        "state": (
            _combined_state(dimensions)
            if available
            else "unavailable"
        ),
        "coverage": round(coverage, 4),
        "dimensions": dimensions,
        "evidence": [
            dimensions[key]
            for key in _DIMENSION_WEIGHTS
        ],
        "limitations": [
            "descriptive_not_forecast",
            "does_not_modify_ridge",
        ],
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "point_in_time": True,
        "unavailable_reason": (
            None
            if available
            else "insufficient_policy_coverage"
        ),
    }


def unavailable_policy_context(
    reason="policy_data_unavailable",
    asof=None,
):
    cutoff = _cutoff(asof)
    return {
        "model_key": POLICY_MODEL_KEY,
        "model_version": POLICY_MODEL_VERSION,
        "asof": cutoff.isoformat(),
        "state": "unavailable",
        "coverage": 0.0,
        "dimensions": {
            key: _empty_dimension(key)
            for key in _DIMENSION_WEIGHTS
        },
        "evidence": [],
        "limitations": [
            "descriptive_not_forecast",
            "does_not_modify_ridge",
        ],
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "point_in_time": True,
        "unavailable_reason": reason,
    }


def _cutoff(asof):
    if asof is None:
        return pd.Timestamp.now(tz="UTC")
    value = pd.Timestamp(asof)
    if value.tz is None:
        value = value.normalize() + pd.Timedelta(days=1)
        value -= pd.Timedelta(microseconds=1)
        return value.tz_localize("UTC")
    return value.tz_convert("UTC")


def _prepare_observations(observations):
    frame = pd.DataFrame(observations).copy()
    required = {
        "series_id",
        "observation_date",
        "available_at",
        "value",
        "realtime_start",
        "realtime_end",
    }
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame(columns=sorted(required))
    frame["series_id"] = frame["series_id"].astype(str)
    frame["observation_date"] = pd.to_datetime(
        frame["observation_date"],
        errors="coerce",
    )
    frame["available_at"] = pd.to_datetime(
        frame["available_at"],
        utc=True,
        errors="coerce",
    )
    frame["realtime_start"] = pd.to_datetime(
        frame["realtime_start"],
        errors="coerce",
    )
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    return frame.loc[
        frame["series_id"].isin(POLICY_SERIES_IDS)
        & frame["observation_date"].notna()
        & frame["available_at"].notna()
        & frame["value"].notna()
    ].sort_values(
        [
            "series_id",
            "observation_date",
            "available_at",
            "realtime_start",
        ]
    )


def _available_observations(frame, cutoff):
    if frame.empty:
        return frame.copy()
    available = frame.loc[frame["available_at"] <= cutoff]
    return available.drop_duplicates(
        ["series_id", "observation_date"],
        keep="last",
    )


def _policy_rate_dimension(frame):
    result = _empty_dimension("policy_rate")
    lower = _series_rows(frame, "DFEDTARL")
    upper = _series_rows(frame, "DFEDTARU")
    if lower.empty or upper.empty:
        return result
    current_date = min(
        lower.iloc[-1]["observation_date"],
        upper.iloc[-1]["observation_date"],
    )
    current_lower = _row_at_or_before(lower, current_date)
    current_upper = _row_at_or_before(upper, current_date)
    prior_date = current_date - pd.Timedelta(days=63)
    prior_lower = _row_at_or_before(lower, prior_date)
    prior_upper = _row_at_or_before(upper, prior_date)
    if (
        current_lower is None
        or current_upper is None
        or prior_lower is None
        or prior_upper is None
    ):
        return result
    lower_value = float(current_lower["value"])
    upper_value = float(current_upper["value"])
    midpoint = (lower_value + upper_value) / 2.0
    prior_midpoint = (
        float(prior_lower["value"])
        + float(prior_upper["value"])
    ) / 2.0
    change = midpoint - prior_midpoint
    result.update(
        {
            "value": _round(midpoint),
            "prior_value": _round(prior_midpoint),
            "change": _round(change),
            "lower": _round(lower_value),
            "upper": _round(upper_value),
            "level": (
                "restrictive"
                if midpoint >= 3.0
                else (
                    "accommodative"
                    if midpoint <= 1.0
                    else "moderate"
                )
            ),
            "direction": _direction(
                change,
                0.10,
                positive="rising",
                negative="falling",
                neutral="flat",
            ),
            "lookback_days": 63,
            "threshold": 0.10,
            "observation_date": current_date.date().isoformat(),
            "available_at": max(
                current_lower["available_at"],
                current_upper["available_at"],
            ).isoformat(),
            "available": True,
            "unavailable_reason": None,
        }
    )
    return result


def _change_dimension(
    frame,
    series_id,
    *,
    key,
    days,
    threshold,
    change_kind,
):
    result = _empty_dimension(key)
    rows = _series_rows(frame, series_id)
    if rows.empty:
        return result
    current = rows.iloc[-1]
    prior = _row_at_or_before(
        rows,
        current["observation_date"] - pd.Timedelta(days=days),
    )
    if prior is None:
        return result
    current_value = float(current["value"])
    prior_value = float(prior["value"])
    if change_kind == "percent":
        if prior_value == 0.0:
            return result
        change = current_value / prior_value - 1.0
        positive = "expanding"
        negative = "contracting"
        neutral = "stable"
    else:
        change = current_value - prior_value
        positive = "rising"
        negative = "falling"
        neutral = "flat"
    result.update(
        {
            "value": _round(current_value),
            "prior_value": _round(prior_value),
            "change": _round(change),
            "direction": _direction(
                change,
                threshold,
                positive=positive,
                negative=negative,
                neutral=neutral,
            ),
            "lookback_days": days,
            "threshold": threshold,
            "observation_date": (
                current["observation_date"].date().isoformat()
            ),
            "available_at": current["available_at"].isoformat(),
            "available": True,
            "unavailable_reason": None,
        }
    )
    return result


def _inflation_dimension(frame, series_id, *, key):
    result = _empty_dimension(key)
    rows = _series_rows(frame, series_id)
    if rows.empty:
        return result
    current = rows.iloc[-1]
    current_date = current["observation_date"]
    year_ago = _row_at_or_before(
        rows,
        current_date - pd.DateOffset(years=1),
    )
    three_month = _row_at_or_before(
        rows,
        current_date - pd.DateOffset(months=3),
    )
    if year_ago is None or three_month is None:
        return result
    three_month_year_ago = _row_at_or_before(
        rows,
        three_month["observation_date"] - pd.DateOffset(years=1),
    )
    if three_month_year_ago is None:
        return result
    current_yoy = _percentage_change(
        float(current["value"]),
        float(year_ago["value"]),
    )
    prior_yoy = _percentage_change(
        float(three_month["value"]),
        float(three_month_year_ago["value"]),
    )
    if current_yoy is None or prior_yoy is None:
        return result
    change = current_yoy - prior_yoy
    result.update(
        {
            "value": _round(current_yoy),
            "prior_value": _round(prior_yoy),
            "change": _round(change),
            "level": (
                "high"
                if current_yoy >= 3.0
                else ("low" if current_yoy < 2.0 else "medium")
            ),
            "direction": _direction(
                change,
                0.15,
                positive="rising",
                negative="falling",
                neutral="flat",
            ),
            "lookback_days": 92,
            "threshold": 0.15,
            "observation_date": current_date.date().isoformat(),
            "available_at": current["available_at"].isoformat(),
            "available": True,
            "unavailable_reason": None,
        }
    )
    return result


def _coverage(dimensions):
    return sum(
        weight
        for key, weight in _DIMENSION_WEIGHTS.items()
        if dimensions[key]["available"]
    ) / 100.0


def _combined_state(dimensions):
    policy = dimensions["policy_rate"]
    liquidity = dimensions["liquidity"]
    policy_level = policy["level"]
    rate_direction = policy["direction"]
    liquidity_direction = liquidity["direction"]
    if (
        policy_level == "restrictive"
        and liquidity_direction == "expanding"
    ):
        return "rate_restrictive_liquidity_support"
    if (
        rate_direction == "rising"
        and liquidity_direction == "contracting"
    ):
        return "dual_tightening"
    if (
        rate_direction == "falling"
        and liquidity_direction == "expanding"
    ):
        return "broad_easing"
    if liquidity_direction == "contracting":
        return "liquidity_tightening"
    return "mixed"


def _empty_dimension(key):
    return {
        "key": key,
        "series_ids": list(_DIMENSION_SERIES[key]),
        "value": None,
        "prior_value": None,
        "change": None,
        "lower": None,
        "upper": None,
        "level": None,
        "direction": None,
        "lookback_days": None,
        "threshold": None,
        "observation_date": None,
        "available_at": None,
        "available": False,
        "unavailable_reason": "macro_series_or_history_missing",
    }


def _series_rows(frame, series_id):
    return frame.loc[
        frame["series_id"] == series_id
    ].sort_values("observation_date")


def _row_at_or_before(rows, date):
    eligible = rows.loc[rows["observation_date"] <= pd.Timestamp(date)]
    return None if eligible.empty else eligible.iloc[-1]


def _direction(
    change,
    threshold,
    *,
    positive,
    negative,
    neutral,
):
    if change >= threshold:
        return positive
    if change <= -threshold:
        return negative
    return neutral


def _percentage_change(current, prior):
    if prior == 0.0 or not math.isfinite(prior):
        return None
    return (current / prior - 1.0) * 100.0


def _round(value):
    return round(float(value), 6)
