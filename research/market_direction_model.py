"""Leakage-safe, executable direction-model research helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Integral

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (
    balanced_accuracy_score,
    f1_score,
    precision_score,
    recall_score,
)


INDEX_NAMES = ("ticker", "observation_date")
NEUTRAL_BANDS = {5: 0.01, 10: 0.015, 20: 0.02, 60: 0.04}


def attach_next_open_targets(
    frame: pd.DataFrame,
    histories: Mapping[str, pd.DataFrame],
    horizons: Sequence[int] = (5, 20),
) -> pd.DataFrame:
    """Attach next-session-open to horizon-close executable returns."""
    _validate_index(frame)
    checked_horizons = _validate_horizons(horizons)
    result = frame.copy(deep=True)
    tickers = result.index.get_level_values("ticker")
    for horizon in checked_horizons:
        result[f"executable_return_{horizon}"] = np.nan
        result[f"executable_entry_date_{horizon}"] = pd.NaT
        result[f"executable_label_end_date_{horizon}"] = pd.NaT
        for ticker in tickers.unique():
            history = histories.get(str(ticker))
            if history is None or history.empty:
                continue
            ordered = history.sort_index()
            ordered_index = pd.DatetimeIndex(ordered.index).tz_localize(None)
            ordered = ordered.set_axis(ordered_index)
            entry_open = ordered["Open"].astype(float).shift(-1)
            exit_close = ordered["Close"].astype(float).shift(-horizon)
            target = exit_close / entry_open.replace(0.0, np.nan) - 1.0
            entry_date = pd.Series(
                ordered.index,
                index=ordered.index,
                dtype="datetime64[ns]",
            ).shift(-1)
            end_date = pd.Series(
                ordered.index,
                index=ordered.index,
                dtype="datetime64[ns]",
            ).shift(-horizon)
            group_index = result.loc[str(ticker)].index
            keys = pd.MultiIndex.from_product(
                ((str(ticker),), group_index),
                names=INDEX_NAMES,
            )
            result.loc[keys, f"executable_return_{horizon}"] = (
                target.reindex(group_index).to_numpy()
            )
            result.loc[keys, f"executable_entry_date_{horizon}"] = (
                entry_date.reindex(group_index).to_numpy()
            )
            result.loc[keys, f"executable_label_end_date_{horizon}"] = (
                end_date.reindex(group_index).to_numpy()
            )
    return result


def chronological_purged_folds(
    frame: pd.DataFrame,
    horizon: int,
    n_folds: int = 5,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Return expanding date folds with exact label-end purging."""
    _validate_index(frame)
    checked_horizon = _validate_horizons((horizon,))[0]
    if isinstance(n_folds, bool) or not isinstance(n_folds, Integral):
        raise TypeError("n_folds must be an integer")
    if int(n_folds) < 2:
        raise ValueError("n_folds must be at least 2")
    target_name = f"executable_return_{checked_horizon}"
    end_name = f"executable_label_end_date_{checked_horizon}"
    missing = [name for name in (target_name, end_name) if name not in frame]
    if missing:
        raise ValueError(f"frame is missing executable target columns: {missing}")

    observation_dates = pd.Series(
        frame.index.get_level_values("observation_date"),
        index=frame.index,
    )
    unique_dates = np.asarray(sorted(observation_dates.unique()))
    edges = np.linspace(0, len(unique_dates), int(n_folds) + 1, dtype=int)
    folds = []
    for fold in range(1, int(n_folds)):
        test_dates = unique_dates[edges[fold] : edges[fold + 1]]
        if len(test_dates) == 0:
            continue
        test_start = pd.Timestamp(test_dates[0])
        train_mask = (
            frame[target_name].notna()
            & frame[end_name].notna()
            & (frame[end_name] < test_start)
        )
        test_mask = (
            frame[target_name].notna()
            & frame[end_name].notna()
            & observation_dates.isin(test_dates)
        )
        train_index = np.flatnonzero(train_mask.to_numpy())
        test_index = np.flatnonzero(test_mask.to_numpy())
        if len(train_index) and len(test_index):
            folds.append((train_index, test_index))
    return folds


def walk_forward_direction_predictions(
    frame: pd.DataFrame,
    *,
    horizon: int,
    feature_sets: Mapping[str, Sequence[str]],
    n_folds: int = 5,
    minimum_samples: int = 100,
) -> pd.DataFrame:
    """Fit class-balanced logistic challengers in purged expanding folds."""
    checked_horizon = _validate_horizons((horizon,))[0]
    if not feature_sets:
        raise ValueError("feature_sets must not be empty")
    if (
        isinstance(minimum_samples, bool)
        or not isinstance(minimum_samples, Integral)
        or int(minimum_samples) <= 0
    ):
        raise ValueError("minimum_samples must be a positive integer")
    checked_sets = {}
    for name, columns in feature_sets.items():
        selected = tuple(columns)
        if not name or not selected:
            raise ValueError("feature set names and columns must not be empty")
        missing = [column for column in selected if column not in frame]
        if missing:
            raise ValueError(f"feature set {name} is missing columns: {missing}")
        checked_sets[str(name)] = selected

    target_name = f"executable_return_{checked_horizon}"
    output = []
    folds = chronological_purged_folds(frame, checked_horizon, n_folds)
    for fold_number, (train_index, test_index) in enumerate(folds, start=1):
        train = frame.iloc[train_index]
        test = frame.iloc[test_index]
        if len(train) < int(minimum_samples):
            continue
        y_train = direction_labels(train[target_name], checked_horizon)
        y_test = direction_labels(test[target_name], checked_horizon)
        classes, counts = np.unique(y_train, return_counts=True)
        if len(classes) < 2:
            continue
        majority = str(classes[np.argmax(counts)])
        output.append(
            _prediction_rows(
                test,
                y_test,
                np.repeat(majority, len(test)),
                checked_horizon,
                fold_number,
                "majority_baseline",
                len(train),
            )
        )
        for name, columns in checked_sets.items():
            x_train, x_test = training_only_design(train, test, columns)
            model = LogisticRegression(
                class_weight="balanced",
                max_iter=1_000,
                random_state=0,
                solver="liblinear",
            )
            model.fit(x_train, y_train)
            predicted = _logistic_predict(model, x_test)
            output.append(
                _prediction_rows(
                    test,
                    y_test,
                    predicted,
                    checked_horizon,
                    fold_number,
                    name,
                    len(train),
                )
            )
    if not output:
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
            )
        )
    return pd.concat(output, ignore_index=True)


def walk_forward_ridge_predictions(
    frame: pd.DataFrame,
    *,
    horizon: int,
    feature_columns: Sequence[str],
    n_folds: int = 5,
    minimum_samples: int = 100,
    specification: str = "ridge_baseline",
) -> pd.DataFrame:
    """Evaluate Ridge directions on the same executable, purged folds."""
    checked_horizon = _validate_horizons((horizon,))[0]
    columns = tuple(feature_columns)
    if not columns:
        raise ValueError("feature_columns must not be empty")
    missing = [column for column in columns if column not in frame]
    if missing:
        raise ValueError(f"frame is missing feature columns: {missing}")
    checked_specification = str(specification).strip()
    if not checked_specification:
        raise ValueError("specification must not be empty")
    if (
        isinstance(minimum_samples, bool)
        or not isinstance(minimum_samples, Integral)
        or int(minimum_samples) <= 0
    ):
        raise ValueError("minimum_samples must be a positive integer")

    target_name = f"executable_return_{checked_horizon}"
    output = []
    folds = chronological_purged_folds(frame, checked_horizon, n_folds)
    for fold_number, (train_index, test_index) in enumerate(folds, start=1):
        train = frame.iloc[train_index]
        test = frame.iloc[test_index]
        if len(train) < int(minimum_samples):
            continue
        x_train, x_test = training_only_design(train, test, columns)
        target = train[target_name].to_numpy(dtype=float)
        if np.min(target) == np.max(target):
            continue
        model = Ridge(alpha=1.0, solver="lsqr")
        model.fit(x_train, target)
        predicted_return = (
            np.sum(x_test * model.coef_[None, :], axis=1)
            + float(model.intercept_)
        )
        rows = _prediction_rows(
            test,
            direction_labels(test[target_name], checked_horizon),
            direction_labels(
                pd.Series(predicted_return),
                checked_horizon,
            ),
            checked_horizon,
            fold_number,
            checked_specification,
            len(train),
        )
        rows["predicted_return"] = predicted_return
        output.append(rows)
    if not output:
        return pd.DataFrame()
    return pd.concat(output, ignore_index=True)


def walk_forward_boosted_predictions(
    frame: pd.DataFrame,
    *,
    horizon: int,
    feature_columns: Sequence[str],
    n_folds: int = 5,
    minimum_samples: int = 100,
) -> pd.DataFrame:
    """Evaluate a shallow nonlinear context challenger on purged folds."""
    checked_horizon = _validate_horizons((horizon,))[0]
    columns = tuple(feature_columns)
    if not columns:
        raise ValueError("feature_columns must not be empty")
    missing = [column for column in columns if column not in frame]
    if missing:
        raise ValueError(f"frame is missing feature columns: {missing}")
    if (
        isinstance(minimum_samples, bool)
        or not isinstance(minimum_samples, Integral)
        or int(minimum_samples) <= 0
    ):
        raise ValueError("minimum_samples must be a positive integer")

    target_name = f"executable_return_{checked_horizon}"
    output = []
    folds = chronological_purged_folds(frame, checked_horizon, n_folds)
    for fold_number, (train_index, test_index) in enumerate(folds, start=1):
        train = frame.iloc[train_index]
        test = frame.iloc[test_index]
        if len(train) < int(minimum_samples):
            continue
        target = direction_labels(train[target_name], checked_horizon)
        classes, counts = np.unique(target, return_counts=True)
        if len(classes) < 2:
            continue
        class_weights = {
            label: len(target) / (len(classes) * count)
            for label, count in zip(classes, counts)
        }
        sample_weight = np.asarray(
            [class_weights[label] for label in target],
            dtype=float,
        )
        x_train, x_test = training_only_design(train, test, columns)
        model = HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=80,
            max_leaf_nodes=15,
            max_depth=3,
            min_samples_leaf=max(20, min(100, len(train) // 100)),
            l2_regularization=1.0,
            random_state=0,
        )
        model.fit(x_train, target, sample_weight=sample_weight)
        output.append(
            _prediction_rows(
                test,
                direction_labels(test[target_name], checked_horizon),
                model.predict(x_test),
                checked_horizon,
                fold_number,
                "boosted_full_context",
                len(train),
            )
        )
    if not output:
        return pd.DataFrame()
    return pd.concat(output, ignore_index=True)


def evaluate_direction_ablation(predictions: pd.DataFrame) -> pd.DataFrame:
    """Aggregate direction metrics for each challenger specification."""
    required = {
        "specification",
        "actual_return",
        "actual_direction",
        "predicted_direction",
        "fold",
    }
    missing = sorted(required.difference(predictions.columns))
    if missing:
        raise ValueError(f"predictions are missing columns: {missing}")
    rows = []
    for name, group in predictions.groupby("specification", sort=True):
        actual = group["actual_direction"].astype(str)
        predicted = group["predicted_direction"].astype(str)
        rows.append(
            {
                "specification": name,
                "sample_count": len(group),
                "coverage": float(predicted.notna().mean()),
                "balanced_accuracy": float(
                    balanced_accuracy_score(actual, predicted)
                ),
                "macro_f1": float(
                    f1_score(
                        actual,
                        predicted,
                        labels=("down", "neutral", "up"),
                        average="macro",
                        zero_division=0,
                    )
                ),
                "down_precision": float(
                    precision_score(
                        actual,
                        predicted,
                        labels=("down",),
                        average="macro",
                        zero_division=0,
                    )
                ),
                "down_recall": float(
                    recall_score(
                        actual,
                        predicted,
                        labels=("down",),
                        average="macro",
                        zero_division=0,
                    )
                ),
                "mean_return_predicted_down": _mean_return(group, "down"),
                "mean_return_predicted_neutral": _mean_return(group, "neutral"),
                "mean_return_predicted_up": _mean_return(group, "up"),
            }
        )
    return pd.DataFrame(rows).sort_values("specification").reset_index(drop=True)


def training_only_design(train, test, columns):
    """Build train-fitted numeric designs without learning from test rows."""
    train_raw = train.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    test_raw = test.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    train_raw = train_raw.replace((np.inf, -np.inf), np.nan)
    test_raw = test_raw.replace((np.inf, -np.inf), np.nan)
    raw_median = train_raw.median().fillna(0.0)
    quartile_25 = train_raw.quantile(0.25).fillna(raw_median)
    quartile_75 = train_raw.quantile(0.75).fillna(raw_median)
    robust_scale = (quartile_75 - quartile_25).abs()
    robust_scale = robust_scale.where(
        robust_scale > 1e-12,
        raw_median.abs().mul(1e-6).clip(lower=1e-12),
    )
    lower = pd.concat(
        (
            train_raw.quantile(0.005).fillna(raw_median),
            raw_median - 20.0 * robust_scale,
        ),
        axis=1,
    ).max(axis=1)
    upper = pd.concat(
        (
            train_raw.quantile(0.995).fillna(raw_median),
            raw_median + 20.0 * robust_scale,
        ),
        axis=1,
    ).min(axis=1)
    train_clipped = train_raw.clip(lower=lower, upper=upper, axis="columns")
    test_clipped = test_raw.clip(lower=lower, upper=upper, axis="columns")
    medians = train_clipped.median().fillna(0.0)
    train_values = train_clipped.fillna(medians).to_numpy(dtype=float)
    test_values = test_clipped.fillna(medians).to_numpy(dtype=float)
    train_missing = train_raw.isna().to_numpy(dtype=float)
    test_missing = test_raw.isna().to_numpy(dtype=float)
    train_design = np.concatenate((train_values, train_missing), axis=1)
    test_design = np.concatenate((test_values, test_missing), axis=1)
    means = train_design.mean(axis=0)
    scales = train_design.std(axis=0)
    scales[~np.isfinite(scales) | (scales < 1e-12)] = 1.0
    return (
        np.clip((train_design - means) / scales, -12.0, 12.0),
        np.clip((test_design - means) / scales, -12.0, 12.0),
    )


_training_only_design = training_only_design


def _logistic_predict(model, design):
    decision = np.sum(
        design[:, None, :] * model.coef_[None, :, :],
        axis=2,
    ) + model.intercept_
    if len(model.classes_) == 2:
        return np.where(decision[:, 0] > 0.0, model.classes_[1], model.classes_[0])
    return model.classes_[np.argmax(decision, axis=1)]


def _prediction_rows(
    test,
    actual,
    predicted,
    horizon,
    fold,
    specification,
    training_samples,
):
    return pd.DataFrame(
        {
            "ticker": test.index.get_level_values("ticker"),
            "observation_date": test.index.get_level_values("observation_date"),
            "horizon": horizon,
            "fold": fold,
            "specification": specification,
            "actual_return": test[f"executable_return_{horizon}"].to_numpy(
                dtype=float
            ),
            "actual_direction": np.asarray(actual, dtype=object),
            "predicted_direction": np.asarray(predicted, dtype=object),
            "training_samples": training_samples,
        }
    )


def direction_labels(returns, horizon):
    """Map executable returns to versioned direction bands."""
    checked_horizon = _validate_horizons((horizon,))[0]
    values = pd.to_numeric(returns, errors="coerce").to_numpy(dtype=float)
    band = NEUTRAL_BANDS[checked_horizon]
    return np.select(
        (values < -band, values > band),
        ("down", "up"),
        default="neutral",
    )


_directions = direction_labels


def _mean_return(group, direction):
    selected = group.loc[
        group["predicted_direction"] == direction,
        "actual_return",
    ]
    return float(selected.mean()) if len(selected) else np.nan


def _validate_index(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    if (
        not isinstance(frame.index, pd.MultiIndex)
        or tuple(frame.index.names) != INDEX_NAMES
    ):
        raise ValueError(
            "frame index must be a MultiIndex named ticker and observation_date"
        )
    if frame.index.has_duplicates:
        raise ValueError("frame index must not contain duplicate keys")


def _validate_horizons(horizons):
    checked = []
    for horizon in horizons:
        if (
            isinstance(horizon, bool)
            or not isinstance(horizon, Integral)
            or int(horizon) not in NEUTRAL_BANDS
        ):
            raise ValueError("horizons must be supported positive session counts")
        checked.append(int(horizon))
    if not checked:
        raise ValueError("horizons must not be empty")
    return tuple(dict.fromkeys(checked))
