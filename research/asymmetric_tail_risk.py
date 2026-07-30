"""Causal labels and models for asymmetric five-session tail risk."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Integral

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression


INDEX_NAMES = ("ticker", "observation_date")
DOWN_TERMINAL_THRESHOLD = -0.05
DOWN_PATH_THRESHOLD = -0.07
EXTREME_REBOUND_THRESHOLD = 0.10


@dataclass(frozen=True)
class CalibrationResult:
    """Immutable OOF isotonic calibration curve."""

    status: str
    reason: object
    score_thresholds: tuple = ()
    probability_thresholds: tuple = ()
    sample_count: int = 0
    positive_count: int = 0

    def transform(self, scores) -> np.ndarray:
        if self.status != "available":
            raise RuntimeError("calibration is unavailable")
        values = np.asarray(scores, dtype=float)
        if values.ndim != 1 or not np.isfinite(values).all():
            raise ValueError("scores must be a finite one-dimensional array")
        calibrated = np.interp(
            values,
            np.asarray(self.score_thresholds, dtype=float),
            np.asarray(self.probability_thresholds, dtype=float),
        )
        return np.clip(calibrated, 0.0, 1.0)


@dataclass(frozen=True)
class TailBoundaryResult:
    """Training-only decision boundary and its raw economic evidence."""

    status: str
    reason: object
    down_threshold: object = None
    rebound_cap: object = None
    down_precision: object = None
    coverage: object = None
    mean_terminal_return: object = None
    diagnostics: tuple = ()


def select_tail_boundary(
    inner_oof: pd.DataFrame,
    *,
    down_thresholds=(0.40, 0.50, 0.60),
    rebound_caps=(0.20, 0.30),
    minimum_rows: int = 500,
    minimum_coverage: float = 0.05,
    maximum_coverage: float = 0.30,
) -> TailBoundaryResult:
    """Choose a risk boundary using inner OOF economic evidence only."""
    required = (
        "calibrated_down_probability",
        "predicted_median_return",
        "predicted_lower_quantile_return",
        "calibrated_rebound_probability",
        "actual_down_event",
        "actual_terminal_return",
    )
    if not isinstance(inner_oof, pd.DataFrame):
        raise TypeError("inner_oof must be a DataFrame")
    missing = [column for column in required if column not in inner_oof]
    if missing:
        raise ValueError(f"inner_oof is missing columns: {missing}")
    checked_minimum = _positive_integer(minimum_rows, "minimum_rows")
    checked_minimum_coverage = float(minimum_coverage)
    checked_maximum_coverage = float(maximum_coverage)
    if not (
        np.isfinite(checked_minimum_coverage)
        and np.isfinite(checked_maximum_coverage)
        and 0.0 < checked_minimum_coverage <= checked_maximum_coverage <= 1.0
    ):
        raise ValueError("coverage boundaries must satisfy 0 < min <= max <= 1")
    checked_down = _probability_grid(down_thresholds, "down_thresholds")
    checked_rebound = _probability_grid(rebound_caps, "rebound_caps")
    rows = inner_oof.loc[:, required].apply(pd.to_numeric, errors="coerce")
    values = rows.to_numpy(dtype=float)
    valid = np.isfinite(values).all(axis=1)
    valid &= rows["actual_down_event"].isin((0.0, 1.0)).to_numpy()
    rows = rows.loc[valid].reset_index(drop=True)
    if rows.empty:
        return TailBoundaryResult(
            status="unavailable",
            reason="tail_boundary_unavailable",
        )

    diagnostics = []
    eligible = []
    for down_threshold in checked_down:
        for rebound_cap in checked_rebound:
            selected = (
                (rows["calibrated_down_probability"] >= down_threshold)
                & (rows["predicted_median_return"] < 0.0)
                & (rows["predicted_lower_quantile_return"] <= -0.05)
                & (rows["calibrated_rebound_probability"] <= rebound_cap)
            )
            count = int(selected.sum())
            coverage = count / len(rows)
            selected_rows = rows.loc[selected]
            precision = (
                float(selected_rows["actual_down_event"].mean())
                if count
                else np.nan
            )
            mean_return = (
                float(selected_rows["actual_terminal_return"].mean())
                if count
                else np.nan
            )
            reasons = []
            if count < checked_minimum:
                reasons.append("insufficient_risk_rows")
            if coverage < checked_minimum_coverage:
                reasons.append("insufficient_risk_coverage")
            if coverage > checked_maximum_coverage:
                reasons.append("excessive_risk_coverage")
            if not np.isfinite(mean_return) or mean_return >= 0.0:
                reasons.append("non_negative_risk_return")
            diagnostic = {
                "down_threshold": down_threshold,
                "rebound_cap": rebound_cap,
                "risk_count": count,
                "coverage": coverage,
                "down_precision": precision,
                "mean_terminal_return": mean_return,
                "status": "eligible" if not reasons else "rejected",
                "reasons": tuple(reasons),
            }
            diagnostics.append(diagnostic)
            if not reasons:
                eligible.append(diagnostic)
    if not eligible:
        return TailBoundaryResult(
            status="unavailable",
            reason="tail_boundary_unavailable",
            diagnostics=tuple(diagnostics),
        )
    best = max(
        eligible,
        key=lambda row: (
            row["down_precision"],
            row["down_threshold"],
            -row["rebound_cap"],
        ),
    )
    return TailBoundaryResult(
        status="available",
        reason=None,
        down_threshold=best["down_threshold"],
        rebound_cap=best["rebound_cap"],
        down_precision=best["down_precision"],
        coverage=best["coverage"],
        mean_terminal_return=best["mean_terminal_return"],
        diagnostics=tuple(diagnostics),
    )


def fit_oof_isotonic(
    scores,
    outcomes,
    *,
    minimum_rows: int = 500,
    minimum_class_rows: int = 50,
) -> CalibrationResult:
    """Fit calibration only when OOF evidence supports both classes."""
    checked_rows = _positive_integer(minimum_rows, "minimum_rows")
    checked_class_rows = _positive_integer(
        minimum_class_rows,
        "minimum_class_rows",
    )
    score_values = np.asarray(scores, dtype=float)
    outcome_values = np.asarray(outcomes)
    if (
        score_values.ndim != 1
        or outcome_values.ndim != 1
        or len(score_values) != len(outcome_values)
    ):
        raise ValueError("scores and outcomes must be aligned 1D arrays")
    if not np.isfinite(score_values).all():
        raise ValueError("scores must be finite")
    if not np.isin(outcome_values, (0, 1, False, True)).all():
        raise ValueError("outcomes must be binary")
    binary = outcome_values.astype(int)
    positive_count = int(binary.sum())
    negative_count = int(len(binary) - positive_count)
    unavailable = (
        len(binary) < checked_rows
        or positive_count < checked_class_rows
        or negative_count < checked_class_rows
        or np.unique(score_values).size < 2
    )
    if unavailable:
        return CalibrationResult(
            status="unavailable",
            reason="calibration_unavailable",
            sample_count=len(binary),
            positive_count=positive_count,
        )
    model = IsotonicRegression(
        y_min=0.0,
        y_max=1.0,
        increasing=True,
        out_of_bounds="clip",
    )
    model.fit(score_values, binary)
    x_thresholds = np.asarray(model.X_thresholds_, dtype=float)
    y_thresholds = np.asarray(model.y_thresholds_, dtype=float)
    if (
        len(x_thresholds) < 2
        or not np.isfinite(x_thresholds).all()
        or not np.isfinite(y_thresholds).all()
    ):
        return CalibrationResult(
            status="unavailable",
            reason="calibration_unavailable",
            sample_count=len(binary),
            positive_count=positive_count,
        )
    return CalibrationResult(
        status="available",
        reason=None,
        score_thresholds=tuple(x_thresholds.tolist()),
        probability_thresholds=tuple(y_thresholds.tolist()),
        sample_count=len(binary),
        positive_count=positive_count,
    )


def attach_asymmetric_tail_targets(
    frame: pd.DataFrame,
    histories: Mapping[str, pd.DataFrame],
    horizon: int = 5,
) -> pd.DataFrame:
    """Attach exact next-open tail labels without shifting missing sessions."""
    _validate_feature_frame(frame)
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    if (
        isinstance(horizon, bool)
        or not isinstance(horizon, Integral)
        or int(horizon) != 5
    ):
        raise ValueError("horizon must be the frozen integer value 5")
    checked_horizon = int(horizon)
    suffix = str(checked_horizon)
    output_columns = (
        f"terminal_return_{suffix}",
        f"path_mae_{suffix}",
        f"down_event_{suffix}",
        f"extreme_rebound_{suffix}",
    )
    end_column = f"tail_label_end_date_{suffix}"
    result = frame.copy(deep=True)
    for column in output_columns:
        result[column] = np.nan
    result[end_column] = pd.NaT

    tickers = result.index.get_level_values("ticker").unique()
    for ticker in tickers:
        source = histories.get(str(ticker))
        if source is None or not isinstance(source, pd.DataFrame) or source.empty:
            continue
        missing = [
            column for column in ("Open", "Low", "Close") if column not in source
        ]
        if missing:
            raise ValueError(
                f"history for {ticker} is missing columns: {missing}"
            )
        history = source.loc[:, ("Open", "Low", "Close")].copy(deep=True)
        history.index = pd.DatetimeIndex(history.index).tz_localize(None)
        if history.index.has_duplicates:
            raise ValueError(f"history for {ticker} contains duplicate dates")
        history = history.sort_index().apply(pd.to_numeric, errors="coerce")
        dates = result.loc[str(ticker)].index
        keys = pd.MultiIndex.from_product(
            ((str(ticker),), dates),
            names=INDEX_NAMES,
        )

        entry_open = history["Open"].shift(-1)
        terminal_close = history["Close"].shift(-checked_horizon)
        future_lows = pd.concat(
            [
                history["Low"].shift(-offset)
                for offset in range(1, checked_horizon + 1)
            ],
            axis=1,
        )
        date_series = pd.Series(
            history.index,
            index=history.index,
            dtype="datetime64[ns]",
        )
        label_end = date_series.shift(-checked_horizon)
        complete = (
            entry_open.notna()
            & terminal_close.notna()
            & (entry_open > 0.0)
            & (terminal_close > 0.0)
            & future_lows.notna().all(axis=1)
            & (future_lows > 0.0).all(axis=1)
            & label_end.notna()
        )
        terminal_return = (terminal_close / entry_open - 1.0).where(complete)
        path_mae = (
            future_lows.min(axis=1, skipna=False) / entry_open - 1.0
        ).where(complete)
        down_event = (
            (
                (terminal_return <= DOWN_TERMINAL_THRESHOLD)
                | (path_mae <= DOWN_PATH_THRESHOLD)
            )
            .astype(float)
            .where(complete)
        )
        rebound_event = (
            (terminal_return >= EXTREME_REBOUND_THRESHOLD)
            .astype(float)
            .where(complete)
        )
        values = {
            output_columns[0]: terminal_return,
            output_columns[1]: path_mae,
            output_columns[2]: down_event,
            output_columns[3]: rebound_event,
            end_column: label_end.where(complete),
        }
        for column, series in values.items():
            result.loc[keys, column] = series.reindex(dates).to_numpy()
    return result


def _validate_feature_frame(frame: pd.DataFrame) -> None:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a DataFrame")
    if not isinstance(frame.index, pd.MultiIndex):
        raise ValueError("frame must use a MultiIndex")
    if tuple(frame.index.names) != INDEX_NAMES:
        raise ValueError(f"frame index names must be {INDEX_NAMES}")
    if frame.index.has_duplicates:
        raise ValueError("frame contains duplicate ticker/date keys")
    dates = pd.DatetimeIndex(frame.index.get_level_values("observation_date"))
    if dates.tz is not None:
        raise ValueError("observation dates must be timezone-naive")


def _positive_integer(value, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, Integral)
        or int(value) <= 0
    ):
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _probability_grid(values, name: str) -> tuple:
    try:
        checked = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must contain probabilities") from None
    if (
        not checked
        or not all(np.isfinite(value) and 0.0 < value < 1.0 for value in checked)
    ):
        raise ValueError(f"{name} must contain probabilities")
    return checked
