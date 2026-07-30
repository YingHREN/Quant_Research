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
