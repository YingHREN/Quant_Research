"""Conflict-detecting append-only SQLite ledger for shadow predictions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Optional

import numpy as np
import pandas as pd


PREDICTION_STATUSES = frozenset(("available", "unavailable", "not_applicable"))


@dataclass(frozen=True)
class ShadowExperiment:
    experiment_id: str
    study_version: str
    created_at: str
    frozen_market_asof: str
    universe: tuple[str, ...]
    horizons: tuple[int, ...]
    model_artifact_path: str
    model_artifact_checksum: str
    database_fingerprint: str
    code_commit: str
    status: str
    online_authority: str

    def __post_init__(self):
        for field_name in (
            "experiment_id",
            "study_version",
            "model_artifact_path",
            "code_commit",
            "status",
        ):
            _required_text(getattr(self, field_name), field_name)
        _aware_timestamp(self.created_at, "created_at")
        _iso_date(self.frozen_market_asof, "frozen_market_asof")
        _checksum(self.model_artifact_checksum, "model_artifact_checksum")
        _checksum(self.database_fingerprint, "database_fingerprint")
        if self.online_authority != "none":
            raise ValueError("online_authority must be none")
        universe = tuple(self.universe)
        if (
            not universe
            or len(set(universe)) != len(universe)
            or any(_ticker(value) != value for value in universe)
        ):
            raise ValueError("universe must contain unique normalized tickers")
        horizons = tuple(self.horizons)
        if (
            not horizons
            or len(set(horizons)) != len(horizons)
            or any(
                isinstance(value, bool) or int(value) <= 0
                for value in horizons
            )
        ):
            raise ValueError("horizons must contain unique positive integers")


@dataclass(frozen=True)
class ShadowPrediction:
    experiment_id: str
    specification: str
    ticker: str
    observation_date: str
    horizon: int
    predicted_event: Optional[bool]
    predicted_score: Optional[float]
    status: str
    unavailable_reason: Optional[str]
    group_key: str
    market_regime: str
    model_version: str
    risk_rule_version: str
    feature_version: str
    available_at_close: str
    executable_at: str
    market_signature: str
    recorded_at: str

    def __post_init__(self):
        for field_name in (
            "experiment_id",
            "specification",
            "group_key",
            "market_regime",
            "model_version",
            "risk_rule_version",
            "feature_version",
        ):
            _required_text(getattr(self, field_name), field_name)
        _ticker(self.ticker)
        _iso_date(self.observation_date, "observation_date")
        if isinstance(self.horizon, bool) or int(self.horizon) <= 0:
            raise ValueError("horizon must be positive")
        if self.status not in PREDICTION_STATUSES:
            raise ValueError("prediction status is invalid")
        if self.status == "available":
            if not isinstance(self.predicted_event, (bool, np.bool_)):
                raise ValueError(
                    "available prediction requires predicted_event"
                )
            if self.predicted_score is None or not np.isfinite(
                float(self.predicted_score)
            ):
                raise ValueError(
                    "available prediction requires finite predicted_score"
                )
            if self.unavailable_reason is not None:
                raise ValueError(
                    "available prediction cannot have unavailable_reason"
                )
        else:
            if self.predicted_event is not None or self.predicted_score is not None:
                raise ValueError(
                    "unavailable prediction cannot fabricate output"
                )
            _required_text(self.unavailable_reason, "unavailable_reason")
        _aware_timestamp(self.available_at_close, "available_at_close")
        _aware_timestamp(self.recorded_at, "recorded_at")
        if self.executable_at != "next_session_open":
            raise ValueError("executable_at must be next_session_open")
        _checksum(self.market_signature, "market_signature")


@dataclass(frozen=True)
class ShadowOutcome:
    experiment_id: str
    specification: str
    ticker: str
    observation_date: str
    horizon: int
    entry_date: str
    entry_open: float
    label_end_date: str
    terminal_return: float
    mae: float
    mfe: float
    actual_event: bool
    matured_at: str
    market_signature: str

    def __post_init__(self):
        _required_text(self.experiment_id, "experiment_id")
        _required_text(self.specification, "specification")
        _ticker(self.ticker)
        observation = _iso_date(self.observation_date, "observation_date")
        entry = _iso_date(self.entry_date, "entry_date")
        label_end = _iso_date(self.label_end_date, "label_end_date")
        if not observation < entry <= label_end:
            raise ValueError("outcome dates are not ordered")
        if isinstance(self.horizon, bool) or int(self.horizon) <= 0:
            raise ValueError("horizon must be positive")
        numeric = (
            self.entry_open,
            self.terminal_return,
            self.mae,
            self.mfe,
        )
        if not np.isfinite(np.asarray(numeric, dtype=float)).all():
            raise ValueError("outcome values must be finite")
        if float(self.entry_open) <= 0.0:
            raise ValueError("entry_open must be positive")
        if not isinstance(self.actual_event, (bool, np.bool_)):
            raise ValueError("actual_event must be Boolean")
        _aware_timestamp(self.matured_at, "matured_at")
        _checksum(self.market_signature, "market_signature")


class DownsideShadowStore:
    """Own immutable experiment identity and append-only result rows."""

    def __init__(self, database):
        self.database = Path(database)
        self._lock = RLock()

    def create_experiment(self, experiment):
        if not isinstance(experiment, ShadowExperiment):
            raise TypeError("experiment must be ShadowExperiment")
        payload, checksum = _payload(experiment)
        with self._lock:
            try:
                self.database.parent.mkdir(parents=True, exist_ok=True)
                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    existing = connection.execute(
                        """
                        SELECT payload_checksum
                        FROM shadow_experiments
                        WHERE experiment_id = ?
                        """,
                        (experiment.experiment_id,),
                    ).fetchone()
                    if existing is not None:
                        if existing[0] == checksum:
                            return False
                        raise ValueError("shadow experiment identity conflict")
                    connection.execute(
                        """
                        INSERT INTO shadow_experiments (
                            experiment_id, study_version, created_at,
                            frozen_market_asof, universe_json, horizons_json,
                            model_artifact_path, model_artifact_checksum,
                            database_fingerprint, code_commit, status,
                            online_authority, payload_json, payload_checksum
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            experiment.experiment_id,
                            experiment.study_version,
                            experiment.created_at,
                            experiment.frozen_market_asof,
                            _canonical_json(list(experiment.universe)),
                            _canonical_json(list(experiment.horizons)),
                            experiment.model_artifact_path,
                            experiment.model_artifact_checksum,
                            experiment.database_fingerprint,
                            experiment.code_commit,
                            experiment.status,
                            experiment.online_authority,
                            payload,
                            checksum,
                        ),
                    )
                return True
            except ValueError:
                raise
            except (OSError, sqlite3.Error) as error:
                raise RuntimeError("shadow experiment write failed") from error

    def load_experiment(self, experiment_id):
        checked_id = _required_text(experiment_id, "experiment_id")
        if not self.database.exists():
            return None
        with self._lock:
            try:
                with self._connect() as connection:
                    row = connection.execute(
                        """
                        SELECT payload_json, payload_checksum
                        FROM shadow_experiments
                        WHERE experiment_id = ?
                        """,
                        (checked_id,),
                    ).fetchone()
            except sqlite3.Error as error:
                raise RuntimeError("shadow experiment read failed") from error
        if row is None:
            return None
        if sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
            raise RuntimeError("stored shadow experiment checksum mismatch")
        return _experiment_from_payload(row[0])

    def append_predictions(self, experiment_id, rows):
        experiment = self._required_experiment(experiment_id)
        checked = _validated_prediction_batch(rows, experiment)
        return self._append_rows(
            table="shadow_predictions",
            experiment=experiment,
            rows=checked,
            key_columns=(
                "experiment_id",
                "specification",
                "ticker",
                "observation_date",
                "horizon",
            ),
            insert_columns=(
                "experiment_id",
                "specification",
                "ticker",
                "observation_date",
                "horizon",
                "predicted_event",
                "predicted_score",
                "status",
                "unavailable_reason",
                "group_key",
                "market_regime",
                "model_version",
                "risk_rule_version",
                "feature_version",
                "available_at_close",
                "executable_at",
                "market_signature",
                "recorded_at",
                "payload_json",
                "payload_checksum",
            ),
        )

    def append_outcomes(self, experiment_id, rows):
        experiment = self._required_experiment(experiment_id)
        checked = _validated_outcome_batch(rows, experiment)
        with self._lock:
            try:
                with self._connect() as connection:
                    for item, _payload_json, _payload_checksum in checked:
                        key = _model_key(item)
                        present = connection.execute(
                            """
                            SELECT 1 FROM shadow_predictions
                            WHERE experiment_id = ? AND specification = ?
                              AND ticker = ? AND observation_date = ?
                              AND horizon = ?
                            """,
                            key,
                        ).fetchone()
                        if present is None:
                            raise ValueError(
                                "shadow outcome requires existing prediction"
                            )
            except ValueError:
                raise
            except sqlite3.Error as error:
                raise RuntimeError("shadow outcome read failed") from error
        return self._append_rows(
            table="shadow_outcomes",
            experiment=experiment,
            rows=checked,
            key_columns=(
                "experiment_id",
                "specification",
                "ticker",
                "observation_date",
                "horizon",
            ),
            insert_columns=(
                "experiment_id",
                "specification",
                "ticker",
                "observation_date",
                "horizon",
                "entry_date",
                "entry_open",
                "label_end_date",
                "terminal_return",
                "mae",
                "mfe",
                "actual_event",
                "matured_at",
                "market_signature",
                "payload_json",
                "payload_checksum",
            ),
        )

    def load_predictions(self, experiment_id):
        return self._load_payload_rows("shadow_predictions", experiment_id)

    def load_outcomes(self, experiment_id):
        return self._load_payload_rows("shadow_outcomes", experiment_id)

    def _required_experiment(self, experiment_id):
        checked_id = _required_text(experiment_id, "experiment_id")
        experiment = self.load_experiment(checked_id)
        if experiment is None:
            raise ValueError("shadow experiment does not exist")
        return experiment

    def _append_rows(
        self,
        *,
        table,
        experiment,
        rows,
        key_columns,
        insert_columns,
    ):
        if not rows:
            return 0
        key_placeholders = " AND ".join(
            f"{column} = ?" for column in key_columns
        )
        placeholders = ", ".join("?" for _ in insert_columns)
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(insert_columns)}) "
            f"VALUES ({placeholders})"
        )
        pending = []
        with self._lock:
            try:
                with self._connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    for item, payload_json, payload_checksum in rows:
                        key = _model_key(item)
                        existing = connection.execute(
                            f"""
                            SELECT payload_checksum FROM {table}
                            WHERE {key_placeholders}
                            """,
                            key,
                        ).fetchone()
                        if existing is not None:
                            if existing[0] == payload_checksum:
                                continue
                            raise ValueError(
                                f"{table} append conflict for {key}"
                            )
                        pending.append(
                            _insert_values(
                                item,
                                insert_columns,
                                payload_json,
                                payload_checksum,
                            )
                        )
                    connection.executemany(insert_sql, pending)
                return len(pending)
            except ValueError:
                raise
            except (OSError, sqlite3.Error) as error:
                label = (
                    "shadow prediction"
                    if table == "shadow_predictions"
                    else "shadow outcome"
                )
                raise RuntimeError(f"{label} write failed") from error

    def _load_payload_rows(self, table, experiment_id):
        checked_id = _required_text(experiment_id, "experiment_id")
        if not self.database.exists():
            return pd.DataFrame()
        with self._lock:
            try:
                with self._connect() as connection:
                    rows = connection.execute(
                        f"""
                        SELECT payload_json, payload_checksum FROM {table}
                        WHERE experiment_id = ?
                        ORDER BY observation_date, ticker, horizon,
                                 specification
                        """,
                        (checked_id,),
                    ).fetchall()
            except sqlite3.Error as error:
                raise RuntimeError(f"{table} read failed") from error
        if not rows:
            return pd.DataFrame()
        records = []
        for payload_json, payload_checksum in rows:
            if (
                sha256(payload_json.encode("utf-8")).hexdigest()
                != payload_checksum
            ):
                raise RuntimeError(f"{table} payload checksum mismatch")
            try:
                records.append(json.loads(payload_json))
            except json.JSONDecodeError as error:
                raise RuntimeError(f"{table} payload is invalid") from error
        frame = pd.DataFrame(records)
        if "predicted_event" in frame:
            frame["predicted_event"] = frame["predicted_event"].astype(
                "boolean"
            )
        if "actual_event" in frame:
            frame["actual_event"] = frame["actual_event"].astype("boolean")
        return frame

    def _connect(self):
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        _ensure_schema(connection)
        return connection


def _ensure_schema(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS shadow_experiments (
            experiment_id TEXT PRIMARY KEY,
            study_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            frozen_market_asof TEXT NOT NULL,
            universe_json TEXT NOT NULL,
            horizons_json TEXT NOT NULL,
            model_artifact_path TEXT NOT NULL,
            model_artifact_checksum TEXT NOT NULL,
            database_fingerprint TEXT NOT NULL,
            code_commit TEXT NOT NULL,
            status TEXT NOT NULL,
            online_authority TEXT NOT NULL CHECK (online_authority = 'none'),
            payload_json TEXT NOT NULL,
            payload_checksum TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS shadow_predictions (
            experiment_id TEXT NOT NULL,
            specification TEXT NOT NULL,
            ticker TEXT NOT NULL,
            observation_date TEXT NOT NULL,
            horizon INTEGER NOT NULL,
            predicted_event INTEGER,
            predicted_score REAL,
            status TEXT NOT NULL,
            unavailable_reason TEXT,
            group_key TEXT NOT NULL,
            market_regime TEXT NOT NULL,
            model_version TEXT NOT NULL,
            risk_rule_version TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            available_at_close TEXT NOT NULL,
            executable_at TEXT NOT NULL,
            market_signature TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_checksum TEXT NOT NULL,
            PRIMARY KEY (
                experiment_id, specification, ticker,
                observation_date, horizon
            ),
            FOREIGN KEY (experiment_id)
                REFERENCES shadow_experiments(experiment_id)
        );
        CREATE TABLE IF NOT EXISTS shadow_outcomes (
            experiment_id TEXT NOT NULL,
            specification TEXT NOT NULL,
            ticker TEXT NOT NULL,
            observation_date TEXT NOT NULL,
            horizon INTEGER NOT NULL,
            entry_date TEXT NOT NULL,
            entry_open REAL NOT NULL,
            label_end_date TEXT NOT NULL,
            terminal_return REAL NOT NULL,
            mae REAL NOT NULL,
            mfe REAL NOT NULL,
            actual_event INTEGER NOT NULL,
            matured_at TEXT NOT NULL,
            market_signature TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_checksum TEXT NOT NULL,
            PRIMARY KEY (
                experiment_id, specification, ticker,
                observation_date, horizon
            ),
            FOREIGN KEY (
                experiment_id, specification, ticker,
                observation_date, horizon
            ) REFERENCES shadow_predictions (
                experiment_id, specification, ticker,
                observation_date, horizon
            )
        );
        """
    )


def _validated_prediction_batch(rows, experiment):
    checked = []
    seen = {}
    cutoff = experiment.frozen_market_asof
    universe = set(experiment.universe)
    horizons = set(experiment.horizons)
    for item in tuple(rows):
        if not isinstance(item, ShadowPrediction):
            raise TypeError("prediction rows must be ShadowPrediction")
        if item.experiment_id != experiment.experiment_id:
            raise ValueError("prediction experiment_id mismatch")
        if item.ticker not in universe:
            raise ValueError("prediction ticker is outside frozen universe")
        if item.horizon not in horizons:
            raise ValueError("prediction horizon is outside frozen horizons")
        if item.observation_date <= cutoff:
            raise ValueError(
                "prediction observation_date must be strictly after freeze"
            )
        payload_json, payload_checksum = _payload(item)
        key = _model_key(item)
        previous = seen.get(key)
        if previous is not None and previous != payload_checksum:
            raise ValueError("prediction batch contains conflicting keys")
        if previous is None:
            checked.append((item, payload_json, payload_checksum))
            seen[key] = payload_checksum
    return checked


def _validated_outcome_batch(rows, experiment):
    checked = []
    seen = {}
    universe = set(experiment.universe)
    horizons = set(experiment.horizons)
    for item in tuple(rows):
        if not isinstance(item, ShadowOutcome):
            raise TypeError("outcome rows must be ShadowOutcome")
        if item.experiment_id != experiment.experiment_id:
            raise ValueError("outcome experiment_id mismatch")
        if item.ticker not in universe:
            raise ValueError("outcome ticker is outside frozen universe")
        if item.horizon not in horizons:
            raise ValueError("outcome horizon is outside frozen horizons")
        payload_json, payload_checksum = _payload(item)
        key = _model_key(item)
        previous = seen.get(key)
        if previous is not None and previous != payload_checksum:
            raise ValueError("outcome batch contains conflicting keys")
        if previous is None:
            checked.append((item, payload_json, payload_checksum))
            seen[key] = payload_checksum
    return checked


def _insert_values(item, columns, payload_json, payload_checksum):
    values = []
    for column in columns:
        if column == "payload_json":
            value = payload_json
        elif column == "payload_checksum":
            value = payload_checksum
        else:
            value = getattr(item, column)
        if isinstance(value, (bool, np.bool_)):
            value = int(value)
        values.append(value)
    return tuple(values)


def _model_key(item):
    return (
        item.experiment_id,
        item.specification,
        item.ticker,
        item.observation_date,
        int(item.horizon),
    )


def _payload(item):
    payload_json = _canonical_json(asdict(item))
    return payload_json, sha256(payload_json.encode("utf-8")).hexdigest()


def _canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _experiment_from_payload(payload_json):
    try:
        payload = json.loads(payload_json)
        payload["universe"] = tuple(payload["universe"])
        payload["horizons"] = tuple(payload["horizons"])
        return ShadowExperiment(**payload)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError("stored shadow experiment is invalid") from error


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _ticker(value):
    checked = _required_text(value, "ticker").upper()
    if checked != value or len(checked) > 10:
        raise ValueError("ticker must be normalized")
    return checked


def _iso_date(value, label):
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a valid date") from error
    if pd.isna(timestamp) or timestamp.tz is not None:
        raise ValueError(f"{label} must be a timezone-naive date")
    normalized = timestamp.normalize().date().isoformat()
    if normalized != value:
        raise ValueError(f"{label} must be an ISO date")
    return normalized


def _aware_timestamp(value, label):
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a valid timestamp") from error
    if pd.isna(timestamp) or timestamp.tz is None:
        raise ValueError(f"{label} must include a timezone")
    return timestamp


def _checksum(value, label):
    checked = _required_text(value, label).lower()
    if len(checked) != 64 or any(
        character not in "0123456789abcdef" for character in checked
    ):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return checked
