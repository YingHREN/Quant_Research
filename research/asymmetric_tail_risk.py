"""Causal labels and models for asymmetric five-session tail risk."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Integral
import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

from research.market_direction_model import training_only_design


INDEX_NAMES = ("ticker", "observation_date")
DOWN_TERMINAL_THRESHOLD = -0.05
DOWN_PATH_THRESHOLD = -0.07
EXTREME_REBOUND_THRESHOLD = 0.10
PREDICTION_COLUMNS = (
    "ticker",
    "observation_date",
    "fold",
    "test_start",
    "training_samples",
    "training_label_end_max",
    "model_status",
    "raw_down_probability",
    "calibrated_down_probability",
    "raw_rebound_probability",
    "calibrated_rebound_probability",
    "down_calibration_samples",
    "down_calibration_positive_count",
    "rebound_calibration_samples",
    "rebound_calibration_positive_count",
    "raw_predicted_median_return",
    "raw_predicted_lower_quantile_return",
    "predicted_median_return",
    "predicted_lower_quantile_return",
    "boundary_status",
    "boundary_reason",
    "down_threshold",
    "rebound_cap",
    "boundary_inner_down_precision",
    "boundary_inner_coverage",
    "boundary_inner_mean_terminal_return",
    "predicted_tail_risk",
    "actual_down_event",
    "actual_rebound_event",
    "actual_terminal_return",
    "actual_path_mae",
)


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


def evaluate_tail_predictions(
    predictions: pd.DataFrame,
    *,
    group_map=None,
) -> pd.DataFrame:
    """Evaluate raw economic outcomes on overlapping and spaced samples."""
    required = (
        "ticker",
        "observation_date",
        "fold",
        "regime",
        "boundary_status",
        "predicted_tail_risk",
        "actual_down_event",
        "actual_rebound_event",
        "actual_terminal_return",
    )
    if not isinstance(predictions, pd.DataFrame):
        raise TypeError("predictions must be a DataFrame")
    missing = [column for column in required if column not in predictions]
    if missing:
        raise ValueError(f"predictions are missing columns: {missing}")
    checked = predictions.copy(deep=True)
    checked["ticker"] = checked["ticker"].astype(str).str.upper()
    checked["observation_date"] = pd.to_datetime(
        checked["observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    if checked.duplicated(["ticker", "observation_date"]).any():
        raise ValueError("predictions contain duplicate ticker/date keys")
    numeric_columns = (
        "actual_terminal_return",
        "actual_down_event",
        "actual_rebound_event",
    )
    for column in numeric_columns:
        checked[column] = pd.to_numeric(checked[column], errors="coerce")
    checked = checked.sort_values(
        ["ticker", "observation_date"],
        kind="mergesort",
    )
    checked["_non_overlapping"] = (
        checked.groupby("ticker", sort=False).cumcount().mod(5).eq(0)
    )
    checked = checked.loc[
        (checked["boundary_status"] == "available")
        & checked["predicted_tail_risk"].notna()
        & np.isfinite(checked[list(numeric_columns)].to_numpy(dtype=float)).all(
            axis=1
        )
    ].copy()
    checked["predicted_tail_risk"] = checked[
        "predicted_tail_risk"
    ].astype(bool)
    checked["actual_down_event"] = checked["actual_down_event"].astype(bool)
    checked["actual_rebound_event"] = checked[
        "actual_rebound_event"
    ].astype(bool)
    if "baseline_predicted_down" in checked:
        checked["baseline_predicted_down"] = checked[
            "baseline_predicted_down"
        ].fillna(False).astype(bool)
    else:
        checked["baseline_predicted_down"] = False
        checked.attrs["baseline_unavailable"] = True
    groups = {}
    if group_map is not None:
        if not isinstance(group_map, Mapping):
            raise TypeError("group_map must be a mapping")
        groups = {
            str(ticker).strip().upper(): str(group).strip()
            for ticker, group in group_map.items()
            if str(ticker).strip() and str(group).strip()
        }
    if groups:
        checked["group"] = checked["ticker"].map(groups).fillna(
            "unclassified"
        )
    elif "group" not in checked:
        checked["group"] = "unclassified"
    sample_frames = {
        "overlapping": checked,
        "non_overlapping": checked.loc[checked["_non_overlapping"]],
    }
    metric_rows = []
    for sample_mode, sample in sample_frames.items():
        scopes = [("overall", "all", None, sample)]
        scopes.extend(
            (
                "group",
                str(name),
                None,
                selected,
            )
            for name, selected in sample.groupby("group", sort=True)
        )
        scopes.extend(
            (
                "regime",
                str(name),
                None,
                selected,
            )
            for name, selected in sample.groupby("regime", sort=True)
        )
        scopes.extend(
            (
                "fold",
                str(int(name)),
                int(name),
                selected,
            )
            for name, selected in sample.groupby("fold", sort=True)
        )
        for scope_type, scope_name, fold, selected in scopes:
            metric_rows.append(
                _tail_metric_row(
                    selected,
                    sample_mode=sample_mode,
                    scope_type=scope_type,
                    scope_name=scope_name,
                    fold=fold,
                    baseline_available=(
                        not checked.attrs.get("baseline_unavailable", False)
                    ),
                )
            )
    return pd.DataFrame(metric_rows).sort_values(
        ["sample_mode", "scope_type", "scope_name"],
        kind="mergesort",
    ).reset_index(drop=True)


def audit_extreme_counterexamples(predictions: pd.DataFrame) -> pd.DataFrame:
    """Return high-down-score extreme winners even when boundaries reject."""
    required = (
        "ticker",
        "observation_date",
        "predicted_tail_risk",
        "actual_terminal_return",
        "calibrated_down_probability",
        "calibrated_rebound_probability",
    )
    if not isinstance(predictions, pd.DataFrame):
        raise TypeError("predictions must be a DataFrame")
    missing = [column for column in required if column not in predictions]
    if missing:
        raise ValueError(f"predictions are missing columns: {missing}")
    checked = predictions.copy(deep=True)
    checked["actual_terminal_return"] = pd.to_numeric(
        checked["actual_terminal_return"],
        errors="coerce",
    )
    checked["calibrated_down_probability"] = pd.to_numeric(
        checked["calibrated_down_probability"],
        errors="coerce",
    )
    selected = checked.loc[
        (checked["calibrated_down_probability"] >= 0.40)
        & (checked["actual_terminal_return"] >= EXTREME_REBOUND_THRESHOLD)
    ].copy()
    ordered = [
        column
        for column in (
            "ticker",
            "observation_date",
            "fold",
            "group",
            "regime",
            "boundary_status",
            "predicted_tail_risk",
            "calibrated_down_probability",
            "calibrated_rebound_probability",
            "actual_terminal_return",
            "actual_path_mae",
            "opening_gap",
            "realized_volatility",
            "dollar_volume",
            "earnings_proximity",
        )
        if column in selected
    ]
    if selected.empty:
        return pd.DataFrame(columns=ordered)
    return selected.loc[:, ordered].sort_values(
        ["calibrated_down_probability", "actual_terminal_return"],
        ascending=(False, False),
        kind="mergesort",
    ).reset_index(drop=True)


def tail_promotion_decision(
    metrics: pd.DataFrame,
    causal_audit: Mapping,
    *,
    minimum_group_risk_rows: int = 200,
) -> dict:
    """Apply the frozen research gate without granting online authority."""
    if not isinstance(metrics, pd.DataFrame):
        raise TypeError("metrics must be a DataFrame")
    if not isinstance(causal_audit, Mapping):
        raise TypeError("causal_audit must be a mapping")
    minimum_group = _positive_integer(
        minimum_group_risk_rows,
        "minimum_group_risk_rows",
    )
    required = (
        "sample_mode",
        "scope_type",
        "scope_name",
        "risk_count",
        "coverage",
        "down_precision_gain",
        "mean_terminal_return",
        "risk_rebound_rate",
        "all_rebound_rate",
    )
    missing = [column for column in required if column not in metrics]
    if missing:
        raise ValueError(f"metrics are missing columns: {missing}")
    selected = metrics.loc[metrics["sample_mode"] == "non_overlapping"]
    overall = selected.loc[selected["scope_type"] == "overall"]
    reasons = []
    if len(overall) != 1:
        reasons.append("overall_metrics_unavailable")
        overall_row = None
    else:
        overall_row = overall.iloc[0]
        if not 0.05 <= float(overall_row["coverage"]) <= 0.30:
            reasons.append("coverage_gate_failed")
        if not float(overall_row["mean_terminal_return"]) < 0.0:
            reasons.append("economic_return_gate_failed")
        if not float(overall_row["down_precision_gain"]) >= 0.03:
            reasons.append("precision_gain_gate_failed")
        all_rebound = float(overall_row["all_rebound_rate"])
        risk_rebound = float(overall_row["risk_rebound_rate"])
        rebound_limit = 0.70 * all_rebound
        if not risk_rebound <= rebound_limit:
            reasons.append("rebound_rate_gate_failed")
    fold_rows = selected.loc[selected["scope_type"] == "fold"]
    negative_folds = int(
        (pd.to_numeric(
            fold_rows["mean_terminal_return"],
            errors="coerce",
        ) < 0.0).sum()
    )
    if len(fold_rows) < 5 or negative_folds < 4:
        reasons.append("fold_stability_gate_failed")
    for group in ("semiconductor", "software"):
        group_rows = selected.loc[
            (selected["scope_type"] == "group")
            & (selected["scope_name"] == group)
        ]
        if len(group_rows) != 1:
            reasons.append(f"{group}_group_unavailable")
            continue
        row = group_rows.iloc[0]
        if (
            int(row["risk_count"]) < minimum_group
            or not float(row["mean_terminal_return"]) < 0.0
        ):
            reasons.append(f"{group}_group_gate_failed")
    if causal_audit.get("passed") is not True:
        reasons.append("causal_audit_failed")
    conditions = {
        "overall_available": overall_row is not None,
        "negative_fold_count": negative_folds,
        "semiconductor_available": (
            "semiconductor_group_unavailable" not in reasons
        ),
        "software_available": "software_group_unavailable" not in reasons,
        "causal_audit_passed": causal_audit.get("passed") is True,
    }
    return {
        "promoted": not reasons,
        "status": "passed" if not reasons else "rejected",
        "reasons": tuple(reasons),
        "conditions": conditions,
        "lifecycle": "research",
        "online_authority": "none",
    }


def walk_forward_asymmetric_tail_predictions(
    frame: pd.DataFrame,
    *,
    feature_columns,
    n_test_folds: int = 5,
    minimum_samples: int = 1_000,
    minimum_calibration_rows: int = 500,
    minimum_class_rows: int = 50,
    minimum_boundary_rows: int = 500,
) -> pd.DataFrame:
    """Fit four causal heads with nested OOF calibration and boundaries."""
    _validate_feature_frame(frame)
    columns = tuple(str(column).strip() for column in feature_columns)
    if not columns or any(not column for column in columns):
        raise ValueError("feature_columns must not be empty")
    required = (
        *columns,
        "terminal_return_5",
        "path_mae_5",
        "down_event_5",
        "extreme_rebound_5",
        "tail_label_end_date_5",
    )
    missing = [column for column in required if column not in frame]
    if missing:
        raise ValueError(f"frame is missing tail model columns: {missing}")
    checked_folds = _at_least_two_integer(n_test_folds, "n_test_folds")
    checked_minimum = _positive_integer(minimum_samples, "minimum_samples")
    checked_calibration = _positive_integer(
        minimum_calibration_rows,
        "minimum_calibration_rows",
    )
    checked_class = _positive_integer(
        minimum_class_rows,
        "minimum_class_rows",
    )
    checked_boundary = _positive_integer(
        minimum_boundary_rows,
        "minimum_boundary_rows",
    )
    folds = _purged_folds(frame, checked_folds)
    outputs = []
    failure_reasons = []
    fold_evidence = []
    for fold_number, (train_positions, test_positions) in enumerate(
        folds,
        start=1,
    ):
        train = frame.iloc[train_positions]
        test = frame.iloc[test_positions]
        if len(train) < checked_minimum or test.empty:
            failure_reasons.append("insufficient_training_samples")
            fold_evidence.append(
                {
                    "fold": fold_number,
                    "status": "unavailable",
                    "reason": "insufficient_training_samples",
                }
            )
            continue
        inner_oof = _inner_oof_head_predictions(
            train,
            columns,
            minimum_samples=checked_minimum,
        )
        if inner_oof.empty:
            reason = inner_oof.attrs.get(
                "reason",
                "calibration_unavailable",
            )
            failure_reasons.append(reason)
            fold_evidence.append(
                {
                    "fold": fold_number,
                    "status": "unavailable",
                    "reason": reason,
                }
            )
            continue
        down_calibration = fit_oof_isotonic(
            inner_oof["raw_down_probability"].to_numpy(dtype=float),
            inner_oof["actual_down_event"].to_numpy(dtype=int),
            minimum_rows=checked_calibration,
            minimum_class_rows=checked_class,
        )
        rebound_calibration = fit_oof_isotonic(
            inner_oof["raw_rebound_probability"].to_numpy(dtype=float),
            inner_oof["actual_rebound_event"].to_numpy(dtype=int),
            minimum_rows=checked_calibration,
            minimum_class_rows=checked_class,
        )
        if (
            down_calibration.status != "available"
            or rebound_calibration.status != "available"
        ):
            failure_reasons.append("calibration_unavailable")
            fold_evidence.append(
                {
                    "fold": fold_number,
                    "status": "unavailable",
                    "reason": "calibration_unavailable",
                    "down_calibration_status": down_calibration.status,
                    "rebound_calibration_status": (
                        rebound_calibration.status
                    ),
                }
            )
            continue
        inner_oof = inner_oof.copy()
        inner_oof["calibrated_down_probability"] = (
            down_calibration.transform(
                inner_oof["raw_down_probability"].to_numpy(dtype=float)
            )
        )
        inner_oof["calibrated_rebound_probability"] = (
            rebound_calibration.transform(
                inner_oof["raw_rebound_probability"].to_numpy(dtype=float)
            )
        )
        boundary = select_tail_boundary(
            inner_oof,
            minimum_rows=checked_boundary,
        )
        fold_evidence.append(
            {
                "fold": fold_number,
                "status": "available",
                "reason": None,
                "down_calibration_status": down_calibration.status,
                "down_calibration_samples": down_calibration.sample_count,
                "down_calibration_positive_count": (
                    down_calibration.positive_count
                ),
                "rebound_calibration_status": rebound_calibration.status,
                "rebound_calibration_samples": (
                    rebound_calibration.sample_count
                ),
                "rebound_calibration_positive_count": (
                    rebound_calibration.positive_count
                ),
                "boundary_status": boundary.status,
                "boundary_reason": boundary.reason,
                "selected_down_threshold": boundary.down_threshold,
                "selected_rebound_cap": boundary.rebound_cap,
                "boundary_candidates": boundary.diagnostics,
            }
        )
        outer_heads = _fit_predict_heads(train, test, columns)
        if outer_heads is None:
            failure_reasons.append("model_classes_unavailable")
            continue
        down_probability = down_calibration.transform(
            outer_heads["raw_down_probability"]
        )
        rebound_probability = rebound_calibration.transform(
            outer_heads["raw_rebound_probability"]
        )
        raw_median = outer_heads["raw_predicted_median_return"]
        raw_lower = outer_heads["raw_predicted_lower_quantile_return"]
        ordered_lower = np.minimum(raw_lower, raw_median)
        if boundary.status == "available":
            tail_risk = (
                (down_probability >= boundary.down_threshold)
                & (raw_median < 0.0)
                & (ordered_lower <= -0.05)
                & (rebound_probability <= boundary.rebound_cap)
            )
            down_threshold = boundary.down_threshold
            rebound_cap = boundary.rebound_cap
        else:
            tail_risk = pd.array([pd.NA] * len(test), dtype="boolean")
            down_threshold = np.nan
            rebound_cap = np.nan
        test_start = pd.Timestamp(
            test.index.get_level_values("observation_date").min()
        )
        outputs.append(
            pd.DataFrame(
                {
                    "ticker": test.index.get_level_values("ticker"),
                    "observation_date": test.index.get_level_values(
                        "observation_date"
                    ),
                    "fold": fold_number,
                    "test_start": test_start,
                    "training_samples": len(train),
                    "training_label_end_max": pd.Timestamp(
                        train["tail_label_end_date_5"].max()
                    ),
                    "model_status": "available",
                    "raw_down_probability": outer_heads[
                        "raw_down_probability"
                    ],
                    "calibrated_down_probability": down_probability,
                    "raw_rebound_probability": outer_heads[
                        "raw_rebound_probability"
                    ],
                    "calibrated_rebound_probability": rebound_probability,
                    "down_calibration_samples": (
                        down_calibration.sample_count
                    ),
                    "down_calibration_positive_count": (
                        down_calibration.positive_count
                    ),
                    "rebound_calibration_samples": (
                        rebound_calibration.sample_count
                    ),
                    "rebound_calibration_positive_count": (
                        rebound_calibration.positive_count
                    ),
                    "raw_predicted_median_return": raw_median,
                    "raw_predicted_lower_quantile_return": raw_lower,
                    "predicted_median_return": raw_median,
                    "predicted_lower_quantile_return": ordered_lower,
                    "boundary_status": boundary.status,
                    "boundary_reason": boundary.reason,
                    "down_threshold": down_threshold,
                    "rebound_cap": rebound_cap,
                    "boundary_inner_down_precision": (
                        boundary.down_precision
                        if boundary.down_precision is not None
                        else np.nan
                    ),
                    "boundary_inner_coverage": (
                        boundary.coverage
                        if boundary.coverage is not None
                        else np.nan
                    ),
                    "boundary_inner_mean_terminal_return": (
                        boundary.mean_terminal_return
                        if boundary.mean_terminal_return is not None
                        else np.nan
                    ),
                    "predicted_tail_risk": pd.array(
                        tail_risk,
                        dtype="boolean",
                    ),
                    "actual_down_event": test["down_event_5"]
                    .astype(bool)
                    .to_numpy(),
                    "actual_rebound_event": test["extreme_rebound_5"]
                    .astype(bool)
                    .to_numpy(),
                    "actual_terminal_return": test[
                        "terminal_return_5"
                    ].to_numpy(dtype=float),
                    "actual_path_mae": test["path_mae_5"].to_numpy(
                        dtype=float
                    ),
                }
            )
        )
    if not outputs:
        empty = _empty_predictions(
            failure_reasons[0] if failure_reasons else "folds_unavailable"
        )
        empty.attrs["fold_evidence"] = tuple(fold_evidence)
        return empty
    result = pd.concat(outputs, ignore_index=True, sort=False)
    if result.duplicated(["ticker", "observation_date"]).any():
        raise RuntimeError("outer prediction keys must be unique")
    result["down_threshold"] = pd.to_numeric(
        result["down_threshold"],
        errors="coerce",
    ).astype(float)
    result["rebound_cap"] = pd.to_numeric(
        result["rebound_cap"],
        errors="coerce",
    ).astype(float)
    result = result.loc[:, PREDICTION_COLUMNS].sort_values(
        ["fold", "ticker", "observation_date"],
        kind="mergesort",
    ).reset_index(drop=True)
    result.attrs["fold_evidence"] = tuple(fold_evidence)
    return result


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


def _purged_folds(frame, n_folds):
    observation_dates = pd.Series(
        frame.index.get_level_values("observation_date"),
        index=frame.index,
    )
    label_end = pd.to_datetime(
        frame["tail_label_end_date_5"],
        errors="coerce",
    )
    complete = (
        frame[
            [
                "terminal_return_5",
                "path_mae_5",
                "down_event_5",
                "extreme_rebound_5",
            ]
        ]
        .apply(pd.to_numeric, errors="coerce")
        .notna()
        .all(axis=1)
        & label_end.notna()
    )
    unique_dates = np.asarray(sorted(observation_dates.unique()))
    edges = np.linspace(0, len(unique_dates), n_folds + 1, dtype=int)
    folds = []
    for fold in range(1, n_folds):
        test_dates = unique_dates[edges[fold] : edges[fold + 1]]
        if not len(test_dates):
            continue
        test_start = pd.Timestamp(test_dates[0])
        train_mask = complete & (label_end < test_start)
        test_mask = complete & observation_dates.isin(test_dates)
        train_positions = np.flatnonzero(train_mask.to_numpy())
        test_positions = np.flatnonzero(test_mask.to_numpy())
        if len(train_positions) and len(test_positions):
            folds.append((train_positions, test_positions))
    return folds


def _tail_metric_row(
    selected,
    *,
    sample_mode,
    scope_type,
    scope_name,
    fold,
    baseline_available,
):
    row_count = len(selected)
    risk = selected["predicted_tail_risk"].astype(bool)
    actual_down = selected["actual_down_event"].astype(bool)
    actual_rebound = selected["actual_rebound_event"].astype(bool)
    risk_count = int(risk.sum())
    actual_down_count = int(actual_down.sum())
    risk_rows = selected.loc[risk]
    true_down = int((risk & actual_down).sum())
    baseline = selected["baseline_predicted_down"].astype(bool)
    baseline_count = int(baseline.sum())
    risk_precision = true_down / risk_count if risk_count else np.nan
    baseline_precision = (
        int((baseline & actual_down).sum()) / baseline_count
        if baseline_available and baseline_count
        else np.nan
    )
    return {
        "sample_mode": sample_mode,
        "scope_type": scope_type,
        "scope_name": scope_name,
        "fold": fold,
        "row_count": row_count,
        "risk_count": risk_count,
        "coverage": risk_count / row_count if row_count else np.nan,
        "down_precision": risk_precision,
        "down_recall": (
            true_down / actual_down_count if actual_down_count else np.nan
        ),
        "baseline_down_precision": baseline_precision,
        "down_precision_gain": (
            risk_precision - baseline_precision
            if np.isfinite(risk_precision)
            and np.isfinite(baseline_precision)
            else np.nan
        ),
        "mean_terminal_return": (
            float(risk_rows["actual_terminal_return"].mean())
            if risk_count
            else np.nan
        ),
        "risk_rebound_rate": (
            float(risk_rows["actual_rebound_event"].mean())
            if risk_count
            else np.nan
        ),
        "all_rebound_rate": (
            float(actual_rebound.mean()) if row_count else np.nan
        ),
    }


def _inner_oof_head_predictions(frame, columns, *, minimum_samples):
    outputs = []
    for train_positions, test_positions in _purged_folds(frame, 4):
        train = frame.iloc[train_positions]
        test = frame.iloc[test_positions]
        if len(train) < minimum_samples:
            continue
        heads = _fit_predict_heads(train, test, columns)
        if heads is None:
            continue
        outputs.append(
            pd.DataFrame(
                {
                    "raw_down_probability": heads[
                        "raw_down_probability"
                    ],
                    "raw_rebound_probability": heads[
                        "raw_rebound_probability"
                    ],
                    "predicted_median_return": heads[
                        "raw_predicted_median_return"
                    ],
                    "predicted_lower_quantile_return": np.minimum(
                        heads["raw_predicted_lower_quantile_return"],
                        heads["raw_predicted_median_return"],
                    ),
                    "actual_down_event": test["down_event_5"]
                    .astype(int)
                    .to_numpy(),
                    "actual_rebound_event": test["extreme_rebound_5"]
                    .astype(int)
                    .to_numpy(),
                    "actual_terminal_return": test[
                        "terminal_return_5"
                    ].to_numpy(dtype=float),
                }
            )
        )
    if not outputs:
        empty = pd.DataFrame()
        empty.attrs["reason"] = "calibration_unavailable"
        return empty
    return pd.concat(outputs, ignore_index=True, sort=False)


def _fit_predict_heads(train, test, columns):
    down = train["down_event_5"].astype(int).to_numpy()
    rebound = train["extreme_rebound_5"].astype(int).to_numpy()
    if set(np.unique(down)) != {0, 1} or set(np.unique(rebound)) != {0, 1}:
        return None
    x_train, x_test = training_only_design(train, test, columns)
    target = train["terminal_return_5"].to_numpy(dtype=float)
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Could not find the number of physical cores",
            category=UserWarning,
        )
        down_model = _fit_binary_head(x_train, down)
        rebound_model = _fit_binary_head(x_train, rebound)
        median_model = _fit_quantile_head(x_train, target, 0.50)
        lower_model = _fit_quantile_head(x_train, target, 0.20)
    return {
        "raw_down_probability": _positive_probability(
            down_model,
            x_test,
        ),
        "raw_rebound_probability": _positive_probability(
            rebound_model,
            x_test,
        ),
        "raw_predicted_median_return": median_model.predict(x_test),
        "raw_predicted_lower_quantile_return": lower_model.predict(x_test),
    }


def _fit_binary_head(design, target):
    model = LogisticRegression(
        C=0.1,
        class_weight="balanced",
        max_iter=1_000,
        random_state=0,
        solver="liblinear",
    )
    model.fit(design, target)
    return model


def _positive_probability(model, design):
    coefficients = np.asarray(model.coef_[0], dtype=float)
    intercept = float(model.intercept_[0])
    logits = (
        np.einsum(
            "ij,j->i",
            np.asarray(design, dtype=float),
            coefficients,
            optimize=False,
        )
        + intercept
    )
    if not np.isfinite(logits).all():
        raise RuntimeError("binary tail head produced invalid scores")
    logits = np.clip(logits, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-logits))


def _fit_quantile_head(design, target, quantile):
    model = HistGradientBoostingRegressor(
        loss="quantile",
        quantile=quantile,
        learning_rate=0.05,
        max_iter=100,
        max_leaf_nodes=15,
        min_samples_leaf=50,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=0,
    )
    model.fit(design, target)
    return model


def _empty_predictions(reason):
    result = pd.DataFrame(columns=PREDICTION_COLUMNS)
    result.attrs["reason"] = str(reason)
    return result


def _positive_integer(value, name: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, Integral)
        or int(value) <= 0
    ):
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _at_least_two_integer(value, name: str) -> int:
    checked = _positive_integer(value, name)
    if checked < 2:
        raise ValueError(f"{name} must be at least 2")
    return checked


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
