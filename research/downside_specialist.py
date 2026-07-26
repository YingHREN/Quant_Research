"""Executable path-risk labels and pressure-regime specialist research."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from research.market_direction_model import _training_only_design


DOWNSIDE_THRESHOLDS = {5: -0.05, 20: -0.10}
INDEX_NAMES = ("ticker", "observation_date")
PRESSURE_REGIMES = frozenset(
    ("under_pressure", "correction", "acute_selloff")
)


def attach_next_open_mae_targets(
    frame: pd.DataFrame,
    histories: Mapping[str, pd.DataFrame],
    horizons: Sequence[int] = (5, 20),
) -> pd.DataFrame:
    """Attach next-open maximum adverse excursion and binary path events."""
    _validate_frame(frame)
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    checked_horizons = _validate_horizons(horizons)
    result = frame.copy(deep=True)
    for horizon in checked_horizons:
        result[f"executable_mae_{horizon}"] = np.nan
        result[f"downside_event_{horizon}"] = np.nan
        result[f"downside_label_end_date_{horizon}"] = pd.NaT

    for ticker in result.index.get_level_values("ticker").unique():
        source = histories.get(str(ticker))
        if source is None or not isinstance(source, pd.DataFrame) or source.empty:
            continue
        missing = [column for column in ("Open", "Low") if column not in source]
        if missing:
            raise ValueError(
                f"history for {ticker} is missing columns: {missing}"
            )
        history = source.loc[:, ("Open", "Low")].copy(deep=True)
        history.index = pd.DatetimeIndex(history.index).tz_localize(None)
        if history.index.has_duplicates:
            raise ValueError(f"history for {ticker} contains duplicate dates")
        history = history.sort_index().apply(pd.to_numeric, errors="coerce")
        group_dates = result.loc[str(ticker)].index
        keys = pd.MultiIndex.from_product(
            ((str(ticker),), group_dates),
            names=INDEX_NAMES,
        )
        entry_open = history["Open"].shift(-1).replace(0.0, np.nan)
        date_series = pd.Series(
            history.index,
            index=history.index,
            dtype="datetime64[ns]",
        )
        for horizon in checked_horizons:
            future_lows = pd.concat(
                [
                    history["Low"].shift(-offset)
                    for offset in range(1, horizon + 1)
                ],
                axis=1,
            )
            minimum_low = future_lows.min(axis=1, skipna=False)
            label_end = date_series.shift(-horizon)
            complete = (
                entry_open.notna()
                & (entry_open > 0.0)
                & future_lows.notna().all(axis=1)
                & label_end.notna()
            )
            mae = (minimum_low / entry_open - 1.0).where(complete)
            event = (
                (mae <= DOWNSIDE_THRESHOLDS[horizon])
                .astype(float)
                .where(complete)
            )
            result.loc[keys, f"executable_mae_{horizon}"] = (
                mae.reindex(group_dates).to_numpy()
            )
            result.loc[keys, f"downside_event_{horizon}"] = (
                event.reindex(group_dates).to_numpy()
            )
            result.loc[keys, f"downside_label_end_date_{horizon}"] = (
                label_end.where(complete).reindex(group_dates).to_numpy()
            )
    return result


def walk_forward_downside_predictions(
    frame: pd.DataFrame,
    *,
    horizon: int,
    feature_columns: Sequence[str],
    n_folds: int = 5,
    minimum_samples: int = 1_000,
) -> pd.DataFrame:
    """Fit a pressure-only binary Logistic on purged expanding folds."""
    _validate_frame(frame)
    checked_horizon = _validate_horizons((horizon,))[0]
    columns = tuple(str(column) for column in feature_columns)
    if not columns or any(not column for column in columns):
        raise ValueError("feature_columns must not be empty")
    required = (
        *columns,
        "regime",
        f"executable_mae_{checked_horizon}",
        f"downside_event_{checked_horizon}",
        f"downside_label_end_date_{checked_horizon}",
    )
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"frame is missing specialist columns: {missing}")
    if int(n_folds) < 2:
        raise ValueError("n_folds must be at least 2")
    if int(minimum_samples) <= 0:
        raise ValueError("minimum_samples must be positive")

    target_name = f"downside_event_{checked_horizon}"
    end_name = f"downside_label_end_date_{checked_horizon}"
    observation_dates = pd.Series(
        frame.index.get_level_values("observation_date"),
        index=frame.index,
    )
    unique_dates = np.asarray(sorted(observation_dates.unique()))
    edges = np.linspace(0, len(unique_dates), int(n_folds) + 1, dtype=int)
    output = []
    for fold in range(1, int(n_folds)):
        test_dates = unique_dates[edges[fold] : edges[fold + 1]]
        if len(test_dates) == 0:
            continue
        test_start = pd.Timestamp(test_dates[0])
        pressure = frame["regime"].astype(str).isin(PRESSURE_REGIMES)
        train_mask = (
            pressure
            & frame[target_name].notna()
            & frame[end_name].notna()
            & (frame[end_name] < test_start)
        )
        test_mask = (
            pressure
            & frame[target_name].notna()
            & frame[end_name].notna()
            & observation_dates.isin(test_dates)
        )
        train = frame.loc[train_mask]
        test = frame.loc[test_mask]
        if len(train) < int(minimum_samples) or test.empty:
            continue
        target = train[target_name].astype(int).to_numpy()
        if set(np.unique(target)) != {0, 1}:
            continue
        x_train, x_test = _training_only_design(train, test, columns)
        model = LogisticRegression(
            class_weight="balanced",
            max_iter=1_000,
            random_state=0,
            solver="liblinear",
        )
        model.fit(x_train, target)
        positive_index = int(np.flatnonzero(model.classes_ == 1)[0])
        score = model.predict_proba(x_test)[:, positive_index]
        output.append(
            pd.DataFrame(
                {
                    "ticker": test.index.get_level_values("ticker"),
                    "observation_date": test.index.get_level_values(
                        "observation_date"
                    ),
                    "horizon": checked_horizon,
                    "fold": fold,
                    "regime": test["regime"].astype(str).to_numpy(),
                    "specification": "pressure_downside_logistic_v1",
                    "actual_event": test[target_name].astype(bool).to_numpy(),
                    "actual_mae": test[
                        f"executable_mae_{checked_horizon}"
                    ].to_numpy(dtype=float),
                    "predicted_event": score >= 0.5,
                    "predicted_score": score,
                    "training_samples": len(train),
                    "training_event_rate": float(np.mean(target)),
                    "training_label_end_max": pd.Timestamp(
                        train[end_name].max()
                    ),
                }
            )
        )
    if not output:
        return _empty_prediction_frame()
    return pd.concat(output, ignore_index=True, sort=False)


def _empty_prediction_frame():
    return pd.DataFrame(
        columns=(
            "ticker",
            "observation_date",
            "horizon",
            "fold",
            "regime",
            "specification",
            "actual_event",
            "actual_mae",
            "predicted_event",
            "predicted_score",
            "training_samples",
            "training_event_rate",
            "training_label_end_max",
        )
    )


def _validate_frame(frame):
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
    for raw_horizon in horizons:
        if (
            isinstance(raw_horizon, bool)
            or not isinstance(raw_horizon, (int, np.integer))
        ):
            raise ValueError("unsupported downside horizon")
        horizon = int(raw_horizon)
        if horizon not in DOWNSIDE_THRESHOLDS:
            raise ValueError("unsupported downside horizon")
        if horizon not in checked:
            checked.append(horizon)
    if not checked:
        raise ValueError("horizons must not be empty")
    return tuple(checked)
