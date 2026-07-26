"""Bounded selected-ticker cache for causal historical entry signals."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
from hashlib import blake2b
from pathlib import Path
import pickle
import sqlite3
from threading import RLock
import zlib

import pandas as pd

from research.entry_signals import (
    ENTRY_SIGNAL_VERSION,
    build_entry_signal_rows,
)


_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
_ARTIFACT_FORMAT_VERSION = "entry-signal-artifact-v1"
_ARTIFACT_CODEC = "pickle5+zlib"


class EntrySignalService:
    def __init__(self, max_cache_size=16, artifact_store=None):
        if isinstance(max_cache_size, bool) or not isinstance(
            max_cache_size,
            int,
        ):
            raise TypeError("max_cache_size must be an integer")
        if max_cache_size <= 0:
            raise ValueError("max_cache_size must be positive")
        self._max_cache_size = max_cache_size
        self._artifact_store = artifact_store
        self._cache = OrderedDict()
        self._lock = RLock()

    def build(self, ticker, history):
        normalized_ticker = str(ticker).strip().upper()
        if not normalized_ticker:
            raise ValueError("ticker must be non-empty")
        fingerprint = _history_fingerprint(history)
        key = (
            normalized_ticker,
            ENTRY_SIGNAL_VERSION,
            fingerprint,
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return pickle.loads(cached)

        rows = (
            None
            if self._artifact_store is None
            else self._artifact_store.load(
                normalized_ticker,
                fingerprint,
            )
        )
        if rows is None:
            rows = build_entry_signal_rows(history)
            if self._artifact_store is not None:
                self._artifact_store.save(
                    normalized_ticker,
                    fingerprint,
                    rows,
                )
        encoded_rows = pickle.dumps(rows, protocol=5)
        with self._lock:
            self._cache[key] = encoded_rows
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
        return pickle.loads(encoded_rows)


class EntrySignalArtifactStore:
    """Best-effort persistent cache for complete per-ticker signal rows."""

    def __init__(self, path, max_entries=64):
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise TypeError("max_entries must be an integer")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.path = Path(path)
        self._max_entries = max_entries
        self._lock = RLock()

    def load(self, ticker, fingerprint):
        if not self.path.exists():
            return None
        cache_key = _artifact_key(ticker, fingerprint)
        with self._lock:
            try:
                with sqlite3.connect(self.path) as connection:
                    _ensure_artifact_schema(connection)
                    row = connection.execute(
                        """
                        SELECT ticker, algorithm_version, history_fingerprint,
                               format_version, payload_codec,
                               payload_checksum, payload
                        FROM entry_signal_artifacts
                        WHERE cache_key = ?
                        """,
                        (cache_key,),
                    ).fetchone()
            except sqlite3.Error:
                return None
            if row is None:
                return None
            try:
                return _decode_artifact(
                    row,
                    ticker,
                    fingerprint,
                )
            except (
                EOFError,
                pickle.UnpicklingError,
                TypeError,
                ValueError,
                zlib.error,
            ):
                self._delete_best_effort(cache_key)
                return None

    def save(self, ticker, fingerprint, rows):
        cache_key = _artifact_key(ticker, fingerprint)
        try:
            payload = zlib.compress(pickle.dumps(rows, protocol=5))
            checksum = blake2b(payload, digest_size=20).hexdigest()
        except (pickle.PickleError, TypeError, ValueError, zlib.error):
            return False
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(self.path) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    _ensure_artifact_schema(connection)
                    connection.execute(
                        """
                        INSERT INTO entry_signal_artifacts (
                            cache_key, ticker, algorithm_version,
                            history_fingerprint, format_version,
                            created_at, payload_codec, payload_checksum,
                            payload
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            created_at = excluded.created_at,
                            payload_codec = excluded.payload_codec,
                            payload_checksum = excluded.payload_checksum,
                            payload = excluded.payload
                        """,
                        (
                            cache_key,
                            ticker,
                            ENTRY_SIGNAL_VERSION,
                            fingerprint,
                            _ARTIFACT_FORMAT_VERSION,
                            datetime.now(timezone.utc).isoformat(),
                            _ARTIFACT_CODEC,
                            checksum,
                            sqlite3.Binary(payload),
                        ),
                    )
                    connection.execute(
                        """
                        DELETE FROM entry_signal_artifacts
                        WHERE cache_key NOT IN (
                            SELECT cache_key
                            FROM entry_signal_artifacts
                            ORDER BY created_at DESC, rowid DESC
                            LIMIT ?
                        )
                        """,
                        (self._max_entries,),
                    )
                return True
            except (OSError, sqlite3.Error):
                return False

    def _delete_best_effort(self, cache_key):
        try:
            with sqlite3.connect(self.path) as connection:
                connection.execute(
                    "DELETE FROM entry_signal_artifacts WHERE cache_key = ?",
                    (cache_key,),
                )
        except sqlite3.Error:
            pass


def _ensure_artifact_schema(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS entry_signal_artifacts (
            cache_key TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            algorithm_version TEXT NOT NULL,
            history_fingerprint TEXT NOT NULL,
            format_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_codec TEXT NOT NULL,
            payload_checksum TEXT NOT NULL,
            payload BLOB NOT NULL
        )
        """
    )


def _artifact_key(ticker, fingerprint):
    payload = "\0".join(
        (
            str(ticker),
            ENTRY_SIGNAL_VERSION,
            str(fingerprint),
            _ARTIFACT_FORMAT_VERSION,
        )
    ).encode("utf-8")
    return blake2b(payload, digest_size=32).hexdigest()


def _decode_artifact(row, ticker, fingerprint):
    (
        stored_ticker,
        algorithm_version,
        stored_fingerprint,
        format_version,
        payload_codec,
        checksum,
        payload,
    ) = row
    if (
        stored_ticker != ticker
        or algorithm_version != ENTRY_SIGNAL_VERSION
        or stored_fingerprint != fingerprint
        or format_version != _ARTIFACT_FORMAT_VERSION
        or payload_codec != _ARTIFACT_CODEC
        or blake2b(payload, digest_size=20).hexdigest() != checksum
    ):
        raise ValueError("entry signal artifact identity mismatch")
    rows = pickle.loads(zlib.decompress(payload))
    if not isinstance(rows, list) or not all(
        isinstance(item, dict) and isinstance(item.get("time"), str)
        for item in rows
    ):
        raise ValueError("entry signal artifact payload is invalid")
    return rows


def merge_entry_signal_rows(chart, signals):
    """Merge entry evidence by exact ISO trading date."""
    by_date = {}
    for signal in signals:
        date = signal.get("time") if isinstance(signal, dict) else None
        if not isinstance(date, str) or date in by_date:
            raise ValueError("entry signal dates must be unique ISO strings")
        by_date[date] = signal

    merged = []
    for row in chart:
        date = row.get("time") if isinstance(row, dict) else None
        signal = by_date.get(date)
        if signal is None:
            raise ValueError("entry signal history is missing a chart date")
        merged.append({**row, **signal, "time": date})
    return merged


def _history_fingerprint(history):
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a pandas DataFrame")
    missing = [column for column in _OHLCV_COLUMNS if column not in history]
    if missing:
        raise ValueError("history must contain OHLCV columns")
    frame = history.loc[:, _OHLCV_COLUMNS].sort_index()
    hashed = pd.util.hash_pandas_object(frame, index=True)
    digest = blake2b(digest_size=20)
    digest.update(hashed.to_numpy(dtype="uint64").tobytes())
    return digest.hexdigest()
