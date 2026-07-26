"""Read-only access to precomputed cross-sectional RS snapshots."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
import sqlite3
from threading import RLock


MODEL_VERSION = "cross_sectional_rs_v1"


class ResearchRelativeStrengthService:
    def __init__(self, database_path, max_cache_size=2):
        if isinstance(max_cache_size, bool) or not isinstance(max_cache_size, int):
            raise TypeError("max_cache_size must be an integer")
        if max_cache_size <= 0:
            raise ValueError("max_cache_size must be positive")
        self.database_path = Path(database_path)
        self._max_cache_size = max_cache_size
        self._cache = OrderedDict()
        self._lock = RLock()

    def build(self, tickers):
        normalized = tuple(
            sorted(
                {
                    str(ticker).strip().upper()
                    for ticker in tickers
                    if str(ticker).strip()
                }
            )
        )
        try:
            revision = self.database_path.stat().st_mtime_ns
        except OSError:
            return _unavailable_payload(normalized)
        key = (revision, normalized)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)
        try:
            payload = self._read(normalized)
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return _unavailable_payload(normalized)
        with self._lock:
            self._cache[key] = deepcopy(payload)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
        return deepcopy(payload)

    def _read(self, tickers):
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            metadata = connection.execute(
                """
                SELECT asof, model_version, MAX(sample_count) AS sample_count
                FROM relative_strength_snapshots
                WHERE model_version = ?
                GROUP BY asof, model_version
                ORDER BY asof DESC
                LIMIT 1
                """,
                (MODEL_VERSION,),
            ).fetchone()
            if metadata is None:
                return _unavailable_payload(tickers)
            rows = _rating_rows(
                connection,
                tickers,
                metadata["asof"],
                metadata["model_version"],
            )
        by_ticker = {
            ticker: _missing_rating(
                metadata["asof"],
                metadata["sample_count"],
                metadata["model_version"],
            )
            for ticker in tickers
        }
        for row in rows:
            by_ticker[row["ticker"]] = {
                "rs_rating": int(row["rs_rating"]),
                "rs_asof": row["asof"],
                "rs_sample_count": int(row["sample_count"]),
                "rs_model_version": row["model_version"],
            }
        return {
            "status": "available",
            "asof": metadata["asof"],
            "sample_count": int(metadata["sample_count"]),
            "model_version": metadata["model_version"],
            "by_ticker": by_ticker,
        }


def _rating_rows(connection, tickers, asof, model_version):
    rows = []
    for offset in range(0, len(tickers), 900):
        chunk = tickers[offset : offset + 900]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            connection.execute(
                f"""
                SELECT ticker, asof, rs_rating, sample_count, model_version
                FROM relative_strength_snapshots
                WHERE asof = ?
                  AND model_version = ?
                  AND ticker IN ({placeholders})
                ORDER BY ticker
                """,
                (asof, model_version, *chunk),
            ).fetchall()
        )
    return rows


def _missing_rating(asof=None, sample_count=None, model_version=None):
    return {
        "rs_rating": None,
        "rs_asof": asof,
        "rs_sample_count": (
            None if sample_count is None else int(sample_count)
        ),
        "rs_model_version": model_version,
    }


def _unavailable_payload(tickers):
    return {
        "status": "unavailable",
        "asof": None,
        "sample_count": 0,
        "model_version": MODEL_VERSION,
        "by_ticker": {
            ticker: _missing_rating(model_version=MODEL_VERSION)
            for ticker in tickers
        },
    }
