"""Point-in-time storage for official Fed events and human period labels."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

import pandas as pd


ALLOWED_EVENT_TYPES = frozenset(
    {
        "policy_rate",
        "qe",
        "qt",
        "reinvestment",
        "reserve_management_purchase",
    }
)


class PolicyDataUnavailable(RuntimeError):
    pass


class PolicyEventStore:
    def __init__(self, path):
        self.path = Path(path)

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_events (
                    event_id TEXT NOT NULL,
                    catalog_version TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    source_published_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (event_id, catalog_version)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS policy_events_available_idx
                ON policy_events(available_at, event_type, effective_date)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS policy_periods (
                    period_id TEXT NOT NULL,
                    catalog_version TEXT NOT NULL,
                    label_zh TEXT NOT NULL,
                    label_en TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT,
                    available_at TEXT NOT NULL,
                    interpretation_zh TEXT NOT NULL,
                    interpretation_en TEXT NOT NULL,
                    source_event_ids_json TEXT NOT NULL,
                    PRIMARY KEY (period_id, catalog_version)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS policy_periods_available_idx
                ON policy_periods(available_at, start_date, end_date)
                """
            )

    def upsert_events(self, rows):
        normalized = [_normalize_event(row) for row in rows]
        if not normalized:
            return 0
        with sqlite3.connect(self.path) as connection:
            _upsert_events(connection, normalized)
        return len(normalized)

    def upsert_periods(self, rows):
        normalized = [_normalize_period(row) for row in rows]
        if not normalized:
            return 0
        with sqlite3.connect(self.path) as connection:
            _upsert_periods(connection, normalized)
        return len(normalized)

    def upsert_catalog(self, events, periods):
        normalized_events = [_normalize_event(row) for row in events]
        normalized_periods = [_normalize_period(row) for row in periods]
        with sqlite3.connect(self.path) as connection:
            _upsert_events(connection, normalized_events)
            _upsert_periods(connection, normalized_periods)
        return {
            "events": len(normalized_events),
            "periods": len(normalized_periods),
        }

    def load_events(self, asof, *, event_types=()):
        cutoff = _utc_iso(asof, field="asof")
        query = """
            SELECT event_id, catalog_version, event_type, effective_date,
                   available_at, source_url, source_title,
                   source_published_at, payload_json
            FROM policy_events
            WHERE available_at <= ?
        """
        params = [cutoff]
        normalized_types = tuple(
            dict.fromkeys(str(value) for value in event_types)
        )
        if normalized_types:
            invalid = sorted(set(normalized_types) - ALLOWED_EVENT_TYPES)
            if invalid:
                raise ValueError(
                    f"unsupported policy event type: {', '.join(invalid)}"
                )
            placeholders = ",".join("?" for _ in normalized_types)
            query += f" AND event_type IN ({placeholders})"
            params.extend(normalized_types)
        query += (
            " ORDER BY effective_date, available_at, "
            "event_id, catalog_version"
        )
        return self._read(query, params)

    def load_periods(self, asof, *, include_incomplete=True):
        cutoff = _utc_iso(asof, field="asof")
        query = """
            SELECT period_id, catalog_version, label_zh, label_en,
                   start_date, end_date, available_at,
                   interpretation_zh, interpretation_en,
                   source_event_ids_json
            FROM policy_periods
            WHERE available_at <= ?
        """
        if not include_incomplete:
            query += " AND end_date IS NOT NULL"
        query += (
            " ORDER BY start_date, period_id, catalog_version"
        )
        return self._read(query, [cutoff])

    def _read(self, query, params):
        if not self.path.is_file():
            raise PolicyDataUnavailable(
                "policy event database is unavailable"
            )
        try:
            with sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro",
                uri=True,
            ) as connection:
                return pd.read_sql_query(
                    query,
                    connection,
                    params=params,
                )
        except sqlite3.Error as error:
            raise PolicyDataUnavailable(
                "policy event database is unavailable"
            ) from error


def _normalize_event(row):
    required = (
        "event_id",
        "catalog_version",
        "event_type",
        "effective_date",
        "available_at",
        "source_url",
        "source_title",
        "source_published_at",
        "payload_json",
    )
    _require_values(row, required, kind="policy event")
    event_type = str(row["event_type"])
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"unsupported policy event type: {event_type}")
    source_url = str(row["source_url"])
    hostname = (urlparse(source_url).hostname or "").lower()
    if not (
        hostname == "federalreserve.gov"
        or hostname.endswith(".federalreserve.gov")
    ):
        raise ValueError(
            "policy event source must be an official Federal Reserve URL"
        )
    available_at = _utc_iso(row["available_at"], field="available_at")
    published_at = _utc_iso(
        row["source_published_at"],
        field="source_published_at",
    )
    if pd.Timestamp(available_at) < pd.Timestamp(published_at):
        raise ValueError(
            "policy event available_at must not precede "
            "source_published_at"
        )
    return (
        str(row["event_id"]),
        str(row["catalog_version"]),
        event_type,
        _date_iso(row["effective_date"], field="effective_date"),
        available_at,
        source_url,
        str(row["source_title"]),
        published_at,
        _canonical_json(row["payload_json"], expected_type=dict),
    )


def _normalize_period(row):
    required = (
        "period_id",
        "catalog_version",
        "label_zh",
        "label_en",
        "start_date",
        "available_at",
        "interpretation_zh",
        "interpretation_en",
        "source_event_ids_json",
    )
    _require_values(row, required, kind="policy period")
    start = _date_iso(row["start_date"], field="start_date")
    end = (
        None
        if row.get("end_date") in (None, "")
        else _date_iso(row["end_date"], field="end_date")
    )
    if end is not None and end < start:
        raise ValueError("policy period end_date must not precede start_date")
    return (
        str(row["period_id"]),
        str(row["catalog_version"]),
        str(row["label_zh"]),
        str(row["label_en"]),
        start,
        end,
        _utc_iso(row["available_at"], field="available_at"),
        str(row["interpretation_zh"]),
        str(row["interpretation_en"]),
        _canonical_json(
            row["source_event_ids_json"],
            expected_type=list,
            item_type=str,
        ),
    )


def _require_values(row, fields, *, kind):
    missing = [
        field
        for field in fields
        if row.get(field) is None or str(row.get(field)).strip() == ""
    ]
    if missing:
        raise ValueError(f"{kind} missing: {', '.join(missing)}")


def _utc_iso(value, *, field):
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        raise ValueError(f"{field} must include a UTC offset")
    return timestamp.tz_convert("UTC").isoformat()


def _date_iso(value, *, field):
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError(f"{field} must be a valid date")
    return timestamp.date().isoformat()


def _canonical_json(value, *, expected_type, item_type=None):
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("policy JSON must be valid") from error
    if not isinstance(decoded, expected_type):
        raise ValueError(
            f"policy JSON must decode to {expected_type.__name__}"
        )
    if item_type is not None and any(
        not isinstance(item, item_type)
        for item in decoded
    ):
        raise ValueError(
            f"policy JSON items must be {item_type.__name__}"
        )
    return json.dumps(
        decoded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _upsert_events(connection, rows):
    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO policy_events (
            event_id, catalog_version, event_type, effective_date,
            available_at, source_url, source_title,
            source_published_at, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (event_id, catalog_version) DO UPDATE SET
            event_type=excluded.event_type,
            effective_date=excluded.effective_date,
            available_at=excluded.available_at,
            source_url=excluded.source_url,
            source_title=excluded.source_title,
            source_published_at=excluded.source_published_at,
            payload_json=excluded.payload_json
        """,
        rows,
    )


def _upsert_periods(connection, rows):
    if not rows:
        return
    connection.executemany(
        """
        INSERT INTO policy_periods (
            period_id, catalog_version, label_zh, label_en,
            start_date, end_date, available_at,
            interpretation_zh, interpretation_en,
            source_event_ids_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (period_id, catalog_version) DO UPDATE SET
            label_zh=excluded.label_zh,
            label_en=excluded.label_en,
            start_date=excluded.start_date,
            end_date=excluded.end_date,
            available_at=excluded.available_at,
            interpretation_zh=excluded.interpretation_zh,
            interpretation_en=excluded.interpretation_en,
            source_event_ids_json=excluded.source_event_ids_json
        """,
        rows,
    )
