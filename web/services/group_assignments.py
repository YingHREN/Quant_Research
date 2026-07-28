"""Bounded, point-in-time reads of persisted stock group assignments."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import sqlite3
from threading import RLock


_BATCH_SIZE = 900


class GroupAssignmentRepository:
    """Read assignment metadata without loading research price history."""

    def __init__(self, database_path, max_cache_size=2):
        if isinstance(max_cache_size, bool) or not isinstance(max_cache_size, int):
            raise TypeError("max_cache_size must be an integer")
        if max_cache_size <= 0:
            raise ValueError("max_cache_size must be positive")
        self.database_path = Path(database_path)
        self._max_cache_size = max_cache_size
        self._cache = OrderedDict()
        self._lock = RLock()

    def build(self, tickers, asof=None):
        """Return one deterministic assignment snapshot for every ticker."""
        normalized = _normalize_tickers(tickers)
        requested_asof = None if asof is None else _normalize_date(asof)
        try:
            revision = self.database_path.stat().st_mtime_ns
        except OSError:
            return _unavailable_payload(normalized, requested_asof)
        try:
            observation_date = (
                requested_asof
                if requested_asof is not None
                else self._latest_observation_date()
            )
        except (OSError, sqlite3.Error):
            return _unavailable_payload(normalized, requested_asof)
        key = (revision, normalized, observation_date)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)
        try:
            payload = self._read(normalized, observation_date, revision)
        except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            return _unavailable_payload(normalized, observation_date)
        with self._lock:
            self._cache[key] = deepcopy(payload)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
        return deepcopy(payload)

    def build_history(self, tickers, *, start_asof=None, end_asof=None):
        """Return every assignment interval overlapping a bounded date range."""
        normalized = _normalize_tickers(tickers)
        start = None if start_asof is None else _normalize_date(start_asof)
        end = None if end_asof is None else _normalize_date(end_asof)
        if start is not None and end is not None and start > end:
            raise ValueError("assignment history range is invalid")
        try:
            revision = self.database_path.stat().st_mtime_ns
        except OSError:
            return _unavailable_history_payload(normalized, start, end)
        try:
            if end is None:
                end = self._latest_observation_date()
            key = (revision, "history", normalized, start, end)
            with self._lock:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    return deepcopy(cached)
            payload = self._read_history(normalized, start, end, revision)
        except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
            return _unavailable_history_payload(normalized, start, end)
        with self._lock:
            self._cache[key] = deepcopy(payload)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
        return deepcopy(payload)

    def _latest_observation_date(self):
        connection = _readonly_connection(self.database_path)
        try:
            value = connection.execute(
                "SELECT MAX(observed_at) FROM group_assignments"
            ).fetchone()[0]
        finally:
            connection.close()
        return None if value is None else _normalize_date(value)

    def _read(self, tickers, asof, revision):
        if asof is None:
            return {
                "status": "available",
                "asof": None,
                "revision": revision,
                "coverage": 0.0,
                "review_count": 0,
                "by_ticker": {
                    ticker: _missing("no_assignment_effective_at_asof")
                    for ticker in tickers
                },
            }
        connection = _readonly_connection(self.database_path)
        try:
            rows = _assignment_rows(connection, tickers, asof)
        finally:
            connection.close()

        by_ticker = {
            ticker: _missing("no_assignment_effective_at_asof") for ticker in tickers
        }
        review_count = 0
        assigned_count = 0
        for row in rows:
            ticker = row["ticker"]
            if row["same_effective_date_count"] > 1:
                by_ticker[ticker] = _missing("ambiguous_assignment_effective_at_asof")
                continue
            assignment = _assignment_dict(row)
            by_ticker[ticker] = assignment
            assigned_count += 1
            if assignment["classification_state"] == "needs_review":
                review_count += 1
        return {
            "status": "available",
            "asof": asof,
            "revision": revision,
            "coverage": (assigned_count / len(tickers)) if tickers else 1.0,
            "review_count": review_count,
            "by_ticker": by_ticker,
        }

    def _read_history(self, tickers, start, end, revision):
        if end is None:
            rows = ()
        else:
            connection = _readonly_connection(self.database_path)
            try:
                rows = _assignment_history_rows(
                    connection,
                    tickers,
                    start,
                    end,
                )
            finally:
                connection.close()
        by_ticker = {ticker: [] for ticker in tickers}
        for row in rows:
            by_ticker[row["ticker"]].append(_assignment_dict(row))
        return {
            "status": "available",
            "start_asof": start,
            "end_asof": end,
            "revision": revision,
            "by_ticker": by_ticker,
        }


def _normalize_tickers(tickers):
    return tuple(
        sorted(
            {
                str(ticker).strip().upper()
                for ticker in tickers
                if str(ticker).strip()
            }
        )
    )


def _normalize_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_asof_date") from exc


def _readonly_connection(database_path):
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _assignment_rows(connection, tickers, asof):
    rows = []
    for offset in range(0, len(tickers), _BATCH_SIZE):
        chunk = tickers[offset : offset + _BATCH_SIZE]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            connection.execute(
                f"""
                WITH candidates AS (
                    SELECT
                        ticker, rule_version, effective_from, effective_to,
                        observed_at, sector_key, sector_benchmark,
                        theme_keys_json, theme_benchmarks_json,
                        primary_model_group, classification_state, source,
                        confidence, override_reason,
                        ROW_NUMBER() OVER (
                            PARTITION BY ticker
                            ORDER BY effective_from DESC, rule_version DESC
                        ) AS selection_rank,
                        COUNT(*) OVER (
                            PARTITION BY ticker, effective_from
                        ) AS same_effective_date_count
                    FROM group_assignments
                    WHERE ticker IN ({placeholders})
                      AND effective_from <= ?
                      AND (effective_to IS NULL OR ? < effective_to)
                )
                SELECT * FROM candidates
                WHERE selection_rank = 1
                ORDER BY ticker
                """,
                [*chunk, asof, asof],
            ).fetchall()
        )
    return rows


def _assignment_history_rows(connection, tickers, start, end):
    rows = []
    for offset in range(0, len(tickers), _BATCH_SIZE):
        chunk = tickers[offset : offset + _BATCH_SIZE]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        conditions = [
            f"ticker IN ({placeholders})",
            "effective_from <= ?",
        ]
        parameters = [*chunk, end]
        if start is not None:
            conditions.append("(effective_to IS NULL OR ? < effective_to)")
            parameters.append(start)
        rows.extend(
            connection.execute(
                f"""
                SELECT
                    ticker, rule_version, effective_from, effective_to,
                    observed_at, sector_key, sector_benchmark,
                    theme_keys_json, theme_benchmarks_json,
                    primary_model_group, classification_state, source,
                    confidence, override_reason
                FROM group_assignments
                WHERE {" AND ".join(conditions)}
                ORDER BY ticker, effective_from, rule_version
                """,
                parameters,
            ).fetchall()
        )
    return rows


def _assignment_dict(row):
    theme_keys = json.loads(row["theme_keys_json"])
    theme_benchmarks = json.loads(row["theme_benchmarks_json"])
    if not isinstance(theme_keys, list) or not isinstance(theme_benchmarks, dict):
        raise ValueError("invalid_group_assignment_json")
    return {
        "state": "assigned",
        "ticker": row["ticker"],
        "rule_version": row["rule_version"],
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "observed_at": row["observed_at"],
        "sector_key": row["sector_key"],
        "sector_benchmark": row["sector_benchmark"],
        "theme_keys": theme_keys,
        "theme_benchmarks": theme_benchmarks,
        "primary_model_group": row["primary_model_group"],
        "classification_state": row["classification_state"],
        "source": row["source"],
        "confidence": float(row["confidence"]),
        "override_reason": row["override_reason"],
    }


def _missing(reason):
    return {"state": "missing", "reason": reason}


def _unavailable_payload(tickers, asof):
    return {
        "status": "unavailable",
        "asof": asof,
        "revision": None,
        "coverage": 0.0,
        "review_count": 0,
        "by_ticker": {
            ticker: _missing("assignment_repository_unavailable")
            for ticker in tickers
        },
    }


def _unavailable_history_payload(tickers, start, end):
    return {
        "status": "unavailable",
        "start_asof": start,
        "end_asof": end,
        "revision": None,
        "by_ticker": {ticker: [] for ticker in tickers},
    }
