#!/usr/bin/env python3
"""Import a reviewed Fed policy catalog into the macro database."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from web.services.policy_event_store import PolicyEventStore


DEFAULT_CATALOG = (
    Path(__file__).resolve().parent
    / "data"
    / "fed_policy_catalog_v1.json"
)
DEFAULT_DATABASE = (
    Path(__file__).resolve().parent / "data" / "macro_data.db"
)


def import_catalog(path, database):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    version, events, periods = _validated_catalog(payload)
    store = PolicyEventStore(database)
    store.initialize()
    counts = store.upsert_catalog(events, periods)
    return {
        "catalog_version": version,
        "events": counts["events"],
        "periods": counts["periods"],
    }


def _validated_catalog(payload):
    if not isinstance(payload, dict):
        raise ValueError("policy catalog must be a JSON object")
    version = str(payload.get("catalog_version", "")).strip()
    events = payload.get("events")
    periods = payload.get("periods")
    if not version:
        raise ValueError("policy catalog missing catalog_version")
    if not isinstance(events, list) or not isinstance(periods, list):
        raise ValueError("policy catalog events and periods must be arrays")

    normalized_events = [
        _with_version(row, version, kind="event")
        for row in events
    ]
    normalized_periods = [
        _with_version(row, version, kind="period")
        for row in periods
    ]
    event_ids = _unique_ids(
        normalized_events,
        field="event_id",
        kind="event",
    )
    _unique_ids(
        normalized_periods,
        field="period_id",
        kind="period",
    )
    for period in normalized_periods:
        references = period.get("source_event_ids_json")
        if isinstance(references, str):
            try:
                references = json.loads(references)
            except json.JSONDecodeError as error:
                raise ValueError(
                    "period source_event_ids_json must be valid JSON"
                ) from error
        if not isinstance(references, list) or any(
            not isinstance(value, str)
            for value in references
        ):
            raise ValueError(
                "period source_event_ids_json must be an array of strings"
            )
        unknown = sorted(set(references) - event_ids)
        if unknown:
            raise ValueError(
                "policy period references unknown event: "
                + ", ".join(unknown)
            )
        period["source_event_ids_json"] = references
    return version, normalized_events, normalized_periods


def _with_version(row, version, *, kind):
    if not isinstance(row, dict):
        raise ValueError(f"policy catalog {kind} must be an object")
    normalized = dict(row)
    embedded = normalized.get("catalog_version")
    if embedded is not None and str(embedded) != version:
        raise ValueError(
            f"policy catalog {kind} version does not match catalog"
        )
    normalized["catalog_version"] = version
    return normalized


def _unique_ids(rows, *, field, kind):
    values = []
    for row in rows:
        value = str(row.get(field, "")).strip()
        if not value:
            raise ValueError(f"policy catalog {kind} missing {field}")
        values.append(value)
    duplicates = sorted(
        value
        for value in set(values)
        if values.count(value) > 1
    )
    if duplicates:
        raise ValueError(
            f"duplicate policy {kind} id: {', '.join(duplicates)}"
        )
    return set(values)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Import the reviewed point-in-time Fed policy catalog."
    )
    parser.add_argument("--catalog", default=os.fspath(DEFAULT_CATALOG))
    parser.add_argument("--database", default=os.fspath(DEFAULT_DATABASE))
    arguments = parser.parse_args(argv)
    result = import_catalog(arguments.catalog, arguments.database)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
