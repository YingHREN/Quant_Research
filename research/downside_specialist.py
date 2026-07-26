"""Executable path-risk labels and pressure-regime specialist research."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from research.market_direction_model import _training_only_design


DOWNSIDE_THRESHOLDS = {5: -0.05, 20: -0.10}
INDEX_NAMES = ("ticker", "observation_date")
FEATURE_INPUT_ABS_CAP = 1.0e12
LOGISTIC_REGULARIZATION_C = 0.1
LOGIT_ABS_CAP = 35.0
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
        x_train, x_test = _specialist_design(train, test, columns)
        model = LogisticRegression(
            C=LOGISTIC_REGULARIZATION_C,
            class_weight="balanced",
            max_iter=1_000,
            random_state=0,
            solver="liblinear",
        )
        model.fit(x_train, target)
        score = _stable_positive_probability(model, x_test)
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


def _specialist_design(train, test, columns):
    """Bound finite source values before the shared training-only transform."""
    train_safe = train.copy(deep=False)
    test_safe = test.copy(deep=False)
    train_safe = train_safe.assign(
        **{
            column: pd.to_numeric(train[column], errors="coerce").clip(
                -FEATURE_INPUT_ABS_CAP,
                FEATURE_INPUT_ABS_CAP,
            )
            for column in columns
        }
    )
    test_safe = test_safe.assign(
        **{
            column: pd.to_numeric(test[column], errors="coerce").clip(
                -FEATURE_INPUT_ABS_CAP,
                FEATURE_INPUT_ABS_CAP,
            )
            for column in columns
        }
    )
    return _training_only_design(train_safe, test_safe, columns)


def _stable_positive_probability(model, design):
    """Return binary probabilities without allowing an unstable dot product."""
    coefficients = np.asarray(model.coef_, dtype=float)
    intercept = np.asarray(model.intercept_, dtype=float)
    if (
        coefficients.shape[0] != 1
        or not np.isfinite(coefficients).all()
        or not np.isfinite(intercept).all()
    ):
        raise RuntimeError("specialist Logistic produced invalid coefficients")
    decision = np.sum(
        np.asarray(design, dtype=float) * coefficients[0],
        axis=1,
    ) + intercept[0]
    if not np.isfinite(decision).all():
        raise RuntimeError("specialist Logistic produced invalid scores")
    decision = np.clip(decision, -LOGIT_ABS_CAP, LOGIT_ABS_CAP)
    return 1.0 / (1.0 + np.exp(-decision))


def evaluate_downside_predictions(
    predictions: pd.DataFrame,
    *,
    group_map=None,
    minimum_fold_samples: int = 30,
) -> pd.DataFrame:
    """Report binary path-risk metrics on matched rows and folds."""
    required = {
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
    }
    missing = sorted(required.difference(predictions.columns))
    if missing:
        raise ValueError(f"predictions are missing columns: {missing}")
    minimum = int(minimum_fold_samples)
    if minimum < 1:
        raise ValueError("minimum_fold_samples must be positive")
    checked = predictions.copy(deep=True)
    checked["ticker"] = checked["ticker"].astype(str).str.upper()
    checked["observation_date"] = pd.to_datetime(
        checked["observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    groups = {
        str(ticker).strip().upper(): str(group)
        for ticker, group in (group_map or {}).items()
    }
    checked["study_group"] = checked["ticker"].map(
        lambda ticker: groups.get(ticker, "other")
    )
    rows = []
    for horizon, horizon_rows in checked.groupby("horizon", sort=True):
        modes = {
            "overlapping": horizon_rows,
            "non_overlapping": _non_overlapping_rows(
                horizon_rows,
                int(horizon),
            ),
        }
        for sample_mode, sample_rows in modes.items():
            scope_frames = {"all": sample_rows}
            for scope in ("semiconductor", "software", "other"):
                selected_scope = sample_rows.loc[
                    sample_rows["study_group"] == scope
                ]
                if not selected_scope.empty:
                    scope_frames[scope] = selected_scope
            for scope, scope_rows in scope_frames.items():
                regime_frames = {"all_pressure": scope_rows}
                for regime in sorted(PRESSURE_REGIMES):
                    selected_regime = scope_rows.loc[
                        scope_rows["regime"] == regime
                    ]
                    if not selected_regime.empty:
                        regime_frames[regime] = selected_regime
                for regime_scope, selected in regime_frames.items():
                    comparisons = _fold_comparisons(
                        selected,
                        baseline="ridge_down",
                        minimum_fold_samples=minimum,
                    )
                    for specification, model_rows in selected.groupby(
                        "specification",
                        sort=True,
                    ):
                        metrics = _binary_metric_row(model_rows)
                        comparable, win_rate = comparisons.get(
                            str(specification),
                            (0, np.nan),
                        )
                        metrics.update(
                            {
                                "scope": scope,
                                "horizon": int(horizon),
                                "regime_scope": regime_scope,
                                "sample_mode": sample_mode,
                                "specification": str(specification),
                                "comparable_fold_count": comparable,
                                "fold_win_rate_vs_ridge_down": win_rate,
                            }
                        )
                        rows.append(metrics)
    return pd.DataFrame(rows)


def downside_promotion_decision(metrics: pd.DataFrame) -> dict:
    """Apply frozen research gates while retaining the production block."""
    reasons = []

    def row(scope, horizon, regime, mode, specification):
        selected = metrics.loc[
            (metrics["scope"] == scope)
            & (metrics["horizon"] == horizon)
            & (metrics["regime_scope"] == regime)
            & (metrics["sample_mode"] == mode)
            & (metrics["specification"] == specification)
        ]
        return None if selected.empty else selected.iloc[0]

    specialist_name = "pressure_downside_logistic_v1"
    for mode in ("overlapping", "non_overlapping"):
        specialist = row(
            "all", 5, "all_pressure", mode, specialist_name
        )
        ridge = row("all", 5, "all_pressure", mode, "ridge_down")
        prefix = f"5d:{mode}"
        if specialist is None or ridge is None:
            reasons.append(f"{prefix}:comparison_missing")
            continue
        if (
            specialist["balanced_accuracy"]
            < ridge["balanced_accuracy"] + 0.01
        ):
            reasons.append(f"{prefix}:balanced_accuracy_gain_below_0.01")
        if specialist["pr_auc"] <= specialist["event_rate"]:
            reasons.append(f"{prefix}:pr_auc_not_above_event_rate")
        if specialist["recall"] < ridge["recall"]:
            reasons.append(f"{prefix}:recall_below_ridge")
        if (
            pd.isna(specialist["fold_win_rate_vs_ridge_down"])
            or specialist["fold_win_rate_vs_ridge_down"] <= 0.5
        ):
            reasons.append(f"{prefix}:fold_majority_not_won")

    for scope in ("semiconductor", "software"):
        for mode in ("overlapping", "non_overlapping"):
            specialist = row(
                scope, 5, "all_pressure", mode, specialist_name
            )
            ridge = row(scope, 5, "all_pressure", mode, "ridge_down")
            prefix = f"5d:{scope}:{mode}"
            if specialist is None or ridge is None:
                reasons.append(f"{prefix}:comparison_missing")
                continue
            if (
                specialist["balanced_accuracy"] + 0.005
                < ridge["balanced_accuracy"]
            ):
                reasons.append(f"{prefix}:subgroup_degraded")
            if (
                pd.isna(specialist["fold_win_rate_vs_ridge_down"])
                or specialist["fold_win_rate_vs_ridge_down"] <= 0.5
            ):
                reasons.append(f"{prefix}:fold_majority_not_won")

    regime_wins = 0
    available_regimes = 0
    for regime in PRESSURE_REGIMES:
        specialist = row(
            "all", 5, regime, "overlapping", specialist_name
        )
        if specialist is None:
            continue
        win_rate = specialist["fold_win_rate_vs_ridge_down"]
        if pd.notna(win_rate):
            available_regimes += 1
            regime_wins += int(win_rate > 0.5)
    if available_regimes < 3 or regime_wins < 2:
        reasons.append("5d:pressure_regimes:majority_not_won_in_two_states")

    twenty_available = False
    for mode in ("overlapping", "non_overlapping"):
        specialist = row(
            "all", 20, "all_pressure", mode, specialist_name
        )
        ridge = row("all", 20, "all_pressure", mode, "ridge_down")
        if specialist is None or ridge is None:
            continue
        twenty_available = True
        if (
            specialist["balanced_accuracy"] + 0.01
            < ridge["balanced_accuracy"]
        ):
            reasons.append(f"20d:{mode}:balanced_accuracy_degraded")
    if not twenty_available:
        reasons.append("20d_comparison_missing")

    return {
        "eligible": False,
        "metric_gate_passed": not reasons,
        "reasons": reasons,
        "production_block_reason": (
            "survivorship_and_point_in_time_classification_history_missing"
        ),
    }


def _binary_metric_row(rows):
    actual = rows["actual_event"].astype(bool)
    predicted = rows["predicted_event"].astype(bool)
    score = pd.to_numeric(rows["predicted_score"], errors="coerce")
    valid = score.notna() & np.isfinite(score)
    actual = actual.loc[valid]
    predicted = predicted.loc[valid]
    score = score.loc[valid].clip(0.0, 1.0)
    tp = int((predicted & actual).sum())
    fp = int((predicted & ~actual).sum())
    fn = int((~predicted & actual).sum())
    tn = int((~predicted & ~actual).sum())
    recall = _ratio(tp, tp + fn)
    specificity = _ratio(tn, tn + fp)
    both_classes = actual.nunique() == 2
    return {
        "sample_count": len(actual),
        "coverage": len(actual) / len(rows) if len(rows) else np.nan,
        "event_rate": float(actual.mean()) if len(actual) else np.nan,
        "signal_rate": float(predicted.mean()) if len(predicted) else np.nan,
        "precision": _ratio(tp, tp + fp),
        "recall": recall,
        "specificity": specificity,
        "balanced_accuracy": (
            np.nan
            if pd.isna(recall) or pd.isna(specificity)
            else (recall + specificity) / 2.0
        ),
        "roc_auc": (
            float(roc_auc_score(actual, score))
            if both_classes
            else np.nan
        ),
        "pr_auc": (
            float(average_precision_score(actual, score))
            if both_classes
            else np.nan
        ),
        "brier_score": (
            float(np.mean((score.to_numpy() - actual.to_numpy()) ** 2))
            if len(actual)
            else np.nan
        ),
        "mean_mae_when_signaled": _finite_mean(
            rows.loc[predicted.index[predicted], "actual_mae"]
        ),
    }


def _fold_comparisons(
    predictions,
    *,
    baseline,
    minimum_fold_samples,
):
    by_spec = {
        str(specification): selected
        for specification, selected in predictions.groupby(
            "specification",
            sort=False,
        )
    }
    baseline_rows = by_spec.get(baseline)
    if baseline_rows is None:
        return {
            specification: (0, np.nan)
            for specification in by_spec
        }
    result = {baseline: (0, np.nan)}
    for specification, challenger in by_spec.items():
        if specification == baseline:
            continue
        wins = []
        folds = sorted(
            set(baseline_rows["fold"]).intersection(challenger["fold"])
        )
        for fold in folds:
            current = baseline_rows.loc[baseline_rows["fold"] == fold]
            selected = challenger.loc[challenger["fold"] == fold]
            if (
                len(current) < minimum_fold_samples
                or len(selected) < minimum_fold_samples
                or current["actual_event"].nunique() < 2
                or selected["actual_event"].nunique() < 2
            ):
                continue
            delta = (
                _binary_metric_row(selected)["balanced_accuracy"]
                - _binary_metric_row(current)["balanced_accuracy"]
            )
            wins.append(
                1.0 if delta > 1e-12 else 0.0 if delta < -1e-12 else 0.5
            )
        result[specification] = (
            len(wins),
            np.nan if not wins else float(np.mean(wins)),
        )
    return result


def _non_overlapping_rows(predictions, horizon):
    ordered = predictions.sort_values(
        ["specification", "fold", "ticker", "observation_date"],
        kind="mergesort",
    ).copy()
    position = ordered.groupby(
        ["specification", "fold", "ticker"],
        sort=False,
    ).cumcount()
    return ordered.loc[position.mod(int(horizon)) == 0].copy()


def _ratio(numerator, denominator):
    return np.nan if denominator == 0 else numerator / denominator


def _finite_mean(values):
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric[np.isfinite(numeric)]
    return np.nan if finite.empty else float(finite.mean())


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
