"""Leakage-safe walk-forward metrics and empirical probability calibration."""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Integral, Real

import numpy as np
import pandas as pd

from web.forecasts.base import (
    EvaluationUnavailableReason,
    ForecastEvaluation,
    ForecastResult,
    SUPPORTED_HORIZONS,
    UnavailableReason,
)
from web.forecasts.dataset import eligible_training_rows, label_end_column, target_column
from web.forecasts.ridge import direction_for_return


CROSS_SECTION_MINIMUM = 5
SIGNAL_BUCKETS = ("down", "neutral", "up")
CALIBRATION_MINIMUM_SAMPLES = 100
EVIDENCE_MINIMUM_SAMPLES = 100
CALIBRATION_INSUFFICIENT_SAMPLES = "insufficient_calibration_samples"
CALIBRATION_INSUFFICIENT_CLASSES = "calibration_requires_both_classes"


@dataclass(frozen=True)
class CalibrationResult:
    """Result of calibrating one current prediction from earlier OOS rows."""

    up_probability: float | None
    reason: str | None
    sample_count: int


def walk_forward_evaluate(frame, horizon, provider):
    """Evaluate one provider/horizon on realized rows in chronological order.

    Coverage is available forecasts divided by all structurally valid rows with
    a finite realized target. All error metrics and both baselines use exactly
    the available-forecast rows. The historical-mean baseline is recomputed at
    every observation with the same strict label-end boundary as the live
    model. Rank IC is the mean per-date Spearman correlation; neither it nor
    signal buckets uses a date with fewer than ``CROSS_SECTION_MINIMUM`` names.
    """
    checked_horizon = _validate_horizon(horizon)
    model_key, model_version = _provider_identity(provider)
    candidates = _evaluation_candidates(frame, checked_horizon)
    if candidates.empty:
        return _unavailable_evaluation(
            checked_horizon,
            model_key,
            model_version,
            UnavailableReason.INSUFFICIENT_HISTORY,
        )

    observations = []
    unavailable_reasons = []
    for (ticker, asof), row in candidates.iterrows():
        # This call is intentional even though the provider independently uses
        # the same helper: it gives the historical baseline the identical purge
        # boundary and keeps that boundary explicit in evaluation code.
        training = eligible_training_rows(frame, asof, checked_horizon)
        training_values = pd.to_numeric(
            training[target_column(checked_horizon)], errors="coerce"
        ).to_numpy(dtype=float, copy=False)
        training_values = training_values[np.isfinite(training_values)]

        results = provider.forecast_series(ticker, (asof,), (checked_horizon,))
        if not isinstance(results, (list, tuple)) or len(results) != 1:
            raise ValueError("provider must return exactly one forecast per evaluation row")
        forecast = results[0]
        _validate_forecast_alignment(
            forecast, ticker, asof, checked_horizon, model_key, model_version
        )
        if forecast.predicted_return is None:
            unavailable_reasons.append(forecast.unavailable_reason)
            continue
        if training_values.size == 0:
            raise ValueError(
                "available forecast has no point-in-time eligible historical-mean baseline"
            )
        observations.append(
            {
                "ticker": ticker,
                "asof_date": asof,
                "prediction": forecast.predicted_return,
                "actual": float(row[target_column(checked_horizon)]),
                "predicted_direction": forecast.direction,
                "actual_direction": direction_for_return(
                    float(row[target_column(checked_horizon)]), checked_horizon
                ),
                "historical_mean": float(np.mean(training_values)),
            }
        )

    if not observations:
        reason = (
            unavailable_reasons[0]
            if len(set(unavailable_reasons)) == 1
            else EvaluationUnavailableReason.NO_AVAILABLE_FORECASTS
        )
        return _unavailable_evaluation(
            checked_horizon,
            model_key,
            model_version,
            reason,
            candidates=candidates,
        )

    evaluated = pd.DataFrame(observations)
    prediction = evaluated["prediction"].to_numpy(dtype=float)
    actual = evaluated["actual"].to_numpy(dtype=float)
    residual = prediction - actual
    historical_mean = evaluated["historical_mean"].to_numpy(dtype=float)
    adequate = _adequate_cross_sections(evaluated)
    direction_accuracy = float(
        np.mean(evaluated["predicted_direction"] == evaluated["actual_direction"])
    )
    always_up_accuracy = float(np.mean(evaluated["actual_direction"] == "up"))
    balanced_accuracy, macro_f1 = _classification_metrics(evaluated)
    non_overlapping = _non_overlapping_rows(evaluated, checked_horizon)
    non_overlapping_accuracy = float(
        np.mean(
            non_overlapping["predicted_direction"]
            == non_overlapping["actual_direction"]
        )
    )
    mae = float(np.mean(np.abs(residual)))
    zero_return_mae = float(np.mean(np.abs(actual)))
    historical_mean_mae = float(np.mean(np.abs(historical_mean - actual)))
    if len(evaluated) < EVIDENCE_MINIMUM_SAMPLES:
        evidence_status = "insufficient"
    elif (
        mae < zero_return_mae
        and mae < historical_mean_mae
        and direction_accuracy > always_up_accuracy
    ):
        evidence_status = "proven"
    else:
        evidence_status = "unproven"

    return ForecastEvaluation(
        horizon_sessions=checked_horizon,
        sample_count=len(evaluated),
        coverage=len(evaluated) / len(candidates),
        mae=mae,
        rmse=float(math.sqrt(float(np.mean(residual**2)))),
        direction_accuracy=direction_accuracy,
        zero_return_mae=zero_return_mae,
        historical_mean_mae=historical_mean_mae,
        always_up_direction_accuracy=always_up_accuracy,
        balanced_accuracy=balanced_accuracy,
        macro_f1=macro_f1,
        non_overlapping_sample_count=len(non_overlapping),
        non_overlapping_direction_accuracy=non_overlapping_accuracy,
        evidence_status=evidence_status,
        rank_ic=_mean_cross_sectional_rank_ic(adequate),
        signal_bucket_returns=_signal_bucket_returns(adequate),
        evaluation_start=evaluated["asof_date"].min(),
        evaluation_end=evaluated["asof_date"].max(),
        model_key=model_key,
        model_version=model_version,
    )


def calibrate_up_probability(predictions, actuals, horizon, minimum_samples=100):
    """Calibrate the last prediction from strictly earlier OOS prediction rows.

    ``predictions`` contains the current query as its final value. ``actuals``
    may contain one fewer value, or a same-length final value that is ignored.
    This API shape makes it impossible for the current outcome to affect its
    own probability. Earlier non-finite pairs are excluded from the calibration
    sample count. The positive class is the horizon-specific ``up`` direction,
    not merely a return above zero. An empirical isotonic fit is used only after
    the sample and both-class gates pass.
    """
    checked_horizon = _validate_horizon(horizon)
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, Integral):
        raise TypeError("minimum_samples must be an integer")
    minimum_samples = int(minimum_samples)
    if minimum_samples < CALIBRATION_MINIMUM_SAMPLES:
        raise ValueError(
            f"minimum_samples must be at least {CALIBRATION_MINIMUM_SAMPLES}"
        )
    prediction_values = _sequence(predictions, "predictions")
    actual_values = _sequence(actuals, "actuals")
    if not prediction_values:
        raise ValueError("predictions must include a current prediction")
    if len(actual_values) == len(prediction_values):
        historical_predictions = prediction_values[:-1]
        historical_actuals = actual_values[:-1]
    elif len(actual_values) == len(prediction_values) - 1:
        historical_predictions = prediction_values[:-1]
        historical_actuals = actual_values
    else:
        raise ValueError(
            "actuals must align with predictions or omit the current outcome"
        )
    query = _finite_number(prediction_values[-1], "current prediction")

    clean_predictions = []
    outcomes = []
    for raw_prediction, raw_actual in zip(
        historical_predictions, historical_actuals
    ):
        pair = _finite_pair(raw_prediction, raw_actual)
        if pair is None:
            continue
        prediction, actual = pair
        clean_predictions.append(prediction)
        outcomes.append(
            1.0
            if direction_for_return(actual, checked_horizon) == "up"
            else 0.0
        )

    sample_count = len(clean_predictions)
    if sample_count < minimum_samples:
        return CalibrationResult(
            None, CALIBRATION_INSUFFICIENT_SAMPLES, sample_count
        )
    if min(outcomes) == max(outcomes):
        return CalibrationResult(
            None, CALIBRATION_INSUFFICIENT_CLASSES, sample_count
        )
    probability = _isotonic_empirical_probability(
        np.asarray(clean_predictions, dtype=float),
        np.asarray(outcomes, dtype=float),
        query,
    )
    return CalibrationResult(float(probability), None, sample_count)


def _evaluation_candidates(frame, horizon):
    # eligible_training_rows supplies the canonical frame/label validation.
    index_dates = frame.index.get_level_values("observation_date")
    validation_asof = (
        pd.Timestamp("2100-01-01")
        if frame.empty
        else pd.Timestamp(index_dates.max()) + pd.Timedelta(days=1)
    )
    eligible_training_rows(frame, validation_asof, horizon)
    target = pd.to_numeric(frame[target_column(horizon)], errors="coerce")
    label_end = frame[label_end_column(horizon)]
    observation_date = pd.Series(index_dates, index=frame.index)
    mask = (
        np.isfinite(target.to_numpy(dtype=float, copy=False))
        & label_end.notna().to_numpy()
        & (observation_date < label_end).to_numpy()
    )
    return frame.loc[mask].sort_index(level=("observation_date", "ticker"))


def _adequate_cross_sections(evaluated):
    groups = []
    for _, group in evaluated.groupby("asof_date", sort=True):
        if len(group) >= CROSS_SECTION_MINIMUM:
            groups.append(group)
    return groups


def _classification_metrics(evaluated):
    actual = evaluated["actual_direction"].astype(str)
    predicted = evaluated["predicted_direction"].astype(str)
    recalls = []
    f1_values = []
    for label in SIGNAL_BUCKETS:
        actual_label = actual == label
        predicted_label = predicted == label
        true_positive = int((actual_label & predicted_label).sum())
        false_positive = int((~actual_label & predicted_label).sum())
        false_negative = int((actual_label & ~predicted_label).sum())
        if actual_label.any():
            recalls.append(true_positive / int(actual_label.sum()))
        denominator = 2 * true_positive + false_positive + false_negative
        if actual_label.any() or predicted_label.any():
            f1_values.append(
                0.0 if denominator == 0 else 2 * true_positive / denominator
            )
    return float(np.mean(recalls)), float(np.mean(f1_values))


def _non_overlapping_rows(evaluated, horizon):
    session_ordinals = {
        date: position
        for position, date in enumerate(sorted(evaluated["asof_date"].unique()))
    }
    selected = []
    for _, group in evaluated.groupby("ticker", sort=False):
        last_ordinal = None
        for index, row in group.sort_values("asof_date").iterrows():
            ordinal = session_ordinals[row["asof_date"]]
            if last_ordinal is None or ordinal - last_ordinal >= horizon:
                selected.append(index)
                last_ordinal = ordinal
    return evaluated.loc[selected]


def _mean_cross_sectional_rank_ic(groups):
    correlations = []
    for group in groups:
        predicted_rank = group["prediction"].rank(method="average")
        actual_rank = group["actual"].rank(method="average")
        if predicted_rank.nunique() < 2 or actual_rank.nunique() < 2:
            continue
        correlation = predicted_rank.corr(actual_rank)
        if pd.notna(correlation):
            correlations.append(float(correlation))
    return None if not correlations else float(np.mean(correlations))


def _signal_bucket_returns(groups):
    if not groups:
        return {bucket: None for bucket in SIGNAL_BUCKETS}
    adequate = pd.concat(groups, ignore_index=True)
    return {
        bucket: (
            None
            if adequate.loc[adequate["predicted_direction"] == bucket].empty
            else float(
                adequate.loc[
                    adequate["predicted_direction"] == bucket, "actual"
                ].mean()
            )
        )
        for bucket in SIGNAL_BUCKETS
    }


def _unavailable_evaluation(
    horizon, model_key, model_version, reason, *, candidates=None
):
    has_candidates = candidates is not None and not candidates.empty
    candidate_dates = (
        candidates.index.get_level_values("observation_date")
        if has_candidates
        else None
    )
    return ForecastEvaluation(
        horizon_sessions=horizon,
        sample_count=0,
        coverage=0.0 if has_candidates else None,
        mae=None,
        rmse=None,
        direction_accuracy=None,
        zero_return_mae=None,
        historical_mean_mae=None,
        rank_ic=None,
        signal_bucket_returns={},
        evaluation_start=None if not has_candidates else candidate_dates.min(),
        evaluation_end=None if not has_candidates else candidate_dates.max(),
        model_key=model_key,
        model_version=model_version,
        unavailable_reason=reason,
        evidence_status="insufficient",
    )


def _validate_horizon(value):
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError("horizon must be a supported integer")
    horizon = int(value)
    if horizon not in SUPPORTED_HORIZONS:
        raise ValueError(f"unsupported forecast horizon: {horizon}")
    return horizon


def _provider_identity(provider):
    try:
        model_key = provider.model_key
        model_version = provider.model_version
        method = provider.forecast_series
    except AttributeError as exc:
        raise TypeError("provider must expose model identity and forecast_series") from exc
    if not callable(method):
        raise TypeError("provider forecast_series must be callable")
    if not isinstance(model_key, str) or not model_key.strip():
        raise ValueError("provider model_key must be a non-empty string")
    if not isinstance(model_version, str) or not model_version.strip():
        raise ValueError("provider model_version must be a non-empty string")
    return model_key.strip(), model_version.strip()


def _validate_forecast_alignment(
    forecast, ticker, asof, horizon, model_key, model_version
):
    if not isinstance(forecast, ForecastResult):
        raise TypeError("provider results must be ForecastResult instances")
    if (
        forecast.ticker != ticker
        or forecast.asof_date != pd.Timestamp(asof).normalize()
        or forecast.horizon_sessions != horizon
    ):
        raise ValueError("provider returned a forecast for the wrong row or horizon")
    if forecast.model_key != model_key or forecast.model_version != model_version:
        raise ValueError("provider result model identity does not match provider")


def _sequence(values, field_name):
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{field_name} must be a sequence")
    try:
        return tuple(values)
    except TypeError as exc:
        raise TypeError(f"{field_name} must be a sequence") from exc


def _finite_number(value, field_name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a finite real number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite real number")
    return number


def _finite_pair(prediction, actual):
    if prediction is None or actual is None or prediction is pd.NA or actual is pd.NA:
        return None
    if isinstance(prediction, bool) or isinstance(actual, bool):
        return None
    if not isinstance(prediction, Real) or not isinstance(actual, Real):
        return None
    prediction = float(prediction)
    actual = float(actual)
    if not math.isfinite(prediction) or not math.isfinite(actual):
        return None
    return prediction, actual


def _isotonic_empirical_probability(predictions, outcomes, query):
    order = np.argsort(predictions, kind="mergesort")
    sorted_predictions = predictions[order]
    sorted_outcomes = outcomes[order]
    unique_predictions, first = np.unique(sorted_predictions, return_index=True)
    counts = np.diff(np.append(first, len(sorted_predictions))).astype(float)
    sums = np.add.reduceat(sorted_outcomes, first)

    blocks = []
    for prediction, total, count in zip(unique_predictions, sums, counts):
        blocks.append([float(prediction), float(total), float(count)])
        while (
            len(blocks) >= 2
            and blocks[-2][1] / blocks[-2][2] > blocks[-1][1] / blocks[-1][2]
        ):
            right = blocks.pop()
            left = blocks.pop()
            blocks.append(
                [right[0], left[1] + right[1], left[2] + right[2]]
            )

    upper_bounds = np.asarray([block[0] for block in blocks], dtype=float)
    levels = np.asarray([block[1] / block[2] for block in blocks], dtype=float)
    position = int(np.searchsorted(upper_bounds, query, side="left"))
    position = min(position, len(levels) - 1)
    return float(np.clip(levels[position], 0.0, 1.0))
