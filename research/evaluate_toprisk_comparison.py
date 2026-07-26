"""Unified point-in-time comparison of downside-risk signals."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from web.forecasts.decision import build_forecast_risk_context
from web.forecasts.ridge import bearish_turn_assessment
from web.market_groups import market_group_for_ticker


SIGNAL_KEYS = (
    "ridge_down",
    "immediate_8",
    "memory_12",
    "toprisk_confirmed",
    "toprisk_stateful",
    "ridge_plus_toprisk",
)
EVALUATION_GROUPS = ("all", "semiconductor", "software", "other")


def build_comparison_frame(
    histories,
    forecasts=None,
    context=None,
    feature_frame=None,
):
    """Attach versioned signal definitions to a causal risk context."""
    if not isinstance(histories, dict):
        histories = dict(histories)
    risk = (
        build_forecast_risk_context(histories)
        if context is None
        else context.copy(deep=True)
    )
    if not isinstance(risk.index, pd.MultiIndex):
        raise ValueError("risk context requires a MultiIndex")
    risk.index = risk.index.set_names(["ticker", "observation_date"])
    frame = risk.copy(deep=True)
    frame["close"] = np.nan
    frame["low"] = np.nan
    for ticker in frame.index.get_level_values("ticker").unique():
        history = histories.get(ticker)
        if not isinstance(history, pd.DataFrame) or history.empty:
            continue
        dates = frame.loc[ticker].index
        aligned = history.sort_index().reindex(dates)
        frame.loc[(ticker, dates), "close"] = pd.to_numeric(
            aligned["Close"], errors="coerce"
        ).to_numpy()
        low = aligned["Low"] if "Low" in aligned else aligned["Close"]
        frame.loc[(ticker, dates), "low"] = pd.to_numeric(
            low, errors="coerce"
        ).to_numpy()

    forecast_frame = _forecast_frame(forecasts)
    ridge = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    immediate = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    if forecast_frame is not None:
        aligned = forecast_frame.reindex(frame.index)
        direction = aligned.get("raw_direction")
        if direction is None:
            direction = aligned.get("direction")
        if direction is not None:
            present = direction.notna()
            ridge.loc[present] = (
                direction.loc[present].astype(str).str.lower() == "down"
            )
        score = pd.to_numeric(
            aligned.get("bearish_turn_score"), errors="coerce"
        )
        if score is not None:
            present = score.notna()
            immediate.loc[present] = score.loc[present] >= 70.0
    if feature_frame is not None:
        rebuilt_immediate = _immediate_signal_from_features(
            feature_frame,
            frame.index,
        )
        missing = immediate.isna() & rebuilt_immediate.notna()
        immediate.loc[missing] = rebuilt_immediate.loc[missing]

    memory = _threshold_signal(frame.get("individual_risk_score"), 30.0)
    raw_top = _state_signal(
        frame.get("high_level_distribution_raw_state"),
        {"confirmed"},
    )
    stateful_top = _state_signal(
        frame.get("high_level_distribution_state"),
        {"high", "confirmed", "fading"},
    )
    combined = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    available = ridge.notna() & stateful_top.notna()
    combined.loc[available] = (
        ridge.loc[available] | stateful_top.loc[available]
    )
    signals = {
        "ridge_down": ridge,
        "immediate_8": immediate,
        "memory_12": memory,
        "toprisk_confirmed": raw_top,
        "toprisk_stateful": stateful_top,
        "ridge_plus_toprisk": combined,
    }
    for key, values in signals.items():
        frame[f"signal_{key}"] = values
    return frame.sort_index()


def _immediate_signal_from_features(features, index):
    if not isinstance(features, pd.DataFrame):
        raise TypeError("feature_frame must be a DataFrame or None")
    result = pd.Series(pd.NA, index=index, dtype="boolean")
    if features.empty:
        return result
    if not isinstance(features.index, pd.MultiIndex):
        raise ValueError("feature_frame requires a MultiIndex")
    if features.index.has_duplicates:
        raise ValueError("feature_frame contains duplicate point-in-time keys")
    aligned = features.reindex(index)
    evidence_columns = (
        "pressure_distribution_day",
        "close_vs_ema20_pct",
        "volume_ratio",
        "volume_change",
        "pressure_close_location",
        "pressure_signed_volume_proxy",
        "stock_sector_relative_strength_20",
        "pressure_failed_breakout",
        "pivot_distance_pct",
    )
    available_columns = [
        column for column in evidence_columns if column in aligned
    ]
    if not available_columns:
        return result
    present = aligned.loc[:, available_columns].notna().any(axis=1)
    scores = aligned.loc[present].apply(
        lambda row: bearish_turn_assessment(row)[0],
        axis=1,
    )
    result.loc[present] = scores >= 70.0
    return result


def evaluate_signals(
    frame,
    horizons=(5, 10, 20),
    adverse_threshold=-0.05,
    groups=None,
):
    """Return transparent classification and outcome metrics."""
    checked_horizons = _horizons(horizons)
    threshold = float(adverse_threshold)
    if not math.isfinite(threshold) or threshold >= 0:
        raise ValueError("adverse_threshold must be finite and negative")
    if not isinstance(frame.index, pd.MultiIndex):
        raise ValueError("frame requires a MultiIndex")
    group_map = _group_map(frame, groups)
    results = []
    for horizon in checked_horizons:
        outcomes = _future_outcomes(frame, horizon)
        for group in EVALUATION_GROUPS:
            group_mask = (
                pd.Series(True, index=frame.index)
                if group == "all"
                else frame.index.get_level_values("ticker").map(
                    lambda ticker: group_map.get(ticker, "other") == group
                )
            )
            for signal_key in SIGNAL_KEYS:
                signal = frame[f"signal_{signal_key}"].astype("boolean")
                valid = (
                    pd.Series(group_mask, index=frame.index).astype(bool)
                    & outcomes["eligible"]
                    & signal.notna()
                )
                predicted = signal.loc[valid].astype(bool)
                actual = (
                    outcomes.loc[valid, "future_mae"] <= threshold
                )
                results.append(
                    _metric_row(
                        group,
                        horizon,
                        signal_key,
                        predicted,
                        actual,
                        outcomes.loc[valid],
                        threshold,
                    )
                )
    return results


def _forecast_frame(forecasts):
    if forecasts is None:
        return None
    if not isinstance(forecasts, pd.DataFrame):
        raise TypeError("forecasts must be a DataFrame or None")
    frame = forecasts.copy(deep=True)
    if not isinstance(frame.index, pd.MultiIndex):
        required = {"ticker", "observation_date"}
        if not required.issubset(frame.columns):
            raise ValueError("forecasts require ticker and observation_date")
        frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
        frame["observation_date"] = pd.to_datetime(
            frame["observation_date"],
            errors="raise",
        )
        frame = frame.set_index(["ticker", "observation_date"])
    frame.index = frame.index.set_names(["ticker", "observation_date"])
    if frame.index.has_duplicates:
        raise ValueError("forecasts contain duplicate point-in-time keys")
    return frame.sort_index()


def _threshold_signal(values, threshold):
    if values is None:
        return pd.Series(dtype="boolean")
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series(pd.NA, index=numeric.index, dtype="boolean")
    present = numeric.notna()
    result.loc[present] = numeric.loc[present] >= threshold
    return result


def _state_signal(values, active_states):
    if values is None:
        return pd.Series(dtype="boolean")
    result = pd.Series(pd.NA, index=values.index, dtype="boolean")
    present = values.notna() & (values != "unavailable")
    result.loc[present] = values.loc[present].isin(active_states)
    return result


def _future_outcomes(frame, horizon):
    result = pd.DataFrame(
        {
            "eligible": False,
            "future_return": np.nan,
            "future_mae": np.nan,
            "lead_sessions": np.nan,
        },
        index=frame.index,
    )
    for ticker in frame.index.get_level_values("ticker").unique():
        subset = frame.loc[ticker].sort_index()
        close = pd.to_numeric(subset["close"], errors="coerce")
        low = pd.to_numeric(subset["low"], errors="coerce")
        eligible_count = max(len(subset) - horizon, 0)
        for position in range(eligible_count):
            start = close.iloc[position]
            path_close = close.iloc[position + 1 : position + horizon + 1]
            path_low = low.iloc[position + 1 : position + horizon + 1]
            if (
                not np.isfinite(start)
                or start <= 0
                or path_close.isna().any()
                or path_low.isna().any()
            ):
                continue
            index = (ticker, subset.index[position])
            result.loc[index, "eligible"] = True
            result.loc[index, "future_return"] = (
                float(path_close.iloc[-1]) / float(start) - 1.0
            )
            excursions = path_low.astype(float) / float(start) - 1.0
            result.loc[index, "future_mae"] = float(excursions.min())
            result.loc[index, "lead_sessions"] = float(
                np.argmin(excursions.to_numpy()) + 1
            )
    result["eligible"] = result["eligible"].astype(bool)
    return result


def _metric_row(
    group,
    horizon,
    signal_key,
    predicted,
    actual,
    outcomes,
    threshold,
):
    sample_count = int(len(predicted))
    signal_count = int(predicted.sum()) if sample_count else 0
    if sample_count == 0:
        return {
            "group": group,
            "horizon_sessions": horizon,
            "signal": signal_key,
            "status": "unavailable",
            "adverse_threshold": threshold,
            "sample_count": 0,
            "signal_count": 0,
            "signal_rate": None,
            "precision": None,
            "recall": None,
            "specificity": None,
            "balanced_accuracy": None,
            "false_positive_rate": None,
            "mean_terminal_return": None,
            "mean_mae": None,
            "mean_lead_sessions": None,
        }
    tp = int((predicted & actual).sum())
    fp = int((predicted & ~actual).sum())
    fn = int((~predicted & actual).sum())
    tn = int((~predicted & ~actual).sum())
    recall = _ratio(tp, tp + fn)
    specificity = _ratio(tn, tn + fp)
    selected = outcomes.loc[predicted.index[predicted]]
    adverse_selected = selected.loc[selected["future_mae"] <= threshold]
    return {
        "group": group,
        "horizon_sessions": horizon,
        "signal": signal_key,
        "status": "available",
        "adverse_threshold": threshold,
        "sample_count": sample_count,
        "signal_count": signal_count,
        "signal_rate": _ratio(signal_count, sample_count),
        "precision": _ratio(tp, tp + fp),
        "recall": recall,
        "specificity": specificity,
        "balanced_accuracy": (
            None
            if recall is None or specificity is None
            else (recall + specificity) / 2.0
        ),
        "false_positive_rate": _ratio(fp, fp + tn),
        "mean_terminal_return": _mean(selected["future_return"]),
        "mean_mae": _mean(selected["future_mae"]),
        "mean_lead_sessions": _mean(adverse_selected["lead_sessions"]),
    }


def _group_map(frame, groups):
    tickers = set(frame.index.get_level_values("ticker"))
    if groups is not None:
        return {
            ticker: str(groups.get(ticker, "other"))
            for ticker in tickers
        }
    result = {}
    for ticker in tickers:
        group = market_group_for_ticker(ticker)
        result[ticker] = (
            group.key
            if group is not None and group.key in {"semiconductor", "software"}
            else "other"
        )
    return result


def _horizons(values):
    result = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("horizons must contain positive integers")
        if value not in result:
            result.append(value)
    if not result:
        raise ValueError("horizons must not be empty")
    return tuple(result)


def _ratio(numerator, denominator):
    return None if denominator == 0 else float(numerator) / float(denominator)


def _mean(values):
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric[np.isfinite(numeric)]
    return None if finite.empty else float(finite.mean())
