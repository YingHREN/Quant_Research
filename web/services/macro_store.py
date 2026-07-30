"""SQLite storage for release-aware FRED/ALFRED observations."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pandas as pd


class MacroDataUnavailable(RuntimeError):
    pass


class MacroObservationStore:
    def __init__(self, path):
        self.path = Path(path)

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS macro_observations (
                    series_id TEXT NOT NULL,
                    observation_date TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    value REAL NOT NULL,
                    realtime_start TEXT NOT NULL,
                    realtime_end TEXT NOT NULL,
                    source TEXT NOT NULL,
                    revision_policy TEXT NOT NULL,
                    PRIMARY KEY (
                        series_id,
                        observation_date,
                        realtime_start
                    )
                )
                """
            )
            _ensure_revision_policy_column(connection)
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS macro_available_idx
                ON macro_observations(series_id, available_at, observation_date)
                """
            )

    def upsert(self, rows):
        normalized = [_normalized_row(row) for row in rows]
        if not normalized:
            return 0
        with sqlite3.connect(self.path) as connection:
            connection.executemany(
                """
                INSERT INTO macro_observations (
                    series_id, observation_date, available_at, value,
                    realtime_start, realtime_end, source, revision_policy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    series_id, observation_date, realtime_start
                ) DO UPDATE SET
                    available_at=excluded.available_at,
                    value=excluded.value,
                    realtime_end=excluded.realtime_end,
                    source=excluded.source,
                    revision_policy=excluded.revision_policy
                """,
                normalized,
            )
        return len(normalized)

    def load_available(self, asof, *, series_ids=()):
        if not self.path.is_file():
            raise MacroDataUnavailable("macro database is unavailable")
        cutoff = pd.Timestamp(asof)
        if cutoff.tz is None:
            cutoff = cutoff.tz_localize("UTC")
        else:
            cutoff = cutoff.tz_convert("UTC")
        params = [cutoff.isoformat()]
        normalized_ids = tuple(dict.fromkeys(str(value) for value in series_ids))
        try:
            with sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro",
                uri=True,
            ) as connection:
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(macro_observations)"
                    )
                }
                revision_policy = (
                    "revision_policy"
                    if "revision_policy" in columns
                    else "'legacy_unspecified' AS revision_policy"
                )
                query = f"""
                    SELECT series_id, observation_date, available_at, value,
                           realtime_start, realtime_end, source,
                           {revision_policy}
                    FROM macro_observations
                    WHERE available_at <= ?
                """
                if normalized_ids:
                    placeholders = ",".join("?" for _ in normalized_ids)
                    query += f" AND series_id IN ({placeholders})"
                    params.extend(normalized_ids)
                query += (
                    " ORDER BY series_id, observation_date, "
                    "available_at, realtime_start"
                )
                return pd.read_sql_query(query, connection, params=params)
        except sqlite3.Error as error:
            raise MacroDataUnavailable(
                "macro database is unavailable"
            ) from error


def _normalized_row(row):
    required = (
        "series_id",
        "observation_date",
        "available_at",
        "value",
        "realtime_start",
        "realtime_end",
        "source",
        "revision_policy",
    )
    missing = [key for key in required if row.get(key) is None]
    if missing:
        raise ValueError(f"macro observation missing: {', '.join(missing)}")
    available = pd.Timestamp(row["available_at"])
    if available.tz is None:
        raise ValueError("available_at must include a UTC offset")
    value = float(row["value"])
    return (
        str(row["series_id"]),
        pd.Timestamp(row["observation_date"]).date().isoformat(),
        available.tz_convert("UTC").isoformat(),
        value,
        pd.Timestamp(row["realtime_start"]).date().isoformat(),
        (
            "9999-12-31"
            if str(row["realtime_end"]) == "9999-12-31"
            else pd.Timestamp(row["realtime_end"]).date().isoformat()
        ),
        str(row["source"]),
        str(row["revision_policy"]),
    )


def _ensure_revision_policy_column(connection):
    columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(macro_observations)"
        )
    }
    if "revision_policy" not in columns:
        connection.execute(
            "ALTER TABLE macro_observations "
            "ADD COLUMN revision_policy TEXT NOT NULL "
            "DEFAULT 'legacy_unspecified'"
        )
