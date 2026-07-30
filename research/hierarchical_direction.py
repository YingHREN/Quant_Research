"""Leakage-safe recency and hierarchy helpers for offline direction research."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral
from types import MappingProxyType

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from data.market_behavior import (
    RULE_VERSION as MARKET_BEHAVIOR_VERSION,
    classify_market_behavior,
)
from research.market_direction_model import (
    chronological_purged_folds,
    direction_labels,
    training_only_design,
)
from web.market_groups import SECTOR_ETFS


HALF_LIFE_BY_HORIZON = {5: 126, 20: 252, 60: 504}
DIRECTION_CLASSES = ("down", "neutral", "up")
GROUP_PRIOR_STRENGTH = 1_000.0
TICKER_PRIOR_STRENGTH = 252.0


@dataclass(frozen=True)
class HierarchicalPriors:
    """Immutable weighted class priors for global, group, and ticker levels."""

    classes: tuple
    global_values: tuple
    group_priors: object
    ticker_priors: object
    ticker_groups: object

    @property
    def global_prior(self):
        return np.asarray(self.global_values, dtype=float)

    def group_prior(self, group):
        values = self.group_priors.get(str(group))
        if values is None:
            return self.global_prior
        return np.asarray(values, dtype=float)

    def ticker_prior(self, ticker, group):
        key = str(ticker).strip().upper()
        expected_group = self.ticker_groups.get(key)
        if expected_group is None or expected_group != str(group):
            return self.group_prior(group)
        values = self.ticker_priors.get(key)
        if values is None:
            return self.group_prior(group)
        return np.asarray(values, dtype=float)


def recency_class_weights(
    index,
    labels,
    horizon,
    *,
    minimum_effective_samples=1_000,
    minimum_class_effective_samples=100,
):
    """Return fixed recency × class-balance weights and auditable diagnostics."""
    checked_horizon = _checked_horizon(horizon)
    checked_total = _positive_number(
        minimum_effective_samples,
        "minimum_effective_samples",
    )
    checked_class = _positive_number(
        minimum_class_effective_samples,
        "minimum_class_effective_samples",
    )
    dates = _observation_dates(index)
    values = np.asarray(labels, dtype=object).copy()
    if len(dates) != len(values) or not len(values):
        raise ValueError("index and labels must have the same non-zero length")
    unknown = sorted(set(map(str, values)).difference(DIRECTION_CLASSES))
    if unknown:
        raise ValueError(f"direction labels must be supported: {unknown}")

    unique_dates = pd.DatetimeIndex(sorted(pd.unique(dates)))
    positions = {value: offset for offset, value in enumerate(unique_dates)}
    latest = len(unique_dates) - 1
    ages = np.asarray(
        [latest - positions[pd.Timestamp(value)] for value in dates],
        dtype=float,
    )
    time_weights = np.power(
        0.5,
        ages / float(HALF_LIFE_BY_HORIZON[checked_horizon]),
    )
    class_time_sums = {
        label: float(time_weights[values == label].sum())
        for label in DIRECTION_CLASSES
        if np.any(values == label)
    }
    if not class_time_sums or any(value <= 0.0 for value in class_time_sums.values()):
        raise ValueError("direction labels must have positive effective counts")
    total_time = float(time_weights.sum())
    class_count = len(class_time_sums)
    multipliers = {
        label: total_time / (class_count * value)
        for label, value in class_time_sums.items()
    }
    combined = time_weights * np.asarray(
        [multipliers[str(label)] for label in values],
        dtype=float,
    )
    combined /= float(combined.mean())

    class_effective = {
        label: _kish_effective_sample(combined[values == label])
        for label in class_time_sums
    }
    diagnostics = {
        "status": "available",
        "reason": None,
        "horizon": checked_horizon,
        "half_life_sessions": HALF_LIFE_BY_HORIZON[checked_horizon],
        "raw_sample_count": len(combined),
        "weight_sum": float(combined.sum()),
        "effective_sample_size": _kish_effective_sample(combined),
        "class_effective_sample_size": class_effective,
        "class_weight_sum": {
            label: float(combined[values == label].sum())
            for label in class_time_sums
        },
        "minimum_weight": float(combined.min()),
        "median_weight": float(np.median(combined)),
        "maximum_weight": float(combined.max()),
    }
    if diagnostics["effective_sample_size"] < checked_total:
        diagnostics["status"] = "unavailable"
        diagnostics["reason"] = "insufficient_effective_samples"
        return None, diagnostics
    if any(value < checked_class for value in class_effective.values()):
        diagnostics["status"] = "unavailable"
        diagnostics["reason"] = "insufficient_class_effective_samples"
        return None, diagnostics
    return combined, diagnostics


def freeze_behavior_groups(
    histories,
    tickers,
    cutoff,
    *,
    sector_etfs=SECTOR_ETFS,
):
    """Freeze price-behavior groups using only prices visible by cutoff."""
    checked_cutoff = pd.Timestamp(cutoff)
    if pd.isna(checked_cutoff):
        raise ValueError("cutoff must be a valid date")
    if checked_cutoff.tz is not None:
        checked_cutoff = checked_cutoff.tz_localize(None)
    checked_cutoff = checked_cutoff.normalize()
    requested = tuple(
        sorted(
            {
                str(ticker).strip().upper()
                for ticker in tickers
                if str(ticker).strip()
            }
        )
    )
    price_rows = {
        str(ticker).strip().upper(): _price_rows(frame, checked_cutoff)
        for ticker, frame in histories.items()
        if str(ticker).strip()
    }
    normalized_etfs = {
        str(sector): str(ticker).strip().upper()
        for sector, ticker in sector_etfs.items()
        if str(sector).strip() and str(ticker).strip()
    }
    groups = {}
    common_days = {}
    sector_counts = {}
    for ticker in requested:
        result = classify_market_behavior(
            price_rows,
            ticker,
            normalized_etfs,
            sec_sector="",
            asof=checked_cutoff.date().isoformat(),
            min_observations=126,
            max_observations=252,
        )
        if result is None:
            groups[ticker] = None
            continue
        groups[ticker] = result.sector_key
        common_days[ticker] = result.common_days
        sector_counts[result.sector_key] = (
            sector_counts.get(result.sector_key, 0) + 1
        )
    classified = sum(group is not None for group in groups.values())
    diagnostics = {
        "cutoff": checked_cutoff.date().isoformat(),
        "rule_version": MARKET_BEHAVIOR_VERSION,
        "requested_count": len(requested),
        "classified_count": classified,
        "unavailable_count": len(requested) - classified,
        "sector_counts": dict(sorted(sector_counts.items())),
        "common_days": dict(sorted(common_days.items())),
    }
    return groups, diagnostics


def fit_hierarchical_priors(
    labels,
    weights,
    tickers,
    groups,
    classes=DIRECTION_CLASSES,
):
    """Fit fixed-strength empirical-Bayes class priors from training rows."""
    checked_classes = tuple(map(str, classes))
    if (
        not checked_classes
        or len(set(checked_classes)) != len(checked_classes)
    ):
        raise ValueError("classes must be unique and non-empty")
    label_values = np.asarray(labels, dtype=object).copy()
    weight_values = np.asarray(weights, dtype=float).copy()
    ticker_values = np.asarray(tickers, dtype=object).copy()
    group_values = np.asarray(groups, dtype=object).copy()
    lengths = {
        len(label_values),
        len(weight_values),
        len(ticker_values),
        len(group_values),
    }
    if lengths != {len(label_values)} or not len(label_values):
        raise ValueError("hierarchy inputs must have the same non-zero length")
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
    group_priors = {}
    valid_group_mask = np.asarray(
        [
            value is not None and str(value).strip()
            for value in group_values
        ],
        dtype=bool,
    )
    for raw_group in sorted(set(map(str, group_values[valid_group_mask]))):
        selected = valid_group_mask & (
            np.asarray(list(map(str, group_values)), dtype=object) == raw_group
        )
        counts = _weighted_counts(
            label_values[selected],
            weight_values[selected],
            checked_classes,
        )
        posterior = (
            counts + GROUP_PRIOR_STRENGTH * global_prior
        ) / (float(counts.sum()) + GROUP_PRIOR_STRENGTH)
        group_priors[raw_group] = tuple(map(float, posterior))

    ticker_priors = {}
    ticker_groups = {}
    normalized_tickers = np.asarray(
        [str(value).strip().upper() for value in ticker_values],
        dtype=object,
    )
    normalized_groups = np.asarray(
        [None if value is None else str(value).strip() for value in group_values],
        dtype=object,
    )
    for ticker in sorted(set(normalized_tickers)):
        selected = normalized_tickers == ticker
        observed_groups = {
            value
            for value in normalized_groups[selected]
            if value
        }
        if len(observed_groups) > 1:
            raise ValueError("each ticker must have one fold-frozen group")
        if not observed_groups:
            continue
        group = next(iter(observed_groups))
        parent = np.asarray(group_priors[group], dtype=float)
        counts = _weighted_counts(
            label_values[selected],
            weight_values[selected],
            checked_classes,
        )
        posterior = (
            counts + TICKER_PRIOR_STRENGTH * parent
        ) / (float(counts.sum()) + TICKER_PRIOR_STRENGTH)
        ticker_groups[ticker] = group
        ticker_priors[ticker] = tuple(map(float, posterior))

    return HierarchicalPriors(
        classes=checked_classes,
        global_values=tuple(map(float, global_prior)),
        group_priors=MappingProxyType(group_priors),
        ticker_priors=MappingProxyType(ticker_priors),
        ticker_groups=MappingProxyType(ticker_groups),
    )


def adjust_log_probabilities(
    log_probabilities,
    tickers,
    groups,
    priors,
    *,
    include_group,
    include_ticker,
):
    """Apply group/ticker log-prior deltas without claiming calibration."""
    if not isinstance(priors, HierarchicalPriors):
        raise TypeError("priors must be HierarchicalPriors")
    scores = np.asarray(log_probabilities, dtype=float).copy()
    ticker_values = np.asarray(tickers, dtype=object)
    group_values = np.asarray(groups, dtype=object)
    if (
        scores.ndim != 2
        or scores.shape[1] != len(priors.classes)
        or len(ticker_values) != len(scores)
        or len(group_values) != len(scores)
    ):
        raise ValueError("score and hierarchy rows must align")
    if not np.isfinite(scores).all():
        raise ValueError("log probabilities must be finite")
    epsilon = np.finfo(float).tiny
    global_prior = np.clip(priors.global_prior, epsilon, 1.0)
    for index, (raw_ticker, raw_group) in enumerate(
        zip(ticker_values, group_values)
    ):
        group = None if raw_group is None else str(raw_group).strip()
        ticker = str(raw_ticker).strip().upper()
        group_prior = priors.group_prior(group)
        group_is_known = group in priors.group_priors
        if include_group and group_is_known:
            scores[index] += np.log(
                np.clip(group_prior, epsilon, 1.0)
            ) - np.log(global_prior)
        if include_ticker and ticker in priors.ticker_priors and group_is_known:
            ticker_prior = priors.ticker_prior(ticker, group)
            parent = group_prior if include_group else global_prior
            scores[index] += np.log(
                np.clip(ticker_prior, epsilon, 1.0)
            ) - np.log(np.clip(parent, epsilon, 1.0))
    return scores


def walk_forward_hierarchical_predictions(
    frame,
    histories,
    *,
    horizon,
    feature_columns,
    n_test_folds=5,
    minimum_samples=1_000,
):
    """Evaluate fixed global, recency, group, and ticker ablations."""
    checked_horizon = _checked_horizon(horizon)
    if (
        isinstance(n_test_folds, bool)
        or not isinstance(n_test_folds, Integral)
        or int(n_test_folds) < 2
    ):
        raise ValueError("n_test_folds must be an integer of at least two")
    if (
        isinstance(minimum_samples, bool)
        or not isinstance(minimum_samples, Integral)
        or int(minimum_samples) <= 0
    ):
        raise ValueError("minimum_samples must be a positive integer")
    columns = tuple(map(str, feature_columns))
    if not columns:
        raise ValueError("feature_columns must not be empty")
    target_name = f"executable_return_{checked_horizon}"
    end_name = f"executable_label_end_date_{checked_horizon}"
    predictions = []
    weight_rows = []
    group_rows = []
    folds = chronological_purged_folds(
        frame,
        checked_horizon,
        n_folds=int(n_test_folds) + 1,
    )
    for fold, (train_index, test_index) in enumerate(folds, start=1):
        train = frame.iloc[train_index]
        test = frame.iloc[test_index]
        test_start = pd.Timestamp(
            test.index.get_level_values("observation_date").min()
        )
        train_label_end_max = pd.Timestamp(train[end_name].max())
        base_group_row = {
            "horizon": checked_horizon,
            "fold": fold,
            "test_start": test_start,
            "training_cutoff": pd.Timestamp(
                train.index.get_level_values("observation_date").max()
            ),
        }
        if len(train) < int(minimum_samples):
            weight_rows.append(
                {
                    "horizon": checked_horizon,
                    "fold": fold,
                    "weight_type": "recency",
                    "status": "unavailable",
                    "reason": "insufficient_training_samples",
                    "raw_sample_count": len(train),
                }
            )
            group_rows.append(
                {
                    **base_group_row,
                    "status": "unavailable",
                    "reason": "insufficient_training_samples",
                }
            )
            continue
        y_train = direction_labels(train[target_name], checked_horizon)
        y_test = direction_labels(test[target_name], checked_horizon)
        if set(y_train) != set(DIRECTION_CLASSES):
            weight_rows.append(
                {
                    "horizon": checked_horizon,
                    "fold": fold,
                    "weight_type": "recency",
                    "status": "unavailable",
                    "reason": "missing_direction_class",
                    "raw_sample_count": len(train),
                }
            )
            group_rows.append(
                {
                    **base_group_row,
                    "status": "unavailable",
                    "reason": "missing_direction_class",
                }
            )
            continue

        minimum_class = max(1.0, float(minimum_samples) / 10.0)
        time_weights, time_diagnostics = recency_class_weights(
            train.index,
            y_train,
            checked_horizon,
            minimum_effective_samples=float(minimum_samples),
            minimum_class_effective_samples=minimum_class,
        )
        weight_rows.append(
            {
                "horizon": checked_horizon,
                "fold": fold,
                "weight_type": "recency",
                **time_diagnostics,
            }
        )
        if time_weights is None:
            group_rows.append(
                {
                    **base_group_row,
                    "status": "unavailable",
                    "reason": time_diagnostics["reason"],
                }
            )
            continue
        balanced_weights = _balanced_class_weights(y_train)
        weight_rows.append(
            {
                "horizon": checked_horizon,
                "fold": fold,
                "weight_type": "balanced",
                "status": "available",
                "reason": None,
                "raw_sample_count": len(train),
                "weight_sum": float(balanced_weights.sum()),
                "effective_sample_size": _kish_effective_sample(
                    balanced_weights
                ),
            }
        )

        ticker_values = np.asarray(
            train.index.get_level_values("ticker"),
            dtype=object,
        )
        test_tickers = np.asarray(
            test.index.get_level_values("ticker"),
            dtype=object,
        )
        group_map, group_diagnostics = freeze_behavior_groups(
            histories,
            sorted(set(ticker_values) | set(test_tickers)),
            base_group_row["training_cutoff"],
        )
        train_groups = np.asarray(
            [group_map.get(str(ticker)) for ticker in ticker_values],
            dtype=object,
        )
        test_groups = np.asarray(
            [group_map.get(str(ticker)) for ticker in test_tickers],
            dtype=object,
        )
        group_rows.append(
            {
                **base_group_row,
                "status": "available",
                "reason": None,
                **group_diagnostics,
            }
        )
        x_train, x_test = training_only_design(train, test, columns)
        balanced_model = _fit_logistic(
            x_train,
            y_train,
            balanced_weights,
        )
        time_model = _fit_logistic(x_train, y_train, time_weights)
        balanced_log = _ordered_log_probabilities(
            balanced_model,
            x_test,
        )
        time_log = _ordered_log_probabilities(time_model, x_test)
        balanced_priors = fit_hierarchical_priors(
            y_train,
            balanced_weights,
            ticker_values,
            train_groups,
            DIRECTION_CLASSES,
        )
        time_priors = fit_hierarchical_priors(
            y_train,
            time_weights,
            ticker_values,
            train_groups,
            DIRECTION_CLASSES,
        )
        candidates = {
            "logistic_global": balanced_log,
            "logistic_time": time_log,
            "logistic_group": adjust_log_probabilities(
                balanced_log,
                test_tickers,
                test_groups,
                balanced_priors,
                include_group=True,
                include_ticker=False,
            ),
            "logistic_time_group": adjust_log_probabilities(
                time_log,
                test_tickers,
                test_groups,
                time_priors,
                include_group=True,
                include_ticker=False,
            ),
            "logistic_time_group_ticker": adjust_log_probabilities(
                time_log,
                test_tickers,
                test_groups,
                time_priors,
                include_group=True,
                include_ticker=True,
            ),
        }
        for specification, scores in candidates.items():
            predicted = np.asarray(DIRECTION_CLASSES, dtype=object)[
                np.argmax(scores, axis=1)
            ]
            predictions.append(
                pd.DataFrame(
                    {
                        "ticker": test_tickers,
                        "observation_date": test.index.get_level_values(
                            "observation_date"
                        ),
                        "horizon": checked_horizon,
                        "fold": fold,
                        "specification": specification,
                        "actual_return": test[target_name].to_numpy(
                            dtype=float
                        ),
                        "actual_direction": y_test,
                        "predicted_direction": predicted,
                        "training_samples": len(train),
                        "training_label_end_max": train_label_end_max,
                        "test_start": test_start,
                    }
                )
            )
    prediction_frame = (
        pd.concat(predictions, ignore_index=True)
        if predictions
        else pd.DataFrame(
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
    )
    if not prediction_frame.empty:
        prediction_frame = prediction_frame.sort_values(
            ["specification", "fold", "ticker", "observation_date"],
            kind="mergesort",
        ).reset_index(drop=True)
    return (
        prediction_frame,
        pd.DataFrame(weight_rows),
        pd.DataFrame(group_rows),
    )


def _price_rows(frame, cutoff):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    column = "Adj Close" if "Adj Close" in frame else "Close"
    if column not in frame:
        return []
    dates = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    rows = []
    for raw_date, value in zip(dates, values):
        if (
            pd.notna(raw_date)
            and raw_date.normalize() <= cutoff
            and np.isfinite(value)
            and value > 0.0
        ):
            rows.append((raw_date.date().isoformat(), float(value)))
    return rows


def _weighted_counts(labels, weights, classes):
    return np.asarray(
        [
            float(weights[np.asarray(labels, dtype=object) == label].sum())
            for label in classes
        ],
        dtype=float,
    )


def _balanced_class_weights(labels):
    values = np.asarray(labels, dtype=object)
    counts = {
        label: int(np.sum(values == label))
        for label in DIRECTION_CLASSES
        if np.any(values == label)
    }
    weights = np.asarray(
        [
            len(values) / (len(counts) * counts[str(label)])
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


def _ordered_log_probabilities(model, design):
    values = np.asarray(design, dtype=float)
    coefficients = np.asarray(model.coef_, dtype=float)
    intercept = np.asarray(model.intercept_, dtype=float)
    logits = np.sum(
        values[:, None, :] * coefficients[None, :, :],
        axis=2,
    ) + intercept
    maximum = np.max(logits, axis=1, keepdims=True)
    raw = logits - maximum - np.log(
        np.exp(logits - maximum).sum(axis=1, keepdims=True)
    )
    positions = {
        str(label): index
        for index, label in enumerate(model.classes_)
    }
    return raw[
        :,
        [positions[label] for label in DIRECTION_CLASSES],
    ]


def _observation_dates(index):
    if isinstance(index, pd.MultiIndex):
        if "observation_date" not in index.names:
            raise ValueError("index must contain observation_date")
        raw = index.get_level_values("observation_date")
    else:
        raw = index
    dates = pd.DatetimeIndex(pd.to_datetime(raw, errors="coerce"))
    if dates.isna().any():
        raise ValueError("observation dates must be valid")
    if dates.tz is not None:
        dates = dates.tz_localize(None)
    return dates.normalize()


def _checked_horizon(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, Integral)
        or int(value) not in HALF_LIFE_BY_HORIZON
    ):
        raise ValueError("horizon must be a supported session count")
    return int(value)


def _positive_number(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a positive number")
    checked = float(value)
    if not np.isfinite(checked) or checked <= 0.0:
        raise ValueError(f"{name} must be a positive number")
    return checked


def _kish_effective_sample(weights):
    values = np.asarray(weights, dtype=float)
    denominator = float(np.square(values).sum())
    if denominator <= 0.0:
        return 0.0
    return float(values.sum() ** 2 / denominator)
