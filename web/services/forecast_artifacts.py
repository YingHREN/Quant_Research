"""Versioned SQLite storage for disposable forecast build artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import blake2b
import json
from pathlib import Path
import pickle
import sqlite3
from threading import RLock
from typing import Any, Mapping
import zlib

import pandas as pd


FORMAT_VERSION = "forecast-artifact-v1"
PAYLOAD_CODEC = "pickle5+zlib"


@dataclass(frozen=True)
class ForecastArtifactIdentity:
    model_key: str
    model_version: str
    feature_version: str
    risk_context_version: str

    def __post_init__(self):
        for field_name in (
            "model_key",
            "model_version",
            "feature_version",
            "risk_context_version",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True)
class ForecastArtifact:
    frame: pd.DataFrame
    risk_context: pd.DataFrame
    evaluations: Mapping[str, Any]
    coverage: Mapping[str, Any]
    fingerprints: Mapping[str, Any]


class ForecastArtifactStore:
    """Best-effort persistent cache; failures always behave like cache misses."""

    def __init__(self, path, max_entries=2):
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise TypeError("max_entries must be an integer")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.path = Path(path)
        self._max_entries = max_entries
        self._lock = RLock()

    def load(self, identity, market_signature):
        identity = _validate_identity(identity)
        market_signature = _required_text(market_signature, "market_signature")
        if not self.path.exists():
            return None
        cache_key = _cache_key(identity, market_signature)
        with self._lock:
            try:
                with sqlite3.connect(self.path) as connection:
                    _ensure_schema(connection)
                    row = connection.execute(
                        """
                        SELECT market_signature, model_key, model_version,
                               feature_version, risk_context_version,
                               format_version, payload_codec,
                               payload_checksum, payload
                        FROM forecast_artifacts
                        WHERE cache_key = ?
                        """,
                        (cache_key,),
                    ).fetchone()
            except sqlite3.Error:
                return None
            if row is None:
                return None
            try:
                return _decode_row(row, identity, market_signature)
            except (
                AttributeError,
                EOFError,
                KeyError,
                pickle.UnpicklingError,
                TypeError,
                ValueError,
                zlib.error,
            ):
                self._delete_best_effort(cache_key)
                return None

    def save(self, identity, market_signature, artifact):
        identity = _validate_identity(identity)
        market_signature = _required_text(market_signature, "market_signature")
        _validate_artifact(artifact)
        cache_key = _cache_key(identity, market_signature)
        try:
            payload = _encode_artifact(artifact)
            checksum = _checksum(payload)
        except (pickle.PickleError, TypeError, ValueError, zlib.error):
            return False

        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(self.path) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    _ensure_schema(connection)
                    connection.execute(
                        """
                        INSERT INTO forecast_artifacts (
                            cache_key, market_signature, model_key,
                            model_version, feature_version,
                            risk_context_version, format_version,
                            created_at, market_asof, payload_codec,
                            payload_checksum, payload
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(cache_key) DO UPDATE SET
                            created_at = excluded.created_at,
                            market_asof = excluded.market_asof,
                            payload_codec = excluded.payload_codec,
                            payload_checksum = excluded.payload_checksum,
                            payload = excluded.payload
                        """,
                        (
                            cache_key,
                            market_signature,
                            identity.model_key,
                            identity.model_version,
                            identity.feature_version,
                            identity.risk_context_version,
                            FORMAT_VERSION,
                            datetime.now(timezone.utc).isoformat(),
                            _artifact_market_asof(artifact.coverage),
                            PAYLOAD_CODEC,
                            checksum,
                            sqlite3.Binary(payload),
                        ),
                    )
                    connection.execute(
                        """
                        DELETE FROM forecast_artifacts
                        WHERE cache_key NOT IN (
                            SELECT cache_key
                            FROM forecast_artifacts
                            ORDER BY created_at DESC, rowid DESC
                            LIMIT ?
                        )
                        """,
                        (self._max_entries,),
                    )
                return True
            except (OSError, sqlite3.Error):
                return False

    def entry_count(self):
        if not self.path.exists():
            return 0
        with self._lock:
            try:
                with sqlite3.connect(self.path) as connection:
                    _ensure_schema(connection)
                    row = connection.execute(
                        "SELECT COUNT(*) FROM forecast_artifacts"
                    ).fetchone()
                return int(row[0])
            except sqlite3.Error:
                return 0

    def status(self):
        """Return safe cache metadata without decoding artifact payloads."""
        result = _empty_status()
        if not self.path.exists():
            return result
        with self._lock:
            try:
                with sqlite3.connect(self.path) as connection:
                    _ensure_schema(connection)
                    count_row = connection.execute(
                        """
                        SELECT COUNT(*), COALESCE(SUM(LENGTH(payload)), 0)
                        FROM forecast_artifacts
                        """
                    ).fetchone()
                    latest = connection.execute(
                        """
                        SELECT created_at, market_asof, model_key,
                               model_version, feature_version,
                               risk_context_version, format_version
                        FROM forecast_artifacts
                        ORDER BY created_at DESC, rowid DESC
                        LIMIT 1
                        """
                    ).fetchone()
            except (OSError, sqlite3.Error):
                result["state"] = "unavailable"
                return result
        result["entry_count"] = int(count_row[0])
        result["size_bytes"] = int(count_row[1])
        if latest is None:
            return result
        (
            result["latest_created_at"],
            result["market_asof"],
            result["model_key"],
            result["model_version"],
            result["feature_version"],
            result["risk_context_version"],
            result["format_version"],
        ) = latest
        result["state"] = "ready"
        return result

    def _delete_best_effort(self, cache_key):
        try:
            with sqlite3.connect(self.path) as connection:
                connection.execute(
                    "DELETE FROM forecast_artifacts WHERE cache_key = ?",
                    (cache_key,),
                )
        except sqlite3.Error:
            pass


def _ensure_schema(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS forecast_artifacts (
            cache_key TEXT PRIMARY KEY,
            market_signature TEXT NOT NULL,
            model_key TEXT NOT NULL,
            model_version TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            risk_context_version TEXT NOT NULL,
            format_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            market_asof TEXT,
            payload_codec TEXT NOT NULL,
            payload_checksum TEXT NOT NULL,
            payload BLOB NOT NULL
        )
        """
    )
    columns = {
        row[1] for row in connection.execute(
            "PRAGMA table_info(forecast_artifacts)"
        )
    }
    if "market_asof" not in columns:
        connection.execute(
            "ALTER TABLE forecast_artifacts ADD COLUMN market_asof TEXT"
        )


def _empty_status():
    return {
        "state": "empty",
        "entry_count": 0,
        "latest_created_at": None,
        "market_asof": None,
        "model_key": None,
        "model_version": None,
        "feature_version": None,
        "risk_context_version": None,
        "format_version": None,
        "size_bytes": 0,
    }


def _artifact_market_asof(coverage):
    dates = []
    for value in coverage.values():
        try:
            timestamp = pd.Timestamp(value[1])
        except (IndexError, TypeError, ValueError):
            continue
        if not pd.isna(timestamp):
            dates.append(timestamp.normalize())
    if not dates:
        return None
    return max(dates).date().isoformat()


def _cache_key(identity, market_signature):
    canonical = json.dumps(
        [
            market_signature,
            identity.model_key,
            identity.model_version,
            identity.feature_version,
            identity.risk_context_version,
            FORMAT_VERSION,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return blake2b(canonical, digest_size=32).hexdigest()


def _encode_artifact(artifact):
    value = {
        "frame": artifact.frame,
        "risk_context": artifact.risk_context,
        "evaluations": dict(artifact.evaluations),
        "coverage": dict(artifact.coverage),
        "fingerprints": dict(artifact.fingerprints),
    }
    return zlib.compress(pickle.dumps(value, protocol=5))


def _decode_row(row, identity, market_signature):
    (
        stored_market_signature,
        model_key,
        model_version,
        feature_version,
        risk_context_version,
        format_version,
        payload_codec,
        payload_checksum,
        payload,
    ) = row
    expected = (
        market_signature,
        identity.model_key,
        identity.model_version,
        identity.feature_version,
        identity.risk_context_version,
        FORMAT_VERSION,
        PAYLOAD_CODEC,
    )
    actual = (
        stored_market_signature,
        model_key,
        model_version,
        feature_version,
        risk_context_version,
        format_version,
        payload_codec,
    )
    if actual != expected:
        raise ValueError("artifact identity mismatch")
    payload = bytes(payload)
    if _checksum(payload) != payload_checksum:
        raise ValueError("artifact checksum mismatch")
    value = pickle.loads(zlib.decompress(payload))
    if not isinstance(value, dict):
        raise TypeError("artifact payload must be a mapping")
    artifact = ForecastArtifact(
        frame=value["frame"],
        risk_context=value["risk_context"],
        evaluations=value["evaluations"],
        coverage=value["coverage"],
        fingerprints=value["fingerprints"],
    )
    _validate_artifact(artifact)
    return artifact


def _checksum(payload):
    return blake2b(payload, digest_size=32).hexdigest()


def _validate_identity(identity):
    if not isinstance(identity, ForecastArtifactIdentity):
        raise TypeError("identity must be ForecastArtifactIdentity")
    return identity


def _validate_artifact(artifact):
    if not isinstance(artifact, ForecastArtifact):
        raise TypeError("artifact must be ForecastArtifact")
    if not isinstance(artifact.frame, pd.DataFrame):
        raise TypeError("artifact frame must be a DataFrame")
    if not isinstance(artifact.risk_context, pd.DataFrame):
        raise TypeError("artifact risk_context must be a DataFrame")
    for field_name in ("evaluations", "coverage", "fingerprints"):
        if not isinstance(getattr(artifact, field_name), Mapping):
            raise TypeError(f"artifact {field_name} must be a mapping")


def _required_text(value, field_name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value
