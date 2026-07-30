"""JSON-safe, descriptive matrix for audited Fed policy periods."""

from __future__ import annotations

from collections import Counter
import json
import math

import numpy as np
import pandas as pd

from research.policy_period_returns import describe_policy_periods


MATRIX_ARTIFACT_KEY = "policy_period_matrix_v1"
MATRIX_METRICS = (
    "total_return",
    "annualized_return",
    "relative_spy_return",
    "max_drawdown",
    "positive_month_ratio",
)


def build_policy_period_matrix(periods, events, histories, asof):
    """Build a point-in-time historical description without ranking ETFs."""
    cutoff = _utc_timestamp(asof)
    visible_periods = _visible_rows(periods, cutoff)
    visible_events = _visible_rows(events, cutoff)
    described = (
        describe_policy_periods(
            visible_periods,
            histories,
            cutoff,
        )
        if not visible_periods.empty
        else pd.DataFrame()
    )
    rows = [_json_record(row) for row in described.to_dict("records")]
    return {
        "artifact_key": MATRIX_ARTIFACT_KEY,
        "asof": cutoff.isoformat(),
        "periods": _period_details(visible_periods, visible_events),
        "rows": rows,
        "metrics": list(MATRIX_METRICS),
        "coverage": _coverage(rows),
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "point_in_time": True,
        "historical_description_only": True,
        "unavailable_reason": (
            None if rows else "policy_periods_unavailable"
        ),
    }


def _visible_rows(rows, cutoff):
    frame = pd.DataFrame(rows).copy()
    if frame.empty:
        return frame
    if "available_at" not in frame.columns:
        raise ValueError("policy rows missing: available_at")
    available = pd.to_datetime(
        frame["available_at"],
        utc=True,
        errors="raise",
    )
    return frame.loc[available <= cutoff].reset_index(drop=True)


def _period_details(periods, events):
    event_by_id = {}
    for event in events.to_dict("records"):
        event_by_id[str(event["event_id"])] = {
            "event_id": str(event["event_id"]),
            "catalog_version": str(event["catalog_version"]),
            "event_type": str(event["event_type"]),
            "effective_date": str(event["effective_date"]),
            "available_at": str(event["available_at"]),
            "source_url": str(event["source_url"]),
            "source_title": str(event["source_title"]),
            "source_published_at": str(event["source_published_at"]),
            "payload": _json_object(event.get("payload_json")),
        }
    details = []
    for period in periods.to_dict("records"):
        requested = _json_array(period.get("source_event_ids_json"))
        resolved_ids = [
            str(event_id)
            for event_id in requested
            if str(event_id) in event_by_id
        ]
        end_date = _json_value(period.get("end_date"))
        details.append(
            {
                "period_id": str(period["period_id"]),
                "catalog_version": str(period["catalog_version"]),
                "label_zh": str(period["label_zh"]),
                "label_en": str(period["label_en"]),
                "start_date": str(period["start_date"]),
                "end_date": end_date,
                "available_at": str(period["available_at"]),
                "interpretation_zh": str(
                    period.get("interpretation_zh", "")
                ),
                "interpretation_en": str(
                    period.get("interpretation_en", "")
                ),
                "is_complete": end_date is not None,
                "source_event_ids": resolved_ids,
                "events": [
                    dict(event_by_id[event_id])
                    for event_id in resolved_ids
                ],
            }
        )
    return details


def _coverage(rows):
    counts = Counter(str(row["status"]) for row in rows)
    eligible_rows = sum(
        count
        for status, count in counts.items()
        if status not in {"incomplete", "unavailable_at_asof"}
    )
    complete_rows = int(counts.get("complete", 0))
    return {
        "period_count": len(
            {str(row["period_id"]) for row in rows}
        ),
        "ticker_period_rows": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "complete_rows": complete_rows,
        "eligible_rows": int(eligible_rows),
        "ratio": (
            complete_rows / eligible_rows
            if eligible_rows
            else None
        ),
    }


def _json_record(row):
    return {
        str(key): _json_value(value)
        for key, value in row.items()
    }


def _json_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _json_array(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    parsed = json.loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError("source_event_ids_json must contain a JSON array")
    return parsed


def _json_object(value):
    if isinstance(value, dict):
        return dict(value)
    if value in (None, ""):
        return {}
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("payload_json must contain a JSON object")
    return parsed


def _utc_timestamp(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")
