"""Immutable SQLite artifact storage for unified benchmark predictions."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd

from research.benchmark_cache_codec import (
    CACHE_CODEC,
    decode_frame_bundle,
    encode_frame_bundle,
)


_ALLOWED_STAGES = {"statistical_predictions", "rule_predictions"}
_RULE_SPECIFICATIONS = {
    "immediate_8",
    "memory_12",
    "toprisk_confirmed",
    "toprisk_stateful",
    "ridge_plus_toprisk",
}
_PREDICTION_COLUMNS = {
    "ticker",
    "observation_date",
    "horizon",
    "fold",
    "predicted_event",
    "predicted_score",
    "model_version",
}
_HEX_DIGEST_LENGTH = 64
_STATUS_COLUMNS = [
    "artifact_key",
    "study_version",
    "stage",
    "created_at",
    "database_fingerprint",
    "assignment_fingerprint",
    "config_fingerprint",
    "code_fingerprint",
    "dependency_artifact_key",
    "schema_version",
    "payload_codec",
    "payload_size_bytes",
    "row_count",
    "payload_checksum",
]
_PRUNE_COLUMNS = [
    "artifact_key",
    "stage",
    "created_at",
    "row_count",
    "payload_size_bytes",
    "would_delete",
    "deleted",
]


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HEX_DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True)
class BenchmarkCacheIdentity:
    study_version: str
    stage: str
    database_fingerprint: str
    assignment_fingerprint: str
    config_fingerprint: str
    code_fingerprint: str
    dependency_artifact_key: Optional[str]
    schema_version: str

    def __post_init__(self) -> None:
        for field_name in ("study_version", "schema_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError("{} must be nonblank".format(field_name))
        if self.stage not in _ALLOWED_STAGES:
            raise ValueError("stage is not cacheable")
        for field_name in (
            "database_fingerprint",
            "assignment_fingerprint",
            "config_fingerprint",
            "code_fingerprint",
        ):
            if not _is_sha256(getattr(self, field_name)):
                raise ValueError("{} must be a SHA-256 fingerprint".format(field_name))
        if self.stage == "statistical_predictions":
            if self.dependency_artifact_key is not None:
                raise ValueError(
                    "statistical_predictions forbids a dependency artifact key"
                )
        elif not _is_sha256(self.dependency_artifact_key):
            raise ValueError("rule_predictions requires a dependency artifact key")

    def as_dict(self) -> dict[str, Any]:
        return {
            "assignment_fingerprint": self.assignment_fingerprint,
            "code_fingerprint": self.code_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "database_fingerprint": self.database_fingerprint,
            "dependency_artifact_key": self.dependency_artifact_key,
            "schema_version": self.schema_version,
            "stage": self.stage,
            "study_version": self.study_version,
        }

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.as_dict())

    @property
    def artifact_key(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BenchmarkCacheArtifact:
    identity: BenchmarkCacheIdentity
    payload: bytes
    payload_checksum: str
    row_count: int
    payload_size_bytes: int
    payload_codec: str = CACHE_CODEC

    @classmethod
    def from_frames(
        cls,
        identity: BenchmarkCacheIdentity,
        frames: Mapping[str, pd.DataFrame],
    ) -> "BenchmarkCacheArtifact":
        payload, checksum, row_count = encode_frame_bundle(frames)
        return cls(
            identity=identity,
            payload=payload,
            payload_checksum=checksum,
            row_count=row_count,
            payload_size_bytes=len(payload),
        )


@dataclass(frozen=True)
class BenchmarkCacheRead:
    status: str
    frames: Optional[dict[str, pd.DataFrame]] = None
    reason: Optional[str] = None
    artifact_key: Optional[str] = None


class UnifiedBenchmarkCacheStore:
    def __init__(
        self,
        database: Path,
        *,
        maximum_uncompressed_bytes: int = 1_000_000_000,
    ) -> None:
        self.database = Path(database)
        if (
            not isinstance(maximum_uncompressed_bytes, int)
            or isinstance(maximum_uncompressed_bytes, bool)
            or maximum_uncompressed_bytes <= 0
        ):
            raise ValueError("maximum_uncompressed_bytes must be positive")
        self.maximum_uncompressed_bytes = maximum_uncompressed_bytes

    def _connect(self) -> sqlite3.Connection:
        try:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(str(self.database), timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_cache_artifacts (
                    artifact_key TEXT PRIMARY KEY,
                    identity_json TEXT NOT NULL,
                    study_version TEXT NOT NULL,
                    stage TEXT NOT NULL CHECK (
                        stage IN (
                            'statistical_predictions',
                            'rule_predictions'
                        )
                    ),
                    created_at TEXT NOT NULL,
                    database_fingerprint TEXT NOT NULL,
                    assignment_fingerprint TEXT NOT NULL,
                    config_fingerprint TEXT NOT NULL,
                    code_fingerprint TEXT NOT NULL,
                    dependency_artifact_key TEXT,
                    schema_version TEXT NOT NULL,
                    payload_codec TEXT NOT NULL,
                    payload_size_bytes INTEGER NOT NULL CHECK (
                        payload_size_bytes >= 0
                    ),
                    row_count INTEGER NOT NULL CHECK (row_count >= 0),
                    payload_checksum TEXT NOT NULL,
                    payload BLOB NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    benchmark_cache_artifacts_stage_created
                ON benchmark_cache_artifacts (
                    stage,
                    created_at DESC,
                    artifact_key DESC
                )
                """
            )
            return connection
        except (OSError, sqlite3.Error) as error:
            raise RuntimeError("benchmark cache database is unavailable") from error

    @staticmethod
    def _identity_from_row(row: sqlite3.Row) -> BenchmarkCacheIdentity:
        try:
            raw_identity = json.loads(row["identity_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("identity JSON is invalid") from error
        if not isinstance(raw_identity, dict):
            raise ValueError("identity JSON is invalid")
        try:
            identity = BenchmarkCacheIdentity(**raw_identity)
        except (TypeError, ValueError) as error:
            raise ValueError("identity fields are invalid") from error
        if row["identity_json"] != identity.canonical_json:
            raise ValueError("identity JSON is not canonical")
        stored_identity = {
            "study_version": row["study_version"],
            "stage": row["stage"],
            "database_fingerprint": row["database_fingerprint"],
            "assignment_fingerprint": row["assignment_fingerprint"],
            "config_fingerprint": row["config_fingerprint"],
            "code_fingerprint": row["code_fingerprint"],
            "dependency_artifact_key": row["dependency_artifact_key"],
            "schema_version": row["schema_version"],
        }
        if stored_identity != identity.as_dict():
            raise ValueError("identity columns do not match identity JSON")
        if row["artifact_key"] != identity.artifact_key:
            raise ValueError("artifact key does not match identity")
        return identity

    def _decode_row(
        self,
        row: sqlite3.Row,
        *,
        expected_identity: Optional[BenchmarkCacheIdentity] = None,
    ) -> dict[str, pd.DataFrame]:
        identity = self._identity_from_row(row)
        if (
            expected_identity is not None
            and identity.artifact_key != expected_identity.artifact_key
        ):
            raise ValueError("artifact identity mismatch")
        if row["payload_codec"] != CACHE_CODEC:
            raise ValueError("payload codec is unsupported")
        payload = row["payload"]
        if not isinstance(payload, bytes):
            raise ValueError("payload is not binary")
        if row["payload_size_bytes"] != len(payload):
            raise ValueError("payload size mismatch")
        if not _is_sha256(row["payload_checksum"]):
            raise ValueError("payload checksum is invalid")
        if hashlib.sha256(payload).hexdigest() != row["payload_checksum"]:
            raise ValueError("payload checksum mismatch")
        frames = decode_frame_bundle(
            payload,
            row["payload_checksum"],
            maximum_uncompressed_bytes=self.maximum_uncompressed_bytes,
        )
        row_count = sum(len(frame) for frame in frames.values())
        if (
            not isinstance(row["row_count"], int)
            or row["row_count"] < 0
            or row_count != row["row_count"]
        ):
            raise ValueError("row count mismatch")
        self._validate_stage_bundle(identity, frames)
        return frames

    @staticmethod
    def _validate_stage_bundle(
        identity: BenchmarkCacheIdentity,
        frames: Mapping[str, pd.DataFrame],
    ) -> None:
        names = set(frames)
        if identity.stage == "statistical_predictions":
            if "ridge_down" not in names:
                raise ValueError("statistical stage requires ridge_down")
        elif not _RULE_SPECIFICATIONS.issubset(names):
            raise ValueError("rule stage is missing required specifications")
        for name, frame in frames.items():
            if frame.empty:
                raise ValueError("{} prediction frame is empty".format(name))
            missing = _PREDICTION_COLUMNS.difference(frame.columns)
            if missing:
                raise ValueError(
                    "{} prediction frame is missing columns".format(name)
                )
            keys = ["ticker", "observation_date", "horizon", "fold"]
            if frame.duplicated(keys).any():
                raise ValueError("{} prediction keys are duplicated".format(name))
            tickers = frame["ticker"].astype("string")
            if tickers.isna().any() or tickers.str.strip().eq("").any():
                raise ValueError("{} tickers are invalid".format(name))
            dates = pd.to_datetime(frame["observation_date"], errors="coerce")
            if dates.isna().any() or getattr(dates.dt, "tz", None) is not None:
                raise ValueError("{} observation dates are invalid".format(name))
            horizons = pd.to_numeric(frame["horizon"], errors="coerce")
            folds = pd.to_numeric(frame["fold"], errors="coerce")
            if (
                horizons.isna().any()
                or (horizons <= 0).any()
                or folds.isna().any()
                or (folds < 1).any()
            ):
                raise ValueError("{} horizons or folds are invalid".format(name))
            try:
                frame["predicted_event"].astype("boolean")
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "{} predicted events are invalid".format(name)
                ) from error
            scores = pd.to_numeric(frame["predicted_score"], errors="coerce")
            invalid_scores = frame["predicted_score"].notna() & scores.isna()
            if invalid_scores.any():
                raise ValueError("{} predicted scores are invalid".format(name))
            versions = frame["model_version"].astype("string")
            if versions.isna().any() or versions.str.strip().eq("").any():
                raise ValueError("{} model versions are invalid".format(name))

    def _validate_artifact(
        self, artifact: BenchmarkCacheArtifact
    ) -> dict[str, pd.DataFrame]:
        if not isinstance(artifact, BenchmarkCacheArtifact):
            raise TypeError("cache artifacts must be BenchmarkCacheArtifact values")
        if artifact.payload_codec != CACHE_CODEC:
            raise ValueError("artifact payload codec is unsupported")
        if not isinstance(artifact.payload, bytes):
            raise ValueError("artifact payload must be bytes")
        if artifact.payload_size_bytes != len(artifact.payload):
            raise ValueError("artifact payload size is invalid")
        if (
            not _is_sha256(artifact.payload_checksum)
            or hashlib.sha256(artifact.payload).hexdigest()
            != artifact.payload_checksum
        ):
            raise ValueError("artifact payload checksum is invalid")
        try:
            frames = decode_frame_bundle(
                artifact.payload,
                artifact.payload_checksum,
                maximum_uncompressed_bytes=self.maximum_uncompressed_bytes,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("artifact payload is invalid") from error
        if (
            not isinstance(artifact.row_count, int)
            or isinstance(artifact.row_count, bool)
            or artifact.row_count < 0
            or sum(len(frame) for frame in frames.values())
            != artifact.row_count
        ):
            raise ValueError("artifact row count is invalid")
        try:
            self._validate_stage_bundle(artifact.identity, frames)
        except ValueError as error:
            raise ValueError("artifact stage semantics are invalid") from error
        return frames

    def read(self, identity: BenchmarkCacheIdentity) -> BenchmarkCacheRead:
        if not isinstance(identity, BenchmarkCacheIdentity):
            raise TypeError("identity must be a BenchmarkCacheIdentity")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT *
                    FROM benchmark_cache_artifacts
                    WHERE artifact_key = ?
                    """,
                    (identity.artifact_key,),
                ).fetchone()
        except sqlite3.Error as error:
            raise RuntimeError("benchmark cache read failed") from error
        if row is None:
            return BenchmarkCacheRead(
                status="miss",
                artifact_key=identity.artifact_key,
            )
        try:
            frames = self._decode_row(row, expected_identity=identity)
        except (TypeError, ValueError) as error:
            return BenchmarkCacheRead(
                status="miss_corrupt",
                reason=str(error),
                artifact_key=identity.artifact_key,
            )
        return BenchmarkCacheRead(
            status="hit",
            frames=frames,
            artifact_key=identity.artifact_key,
        )

    def commit(
        self,
        artifacts,
        *,
        repair_corrupt: bool = False,
    ) -> int:
        if not isinstance(repair_corrupt, bool):
            raise TypeError("repair_corrupt must be Boolean")
        unique = {}
        for artifact in artifacts:
            self._validate_artifact(artifact)
            key = artifact.identity.artifact_key
            previous = unique.get(key)
            if previous is not None and previous != artifact:
                raise ValueError("cache artifact conflict in pending transaction")
            unique[key] = artifact
        if not unique:
            return 0

        connection = self._connect()
        inserted = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            actions = []
            for key, artifact in unique.items():
                existing = connection.execute(
                    """
                    SELECT *
                    FROM benchmark_cache_artifacts
                    WHERE artifact_key = ?
                    """,
                    (key,),
                ).fetchone()
                if existing is None:
                    actions.append(("insert", artifact))
                    continue
                try:
                    self._decode_row(
                        existing,
                        expected_identity=artifact.identity,
                    )
                    existing_valid = True
                except (TypeError, ValueError):
                    existing_valid = False
                if existing_valid:
                    if (
                        existing["payload_checksum"] == artifact.payload_checksum
                        and existing["payload_size_bytes"]
                        == artifact.payload_size_bytes
                        and existing["row_count"] == artifact.row_count
                        and existing["payload_codec"] == artifact.payload_codec
                    ):
                        continue
                    raise ValueError("cache artifact conflict with immutable row")
                if not repair_corrupt:
                    raise ValueError("cache artifact conflict with corrupt row")
                actions.append(("repair", artifact))

            for action, artifact in actions:
                if action == "repair":
                    connection.execute(
                        """
                        DELETE FROM benchmark_cache_artifacts
                        WHERE artifact_key = ?
                        """,
                        (artifact.identity.artifact_key,),
                    )
                self._insert(connection, artifact)
                inserted += 1
            connection.commit()
        except (TypeError, ValueError):
            connection.rollback()
            raise
        except sqlite3.Error as error:
            connection.rollback()
            raise RuntimeError("benchmark cache commit failed") from error
        finally:
            connection.close()
        return inserted

    @staticmethod
    def _insert(
        connection: sqlite3.Connection,
        artifact: BenchmarkCacheArtifact,
    ) -> None:
        identity = artifact.identity
        connection.execute(
            """
            INSERT INTO benchmark_cache_artifacts (
                artifact_key,
                identity_json,
                study_version,
                stage,
                created_at,
                database_fingerprint,
                assignment_fingerprint,
                config_fingerprint,
                code_fingerprint,
                dependency_artifact_key,
                schema_version,
                payload_codec,
                payload_size_bytes,
                row_count,
                payload_checksum,
                payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                identity.artifact_key,
                identity.canonical_json,
                identity.study_version,
                identity.stage,
                datetime.now(timezone.utc).isoformat(),
                identity.database_fingerprint,
                identity.assignment_fingerprint,
                identity.config_fingerprint,
                identity.code_fingerprint,
                identity.dependency_artifact_key,
                identity.schema_version,
                artifact.payload_codec,
                artifact.payload_size_bytes,
                artifact.row_count,
                artifact.payload_checksum,
                artifact.payload,
            ),
        )

    def status(self) -> pd.DataFrame:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        artifact_key,
                        study_version,
                        stage,
                        created_at,
                        database_fingerprint,
                        assignment_fingerprint,
                        config_fingerprint,
                        code_fingerprint,
                        dependency_artifact_key,
                        schema_version,
                        payload_codec,
                        payload_size_bytes,
                        row_count,
                        payload_checksum
                    FROM benchmark_cache_artifacts
                    ORDER BY created_at DESC, artifact_key DESC
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise RuntimeError("benchmark cache status failed") from error
        return pd.DataFrame([dict(row) for row in rows], columns=_STATUS_COLUMNS)

    def verify(self) -> pd.DataFrame:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT *
                    FROM benchmark_cache_artifacts
                    ORDER BY created_at DESC, artifact_key DESC
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise RuntimeError("benchmark cache verification failed") from error
        results = []
        for row in rows:
            try:
                self._decode_row(row)
                status = "valid"
                reason = None
            except (TypeError, ValueError) as error:
                status = "corrupt"
                reason = str(error)
            results.append(
                {
                    "artifact_key": row["artifact_key"],
                    "stage": row["stage"],
                    "status": status,
                    "reason": reason,
                }
            )
        return pd.DataFrame(
            results,
            columns=["artifact_key", "stage", "status", "reason"],
        )

    def prune(
        self,
        keep_per_stage: int,
        apply: bool = False,
    ) -> pd.DataFrame:
        if (
            not isinstance(keep_per_stage, int)
            or isinstance(keep_per_stage, bool)
            or keep_per_stage <= 0
        ):
            raise ValueError("keep_per_stage must be a positive integer")
        if not isinstance(apply, bool):
            raise TypeError("apply must be Boolean")
        try:
            connection = self._connect()
            rows = connection.execute(
                """
                SELECT
                    artifact_key,
                    stage,
                    created_at,
                    row_count,
                    payload_size_bytes
                FROM benchmark_cache_artifacts
                ORDER BY stage ASC, created_at DESC, artifact_key DESC
                """
            ).fetchall()
            stage_counts = {}
            candidates = []
            for row in rows:
                rank = stage_counts.get(row["stage"], 0)
                stage_counts[row["stage"]] = rank + 1
                candidates.append(
                    {
                        "artifact_key": row["artifact_key"],
                        "stage": row["stage"],
                        "created_at": row["created_at"],
                        "row_count": row["row_count"],
                        "payload_size_bytes": row["payload_size_bytes"],
                        "would_delete": rank >= keep_per_stage,
                        "deleted": False,
                    }
                )
            if apply:
                connection.execute("BEGIN IMMEDIATE")
                for candidate in candidates:
                    if candidate["would_delete"]:
                        cursor = connection.execute(
                            """
                            DELETE FROM benchmark_cache_artifacts
                            WHERE artifact_key = ?
                            """,
                            (candidate["artifact_key"],),
                        )
                        candidate["deleted"] = cursor.rowcount == 1
                connection.commit()
            connection.close()
        except sqlite3.Error as error:
            try:
                connection.rollback()
                connection.close()
            except (NameError, sqlite3.Error):
                pass
            raise RuntimeError("benchmark cache prune failed") from error
        return pd.DataFrame(candidates, columns=_PRUNE_COLUMNS)
