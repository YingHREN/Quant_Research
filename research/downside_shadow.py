"""Immutable model artifacts for prospective downside shadow evaluation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge


MODEL_SCHEMA_VERSION = "downside-shadow-model-v1"
FEATURE_ABS_CAP = 1.0e12
LOGIT_ABS_CAP = 35.0
MODEL_KINDS = frozenset(("ridge", "direction_logistic", "binary_logistic"))


@dataclass(frozen=True)
class FrozenLinearArtifact:
    """Serializable preprocessing and coefficients for one frozen model."""

    specification: str
    horizon: int
    model_kind: str
    feature_columns: tuple[str, ...]
    imputation_values: tuple[float, ...]
    centers: tuple[float, ...]
    scales: tuple[float, ...]
    coefficients: tuple[tuple[float, ...], ...]
    intercepts: tuple[float, ...]
    classes: tuple[str, ...]
    event_threshold: float
    neutral_band: float
    training_samples: int
    training_event_rate: Optional[float]
    training_label_end_max: str
    frozen_market_asof: str
    model_version: str

    def __post_init__(self):
        _required_text(self.specification, "specification")
        _required_text(self.model_version, "model_version")
        if self.model_kind not in MODEL_KINDS:
            raise ValueError("model_kind is invalid")
        if isinstance(self.horizon, bool) or int(self.horizon) <= 0:
            raise ValueError("horizon must be positive")
        if (
            isinstance(self.training_samples, bool)
            or int(self.training_samples) <= 0
        ):
            raise ValueError("training_samples must be positive")
        features = tuple(self.feature_columns)
        if not features or any(not str(column).strip() for column in features):
            raise ValueError("feature_columns must not be empty")
        if len(set(features)) != len(features):
            raise ValueError("feature_columns must be unique")
        width = len(features)
        vectors = (
            self.imputation_values,
            self.centers,
            self.scales,
        )
        if any(len(vector) != width for vector in vectors):
            raise ValueError("artifact parameter shape mismatch")
        if not self.coefficients or any(
            len(row) != width for row in self.coefficients
        ):
            raise ValueError("artifact coefficient shape mismatch")
        if len(self.intercepts) != len(self.coefficients):
            raise ValueError("artifact intercept shape mismatch")
        numeric = [
            *self.imputation_values,
            *self.centers,
            *self.scales,
            *(value for row in self.coefficients for value in row),
            *self.intercepts,
            self.event_threshold,
            self.neutral_band,
        ]
        if not np.isfinite(np.asarray(numeric, dtype=float)).all():
            raise ValueError("artifact parameters must be finite")
        if any(float(scale) <= 0.0 for scale in self.scales):
            raise ValueError("artifact scales must be positive")
        if not 0.0 <= float(self.event_threshold) <= 1.0:
            raise ValueError("event_threshold must be between zero and one")
        if float(self.neutral_band) < 0.0:
            raise ValueError("neutral_band must be nonnegative")
        cutoff = _iso_date(self.frozen_market_asof, "frozen_market_asof")
        label_end = _iso_date(
            self.training_label_end_max,
            "training_label_end_max",
        )
        if label_end > cutoff:
            raise ValueError("training label end exceeds frozen cutoff")
        if self.model_kind == "ridge":
            if len(self.coefficients) != 1 or self.classes:
                raise ValueError("ridge artifact class shape mismatch")
        elif self.model_kind == "binary_logistic":
            if len(self.coefficients) != 1 or self.classes != ("0", "1"):
                raise ValueError("binary Logistic artifact class shape mismatch")
        else:
            if (
                len(self.coefficients) != len(self.classes)
                or set(self.classes) != {"down", "neutral", "up"}
            ):
                raise ValueError(
                    "direction Logistic artifact class shape mismatch"
                )
        if self.training_event_rate is not None and not (
            np.isfinite(float(self.training_event_rate))
            and 0.0 <= float(self.training_event_rate) <= 1.0
        ):
            raise ValueError("training_event_rate must be a finite fraction")


def fit_frozen_ridge(
    frame,
    *,
    feature_columns,
    target_column,
    label_end_column,
    frozen_market_asof,
    specification,
    horizon,
    neutral_band=0.0,
):
    """Fit one Ridge artifact using labels observable by the frozen cutoff."""
    selected, design, state = _training_design(
        frame,
        feature_columns,
        target_column,
        label_end_column,
        frozen_market_asof,
    )
    target = pd.to_numeric(selected[target_column], errors="coerce").to_numpy(
        dtype=float
    )
    if not np.isfinite(target).all() or np.min(target) == np.max(target):
        raise ValueError("Ridge target must be finite and nondegenerate")
    model = Ridge(alpha=1.0, solver="lsqr")
    model.fit(design, target)
    return _artifact(
        specification=specification,
        horizon=horizon,
        model_kind="ridge",
        feature_columns=feature_columns,
        state=state,
        coefficients=(tuple(float(value) for value in model.coef_),),
        intercepts=(float(model.intercept_),),
        classes=(),
        neutral_band=neutral_band,
        selected=selected,
        label_end_column=label_end_column,
        frozen_market_asof=frozen_market_asof,
        model_version="shadow-ridge-v1",
        training_event_rate=None,
    )


def fit_frozen_direction_logistic(
    frame,
    *,
    feature_columns,
    target_column,
    label_end_column,
    frozen_market_asof,
    specification,
    horizon,
    neutral_band,
):
    """Fit the frozen three-class direction challenger."""
    selected, design, state = _training_design(
        frame,
        feature_columns,
        target_column,
        label_end_column,
        frozen_market_asof,
    )
    returns = pd.to_numeric(
        selected[target_column],
        errors="coerce",
    ).to_numpy(dtype=float)
    labels = _directions(returns, neutral_band)
    if set(labels) != {"down", "neutral", "up"}:
        raise ValueError("direction target requires down, neutral, and up")
    model = LogisticRegression(
        class_weight="balanced",
        max_iter=1_000,
        random_state=0,
        solver="liblinear",
    )
    model.fit(design, labels)
    classes = tuple(str(value) for value in model.classes_)
    return _artifact(
        specification=specification,
        horizon=horizon,
        model_kind="direction_logistic",
        feature_columns=feature_columns,
        state=state,
        coefficients=tuple(
            tuple(float(value) for value in row)
            for row in model.coef_
        ),
        intercepts=tuple(float(value) for value in model.intercept_),
        classes=classes,
        neutral_band=neutral_band,
        selected=selected,
        label_end_column=label_end_column,
        frozen_market_asof=frozen_market_asof,
        model_version="shadow-direction-logistic-v1",
        training_event_rate=float(np.mean(labels == "down")),
    )


def fit_frozen_binary_logistic(
    frame,
    *,
    feature_columns,
    target_column,
    label_end_column,
    frozen_market_asof,
    specification,
    horizon,
):
    """Fit the frozen binary downside-path specialist."""
    selected, design, state = _training_design(
        frame,
        feature_columns,
        target_column,
        label_end_column,
        frozen_market_asof,
    )
    numeric = pd.to_numeric(
        selected[target_column],
        errors="coerce",
    ).to_numpy(dtype=float)
    if not np.isfinite(numeric).all() or not set(np.unique(numeric)).issubset(
        {0.0, 1.0}
    ):
        raise ValueError("binary target must contain only zero and one")
    target = numeric.astype(int)
    if set(np.unique(target)) != {0, 1}:
        raise ValueError("binary target requires both classes")
    model = LogisticRegression(
        C=0.1,
        class_weight="balanced",
        max_iter=1_000,
        random_state=0,
        solver="liblinear",
    )
    model.fit(design, target)
    return _artifact(
        specification=specification,
        horizon=horizon,
        model_kind="binary_logistic",
        feature_columns=feature_columns,
        state=state,
        coefficients=tuple(
            tuple(float(value) for value in row)
            for row in model.coef_
        ),
        intercepts=tuple(float(value) for value in model.intercept_),
        classes=("0", "1"),
        neutral_band=0.0,
        selected=selected,
        label_end_column=label_end_column,
        frozen_market_asof=frozen_market_asof,
        model_version="shadow-pressure-logistic-v1",
        training_event_rate=float(np.mean(target)),
    )


def predict_frozen_linear(artifact, frame):
    """Apply one frozen artifact without fitting or mutating source rows."""
    if not isinstance(artifact, FrozenLinearArtifact):
        raise TypeError("artifact must be FrozenLinearArtifact")
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    missing = [
        column for column in artifact.feature_columns if column not in frame
    ]
    if missing:
        raise ValueError(f"frame is missing features: {missing}")
    design = _apply_preprocessing(artifact, frame)
    result = pd.DataFrame(index=frame.index.copy())
    if artifact.model_kind == "ridge":
        predicted_return = (
            design @ np.asarray(artifact.coefficients[0], dtype=float)
            + float(artifact.intercepts[0])
        )
        direction = _directions(predicted_return, artifact.neutral_band)
        result["predicted_event"] = direction == "down"
        result["predicted_score"] = -predicted_return
        result["predicted_direction"] = direction
        result["predicted_return"] = predicted_return
        return result
    logits = (
        design @ np.asarray(artifact.coefficients, dtype=float).T
        + np.asarray(artifact.intercepts, dtype=float)
    )
    logits = np.clip(logits, -LOGIT_ABS_CAP, LOGIT_ABS_CAP)
    if artifact.model_kind == "binary_logistic":
        score = 1.0 / (1.0 + np.exp(-logits[:, 0]))
        event = score >= artifact.event_threshold
        result["predicted_event"] = event
        result["predicted_score"] = score
        result["predicted_direction"] = np.where(event, "down", "not_down")
        result["predicted_return"] = np.nan
        return result
    probabilities = _ovr_probabilities(logits)
    classes = np.asarray(artifact.classes, dtype=object)
    predicted_direction = classes[np.argmax(probabilities, axis=1)]
    down_position = artifact.classes.index("down")
    result["predicted_event"] = predicted_direction == "down"
    result["predicted_score"] = probabilities[:, down_position]
    result["predicted_direction"] = predicted_direction
    result["predicted_return"] = np.nan
    return result


def write_shadow_model_bundle(path, bundle):
    """Atomically write one canonical, checksummed shadow model bundle."""
    if not isinstance(bundle, Mapping):
        raise TypeError("bundle must be a mapping")
    payload = dict(bundle)
    if payload.get("schema_version") != MODEL_SCHEMA_VERSION:
        raise ValueError("model bundle schema_version is invalid")
    if payload.get("online_authority") != "none":
        raise ValueError("model bundle online_authority must be none")
    models = payload.get("models")
    if not isinstance(models, (list, tuple)) or not models:
        raise ValueError("model bundle requires models")
    payload["models"] = [
        asdict(model) if isinstance(model, FrozenLinearArtifact) else model
        for model in models
    ]
    encoded = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    checksum = sha256(encoded).hexdigest()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_bytes(encoded)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return checksum


def read_shadow_model_bundle(path, expected_checksum=None):
    """Read and fully validate one immutable model bundle."""
    source = Path(path)
    encoded = source.read_bytes()
    checksum = sha256(encoded).hexdigest()
    if expected_checksum is not None and checksum != str(expected_checksum):
        raise ValueError("model bundle checksum mismatch")
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("model bundle is invalid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("model bundle must be an object")
    if payload.get("schema_version") != MODEL_SCHEMA_VERSION:
        raise ValueError("model bundle schema_version is invalid")
    if payload.get("online_authority") != "none":
        raise ValueError("model bundle online_authority must be none")
    models = payload.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("model bundle requires models")
    try:
        payload["models"] = [
            FrozenLinearArtifact(
                **{
                    **model,
                    "feature_columns": tuple(model["feature_columns"]),
                    "imputation_values": tuple(model["imputation_values"]),
                    "centers": tuple(model["centers"]),
                    "scales": tuple(model["scales"]),
                    "coefficients": tuple(
                        tuple(row) for row in model["coefficients"]
                    ),
                    "intercepts": tuple(model["intercepts"]),
                    "classes": tuple(model["classes"]),
                }
            )
            for model in models
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("model bundle artifact schema is invalid") from error
    payload["checksum"] = checksum
    return payload


def _training_design(
    frame,
    feature_columns,
    target_column,
    label_end_column,
    frozen_market_asof,
):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    features = _feature_columns(feature_columns)
    missing = [
        column
        for column in (*features, target_column, label_end_column)
        if column not in frame
    ]
    if missing:
        raise ValueError(f"training frame is missing columns: {missing}")
    cutoff = pd.Timestamp(
        _iso_date(frozen_market_asof, "frozen_market_asof")
    )
    label_ends = pd.to_datetime(frame[label_end_column], errors="coerce")
    target = pd.to_numeric(frame[target_column], errors="coerce")
    eligible = label_ends.notna() & (label_ends <= cutoff) & target.notna()
    selected = frame.loc[eligible].copy(deep=True)
    if selected.empty:
        raise ValueError("no training rows are observable by frozen cutoff")
    numeric = _numeric_features(selected, features)
    medians = numeric.median(axis=0, skipna=True)
    if medians.isna().any():
        raise ValueError("training features require finite medians")
    imputed = numeric.fillna(medians)
    centers = imputed.mean(axis=0)
    scales = imputed.std(axis=0, ddof=0)
    scales = scales.where(scales > 1.0e-12, 1.0)
    design = ((imputed - centers) / scales).to_numpy(dtype=float)
    if not np.isfinite(design).all():
        raise ValueError("training design must be finite")
    state = {
        "imputation_values": tuple(float(value) for value in medians),
        "centers": tuple(float(value) for value in centers),
        "scales": tuple(float(value) for value in scales),
    }
    return selected, design, state


def _artifact(
    *,
    specification,
    horizon,
    model_kind,
    feature_columns,
    state,
    coefficients,
    intercepts,
    classes,
    neutral_band,
    selected,
    label_end_column,
    frozen_market_asof,
    model_version,
    training_event_rate,
):
    label_end = pd.to_datetime(selected[label_end_column], errors="raise").max()
    return FrozenLinearArtifact(
        specification=str(specification),
        horizon=int(horizon),
        model_kind=model_kind,
        feature_columns=_feature_columns(feature_columns),
        imputation_values=state["imputation_values"],
        centers=state["centers"],
        scales=state["scales"],
        coefficients=tuple(tuple(row) for row in coefficients),
        intercepts=tuple(intercepts),
        classes=tuple(classes),
        event_threshold=0.5,
        neutral_band=float(neutral_band),
        training_samples=int(len(selected)),
        training_event_rate=training_event_rate,
        training_label_end_max=label_end.date().isoformat(),
        frozen_market_asof=_iso_date(
            frozen_market_asof,
            "frozen_market_asof",
        ),
        model_version=model_version,
    )


def _apply_preprocessing(artifact, frame):
    numeric = _numeric_features(frame, artifact.feature_columns)
    medians = pd.Series(
        artifact.imputation_values,
        index=artifact.feature_columns,
    )
    centers = pd.Series(artifact.centers, index=artifact.feature_columns)
    scales = pd.Series(artifact.scales, index=artifact.feature_columns)
    return ((numeric.fillna(medians) - centers) / scales).to_numpy(dtype=float)


def _numeric_features(frame, features):
    numeric = frame.loc[:, features].apply(pd.to_numeric, errors="coerce")
    return numeric.replace([np.inf, -np.inf], np.nan).clip(
        -FEATURE_ABS_CAP,
        FEATURE_ABS_CAP,
    )


def _feature_columns(columns: Sequence[str]):
    values = tuple(str(column).strip() for column in columns)
    if not values or any(not value for value in values):
        raise ValueError("feature_columns must not be empty")
    if len(set(values)) != len(values):
        raise ValueError("feature_columns must be unique")
    return values


def _directions(values, neutral_band):
    numeric = np.asarray(values, dtype=float)
    band = float(neutral_band)
    if not np.isfinite(numeric).all() or not np.isfinite(band) or band < 0.0:
        raise ValueError("direction inputs must be finite")
    return np.where(
        numeric < -band,
        "down",
        np.where(numeric > band, "up", "neutral"),
    )


def _ovr_probabilities(logits):
    positive = 1.0 / (1.0 + np.exp(-logits))
    return positive / positive.sum(axis=1, keepdims=True)


def _required_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _iso_date(value, label):
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a valid date") from error
    if pd.isna(timestamp) or timestamp.tz is not None:
        raise ValueError(f"{label} must be a timezone-naive date")
    return timestamp.normalize().date().isoformat()
