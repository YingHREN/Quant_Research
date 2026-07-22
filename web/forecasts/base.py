"""Immutable, JSON-safe contracts shared by forecast providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import math
from numbers import Integral, Real
from types import MappingProxyType
from typing import Mapping

import pandas as pd

from web.contracts import iso_date, json_safe


SUPPORTED_HORIZONS = (5, 20, 60)
FORECAST_DIRECTIONS = frozenset(("up", "neutral", "down", "unavailable"))
CONFIDENCE_STATUSES = frozenset(("calibrated", "uncalibrated", "unavailable"))


class UnavailableReason(str, Enum):
    """Stable machine-readable forecast failure categories."""

    INSUFFICIENT_HISTORY = "insufficient_history"
    INSUFFICIENT_TRAINING_SAMPLES = "insufficient_training_samples"
    DEGENERATE_TARGET = "degenerate_target"
    MODEL_ERROR = "model_error"


@dataclass(frozen=True)
class ForecastResult:
    """One provider result for a ticker, date, and forecast horizon."""

    ticker: str
    asof_date: object
    horizon_sessions: int
    direction: str
    predicted_return: float | None
    up_probability: float | None
    confidence_status: str
    training_sample_count: int
    training_cutoff: object | None
    model_key: str
    model_version: str
    unavailable_reason: UnavailableReason | str | None = None

    def __post_init__(self):
        ticker = _required_string(self.ticker, "ticker")
        model_key = _required_string(self.model_key, "model_key")
        model_version = _required_string(self.model_version, "model_version")
        _validate_horizon(self.horizon_sessions)
        if self.direction not in FORECAST_DIRECTIONS:
            raise ValueError(f"invalid forecast direction: {self.direction}")
        if self.confidence_status not in CONFIDENCE_STATUSES:
            raise ValueError(f"invalid confidence_status: {self.confidence_status}")
        if isinstance(self.training_sample_count, bool) or not isinstance(
            self.training_sample_count, Integral
        ):
            raise TypeError("training_sample_count must be an integer")
        if int(self.training_sample_count) < 0:
            raise ValueError("training_sample_count must not be negative")
        predicted_return = _optional_number(self.predicted_return, "predicted_return")
        up_probability = _optional_number(self.up_probability, "up_probability")
        _validate_probability(up_probability)
        asof_date = _optional_date(self.asof_date, "asof_date")
        training_cutoff = _optional_date(self.training_cutoff, "training_cutoff")
        reason = _normalize_reason(self.unavailable_reason)
        if asof_date is None:
            raise ValueError("forecasts require asof_date")
        if int(self.training_sample_count) > 0 and training_cutoff is None:
            raise ValueError("positive training_sample_count requires training_cutoff")
        if training_cutoff is not None:
            if training_cutoff >= asof_date:
                raise ValueError("training_cutoff must be strictly before asof_date")
        if self.direction == "unavailable" and reason is None:
            raise ValueError("unavailable forecasts require unavailable_reason")
        if self.direction != "unavailable" and reason is not None:
            raise ValueError("available forecasts cannot have unavailable_reason")
        if self.direction == "unavailable":
            if predicted_return is not None or up_probability is not None:
                raise ValueError("unavailable forecasts cannot contain predictions")
            if self.confidence_status != "unavailable":
                raise ValueError("unavailable forecasts require unavailable confidence")
        else:
            if predicted_return is None:
                raise ValueError("available forecasts require a finite predicted_return")
            if int(self.training_sample_count) <= 0:
                raise ValueError("available forecasts require training samples")
            if self.confidence_status == "unavailable":
                raise ValueError("available forecasts cannot have unavailable confidence")
            if self.confidence_status == "calibrated" and up_probability is None:
                raise ValueError("calibrated forecasts require up_probability")
            if self.confidence_status != "calibrated" and up_probability is not None:
                raise ValueError("up_probability requires calibrated confidence")
        object.__setattr__(self, "ticker", ticker)
        object.__setattr__(self, "model_key", model_key)
        object.__setattr__(self, "model_version", model_version)
        object.__setattr__(self, "horizon_sessions", int(self.horizon_sessions))
        object.__setattr__(self, "training_sample_count", int(self.training_sample_count))
        object.__setattr__(self, "predicted_return", predicted_return)
        object.__setattr__(self, "up_probability", up_probability)
        object.__setattr__(self, "asof_date", asof_date)
        object.__setattr__(self, "training_cutoff", training_cutoff)
        object.__setattr__(self, "unavailable_reason", reason)

    def to_dict(self):
        """Return a fresh JSON-safe representation of this result."""
        return {
            "ticker": self.ticker,
            "asof_date": iso_date(self.asof_date),
            "horizon_sessions": self.horizon_sessions,
            "direction": self.direction,
            "predicted_return": json_safe(self.predicted_return),
            "up_probability": json_safe(self.up_probability),
            "confidence_status": self.confidence_status,
            "training_sample_count": self.training_sample_count,
            "training_cutoff": iso_date(self.training_cutoff),
            "model_key": self.model_key,
            "model_version": self.model_version,
            "unavailable_reason": (
                None
                if self.unavailable_reason is None
                else self.unavailable_reason.value
            ),
        }


@dataclass(frozen=True)
class ForecastEvaluation:
    """Walk-forward evidence summary for one model and horizon."""

    horizon_sessions: int
    sample_count: int
    coverage: float | None
    mae: float | None
    rmse: float | None
    direction_accuracy: float | None
    zero_return_mae: float | None
    historical_mean_mae: float | None
    rank_ic: float | None
    signal_bucket_returns: Mapping[str, float | None] = field(default_factory=dict)
    evaluation_start: object | None = None
    evaluation_end: object | None = None
    model_key: str = ""
    model_version: str = ""
    unavailable_reason: UnavailableReason | str | None = None

    def __post_init__(self):
        model_key = _required_string(self.model_key, "model_key")
        model_version = _required_string(self.model_version, "model_version")
        _validate_horizon(self.horizon_sessions)
        if isinstance(self.sample_count, bool) or not isinstance(
            self.sample_count, Integral
        ):
            raise TypeError("sample_count must be an integer")
        if int(self.sample_count) < 0:
            raise ValueError("sample_count must not be negative")
        reason = _normalize_reason(self.unavailable_reason)
        if not isinstance(self.signal_bucket_returns, Mapping):
            raise TypeError("signal_bucket_returns must be a mapping")
        buckets = MappingProxyType(
            {
                _required_string(key, "signal bucket key"): _optional_number(
                    value, f"signal_bucket_returns[{key!r}]"
                )
                for key, value in self.signal_bucket_returns.items()
            }
        )
        metrics = {
            name: _optional_number(getattr(self, name), name)
            for name in (
                "coverage",
                "mae",
                "rmse",
                "direction_accuracy",
                "zero_return_mae",
                "historical_mean_mae",
                "rank_ic",
            )
        }
        evaluation_start = _optional_date(self.evaluation_start, "evaluation_start")
        evaluation_end = _optional_date(self.evaluation_end, "evaluation_end")
        _validate_evaluation_metrics(metrics)
        _validate_evaluation_dates(
            int(self.sample_count), evaluation_start, evaluation_end
        )
        required_metrics = (
            "coverage",
            "mae",
            "rmse",
            "direction_accuracy",
            "zero_return_mae",
            "historical_mean_mae",
        )
        if reason is None:
            if int(self.sample_count) <= 0:
                raise ValueError("available evaluations require samples")
            missing = [name for name in required_metrics if metrics[name] is None]
            if missing:
                raise ValueError(
                    f"unavailable evaluations require unavailable_reason; missing metrics: {missing}"
                )
        elif any(value is not None for value in metrics.values()) or buckets:
            raise ValueError("unavailable evaluations cannot contain metrics")

        object.__setattr__(self, "model_key", model_key)
        object.__setattr__(self, "model_version", model_version)
        object.__setattr__(self, "horizon_sessions", int(self.horizon_sessions))
        object.__setattr__(self, "sample_count", int(self.sample_count))
        for name, value in metrics.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "evaluation_start", evaluation_start)
        object.__setattr__(self, "evaluation_end", evaluation_end)
        object.__setattr__(self, "signal_bucket_returns", buckets)
        object.__setattr__(self, "unavailable_reason", reason)

    def to_dict(self):
        """Return a fresh JSON-safe representation of the evaluation evidence."""
        return {
            "horizon_sessions": self.horizon_sessions,
            "sample_count": self.sample_count,
            "coverage": json_safe(self.coverage),
            "mae": json_safe(self.mae),
            "rmse": json_safe(self.rmse),
            "direction_accuracy": json_safe(self.direction_accuracy),
            "zero_return_mae": json_safe(self.zero_return_mae),
            "historical_mean_mae": json_safe(self.historical_mean_mae),
            "rank_ic": json_safe(self.rank_ic),
            "signal_bucket_returns": json_safe(dict(self.signal_bucket_returns)),
            "evaluation_start": iso_date(self.evaluation_start),
            "evaluation_end": iso_date(self.evaluation_end),
            "model_key": self.model_key,
            "model_version": self.model_version,
            "unavailable_reason": (
                None
                if self.unavailable_reason is None
                else self.unavailable_reason.value
            ),
        }


def _validate_horizon(value):
    if not isinstance(value, Integral) or int(value) not in SUPPORTED_HORIZONS:
        raise ValueError(f"horizon_sessions must be one of {SUPPORTED_HORIZONS}")


def _validate_probability(value):
    if value is None:
        return
    if not isinstance(value, Real) or not math.isfinite(float(value)):
        raise ValueError("up_probability must be finite or None")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError("up_probability must be between zero and one")


def _optional_number(value, field_name):
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a real number or None")
    number = float(value)
    return number if math.isfinite(number) else None


def _optional_date(value, field_name):
    if value is None or value is pd.NaT:
        return None
    if not isinstance(value, (str, date, datetime, pd.Timestamp)):
        try:
            is_numpy_datetime = pd.api.types.is_datetime64_dtype(type(value))
        except TypeError:
            is_numpy_datetime = False
        if not is_numpy_datetime:
            raise TypeError(f"{field_name} must be date-like or None")
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be date-like or None") from exc
    if pd.isna(timestamp):
        return None
    if timestamp.tz is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _required_string(value, field_name):
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _validate_evaluation_metrics(metrics):
    for name in ("mae", "rmse", "zero_return_mae", "historical_mean_mae"):
        value = metrics[name]
        if value is not None and value < 0.0:
            raise ValueError(f"{name} must not be negative")
    for name in ("coverage", "direction_accuracy"):
        value = metrics[name]
        if value is not None and not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between zero and one")
    rank_ic = metrics["rank_ic"]
    if rank_ic is not None and not -1.0 <= rank_ic <= 1.0:
        raise ValueError("rank_ic must be between negative one and one")


def _validate_evaluation_dates(sample_count, evaluation_start, evaluation_end):
    if (evaluation_start is None) != (evaluation_end is None):
        raise ValueError("evaluation_start and evaluation_end must be provided together")
    if sample_count > 0 and evaluation_start is None:
        raise ValueError("positive sample_count requires an evaluation date range")
    if sample_count == 0 and evaluation_start is not None:
        raise ValueError("zero sample_count cannot have an evaluation date range")
    if evaluation_start is not None and evaluation_start > evaluation_end:
        raise ValueError("evaluation_start must not be after evaluation_end")


def _normalize_reason(value):
    if value is None:
        return None
    try:
        return value if isinstance(value, UnavailableReason) else UnavailableReason(value)
    except ValueError as exc:
        raise ValueError(f"invalid unavailable_reason: {value}") from exc
