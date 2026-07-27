"""Point-in-time macro regime risk built from release-aware observations."""

from __future__ import annotations

import math

import pandas as pd


MODEL_KEY = "macro_risk_v1"
MODEL_VERSION = "v1"
MINIMUM_COVERAGE = 0.70

SERIES_IDS = (
    "DGS2",
    "DGS10",
    "CPIAUCSL",
    "DCOILWTICO",
    "BAMLH0A0HYM2",
    "VIXCLS",
    "DTWEXBGS",
)

HISTORY_SERIES = (
    "DGS2",
    "DGS10",
    "CURVE_10Y_2Y",
    "CPI_YOY",
    "DCOILWTICO",
    "BAMLH0A0HYM2",
    "VIXCLS",
    "DTWEXBGS",
)

_RULES = (
    ("rates", "two_year_yield_high", 10.0, "DGS2", 4.5, "ge"),
    ("rates", "two_year_yield_rising", 8.0, "DGS2_CHANGE", 0.5, "ge"),
    ("rates", "yield_curve_inverted", 12.0, "CURVE_10Y_2Y", -0.5, "le"),
    ("inflation_energy", "cpi_yoy_elevated", 15.0, "CPI_YOY", 3.0, "ge"),
    ("inflation_energy", "oil_price_shock", 10.0, "OIL_CHANGE", 15.0, "ge"),
    (
        "credit_liquidity",
        "high_yield_spread_stressed",
        15.0,
        "BAMLH0A0HYM2",
        5.0,
        "ge",
    ),
    (
        "credit_liquidity",
        "high_yield_spread_widening",
        10.0,
        "HY_CHANGE",
        0.75,
        "ge",
    ),
    ("risk_aversion", "vix_elevated", 10.0, "VIXCLS", 25.0, "ge"),
    ("risk_aversion", "vix_spiking", 5.0, "VIX_CHANGE", 5.0, "ge"),
    ("risk_aversion", "dollar_surge", 5.0, "DOLLAR_CHANGE", 3.0, "ge"),
)


def build_macro_history_rows(observations, dates):
    """Replay the macro score on each date without using later releases."""
    if isinstance(dates, (str, bytes)):
        raise TypeError("dates must be an iterable of date-like values")
    normalized_dates = sorted(
        {
            pd.Timestamp(value).date().isoformat()
            for value in dates
        }
    )
    prepared = _prepare_observations(observations)
    rows = []
    for date in normalized_dates:
        cutoff = _cutoff(date)
        frame = _available_observations(prepared, cutoff)
        values, metadata = _derived_values(frame)
        risk = _build_macro_risk_from_values(values, metadata, cutoff)
        series = {}
        for key in HISTORY_SERIES:
            value = values.get(key)
            finite = value is not None and math.isfinite(float(value))
            source = metadata.get(key, {})
            series[key] = {
                "value": round(float(value), 6) if finite else None,
                "observation_date": source.get("observation_date"),
                "available_at": source.get("available_at"),
                "series_ids": list(source.get("series_ids", ())),
            }
        rows.append(
            {
                "time": date,
                "score": risk["score"],
                "coverage": risk["coverage"],
                "state": risk["state"],
                "components": risk["components"],
                "series": series,
                "evidence": risk["evidence"],
                "unavailable_reason": risk["unavailable_reason"],
            }
        )
    return rows


def build_macro_risk(observations, asof):
    """Return an auditable score using only vintages available by ``asof``."""
    cutoff = _cutoff(asof)
    frame = _normalize_observations(observations, cutoff)
    values, metadata = _derived_values(frame)
    return _build_macro_risk_from_values(values, metadata, cutoff)


def _build_macro_risk_from_values(values, metadata, cutoff):
    evidence = []
    available_weight = 0.0
    triggered_weight = 0.0
    component_available = {
        key: 0.0
        for key in (
            "rates",
            "inflation_energy",
            "credit_liquidity",
            "risk_aversion",
        )
    }
    component_triggered = dict.fromkeys(component_available, 0.0)
    conditions = []

    for component, key, weight, value_key, threshold, operator in _RULES:
        value = values.get(value_key)
        available = value is not None and math.isfinite(float(value))
        met = available and (
            float(value) >= threshold
            if operator == "ge"
            else float(value) <= threshold
        )
        if available:
            available_weight += weight
            component_available[component] += weight
        if met:
            triggered_weight += weight
            component_triggered[component] += weight
            conditions.append(key)
        source = metadata.get(value_key, {})
        evidence.append(
            {
                "key": key,
                "component": component,
                "state": "met" if met else ("unmet" if available else "unavailable"),
                "value": None if not available else round(float(value), 6),
                "threshold": threshold,
                "operator": operator,
                "weight": weight,
                "series_ids": list(source.get("series_ids", ())),
                "observation_date": source.get("observation_date"),
                "available_at": source.get("available_at"),
                "unavailable_reason": (
                    None if available else "macro_series_or_history_missing"
                ),
            }
        )

    coverage = available_weight / 100.0
    available = coverage >= MINIMUM_COVERAGE
    score = (
        round(100.0 * triggered_weight / available_weight, 2)
        if available and available_weight
        else None
    )
    components = {}
    for component in component_available:
        denominator = component_available[component]
        components[component] = {
            "score": (
                round(
                    100.0
                    * component_triggered[component]
                    / denominator,
                    2,
                )
                if denominator
                else None
            ),
            "coverage": round(
                denominator
                / sum(
                    row[2] for row in _RULES if row[0] == component
                ),
                4,
            ),
        }
    return {
        "model_key": MODEL_KEY,
        "model_version": MODEL_VERSION,
        "asof": cutoff.isoformat(),
        "score": score,
        "maximum_score": 100,
        "coverage": round(coverage, 4),
        "state": _state(score),
        "conditions": conditions,
        "components": components,
        "evidence": evidence,
        "unavailable_reason": (
            None if available else "insufficient_macro_coverage"
        ),
        "decision_permission": "advisory",
        "point_in_time": True,
    }


def unavailable_macro_risk(reason="macro_data_unavailable", asof=None):
    cutoff = _cutoff(asof)
    return {
        "model_key": MODEL_KEY,
        "model_version": MODEL_VERSION,
        "asof": cutoff.isoformat(),
        "score": None,
        "maximum_score": 100,
        "coverage": 0.0,
        "state": "unavailable",
        "conditions": [],
        "components": {
            key: {"score": None, "coverage": 0.0}
            for key in (
                "rates",
                "inflation_energy",
                "credit_liquidity",
                "risk_aversion",
            )
        },
        "evidence": [],
        "unavailable_reason": reason,
        "decision_permission": "advisory",
        "point_in_time": True,
    }


def _normalize_observations(observations, cutoff):
    return _available_observations(
        _prepare_observations(observations),
        cutoff,
    )


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
    frame = frame.loc[
        frame["series_id"].isin(SERIES_IDS)
        & frame["observation_date"].notna()
        & frame["available_at"].notna()
        & frame["value"].notna()
    ].copy()
    return frame.sort_values(
        ["series_id", "observation_date", "available_at", "realtime_start"]
    )


def _available_observations(frame, cutoff):
    if frame.empty:
        return frame.copy()
    available = frame.loc[frame["available_at"] <= cutoff]
    return available.drop_duplicates(
        ["series_id", "observation_date"],
        keep="last",
    )


def _derived_values(frame):
    series = {}
    rows = {}
    for series_id in SERIES_IDS:
        scoped = frame.loc[frame["series_id"] == series_id].sort_values(
            "observation_date"
        )
        if scoped.empty:
            continue
        series[series_id] = scoped.set_index("observation_date")["value"]
        rows[series_id] = scoped.iloc[-1]

    values = {}
    metadata = {}
    for series_id, row in rows.items():
        values[series_id] = float(row["value"])
        metadata[series_id] = _metadata(row, (series_id,))

    dgs2 = values.get("DGS2")
    dgs10 = values.get("DGS10")
    if dgs2 is not None and dgs10 is not None:
        values["CURVE_10Y_2Y"] = dgs10 - dgs2
        metadata["CURVE_10Y_2Y"] = _combined_metadata(
            rows,
            ("DGS10", "DGS2"),
        )

    _absolute_change(
        values,
        metadata,
        series,
        rows,
        "DGS2",
        "DGS2_CHANGE",
        20,
    )
    _absolute_change(
        values,
        metadata,
        series,
        rows,
        "BAMLH0A0HYM2",
        "HY_CHANGE",
        20,
    )
    _absolute_change(
        values,
        metadata,
        series,
        rows,
        "VIXCLS",
        "VIX_CHANGE",
        20,
    )
    _percent_change(
        values,
        metadata,
        series,
        rows,
        "DCOILWTICO",
        "OIL_CHANGE",
        20,
    )
    _percent_change(
        values,
        metadata,
        series,
        rows,
        "DTWEXBGS",
        "DOLLAR_CHANGE",
        20,
    )
    _percent_change(
        values,
        metadata,
        series,
        rows,
        "CPIAUCSL",
        "CPI_YOY",
        350,
    )
    return values, metadata


def _absolute_change(values, metadata, series, rows, source, target, days):
    pair = _current_and_prior(series.get(source), days)
    if pair is None:
        return
    current, prior = pair
    values[target] = float(current - prior)
    metadata[target] = _metadata(rows[source], (source,))


def _percent_change(values, metadata, series, rows, source, target, days):
    pair = _current_and_prior(series.get(source), days)
    if pair is None or float(pair[1]) == 0.0:
        return
    current, prior = pair
    values[target] = 100.0 * (float(current) / float(prior) - 1.0)
    metadata[target] = _metadata(rows[source], (source,))


def _current_and_prior(values, calendar_days):
    if values is None or len(values) < 2:
        return None
    current_date = pd.Timestamp(values.index[-1])
    eligible = values.loc[
        values.index <= current_date - pd.Timedelta(days=calendar_days)
    ]
    if eligible.empty:
        return None
    return float(values.iloc[-1]), float(eligible.iloc[-1])


def _metadata(row, series_ids):
    return {
        "series_ids": tuple(series_ids),
        "observation_date": pd.Timestamp(
            row["observation_date"]
        ).date().isoformat(),
        "available_at": pd.Timestamp(row["available_at"]).isoformat(),
    }


def _combined_metadata(rows, series_ids):
    selected = [rows[series_id] for series_id in series_ids]
    latest_observation = max(
        pd.Timestamp(row["observation_date"]) for row in selected
    )
    latest_available = max(
        pd.Timestamp(row["available_at"]) for row in selected
    )
    return {
        "series_ids": tuple(series_ids),
        "observation_date": latest_observation.date().isoformat(),
        "available_at": latest_available.isoformat(),
    }


def _cutoff(asof):
    if asof is None:
        return pd.Timestamp.now(tz="UTC")
    timestamp = pd.Timestamp(asof)
    if timestamp.tz is None:
        if len(str(asof)) == 10:
            timestamp = timestamp + pd.Timedelta(days=1) - pd.Timedelta(
                microseconds=1
            )
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _state(score):
    if score is None:
        return "unavailable"
    if score >= 70.0:
        return "severe"
    if score >= 50.0:
        return "high"
    if score >= 30.0:
        return "watch"
    return "low"
