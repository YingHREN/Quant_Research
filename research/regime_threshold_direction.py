"""Leakage-safe helpers for the offline regime-threshold direction study."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from research.market_direction_model import (
    attach_next_open_targets,
    chronological_purged_folds,
    direction_labels,
    training_only_design,
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


def walk_forward_regime_threshold_predictions(
    frame,
    regimes,
    *,
    feature_columns,
    n_test_folds=5,
    minimum_samples=1_000,
):
    """Evaluate global, causal-regime, and nested-threshold challengers."""
    columns = tuple(map(str, feature_columns))
    if not columns:
        raise ValueError("feature_columns must not be empty")
    missing = [
        column
        for column in (
            *columns,
            "absolute_return_5",
            "executable_return_5",
            "executable_label_end_date_5",
        )
        if column not in frame
    ]
    if missing:
        raise ValueError(f"frame is missing required columns: {missing}")
    if (
        isinstance(n_test_folds, bool)
        or int(n_test_folds) < 2
        or int(n_test_folds) != n_test_folds
    ):
        raise ValueError("n_test_folds must be an integer of at least two")
    if (
        isinstance(minimum_samples, bool)
        or int(minimum_samples) <= 0
        or int(minimum_samples) != minimum_samples
    ):
        raise ValueError("minimum_samples must be a positive integer")
    regime_series = _prepare_regime_series(regimes)
    predictions = []
    diagnostics = []
    outer_folds = chronological_purged_folds(
        frame,
        HORIZON,
        n_folds=int(n_test_folds) + 1,
    )
    for outer_fold, (train_index, test_index) in enumerate(
        outer_folds,
        start=1,
    ):
        train = frame.iloc[train_index]
        test = frame.iloc[test_index]
        test_start = _observation_dates(test.index).min()
        train_label_end_max = pd.Timestamp(
            train["executable_label_end_date_5"].max()
        )
        base_diagnostic = {
            "outer_fold": outer_fold,
            "status": "available",
            "reason": None,
            "outer_training_samples": len(train),
            "outer_test_samples": len(test),
            "outer_train_label_end_max": train_label_end_max,
            "outer_test_start": pd.Timestamp(test_start),
            "inner_oof_rows": 0,
            "inner_fold_boundaries": (),
            "selected_threshold": np.nan,
            "threshold_status": "unavailable",
            "threshold_reason": "economic_threshold_unavailable",
            "threshold_diagnostics": (),
        }
        if len(train) < int(minimum_samples):
            base_diagnostic.update(
                status="unavailable",
                reason="insufficient_training_samples",
            )
            diagnostics.append(base_diagnostic)
            continue
        y_train = direction_labels(
            train["absolute_return_5"],
            HORIZON,
        )
        if set(map(str, y_train)) != set(DIRECTION_CLASSES):
            base_diagnostic.update(
                status="unavailable",
                reason="missing_direction_class",
            )
            diagnostics.append(base_diagnostic)
            continue

        inner_oof, inner_boundaries = _inner_oof_regime_predictions(
            train,
            regime_series,
            columns,
            minimum_samples=int(minimum_samples),
        )
        threshold_result = select_economic_down_threshold(inner_oof)
        base_diagnostic.update(
            inner_oof_rows=len(inner_oof),
            inner_fold_boundaries=inner_boundaries,
            selected_threshold=(
                float(threshold_result.threshold)
                if threshold_result.threshold is not None
                else np.nan
            ),
            threshold_status=threshold_result.status,
            threshold_reason=threshold_result.reason,
            threshold_diagnostics=threshold_result.diagnostics,
        )

        x_train, x_test = training_only_design(train, test, columns)
        weights = _balanced_class_weights(y_train)
        model = _fit_logistic(x_train, y_train, weights)
        global_probabilities = _ordered_probabilities(model, x_test)
        train_regimes = _regimes_for_index(regime_series, train.index)
        test_regimes = _regimes_for_index(regime_series, test.index)
        priors = fit_regime_priors(
            y_train,
            weights,
            train_regimes,
        )
        prior_probabilities = _normalized_probabilities(
            adjust_regime_log_probabilities(
                np.log(
                    np.clip(
                        global_probabilities,
                        np.finfo(float).tiny,
                        1.0,
                    )
                ),
                test_regimes,
                priors,
            )
        )
        y_test = direction_labels(
            test["absolute_return_5"],
            HORIZON,
        )
        test_tickers = np.asarray(
            test.index.get_level_values("ticker"),
            dtype=object,
        )
        test_dates = _observation_dates(test.index)
        candidate_predictions = {
            "logistic_global": np.asarray(
                DIRECTION_CLASSES,
                dtype=object,
            )[np.argmax(global_probabilities, axis=1)],
            "logistic_regime_prior": np.asarray(
                DIRECTION_CLASSES,
                dtype=object,
            )[np.argmax(prior_probabilities, axis=1)],
        }
        if threshold_result.threshold is not None:
            candidate_predictions["logistic_regime_threshold"] = (
                threshold_directions(
                    prior_probabilities,
                    threshold_result.threshold,
                )
            )
        for specification, predicted in candidate_predictions.items():
            predictions.append(
                pd.DataFrame(
                    {
                        "ticker": test_tickers,
                        "observation_date": test_dates,
                        "horizon": HORIZON,
                        "fold": outer_fold,
                        "specification": specification,
                        "actual_return": test[
                            "absolute_return_5"
                        ].to_numpy(dtype=float),
                        "actual_direction": y_test,
                        "predicted_direction": predicted,
                        "training_samples": len(train),
                        "training_label_end_max": train_label_end_max,
                        "test_start": pd.Timestamp(test_start),
                    }
                )
            )
        diagnostics.append(base_diagnostic)
    prediction_frame = (
        pd.concat(predictions, ignore_index=True)
        if predictions
        else _empty_prediction_frame()
    )
    if not prediction_frame.empty:
        prediction_frame = prediction_frame.sort_values(
            ["specification", "fold", "ticker", "observation_date"],
            kind="mergesort",
        ).reset_index(drop=True)
    return prediction_frame, pd.DataFrame(diagnostics)


def walk_forward_qqq_relative_predictions(
    frame,
    *,
    feature_columns,
    n_test_folds=5,
    minimum_samples=1_000,
):
    """Evaluate a separately named QQQ-relative direction head."""
    columns = tuple(map(str, feature_columns))
    if not columns:
        raise ValueError("feature_columns must not be empty")
    required = (
        *columns,
        "absolute_return_5",
        "qqq_relative_return_5",
        "executable_return_5",
        "executable_label_end_date_5",
    )
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"frame is missing required columns: {missing}")
    if (
        isinstance(n_test_folds, bool)
        or int(n_test_folds) < 2
        or int(n_test_folds) != n_test_folds
    ):
        raise ValueError("n_test_folds must be an integer of at least two")
    if (
        isinstance(minimum_samples, bool)
        or int(minimum_samples) <= 0
        or int(minimum_samples) != minimum_samples
    ):
        raise ValueError("minimum_samples must be a positive integer")

    predictions = []
    outer_folds = chronological_purged_folds(
        frame,
        HORIZON,
        n_folds=int(n_test_folds) + 1,
    )
    for fold, (train_index, test_index) in enumerate(
        outer_folds,
        start=1,
    ):
        train = frame.iloc[train_index]
        test = frame.iloc[test_index]
        train_valid = pd.to_numeric(
            train["qqq_relative_return_5"],
            errors="coerce",
        ).notna()
        test_valid = pd.to_numeric(
            test["qqq_relative_return_5"],
            errors="coerce",
        ).notna()
        train = train.loc[train_valid]
        test = test.loc[test_valid]
        if len(train) < int(minimum_samples) or test.empty:
            continue
        y_train = direction_labels(
            train["qqq_relative_return_5"],
            HORIZON,
        )
        if set(map(str, y_train)) != set(DIRECTION_CLASSES):
            continue
        x_train, x_test = training_only_design(train, test, columns)
        weights = _balanced_class_weights(y_train)
        model = _fit_logistic(x_train, y_train, weights)
        probabilities = _ordered_probabilities(model, x_test)
        predicted = np.asarray(
            DIRECTION_CLASSES,
            dtype=object,
        )[np.argmax(probabilities, axis=1)]
        predictions.append(
            pd.DataFrame(
                {
                    "ticker": test.index.get_level_values("ticker"),
                    "observation_date": _observation_dates(test.index),
                    "horizon": HORIZON,
                    "fold": fold,
                    "specification": "logistic_qqq_relative",
                    "actual_return": test[
                        "absolute_return_5"
                    ].to_numpy(dtype=float),
                    "actual_relative_return": test[
                        "qqq_relative_return_5"
                    ].to_numpy(dtype=float),
                    "actual_relative_direction": direction_labels(
                        test["qqq_relative_return_5"],
                        HORIZON,
                    ),
                    "predicted_relative_direction": predicted,
                    "training_samples": len(train),
                    "training_label_end_max": pd.Timestamp(
                        train["executable_label_end_date_5"].max()
                    ),
                    "test_start": pd.Timestamp(
                        _observation_dates(test.index).min()
                    ),
                }
            )
        )
    if not predictions:
        return _empty_relative_prediction_frame()
    return pd.concat(predictions, ignore_index=True).sort_values(
        ["fold", "ticker", "observation_date"],
        kind="mergesort",
    ).reset_index(drop=True)


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


def _inner_oof_regime_predictions(
    train,
    regime_series,
    feature_columns,
    *,
    minimum_samples,
):
    rows = []
    boundaries = []
    folds = chronological_purged_folds(
        train,
        HORIZON,
        n_folds=4,
    )
    for inner_fold, (fit_index, validation_index) in enumerate(
        folds,
        start=1,
    ):
        fit = train.iloc[fit_index]
        validation = train.iloc[validation_index]
        if len(fit) < int(minimum_samples):
            continue
        y_fit = direction_labels(fit["absolute_return_5"], HORIZON)
        if set(map(str, y_fit)) != set(DIRECTION_CLASSES):
            continue
        validation_start = _observation_dates(validation.index).min()
        training_label_end_max = pd.Timestamp(
            fit["executable_label_end_date_5"].max()
        )
        if not training_label_end_max < pd.Timestamp(validation_start):
            raise RuntimeError("inner fold label purge invariant failed")
        x_fit, x_validation = training_only_design(
            fit,
            validation,
            feature_columns,
        )
        weights = _balanced_class_weights(y_fit)
        model = _fit_logistic(x_fit, y_fit, weights)
        probabilities = _ordered_probabilities(model, x_validation)
        fit_regimes = _regimes_for_index(regime_series, fit.index)
        validation_regimes = _regimes_for_index(
            regime_series,
            validation.index,
        )
        priors = fit_regime_priors(
            y_fit,
            weights,
            fit_regimes,
        )
        adjusted = _normalized_probabilities(
            adjust_regime_log_probabilities(
                np.log(
                    np.clip(
                        probabilities,
                        np.finfo(float).tiny,
                        1.0,
                    )
                ),
                validation_regimes,
                priors,
            )
        )
        rows.append(
            pd.DataFrame(
                {
                    "ticker": validation.index.get_level_values(
                        "ticker"
                    ),
                    "observation_date": _observation_dates(
                        validation.index
                    ),
                    "inner_fold": inner_fold,
                    "actual_direction": direction_labels(
                        validation["absolute_return_5"],
                        HORIZON,
                    ),
                    "actual_return": validation[
                        "absolute_return_5"
                    ].to_numpy(dtype=float),
                    "down_probability": adjusted[:, 0],
                    "neutral_probability": adjusted[:, 1],
                    "up_probability": adjusted[:, 2],
                }
            )
        )
        boundaries.append(
            MappingProxyType(
                {
                    "inner_fold": inner_fold,
                    "training_samples": len(fit),
                    "validation_samples": len(validation),
                    "training_label_end_max": training_label_end_max,
                    "test_start": pd.Timestamp(validation_start),
                }
            )
        )
    columns = (
        "ticker",
        "observation_date",
        "inner_fold",
        "actual_direction",
        "actual_return",
        "down_probability",
        "neutral_probability",
        "up_probability",
    )
    output = (
        pd.concat(rows, ignore_index=True)
        if rows
        else pd.DataFrame(columns=columns)
    )
    if not output.empty and not output.duplicated(
        ["ticker", "observation_date"]
    ).any():
        output = output.sort_values(
            ["inner_fold", "ticker", "observation_date"],
            kind="mergesort",
        ).reset_index(drop=True)
    elif not output.empty:
        raise RuntimeError("inner OOF keys must be unique")
    return output, tuple(boundaries)


def _prepare_regime_series(regimes):
    if isinstance(regimes, pd.DataFrame):
        if "regime" not in regimes:
            raise ValueError("regimes DataFrame must contain regime")
        source = regimes["regime"]
    elif isinstance(regimes, pd.Series):
        source = regimes
    else:
        raise TypeError("regimes must be a Series or DataFrame")
    result = source.copy(deep=True)
    dates = pd.DatetimeIndex(pd.to_datetime(result.index, errors="coerce"))
    if dates.isna().any():
        raise ValueError("regime dates must be valid")
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    dates = dates.normalize()
    if dates.has_duplicates:
        raise ValueError("regime dates must be unique")
    result.index = dates
    return result.sort_index()


def _regimes_for_index(regime_series, index):
    values = regime_series.reindex(_observation_dates(index)).to_numpy(
        dtype=object
    )
    return np.asarray(
        [
            None
            if (
                value is None
                or pd.isna(value)
                or not str(value).strip()
                or str(value).strip() == "unavailable"
            )
            else str(value).strip()
            for value in values
        ],
        dtype=object,
    )


def _balanced_class_weights(labels):
    values = np.asarray(labels, dtype=object)
    counts = {
        label: int(np.sum(values == label))
        for label in DIRECTION_CLASSES
    }
    if any(count <= 0 for count in counts.values()):
        raise ValueError("all direction classes are required")
    weights = np.asarray(
        [
            len(values) / (len(DIRECTION_CLASSES) * counts[str(label)])
            for label in values
        ],
        dtype=float,
    )
    return weights / float(weights.mean())


def _fit_logistic(design, labels, weights):
    model = LogisticRegression(
        max_iter=1_000,
        random_state=0,
        solver="liblinear",
    )
    model.fit(design, labels, sample_weight=weights)
    return model


def _ordered_probabilities(model, design):
    raw = np.asarray(model.predict_proba(design), dtype=float)
    positions = {
        str(label): position
        for position, label in enumerate(model.classes_)
    }
    if set(positions) != set(DIRECTION_CLASSES):
        raise ValueError("fitted model must contain all direction classes")
    return raw[
        :,
        [positions[label] for label in DIRECTION_CLASSES],
    ]


def _normalized_probabilities(log_probabilities):
    scores = np.asarray(log_probabilities, dtype=float)
    maximum = np.max(scores, axis=1, keepdims=True)
    exponentiated = np.exp(scores - maximum)
    return exponentiated / exponentiated.sum(axis=1, keepdims=True)


def _observation_dates(index):
    if not isinstance(index, pd.MultiIndex):
        raise ValueError("frame index must be a MultiIndex")
    if "observation_date" not in index.names:
        raise ValueError("frame index must contain observation_date")
    dates = pd.DatetimeIndex(
        pd.to_datetime(
            index.get_level_values("observation_date"),
            errors="coerce",
        )
    )
    if dates.isna().any():
        raise ValueError("observation dates must be valid")
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    return dates.normalize()


def _empty_prediction_frame():
    return pd.DataFrame(
        columns=(
            "ticker",
            "observation_date",
            "horizon",
            "fold",
            "specification",
            "actual_return",
            "actual_direction",
            "predicted_direction",
            "training_samples",
            "training_label_end_max",
            "test_start",
        )
    )


def _empty_relative_prediction_frame():
    return pd.DataFrame(
        columns=(
            "ticker",
            "observation_date",
            "horizon",
            "fold",
            "specification",
            "actual_return",
            "actual_relative_return",
            "actual_relative_direction",
            "predicted_relative_direction",
            "training_samples",
            "training_label_end_max",
            "test_start",
        )
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
