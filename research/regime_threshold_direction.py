"""Leakage-safe helpers for the offline regime-threshold direction study."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import pandas as pd

from research.market_direction_model import (
    attach_next_open_targets,
    direction_labels,
)


HORIZON = 5
DIRECTION_CLASSES = ("down", "neutral", "up")
REGIME_PRIOR_STRENGTH = 1_000.0
DOWN_THRESHOLDS = (0.40, 0.50, 0.60, 0.70)


class RegimeThresholdDataUnavailable(RuntimeError):
    """Raised when required aligned research inputs are unavailable."""


@dataclass(frozen=True)
class RegimePriors:
    """Immutable global and market-regime direction priors."""

    classes: tuple
    global_values: tuple
    regime_values: object

    @property
    def global_prior(self):
        return np.asarray(self.global_values, dtype=float)

    def regime_prior(self, regime):
        if regime is None:
            return self.global_prior
        values = self.regime_values.get(str(regime).strip())
        if values is None:
            return self.global_prior
        return np.asarray(values, dtype=float)


@dataclass(frozen=True)
class EconomicThresholdResult:
    """Immutable result of training-only economic threshold selection."""

    threshold: object
    status: str
    reason: object
    diagnostics: tuple


def threshold_directions(
    probabilities,
    threshold,
    classes=DIRECTION_CLASSES,
):
    """Apply an economic down boundary and preserve neutral/up ordering."""
    checked_classes = tuple(map(str, classes))
    if checked_classes != DIRECTION_CLASSES:
        raise ValueError(
            "classes must be ordered as down, neutral, up"
        )
    values = np.asarray(probabilities, dtype=float).copy()
    checked_threshold = float(threshold)
    if (
        values.ndim != 2
        or values.shape[1] != len(checked_classes)
        or not np.isfinite(values).all()
        or np.any(values < 0.0)
        or np.any(values > 1.0)
        or not np.allclose(values.sum(axis=1), 1.0, atol=1e-6)
    ):
        raise ValueError("probabilities must be finite normalized rows")
    if not np.isfinite(checked_threshold) or not 0.0 < checked_threshold < 1.0:
        raise ValueError("threshold must be a probability")
    return np.where(
        values[:, 0] >= checked_threshold,
        checked_classes[0],
        np.where(
            values[:, 2] > values[:, 1],
            checked_classes[2],
            checked_classes[1],
        ),
    )


def select_economic_down_threshold(
    oof_predictions,
    thresholds=DOWN_THRESHOLDS,
    minimum_rows=500,
    minimum_coverage=0.05,
    precision_gain=0.02,
):
    """Select a down threshold from inner OOF predictions only.

    A candidate must have enough predicted-down rows and coverage, improve
    down precision over the frozen 0.50 reference, and produce a negative
    mean realized return.  No outer-fold outcome is accepted by this API.
    """
    required = {
        "actual_direction",
        "actual_return",
        "down_probability",
        "neutral_probability",
        "up_probability",
    }
    if not isinstance(oof_predictions, pd.DataFrame):
        raise TypeError("oof_predictions must be a DataFrame")
    missing = sorted(required.difference(oof_predictions.columns))
    if missing:
        raise ValueError(
            "oof_predictions missing required columns: "
            + ", ".join(missing)
        )
    checked_thresholds = tuple(float(value) for value in thresholds)
    if (
        not checked_thresholds
        or not all(
            np.isfinite(value) and 0.0 < value < 1.0
            for value in checked_thresholds
        )
    ):
        raise ValueError("thresholds must be finite probabilities")
    if 0.50 not in checked_thresholds:
        raise ValueError("thresholds must include the frozen 0.50 reference")
    checked_minimum_rows = int(minimum_rows)
    checked_coverage = float(minimum_coverage)
    checked_gain = float(precision_gain)
    if checked_minimum_rows <= 0:
        raise ValueError("minimum_rows must be positive")
    if not 0.0 < checked_coverage <= 1.0:
        raise ValueError("minimum_coverage must be in (0, 1]")
    if not np.isfinite(checked_gain) or checked_gain < 0.0:
        raise ValueError("precision_gain must be finite and non-negative")

    rows = oof_predictions.loc[:, sorted(required)].copy(deep=True)
    rows["actual_return"] = pd.to_numeric(
        rows["actual_return"],
        errors="coerce",
    )
    probability_columns = [
        "down_probability",
        "neutral_probability",
        "up_probability",
    ]
    for column in probability_columns:
        rows[column] = pd.to_numeric(rows[column], errors="coerce")
    valid_labels = rows["actual_direction"].isin(DIRECTION_CLASSES)
    numeric = rows[["actual_return", *probability_columns]].to_numpy(
        dtype=float
    )
    valid_numeric = np.isfinite(numeric).all(axis=1)
    valid_probability = (
        (numeric[:, 1:] >= 0.0).all(axis=1)
        & (numeric[:, 1:] <= 1.0).all(axis=1)
        & np.isclose(numeric[:, 1:].sum(axis=1), 1.0, atol=1e-6)
    )
    rows = rows.loc[
        valid_labels.to_numpy() & valid_numeric & valid_probability
    ].reset_index(drop=True)
    if rows.empty:
        return EconomicThresholdResult(
            threshold=None,
            status="unavailable",
            reason="economic_threshold_unavailable",
            diagnostics=(),
        )

    reference = _threshold_diagnostic(rows, 0.50)
    reference_precision = float(reference["down_precision"])
    diagnostics = []
    eligible = []
    for threshold in checked_thresholds:
        diagnostic = _threshold_diagnostic(rows, threshold)
        reasons = []
        if diagnostic["down_count"] < checked_minimum_rows:
            reasons.append("insufficient_down_rows")
        if diagnostic["down_coverage"] < checked_coverage:
            reasons.append("insufficient_down_coverage")
        if not (
            diagnostic["mean_return_predicted_down"] < 0.0
        ):
            reasons.append("non_negative_down_return")
        if threshold != 0.50 and not (
            diagnostic["down_precision"]
            >= reference_precision + checked_gain
        ):
            reasons.append("insufficient_precision_gain")
        if threshold == 0.50:
            reasons.append("reference_threshold")
        diagnostic["status"] = (
            "eligible" if not reasons else "rejected"
        )
        diagnostic["reasons"] = tuple(reasons)
        frozen = MappingProxyType(diagnostic)
        diagnostics.append(frozen)
        if not reasons:
            eligible.append(frozen)

    if not eligible:
        return EconomicThresholdResult(
            threshold=None,
            status="unavailable",
            reason="economic_threshold_unavailable",
            diagnostics=tuple(diagnostics),
        )
    selected = max(
        eligible,
        key=lambda item: (
            float(item["balanced_accuracy"]),
            float(item["threshold"]),
        ),
    )
    return EconomicThresholdResult(
        threshold=float(selected["threshold"]),
        status="available",
        reason=None,
        diagnostics=tuple(diagnostics),
    )


def fit_regime_priors(
    labels,
    weights,
    regimes,
    classes=DIRECTION_CLASSES,
    prior_strength=REGIME_PRIOR_STRENGTH,
):
    """Fit fixed-strength regime class priors from training rows only."""
    checked_classes = tuple(map(str, classes))
    if (
        not checked_classes
        or len(set(checked_classes)) != len(checked_classes)
    ):
        raise ValueError("classes must be unique and non-empty")
    checked_strength = float(prior_strength)
    if not np.isfinite(checked_strength) or checked_strength <= 0.0:
        raise ValueError("prior_strength must be finite and positive")
    label_values = np.asarray(labels, dtype=object).copy()
    weight_values = np.asarray(weights, dtype=float).copy()
    regime_values = np.asarray(regimes, dtype=object).copy()
    if (
        not len(label_values)
        or len(label_values) != len(weight_values)
        or len(label_values) != len(regime_values)
    ):
        raise ValueError("prior inputs must have the same non-zero length")
    if (
        not np.isfinite(weight_values).all()
        or np.any(weight_values <= 0.0)
    ):
        raise ValueError("weights must be finite and positive")
    unknown = sorted(set(map(str, label_values)).difference(checked_classes))
    if unknown:
        raise ValueError(f"direction labels must be supported: {unknown}")

    global_counts = _weighted_counts(
        label_values,
        weight_values,
        checked_classes,
    )
    global_prior = global_counts / float(global_counts.sum())
    normalized_regimes = np.asarray(
        [
            None
            if value is None or not str(value).strip()
            else str(value).strip()
            for value in regime_values
        ],
        dtype=object,
    )
    posterior_values = {}
    known = sorted(
        {value for value in normalized_regimes if value is not None}
    )
    for regime in known:
        selected = normalized_regimes == regime
        counts = _weighted_counts(
            label_values[selected],
            weight_values[selected],
            checked_classes,
        )
        posterior = (
            counts + checked_strength * global_prior
        ) / (float(counts.sum()) + checked_strength)
        posterior_values[regime] = tuple(map(float, posterior))
    return RegimePriors(
        classes=checked_classes,
        global_values=tuple(map(float, global_prior)),
        regime_values=MappingProxyType(posterior_values),
    )


def adjust_regime_log_probabilities(
    log_probabilities,
    regimes,
    priors,
):
    """Add known training-regime prior deltas to ordered log scores."""
    if not isinstance(priors, RegimePriors):
        raise TypeError("priors must be RegimePriors")
    scores = np.asarray(log_probabilities, dtype=float).copy()
    regime_values = np.asarray(regimes, dtype=object)
    if (
        scores.ndim != 2
        or scores.shape[1] != len(priors.classes)
        or scores.shape[0] != len(regime_values)
    ):
        raise ValueError("scores and regimes must have compatible shapes")
    if not np.isfinite(scores).all():
        raise ValueError("log_probabilities must be finite")
    epsilon = np.finfo(float).tiny
    global_log = np.log(np.clip(priors.global_prior, epsilon, 1.0))
    for position, raw_regime in enumerate(regime_values):
        if raw_regime is None or not str(raw_regime).strip():
            continue
        regime = str(raw_regime).strip()
        if regime not in priors.regime_values:
            continue
        regime_log = np.log(
            np.clip(priors.regime_prior(regime), epsilon, 1.0)
        )
        scores[position] += regime_log - global_log
    return scores


def attach_absolute_and_qqq_relative_targets(
    frame,
    histories,
    horizon=HORIZON,
):
    """Attach aligned absolute, QQQ-relative, and path-MAE targets."""
    if int(horizon) != HORIZON or isinstance(horizon, bool):
        raise ValueError("only the frozen five-session horizon is supported")
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    qqq = _prepared_history(histories.get("QQQ"))
    if qqq is None:
        raise RegimeThresholdDataUnavailable(
            "QQQ history is required for relative targets"
        )

    attached = attach_next_open_targets(
        frame,
        histories,
        horizons=(HORIZON,),
    )
    result = attached.copy(deep=True)
    result["absolute_return_5"] = result["executable_return_5"]
    result["entry_date_5"] = result["executable_entry_date_5"]
    result["label_end_date_5"] = result["executable_label_end_date_5"]
    result["absolute_direction_5"] = _nullable_directions(
        result["absolute_return_5"]
    )
    result["qqq_relative_return_5"] = np.nan
    result["qqq_relative_direction_5"] = pd.Series(
        np.nan,
        index=result.index,
        dtype=object,
    )
    result["maximum_adverse_excursion_5"] = np.nan

    qqq_target, qqq_entry, qqq_end = _executable_series(qqq)
    tickers = result.index.get_level_values("ticker").unique()
    for raw_ticker in tickers:
        ticker = str(raw_ticker)
        history = _prepared_history(histories.get(ticker))
        if history is None:
            continue
        observation_dates = result.loc[ticker].index
        keys = pd.MultiIndex.from_product(
            ((raw_ticker,), observation_dates),
            names=result.index.names,
        )
        absolute = pd.to_numeric(
            result.loc[keys, "absolute_return_5"],
            errors="coerce",
        )
        mature = absolute.notna()
        if mature.any():
            qqq_values = qqq_target.reindex(observation_dates)
            expected_entry = pd.to_datetime(
                result.loc[keys, "entry_date_5"]
            )
            expected_end = pd.to_datetime(
                result.loc[keys, "label_end_date_5"]
            )
            aligned = (
                qqq_values.notna().to_numpy()
                & (
                    qqq_entry.reindex(observation_dates).to_numpy()
                    == expected_entry.to_numpy()
                )
                & (
                    qqq_end.reindex(observation_dates).to_numpy()
                    == expected_end.to_numpy()
                )
            )
            if not bool(np.all(aligned[mature.to_numpy()])):
                raise RegimeThresholdDataUnavailable(
                    f"QQQ sessions are not aligned for {ticker}"
                )
            relative = (
                absolute.to_numpy(dtype=float)
                - qqq_values.to_numpy(dtype=float)
            )
            result.loc[keys, "qqq_relative_return_5"] = relative
            result.loc[keys, "qqq_relative_direction_5"] = (
                _nullable_directions(
                    pd.Series(relative, index=keys)
                ).to_numpy()
            )
        mae = _maximum_adverse_excursion(history)
        result.loc[keys, "maximum_adverse_excursion_5"] = (
            mae.reindex(observation_dates).to_numpy(dtype=float)
        )
    return result


def _prepared_history(history):
    if not isinstance(history, pd.DataFrame) or history.empty:
        return None
    required = {"Open", "Low", "Close"}
    if not required.issubset(history.columns):
        return None
    result = history.copy(deep=True).sort_index()
    index = pd.DatetimeIndex(
        pd.to_datetime(result.index, errors="coerce")
    )
    if index.isna().any() or index.has_duplicates:
        return None
    if index.tz is not None:
        index = index.tz_localize(None)
    result.index = index.normalize()
    return result


def _executable_series(history):
    entry_open = pd.to_numeric(
        history["Open"],
        errors="coerce",
    ).shift(-1)
    exit_close = pd.to_numeric(
        history["Close"],
        errors="coerce",
    ).shift(-HORIZON)
    target = exit_close / entry_open.where(entry_open > 0.0) - 1.0
    dates = pd.Series(history.index, index=history.index)
    return target, dates.shift(-1), dates.shift(-HORIZON)


def _maximum_adverse_excursion(history):
    entry_open = pd.to_numeric(
        history["Open"],
        errors="coerce",
    ).shift(-1)
    low = pd.to_numeric(history["Low"], errors="coerce")
    path = pd.concat(
        [low.shift(-step) for step in range(1, HORIZON + 1)],
        axis=1,
    )
    complete = path.notna().all(axis=1)
    minimum = path.min(axis=1).where(complete)
    return minimum / entry_open.where(entry_open > 0.0) - 1.0


def _nullable_directions(returns):
    values = pd.to_numeric(returns, errors="coerce")
    result = pd.Series(np.nan, index=returns.index, dtype=object)
    valid = values.notna() & np.isfinite(values)
    if valid.any():
        result.loc[valid] = direction_labels(
            values.loc[valid],
            HORIZON,
        )
    return result


def _weighted_counts(labels, weights, classes):
    return np.asarray(
        [
            float(weights[np.asarray(labels) == label].sum())
            for label in classes
        ],
        dtype=float,
    )


def _threshold_diagnostic(rows, threshold):
    probabilities = rows[
        ["down_probability", "neutral_probability", "up_probability"]
    ].to_numpy(dtype=float)
    predicted = threshold_directions(probabilities, threshold)
    actual = rows["actual_direction"].to_numpy(dtype=object)
    predicted_down = predicted == "down"
    actual_down = actual == "down"
    down_count = int(predicted_down.sum())
    true_down = int((predicted_down & actual_down).sum())
    actual_down_count = int(actual_down.sum())
    recalls = []
    for label in DIRECTION_CLASSES:
        selected = actual == label
        if selected.any():
            recalls.append(float((predicted[selected] == label).mean()))
    returns = rows.loc[
        predicted_down,
        "actual_return",
    ].to_numpy(dtype=float)
    return {
        "threshold": float(threshold),
        "rows": int(len(rows)),
        "down_count": down_count,
        "down_coverage": float(down_count / len(rows)),
        "down_precision": (
            float(true_down / down_count) if down_count else 0.0
        ),
        "down_recall": (
            float(true_down / actual_down_count)
            if actual_down_count
            else 0.0
        ),
        "balanced_accuracy": (
            float(np.mean(recalls)) if recalls else 0.0
        ),
        "mean_return_predicted_down": (
            float(np.mean(returns)) if len(returns) else np.nan
        ),
        "median_return_predicted_down": (
            float(np.median(returns)) if len(returns) else np.nan
        ),
    }
