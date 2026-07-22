"""Point-in-time expanding-window ridge return forecasts."""

from __future__ import annotations

from collections.abc import Sequence
import math
from numbers import Integral, Real
from types import MappingProxyType

import numpy as np
import pandas as pd

from web.forecasts.base import ForecastResult, SUPPORTED_HORIZONS, UnavailableReason
from web.forecasts.dataset import (
    FEATURE_COLUMNS,
    eligible_training_rows,
    label_end_column,
    target_column,
)


MODEL_KEY = "ridge_direction_v1"
MODEL_VERSION = "v1"
NEUTRAL_BANDS = MappingProxyType({5: 0.01, 20: 0.02, 60: 0.04})


class RidgeForecastProvider:
    """Fit a fresh ridge model for every requested point-in-time cutoff."""

    model_key = MODEL_KEY
    model_version = MODEL_VERSION

    def __init__(
        self,
        frame: pd.DataFrame,
        *,
        alpha: float = 1.0,
        minimum_samples: int = 30,
        feature_columns: Sequence[str] = FEATURE_COLUMNS,
        calibration_history: pd.DataFrame | None = None,
    ):
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("frame must be a DataFrame")
        if (
            not isinstance(frame.index, pd.MultiIndex)
            or tuple(frame.index.names) != ("ticker", "observation_date")
        ):
            raise ValueError(
                "frame index must be a MultiIndex named ticker and observation_date"
            )
        if frame.index.has_duplicates:
            raise ValueError("duplicate (ticker, observation_date) keys are not allowed")
        observation_dates = frame.index.get_level_values("observation_date")
        if not pd.api.types.is_datetime64_any_dtype(observation_dates.dtype):
            raise ValueError("observation_date keys must be datetime values")
        if observation_dates.isna().any():
            raise ValueError("observation_date keys must not be missing")
        if isinstance(alpha, bool) or not isinstance(alpha, Real):
            raise TypeError("alpha must be a real number")
        if not math.isfinite(float(alpha)) or float(alpha) < 0.0:
            raise ValueError("alpha must be finite and not negative")
        if isinstance(minimum_samples, bool) or not isinstance(
            minimum_samples, Integral
        ):
            raise TypeError("minimum_samples must be an integer")
        if int(minimum_samples) <= 0:
            raise ValueError("minimum_samples must be positive")
        columns = tuple(feature_columns)
        if not columns:
            raise ValueError("at least one feature column is required")
        if any(not isinstance(name, str) or not name for name in columns):
            raise ValueError("feature columns must be non-empty strings")
        if len(set(columns)) != len(columns):
            raise ValueError("duplicate feature columns are not allowed")
        missing = [name for name in columns if name not in frame]
        if missing:
            raise ValueError(f"frame is missing feature columns: {missing}")

        self._frame = frame.copy(deep=True)
        self.alpha = float(alpha)
        self.minimum_samples = int(minimum_samples)
        self.feature_columns = columns
        self._calibration_history = _validated_calibration_history(calibration_history)

    def forecast_series(self, ticker, dates, horizons):
        """Return forecasts in requested date-major, horizon-minor order."""
        ticker = _validated_ticker(ticker)
        checked_dates = _validated_dates(dates)
        checked_horizons = _validated_horizons(horizons)
        return [
            self._forecast_one(ticker, asof, horizon)
            for asof in checked_dates
            for horizon in checked_horizons
        ]

    def _forecast_one(self, ticker, asof, horizon):
        key = (ticker, asof)
        if key not in self._frame.index:
            return self._unavailable(
                ticker,
                asof,
                horizon,
                UnavailableReason.INSUFFICIENT_HISTORY,
                0,
                None,
            )

        forecast_row = self._frame.loc[key]
        if isinstance(forecast_row, pd.DataFrame):
            raise ValueError("forecast row ticker/date key must be unique")

        training = eligible_training_rows(self._frame, asof, horizon)
        target_name = target_column(horizon)
        finite_target = np.isfinite(
            pd.to_numeric(training[target_name], errors="coerce").to_numpy(
                dtype=float, copy=False
            )
        )
        training = training.loc[finite_target]
        sample_count = len(training)
        cutoff = (
            None
            if training.empty
            else pd.Timestamp(training[label_end_column(horizon)].max()).normalize()
        )
        if sample_count < self.minimum_samples:
            return self._unavailable(
                ticker,
                asof,
                horizon,
                UnavailableReason.INSUFFICIENT_TRAINING_SAMPLES,
                sample_count,
                cutoff,
            )

        target = training[target_name].to_numpy(dtype=float, copy=True)
        if np.max(target) == np.min(target):
            return self._unavailable(
                ticker,
                asof,
                horizon,
                UnavailableReason.DEGENERATE_TARGET,
                sample_count,
                cutoff,
            )

        try:
            predicted_return = self._fit_predict(training, target, forecast_row)
        except (FloatingPointError, TypeError, ValueError, np.linalg.LinAlgError):
            return self._unavailable(
                ticker,
                asof,
                horizon,
                UnavailableReason.MODEL_ERROR,
                sample_count,
                cutoff,
            )
        if not math.isfinite(predicted_return):
            return self._unavailable(
                ticker,
                asof,
                horizon,
                UnavailableReason.MODEL_ERROR,
                sample_count,
                cutoff,
            )

        calibration = self._calibrated_probability(
            ticker, predicted_return, asof, horizon
        )
        return ForecastResult(
            ticker=ticker,
            asof_date=asof,
            horizon_sessions=horizon,
            direction=direction_for_return(predicted_return, horizon),
            predicted_return=predicted_return,
            up_probability=calibration.up_probability,
            confidence_status=(
                "calibrated"
                if calibration.up_probability is not None
                else "uncalibrated"
            ),
            confidence_reason=calibration.reason,
            training_sample_count=sample_count,
            training_cutoff=cutoff,
            model_key=self.model_key,
            model_version=self.model_version,
            unavailable_reason=None,
        )

    def _calibrated_probability(self, ticker, predicted_return, asof, horizon):
        history = self._calibration_history
        matured = history.loc[
            (history["ticker"] == ticker)
            & (history["horizon_sessions"] == horizon)
            & (history["model_key"] == self.model_key)
            & (history["model_version"] == self.model_version)
            & (history["asof_date"] < asof)
            & (history["label_end_date"] < asof)
        ]
        # Local import avoids coupling the model implementation to evaluation
        # at module-import time.
        from web.forecasts.evaluation import calibrate_up_probability

        calibration = calibrate_up_probability(
            [*matured["predicted_return"], predicted_return],
            matured["actual_return"],
        )
        return calibration

    def _fit_predict(self, training, target, forecast_row):
        raw_training = training.loc[:, self.feature_columns].to_numpy(
            dtype=float, copy=True
        )
        raw_forecast = forecast_row.loc[list(self.feature_columns)].to_numpy(
            dtype=float, copy=True
        )
        raw_training[~np.isfinite(raw_training)] = np.nan
        raw_forecast[~np.isfinite(raw_forecast)] = np.nan

        medians = np.array(
            [
                np.median(column[np.isfinite(column)])
                if np.isfinite(column).any()
                else 0.0
                for column in raw_training.T
            ],
            dtype=float,
        )
        imputed_training = np.where(np.isnan(raw_training), medians, raw_training)
        imputed_forecast = np.where(np.isnan(raw_forecast), medians, raw_forecast)

        with np.errstate(over="raise", divide="raise", invalid="raise"):
            means = imputed_training.mean(axis=0)
            scales = imputed_training.std(axis=0, ddof=0)
            scales[scales == 0.0] = 1.0
            standardized = (imputed_training - means) / scales
            standardized_forecast = (imputed_forecast - means) / scales
            design = np.column_stack((np.ones(len(standardized)), standardized))
            forecast_design = np.concatenate(([1.0], standardized_forecast))
            penalty = np.eye(design.shape[1], dtype=float) * self.alpha
            penalty[0, 0] = 0.0
            lhs = design.T @ design + penalty
            rhs = design.T @ target
            try:
                coefficients = np.linalg.solve(lhs, rhs)
            except np.linalg.LinAlgError:
                coefficients = np.linalg.lstsq(lhs, rhs, rcond=None)[0]
            prediction = forecast_design @ coefficients
        return float(prediction)

    def _unavailable(self, ticker, asof, horizon, reason, sample_count, cutoff):
        return ForecastResult(
            ticker=ticker,
            asof_date=asof,
            horizon_sessions=horizon,
            direction="unavailable",
            predicted_return=None,
            up_probability=None,
            confidence_status="unavailable",
            confidence_reason=None,
            training_sample_count=sample_count,
            training_cutoff=cutoff,
            model_key=self.model_key,
            model_version=self.model_version,
            unavailable_reason=reason,
        )


def direction_for_return(predicted_return, horizon):
    """Map a finite return to the version-one three-class direction policy."""
    checked_horizon = _validated_horizons((horizon,))[0]
    if isinstance(predicted_return, bool) or not isinstance(predicted_return, Real):
        raise TypeError("predicted_return must be a real number")
    value = float(predicted_return)
    if not math.isfinite(value):
        raise ValueError("predicted_return must be finite")
    band = NEUTRAL_BANDS[checked_horizon]
    if value > band:
        return "up"
    if value < -band:
        return "down"
    return "neutral"


def _validated_ticker(value):
    if not isinstance(value, str):
        raise TypeError("ticker must be a string")
    ticker = value.strip()
    if not ticker:
        raise ValueError("ticker must not be empty")
    return ticker


def _validated_dates(values):
    if isinstance(values, (str, bytes)):
        raise TypeError("dates must be a sequence of date-like values")
    try:
        raw_dates = tuple(values)
    except TypeError as exc:
        raise TypeError("dates must be a sequence of date-like values") from exc
    result = []
    for value in raw_dates:
        try:
            timestamp = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("dates must contain valid timestamps") from exc
        if pd.isna(timestamp):
            raise ValueError("dates must contain valid timestamps")
        if timestamp.tz is not None:
            timestamp = timestamp.tz_localize(None)
        result.append(timestamp.normalize())
    if len(set(result)) != len(result):
        raise ValueError("duplicate forecast dates are not allowed")
    return tuple(result)


def _validated_horizons(values):
    try:
        result = tuple(values)
    except TypeError as exc:
        raise TypeError("horizons must be a sequence") from exc
    if not result:
        raise ValueError("at least one horizon is required")
    if any(isinstance(value, bool) or not isinstance(value, Integral) for value in result):
        raise ValueError("forecast horizons must be integers")
    result = tuple(int(value) for value in result)
    if len(set(result)) != len(result):
        raise ValueError("duplicate horizons are not allowed")
    invalid = [value for value in result if value not in SUPPORTED_HORIZONS]
    if invalid:
        raise ValueError(f"unsupported forecast horizons: {invalid}")
    return result


def _validated_calibration_history(history):
    columns = (
        "ticker",
        "asof_date",
        "label_end_date",
        "training_cutoff",
        "horizon_sessions",
        "predicted_return",
        "actual_return",
        "model_key",
        "model_version",
    )
    if history is None:
        return pd.DataFrame(columns=columns)
    if not isinstance(history, pd.DataFrame):
        raise TypeError("calibration_history must be a DataFrame or None")
    missing = [column for column in columns if column not in history]
    if missing:
        raise ValueError(f"calibration_history is missing columns: {missing}")
    result = history.copy(deep=True)
    for column in ("asof_date", "label_end_date", "training_cutoff"):
        try:
            result[column] = pd.to_datetime(result[column])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"calibration_history {column} must contain valid dates"
            ) from exc
        if result[column].isna().any():
            raise ValueError(f"calibration_history {column} must not be missing")
        if result[column].dt.tz is not None:
            result[column] = result[column].dt.tz_localize(None)
        result[column] = result[column].dt.normalize()
    if not (result["asof_date"] < result["label_end_date"]).all():
        raise ValueError("calibration labels must end after their OOS prediction date")
    if not (result["training_cutoff"] < result["asof_date"]).all():
        raise ValueError(
            "calibration training cutoffs must precede their OOS prediction date"
        )
    checked_horizons = []
    for value in result["horizon_sessions"]:
        checked_horizons.append(_validated_horizons((value,))[0])
    result["horizon_sessions"] = checked_horizons
    for column in ("predicted_return", "actual_return"):
        try:
            numeric = pd.to_numeric(result[column], errors="raise").astype(float)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"calibration_history {column} must contain finite numbers"
            ) from exc
        if not np.isfinite(numeric.to_numpy()).all():
            raise ValueError(
                f"calibration_history {column} must contain finite numbers"
            )
        result[column] = numeric
    for column in ("ticker", "model_key", "model_version"):
        if any(not isinstance(value, str) or not value.strip() for value in result[column]):
            raise ValueError(f"calibration_history {column} must be non-empty strings")
        result[column] = result[column].str.strip()
    identity_columns = (
        "ticker",
        "asof_date",
        "horizon_sessions",
        "model_key",
        "model_version",
    )
    if result.duplicated(subset=identity_columns, keep=False).any():
        raise ValueError(
            "duplicate calibration observation identities are not allowed"
        )
    return result.loc[:, columns].sort_values(
        ["label_end_date", "asof_date", "ticker"], kind="mergesort"
    ).reset_index(drop=True)
