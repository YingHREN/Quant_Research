"""Freeze and capture genuinely prospective downside shadow predictions."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, time, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from typing import Callable, Mapping, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from research.downside_shadow import (
    MODEL_SCHEMA_VERSION,
    fit_frozen_binary_logistic,
    fit_frozen_direction_logistic,
    fit_frozen_ridge,
    predict_frozen_linear,
    read_shadow_model_bundle,
    write_shadow_model_bundle,
)
from research.downside_shadow_store import (
    DownsideShadowStore,
    ShadowExperiment,
    ShadowOutcome,
    ShadowPrediction,
)


STUDY_VERSION = "prospective-downside-shadow-v1"
FEATURE_VERSION = "ridge-features-v2"
RISK_RULE_VERSION = "memory-12-v1"
MARKET_TIMEZONE = ZoneInfo("America/New_York")
PRESSURE_REGIMES = frozenset(
    ("under_pressure", "correction", "acute_selloff")
)


@dataclass(frozen=True)
class ShadowConfig:
    research_database: Path
    shadow_database: Path
    model_artifact: Path
    experiment_id: str = "downside-shadow-v1"
    start_date: str = "2018-01-01"
    max_tickers: int = 240
    candidate_tickers: int = 600
    minimum_history: int = 260
    horizons: tuple[int, ...] = (5, 10, 20)
    output_directory: Path = Path("reports")


@dataclass(frozen=True)
class ShadowInputSnapshot:
    feature_frame: pd.DataFrame
    histories: Mapping[str, pd.DataFrame]
    assignments: pd.DataFrame
    regimes: pd.DataFrame
    analysis_tickers: tuple[str, ...]
    reference_tickers: tuple[str, ...] = ("QQQ", "SPY")
    rule_frame: Optional[pd.DataFrame] = None


@dataclass(frozen=True)
class ShadowDependencies:
    load_inputs: Callable[[ShadowConfig], ShadowInputSnapshot]
    now: Callable[[], datetime]
    code_commit: Callable[[], str]
    ridge_features: tuple[str, ...]
    direction_features: tuple[str, ...]
    pressure_features: tuple[str, ...]


@dataclass(frozen=True)
class ShadowEvaluationArtifacts:
    manifest: dict
    metrics: pd.DataFrame
    outcomes: pd.DataFrame
    output_paths: tuple[Path, ...]


def freeze_experiment(config, dependencies=None):
    """Freeze a cohort and all fitted coefficients at one observable cutoff."""
    checked = _validate_config(config)
    store = DownsideShadowStore(checked.shadow_database)
    existing = store.load_experiment(checked.experiment_id)
    if existing is not None:
        read_shadow_model_bundle(
            existing.model_artifact_path,
            expected_checksum=existing.model_artifact_checksum,
        )
        return {
            "experiment_id": checked.experiment_id,
            "created": False,
            "online_authority": existing.online_authority,
            "frozen_market_asof": existing.frozen_market_asof,
            "ticker_count": len(existing.universe),
            "model_count": len(
                read_shadow_model_bundle(
                    existing.model_artifact_path,
                    expected_checksum=existing.model_artifact_checksum,
                )["models"]
            ),
            "model_artifact_checksum": existing.model_artifact_checksum,
            "database_fingerprint": existing.database_fingerprint,
        }
    runtime = dependencies or default_dependencies()
    snapshot = _validated_snapshot(runtime.load_inputs(checked))
    cutoff = _latest_common_reference_session(snapshot)
    cohort = _select_frozen_cohort(snapshot, cutoff, checked)
    frame = snapshot.feature_frame.loc[
        snapshot.feature_frame.index.get_level_values("ticker").isin(cohort)
    ].copy()
    models = []
    from research.market_direction_model import NEUTRAL_BANDS

    for horizon in checked.horizons:
        models.append(
            fit_frozen_ridge(
                frame,
                feature_columns=runtime.ridge_features,
                target_column=f"executable_return_{horizon}",
                label_end_column=f"executable_label_end_date_{horizon}",
                frozen_market_asof=cutoff,
                specification="ridge_current",
                horizon=horizon,
                neutral_band=NEUTRAL_BANDS[horizon],
            )
        )
        models.append(
            fit_frozen_direction_logistic(
                frame,
                feature_columns=runtime.direction_features,
                target_column=f"executable_return_{horizon}",
                label_end_column=f"executable_label_end_date_{horizon}",
                frozen_market_asof=cutoff,
                specification="general_logistic",
                horizon=horizon,
                neutral_band=NEUTRAL_BANDS[horizon],
            )
        )
        if horizon in (5, 20):
            models.append(
                fit_frozen_binary_logistic(
                    frame,
                    feature_columns=runtime.pressure_features,
                    target_column=f"downside_event_{horizon}",
                    label_end_column=f"downside_label_end_date_{horizon}",
                    frozen_market_asof=cutoff,
                    specification="pressure_downside_logistic",
                    horizon=horizon,
                )
            )

    fingerprint = _snapshot_fingerprint(snapshot, cohort, cutoff)
    created_at = _aware_now(runtime.now)
    code_commit = str(runtime.code_commit()).strip()
    if not code_commit:
        raise ValueError("code commit must not be empty")
    bundle = {
        "schema_version": MODEL_SCHEMA_VERSION,
        "study_version": STUDY_VERSION,
        "experiment_id": checked.experiment_id,
        "created_at": created_at,
        "frozen_market_asof": cutoff,
        "cohort": list(cohort),
        "horizons": list(checked.horizons),
        "database_fingerprint": fingerprint,
        "code_commit": code_commit,
        "online_authority": "none",
        "models": models,
    }
    checksum = write_shadow_model_bundle(checked.model_artifact, bundle)
    read_shadow_model_bundle(
        checked.model_artifact,
        expected_checksum=checksum,
    )
    experiment = ShadowExperiment(
        experiment_id=checked.experiment_id,
        study_version=STUDY_VERSION,
        created_at=created_at,
        frozen_market_asof=cutoff,
        universe=cohort,
        horizons=checked.horizons,
        model_artifact_path=str(checked.model_artifact.resolve()),
        model_artifact_checksum=checksum,
        database_fingerprint=fingerprint,
        code_commit=code_commit,
        status="active",
        online_authority="none",
    )
    created = store.create_experiment(experiment)
    return {
        "experiment_id": checked.experiment_id,
        "created": created,
        "online_authority": "none",
        "frozen_market_asof": cutoff,
        "ticker_count": len(cohort),
        "model_count": len(models),
        "model_artifact_checksum": checksum,
        "database_fingerprint": fingerprint,
    }


def capture_latest(config, dependencies=None):
    """Append one snapshot for the latest common reference session only."""
    checked = _validate_config(config)
    runtime = dependencies or default_dependencies()
    store = DownsideShadowStore(checked.shadow_database)
    experiment = store.load_experiment(checked.experiment_id)
    if experiment is None:
        raise ValueError("shadow experiment does not exist")
    bundle = read_shadow_model_bundle(
        experiment.model_artifact_path,
        expected_checksum=experiment.model_artifact_checksum,
    )
    snapshot = _validated_snapshot(runtime.load_inputs(checked))
    observation_date = _latest_common_reference_session(snapshot)
    if observation_date <= experiment.frozen_market_asof:
        return {
            "experiment_id": checked.experiment_id,
            "inserted_predictions": 0,
            "captured_observation_dates": [],
            "reason": "no_new_session_after_freeze",
            "online_authority": "none",
        }
    existing_rows = store.load_predictions(checked.experiment_id)
    existing_recorded_at = None
    if not existing_rows.empty:
        same_date = existing_rows.loc[
            existing_rows["observation_date"] == observation_date
        ]
        if not same_date.empty:
            recorded_values = same_date["recorded_at"].dropna().unique()
            if len(recorded_values) != 1:
                raise RuntimeError(
                    "existing shadow snapshot has inconsistent capture times"
                )
            existing_recorded_at = str(recorded_values[0])
    rows = _prediction_snapshot(
        experiment,
        bundle,
        snapshot,
        observation_date,
        runtime.now,
        existing_recorded_at=existing_recorded_at,
    )
    inserted = store.append_predictions(checked.experiment_id, rows)
    unavailable = sum(row.status == "unavailable" for row in rows)
    not_applicable = sum(row.status == "not_applicable" for row in rows)
    cohort_with_bar = sum(
        _has_session(snapshot.histories.get(ticker), observation_date)
        for ticker in experiment.universe
    )
    return {
        "experiment_id": checked.experiment_id,
        "inserted_predictions": inserted,
        "captured_observation_dates": [observation_date],
        "prediction_count": len(rows),
        "unavailable_predictions": unavailable,
        "not_applicable_predictions": not_applicable,
        "coverage": cohort_with_bar / len(experiment.universe),
        "online_authority": "none",
    }


def evaluate_shadow(config, dependencies=None):
    """Attach only mature future paths and publish audited shadow reports."""
    checked = _validate_config(config)
    runtime = dependencies or default_dependencies()
    store = DownsideShadowStore(checked.shadow_database)
    experiment = store.load_experiment(checked.experiment_id)
    if experiment is None:
        raise ValueError("shadow experiment does not exist")
    read_shadow_model_bundle(
        experiment.model_artifact_path,
        expected_checksum=experiment.model_artifact_checksum,
    )
    predictions = store.load_predictions(checked.experiment_id)
    snapshot = _validated_snapshot(runtime.load_inputs(checked))
    new_outcomes = _mature_outcomes(
        experiment,
        predictions,
        store.load_outcomes(checked.experiment_id),
        snapshot,
        runtime.now,
    )
    inserted = store.append_outcomes(
        checked.experiment_id,
        new_outcomes,
    )
    outcomes = store.load_outcomes(checked.experiment_id)
    metrics = _shadow_metrics(predictions, outcomes)
    manifest = _evaluation_manifest(
        experiment,
        predictions,
        outcomes,
        metrics,
        snapshot,
        inserted,
    )
    output_paths = _shadow_output_paths(
        checked.output_directory,
        checked.experiment_id,
    )
    artifacts = ShadowEvaluationArtifacts(
        manifest=manifest,
        metrics=metrics,
        outcomes=outcomes,
        output_paths=output_paths,
    )
    _publish_shadow_reports(artifacts)
    return artifacts


def default_dependencies():
    from research.run_expanded_walkforward_study import expanded_feature_sets
    from research.run_pressure_downside_study import SPECIALIST_FEATURE_COLUMNS
    from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS

    return ShadowDependencies(
        load_inputs=_load_real_snapshot,
        now=lambda: datetime.now(timezone.utc),
        code_commit=_git_commit,
        ridge_features=tuple(RIDGE_V4_FEATURE_COLUMNS),
        direction_features=tuple(
            expanded_feature_sets(RIDGE_V4_FEATURE_COLUMNS)[
                "ridge_decay_market"
            ]
        ),
        pressure_features=tuple(SPECIALIST_FEATURE_COLUMNS),
    )


def _prediction_snapshot(
    experiment,
    bundle,
    snapshot,
    observation_date,
    now,
    *,
    existing_recorded_at=None,
):
    feature_date = pd.Timestamp(observation_date)
    regime = _regime_at(snapshot.regimes, observation_date)
    signature = _snapshot_fingerprint(
        snapshot,
        experiment.universe,
        observation_date,
    )
    recorded_at = (
        _aware_timestamp(existing_recorded_at)
        if existing_recorded_at is not None
        else _aware_now(now)
    )
    available_at = datetime.combine(
        feature_date.date(),
        time(16, 0),
        tzinfo=MARKET_TIMEZONE,
    ).isoformat()
    model_by_key = {
        (model.specification, model.horizon): model
        for model in bundle["models"]
    }
    rule_frame = snapshot.rule_frame
    if rule_frame is None:
        rule_frame = _build_rule_frame(snapshot)
    rows = []
    for ticker in experiment.universe:
        group = _group_at(snapshot.assignments, ticker, observation_date)
        has_bar = _has_session(snapshot.histories.get(ticker), observation_date)
        key = (ticker, feature_date)
        feature_present = key in snapshot.feature_frame.index
        for specification, horizons in (
            ("ridge_current", experiment.horizons),
            ("general_logistic", experiment.horizons),
            ("pressure_downside_logistic", (5, 20)),
            ("memory_12", experiment.horizons),
        ):
            for horizon in horizons:
                if horizon not in experiment.horizons:
                    continue
                common = dict(
                    experiment_id=experiment.experiment_id,
                    specification=specification,
                    ticker=ticker,
                    observation_date=observation_date,
                    horizon=horizon,
                    group_key=group,
                    market_regime=regime,
                    risk_rule_version=RISK_RULE_VERSION,
                    feature_version=FEATURE_VERSION,
                    available_at_close=available_at,
                    executable_at="next_session_open",
                    market_signature=signature,
                    recorded_at=recorded_at,
                )
                if not has_bar or not feature_present:
                    rows.append(
                        ShadowPrediction(
                            **common,
                            predicted_event=None,
                            predicted_score=None,
                            status="unavailable",
                            unavailable_reason="missing_current_stock_bar",
                            model_version=_model_version(
                                model_by_key, specification, horizon
                            ),
                        )
                    )
                    continue
                if (
                    specification == "pressure_downside_logistic"
                    and regime not in PRESSURE_REGIMES
                ):
                    rows.append(
                        ShadowPrediction(
                            **common,
                            predicted_event=None,
                            predicted_score=None,
                            status="not_applicable",
                            unavailable_reason="outside_pressure_regime",
                            model_version=_model_version(
                                model_by_key, specification, horizon
                            ),
                        )
                    )
                    continue
                if specification == "memory_12":
                    event, score, reason = _memory_prediction(
                        rule_frame, key
                    )
                    if event is None:
                        rows.append(
                            ShadowPrediction(
                                **common,
                                predicted_event=None,
                                predicted_score=None,
                                status="unavailable",
                                unavailable_reason=reason,
                                model_version=RISK_RULE_VERSION,
                            )
                        )
                    else:
                        rows.append(
                            ShadowPrediction(
                                **common,
                                predicted_event=event,
                                predicted_score=score,
                                status="available",
                                unavailable_reason=None,
                                model_version=RISK_RULE_VERSION,
                            )
                        )
                    continue
                artifact = model_by_key[(specification, horizon)]
                predicted = predict_frozen_linear(
                    artifact,
                    snapshot.feature_frame.loc[[key]],
                ).iloc[0]
                rows.append(
                    ShadowPrediction(
                        **common,
                        predicted_event=bool(predicted["predicted_event"]),
                        predicted_score=float(predicted["predicted_score"]),
                        status="available",
                        unavailable_reason=None,
                        model_version=artifact.model_version,
                    )
                )
    return rows


def _mature_outcomes(
    experiment,
    predictions,
    existing_outcomes,
    snapshot,
    now,
):
    if predictions.empty:
        return []
    from research.unified_downside_benchmark import (
        attach_next_open_path_targets,
    )

    prices = _price_frame(snapshot.histories, experiment.universe)
    targets = attach_next_open_path_targets(
        prices,
        horizons=experiment.horizons,
    )
    existing_times = {}
    if not existing_outcomes.empty:
        for row in existing_outcomes.to_dict(orient="records"):
            key = (
                row["specification"],
                row["ticker"],
                row["observation_date"],
                int(row["horizon"]),
            )
            existing_times[key] = row["matured_at"]
    default_matured_at = _aware_now(now)
    rows = []
    for prediction in predictions.to_dict(orient="records"):
        ticker = str(prediction["ticker"])
        observation_date = pd.Timestamp(prediction["observation_date"])
        horizon = int(prediction["horizon"])
        target_key = (ticker, observation_date, horizon)
        if target_key not in targets.index:
            continue
        target = targets.loc[target_key]
        if not bool(target["mature"]):
            continue
        history = snapshot.histories.get(ticker)
        entry_date, label_end_date = _path_dates(
            history,
            observation_date,
            horizon,
        )
        key = (
            str(prediction["specification"]),
            ticker,
            observation_date.date().isoformat(),
            horizon,
        )
        matured_at = (
            _aware_timestamp(existing_times[key])
            if key in existing_times
            else default_matured_at
        )
        signature = _snapshot_fingerprint(
            snapshot,
            (ticker,),
            label_end_date,
        )
        rows.append(
            ShadowOutcome(
                experiment_id=experiment.experiment_id,
                specification=key[0],
                ticker=ticker,
                observation_date=key[2],
                horizon=horizon,
                entry_date=entry_date,
                entry_open=float(target["entry_open"]),
                label_end_date=label_end_date,
                terminal_return=float(target["terminal_return"]),
                mae=float(target["mae"]),
                mfe=float(target["mfe"]),
                actual_event=bool(target["actual_event"]),
                matured_at=matured_at,
                market_signature=signature,
            )
        )
    return rows


def _price_frame(histories, universe):
    parts = []
    for ticker in universe:
        history = histories.get(ticker)
        if not isinstance(history, pd.DataFrame) or history.empty:
            continue
        required = ("Open", "High", "Low", "Close")
        if any(column not in history for column in required):
            continue
        selected = history.loc[:, required].copy(deep=True)
        selected.index = pd.DatetimeIndex(selected.index).tz_localize(None)
        selected.index.name = "observation_date"
        selected.insert(0, "ticker", ticker)
        selected = selected.reset_index().set_index(
            ["ticker", "observation_date"]
        )
        parts.append(selected)
    if not parts:
        raise ValueError("shadow evaluation has no cohort price histories")
    return pd.concat(parts).sort_index()


def _path_dates(history, observation_date, horizon):
    if not isinstance(history, pd.DataFrame) or history.empty:
        raise ValueError("mature target history is unavailable")
    dates = pd.DatetimeIndex(history.index).tz_localize(None).sort_values()
    positions = np.flatnonzero(dates == observation_date)
    if len(positions) != 1 or positions[0] + horizon >= len(dates):
        raise ValueError("mature target dates are inconsistent")
    position = int(positions[0])
    return (
        dates[position + 1].date().isoformat(),
        dates[position + horizon].date().isoformat(),
    )


def _shadow_metrics(predictions, outcomes):
    columns = (
        "specification",
        "horizon",
        "status",
        "sample_count",
        "actual_event_rate",
        "signal_rate",
        "precision",
        "recall",
        "specificity",
        "balanced_accuracy",
        "f1",
        "roc_auc",
        "pr_auc",
        "availability_rate",
        "applicability_rate",
        "mean_signal_terminal_return",
        "mean_nonsignal_terminal_return",
        "maximum_drawdown",
    )
    if predictions.empty:
        return pd.DataFrame(columns=columns)
    keys = [
        "experiment_id",
        "specification",
        "ticker",
        "observation_date",
        "horizon",
    ]
    joined = predictions.merge(
        outcomes,
        on=keys,
        how="inner",
        validate="one_to_one",
        suffixes=("", "_outcome"),
    )
    joined = joined.loc[
        joined["status"] == "available"
    ].copy()
    rows = []
    for (specification, horizon), group in joined.groupby(
        ["specification", "horizon"], sort=True
    ):
        actual = group["actual_event"].astype(bool)
        predicted = group["predicted_event"].astype(bool)
        positives = int(actual.sum())
        negatives = int((~actual).sum())
        true_positive = int((actual & predicted).sum())
        true_negative = int((~actual & ~predicted).sum())
        precision_denominator = int(predicted.sum())
        precision = (
            true_positive / precision_denominator
            if precision_denominator
            else np.nan
        )
        recall = true_positive / positives if positives else np.nan
        specificity = true_negative / negatives if negatives else np.nan
        balanced = (
            (recall + specificity) / 2.0
            if np.isfinite(recall) and np.isfinite(specificity)
            else np.nan
        )
        signal_returns = pd.to_numeric(
            group.loc[predicted]
            .sort_values(["observation_date", "ticker"])[
                "terminal_return"
            ],
            errors="coerce",
        )
        nonsignal_returns = pd.to_numeric(
            group.loc[~predicted, "terminal_return"],
            errors="coerce",
        )
        all_prediction_rows = predictions.loc[
            (predictions["specification"] == specification)
            & (predictions["horizon"] == horizon)
        ]
        applicable = all_prediction_rows["status"] != "not_applicable"
        available = all_prediction_rows["status"] == "available"
        f1 = (
            2.0 * precision * recall / (precision + recall)
            if np.isfinite(precision)
            and np.isfinite(recall)
            and precision + recall > 0.0
            else np.nan
        )
        roc_auc, pr_auc = _probability_metrics(
            specification,
            actual,
            group["predicted_score"],
        )
        rows.append(
            {
                "specification": specification,
                "horizon": int(horizon),
                "status": (
                    "ok"
                    if len(group) >= 50 and positives and negatives
                    else "insufficient"
                ),
                "sample_count": int(len(group)),
                "actual_event_rate": float(actual.mean()),
                "signal_rate": float(predicted.mean()),
                "precision": precision,
                "recall": recall,
                "specificity": specificity,
                "balanced_accuracy": balanced,
                "f1": f1,
                "roc_auc": roc_auc,
                "pr_auc": pr_auc,
                "availability_rate": float(available.mean()),
                "applicability_rate": float(applicable.mean()),
                "mean_signal_terminal_return": (
                    float(signal_returns.mean())
                    if len(signal_returns)
                    else np.nan
                ),
                "mean_nonsignal_terminal_return": (
                    float(nonsignal_returns.mean())
                    if len(nonsignal_returns)
                    else np.nan
                ),
                "maximum_drawdown": _maximum_drawdown(signal_returns),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def _probability_metrics(specification, actual, score):
    if specification not in (
        "general_logistic",
        "pressure_downside_logistic",
    ):
        return np.nan, np.nan
    numeric = pd.to_numeric(score, errors="coerce")
    valid = numeric.notna()
    if valid.sum() < 2 or actual.loc[valid].nunique() < 2:
        return np.nan, np.nan
    from sklearn.metrics import average_precision_score, roc_auc_score

    truth = actual.loc[valid].astype(bool)
    return (
        float(roc_auc_score(truth, numeric.loc[valid])),
        float(average_precision_score(truth, numeric.loc[valid])),
    )


def _maximum_drawdown(returns):
    clean = pd.to_numeric(returns, errors="coerce").dropna()
    if clean.empty:
        return np.nan
    equity = (1.0 + clean).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def _evaluation_manifest(
    experiment,
    predictions,
    outcomes,
    metrics,
    snapshot,
    inserted,
):
    captured_dates = (
        sorted(predictions["observation_date"].unique().tolist())
        if not predictions.empty
        else []
    )
    latest = _latest_common_reference_session(snapshot)
    planned = [
        value.date().isoformat()
        for value in _common_reference_sessions(snapshot)
        if experiment.frozen_market_asof
        < value.date().isoformat()
        <= latest
    ]
    gaps = sorted(set(planned).difference(captured_dates))
    gate = _promotion_gate(
        metrics,
        predictions,
        outcomes,
        captured_dates,
    )
    return {
        "schema_version": "downside-shadow-report-v1",
        "experiment_id": experiment.experiment_id,
        "study_version": experiment.study_version,
        "frozen_market_asof": experiment.frozen_market_asof,
        "latest_market_asof": latest,
        "online_authority": "none",
        "prospective_only": True,
        "captured_observation_dates": captured_dates,
        "planned_observation_dates": planned,
        "capture_gap_dates": gaps,
        "capture_gap_count": len(gaps),
        "prediction_count": int(len(predictions)),
        "mature_outcome_count": int(len(outcomes)),
        "inserted_outcomes": int(inserted),
        "promotion_gate": gate,
    }


def _promotion_gate(metrics, predictions, outcomes, captured_dates):
    reasons = []
    if len(captured_dates) < 60:
        reasons.append("captured_sessions_below_60")
    joined = predictions.merge(
        outcomes,
        on=[
            "experiment_id",
            "specification",
            "ticker",
            "observation_date",
            "horizon",
        ],
        how="inner",
    ) if not predictions.empty and not outcomes.empty else pd.DataFrame()
    for horizon in (5, 20):
        pressure = _metric(metrics, "pressure_downside_logistic", horizon)
        ridge = _metric(metrics, "ridge_current", horizon)
        pressure_samples = (
            0 if pressure is None else int(pressure["sample_count"])
        )
        if pressure_samples < 1_000:
            reasons.append(f"pressure_{horizon}_samples_below_1000")
        if (
            pressure is None
            or ridge is None
            or not np.isfinite(pressure["balanced_accuracy"])
            or not np.isfinite(ridge["balanced_accuracy"])
            or pressure["balanced_accuracy"] <= ridge["balanced_accuracy"]
        ):
            reasons.append(f"pressure_{horizon}_does_not_beat_ridge")
        if (
            pressure is None
            or not np.isfinite(pressure["recall"])
            or pressure["recall"] < 0.45
        ):
            reasons.append(f"pressure_{horizon}_recall_below_0_45")
        if (
            pressure is None
            or not np.isfinite(pressure["specificity"])
            or pressure["specificity"] < 0.55
        ):
            reasons.append(f"pressure_{horizon}_specificity_below_0_55")
        if (
            pressure is None
            or ridge is None
            or not np.isfinite(pressure["maximum_drawdown"])
            or not np.isfinite(ridge["maximum_drawdown"])
            or pressure["maximum_drawdown"]
            < ridge["maximum_drawdown"] - 0.02
        ):
            reasons.append(f"pressure_{horizon}_drawdown_gate_failed")
    if joined.empty:
        group_counts = {}
    else:
        group_counts = (
            joined.loc[joined["status"] == "available"]
            .drop_duplicates(
                ["ticker", "observation_date", "horizon"]
            )
            .groupby("group_key")
            .size()
            .to_dict()
        )
    for group in ("semiconductor", "software"):
        if int(group_counts.get(group, 0)) < 250:
            reasons.append(f"{group}_samples_below_250")
    return {
        "eligible_for_human_review": not reasons,
        "online_authority": "none",
        "audit_violation_count": 0,
        "failed_conditions": reasons,
    }


def _metric(metrics, specification, horizon):
    if metrics.empty:
        return None
    selected = metrics.loc[
        (metrics["specification"] == specification)
        & (metrics["horizon"] == horizon)
    ]
    return None if selected.empty else selected.iloc[0]


def _shadow_output_paths(directory, experiment_id):
    root = Path(directory)
    return (
        root / f"{experiment_id}.json",
        root / f"{experiment_id}.csv",
        root / f"{experiment_id}.md",
    )


def _publish_shadow_reports(artifacts):
    json_path, csv_path, markdown_path = artifacts.output_paths
    for path in artifacts.output_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "manifest": artifacts.manifest,
        "metrics": _records(artifacts.metrics),
    }
    rendered_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ) + "\n"
    rendered_csv = artifacts.metrics.to_csv(index=False)
    rendered_markdown = _render_shadow_markdown(artifacts)
    for path, content in (
        (json_path, rendered_json),
        (csv_path, rendered_csv),
        (markdown_path, rendered_markdown),
    ):
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _render_shadow_markdown(artifacts):
    manifest = artifacts.manifest
    lines = [
        "# 前瞻向下风险影子评估",
        "",
        "这是冻结日之后的前瞻影子结果，不是历史回填。",
        "即使达到研究门槛，也不具备线上否决权。",
        "",
        f"- 实验：`{manifest['experiment_id']}`",
        f"- 冻结日：{manifest['frozen_market_asof']}",
        f"- 已捕获交易日：{len(manifest['captured_observation_dates'])}",
        f"- 捕获缺口：{manifest['capture_gap_count']}",
        f"- 成熟结果：{manifest['mature_outcome_count']}",
        f"- 线上权限：`{manifest['online_authority']}`",
        "",
        "## 模型结果",
        "",
    ]
    if artifacts.metrics.empty:
        lines.append("尚无成熟样本。")
    else:
        lines.extend(
            [
                "| 模型 | 周期 | 样本 | 状态 | 召回率 | 特异度 | 平衡准确率 |",
                "|---|---:|---:|---|---:|---:|---:|",
            ]
        )
        for row in artifacts.metrics.to_dict(orient="records"):
            lines.append(
                "| {specification} | {horizon} | {sample_count} | "
                "{status} | {recall} | {specificity} | "
                "{balanced_accuracy} |".format(
                    **{
                        **row,
                        "recall": _display_metric(row["recall"]),
                        "specificity": _display_metric(
                            row["specificity"]
                        ),
                        "balanced_accuracy": _display_metric(
                            row["balanced_accuracy"]
                        ),
                    }
                )
            )
    lines.extend(
        [
            "",
            "## 晋级门槛",
            "",
            (
                "允许进入人工评审。"
                if manifest["promotion_gate"][
                    "eligible_for_human_review"
                ]
                else "尚未达到人工评审门槛。"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _records(frame):
    records = []
    for row in frame.to_dict(orient="records"):
        records.append(
            {
                key: (
                    None
                    if isinstance(value, (float, np.floating))
                    and not np.isfinite(value)
                    else _canonical_scalar(value)
                )
                for key, value in row.items()
            }
        )
    return records


def _display_metric(value):
    return "—" if not np.isfinite(value) else f"{float(value):.3f}"


def _memory_prediction(rule_frame, key):
    if (
        not isinstance(rule_frame, pd.DataFrame)
        or key not in rule_frame.index
    ):
        return None, None, "memory_rule_unavailable"
    row = rule_frame.loc[key]
    signal = row.get("signal_memory_12")
    score = pd.to_numeric(
        pd.Series([row.get("individual_risk_score")]),
        errors="coerce",
    ).iloc[0]
    if pd.isna(signal) or pd.isna(score):
        return None, None, "memory_rule_unavailable"
    return bool(signal), float(score) / 100.0, None


def _model_version(models, specification, horizon):
    model = models.get((specification, horizon))
    return "unavailable-model" if model is None else model.model_version


def _select_frozen_cohort(snapshot, cutoff, config):
    eligible = []
    for ticker in snapshot.analysis_tickers:
        history = snapshot.histories.get(ticker)
        if (
            _has_session(history, cutoff)
            and len(history.loc[:pd.Timestamp(cutoff)]) >= config.minimum_history
        ):
            eligible.append(str(ticker).upper())
    if len(eligible) < config.max_tickers:
        raise ValueError(
            "not enough active stocks at the frozen market cutoff"
        )
    return tuple(eligible[: config.max_tickers])


def _latest_common_reference_session(snapshot):
    common = _common_reference_sessions(snapshot)
    if not len(common):
        raise ValueError("required references have no common market session")
    return common[-1].date().isoformat()


def _common_reference_sessions(snapshot):
    common = None
    for ticker in snapshot.reference_tickers:
        history = snapshot.histories.get(ticker)
        if not isinstance(history, pd.DataFrame) or history.empty:
            raise ValueError(f"required reference history is missing: {ticker}")
        dates = set(
            pd.DatetimeIndex(history.index).tz_localize(None).normalize()
        )
        common = dates if common is None else common.intersection(dates)
    return pd.DatetimeIndex(sorted(common or ()))


def _snapshot_fingerprint(snapshot, cohort, cutoff):
    digest = sha256()
    limit = pd.Timestamp(cutoff)
    for ticker in sorted(set(cohort).union(snapshot.reference_tickers)):
        history = snapshot.histories.get(ticker)
        digest.update(str(ticker).encode("utf-8"))
        if not isinstance(history, pd.DataFrame) or history.empty:
            digest.update(b"<missing>")
            continue
        selected = history.loc[
            pd.DatetimeIndex(history.index).tz_localize(None) <= limit
        ].sort_index()
        digest.update(
            pd.util.hash_pandas_object(selected, index=True)
            .to_numpy(dtype="uint64", copy=False)
            .tobytes()
        )
    assignments = snapshot.assignments.loc[
        snapshot.assignments["ticker"].astype(str).str.upper().isin(cohort)
    ].copy(deep=True)
    if not assignments.empty:
        start_column = (
            "effective_from"
            if "effective_from" in assignments
            else "effective_date"
        )
        starts = pd.to_datetime(
            assignments[start_column],
            errors="coerce",
        )
        assignments = assignments.loc[starts <= limit]
        records = [
            {
                str(column): _canonical_scalar(value)
                for column, value in row.items()
            }
            for row in assignments.to_dict(orient="records")
        ]
        records.sort(
            key=lambda row: json.dumps(
                row,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        digest.update(
            json.dumps(
                records,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _has_session(history, date):
    if not isinstance(history, pd.DataFrame) or history.empty:
        return False
    dates = pd.DatetimeIndex(history.index).tz_localize(None).normalize()
    return pd.Timestamp(date) in dates


def _regime_at(regimes, date):
    frame = regimes.copy(deep=True)
    frame["observation_date"] = pd.to_datetime(
        frame["observation_date"], errors="raise"
    ).dt.tz_localize(None)
    selected = frame.loc[
        frame["observation_date"] == pd.Timestamp(date), "regime"
    ]
    if selected.empty:
        raise ValueError("market regime is unavailable at capture close")
    return str(selected.iloc[-1])


def _group_at(assignments, ticker, date):
    if assignments.empty:
        return "unclassified"
    rows = assignments.loc[
        assignments["ticker"].astype(str).str.upper() == ticker
    ].copy()
    if rows.empty:
        return "unclassified"
    observed = pd.Timestamp(date)
    start_column = (
        "effective_from"
        if "effective_from" in rows
        else "effective_date"
    )
    starts = pd.to_datetime(rows[start_column], errors="coerce")
    active = starts.le(observed)
    if "effective_to" in rows:
        ends = pd.to_datetime(rows["effective_to"], errors="coerce")
        active &= ends.isna() | ends.ge(observed)
    rows = rows.loc[active].copy()
    if rows.empty:
        return "unclassified"
    rows["_start"] = starts.loc[rows.index]
    row = rows.sort_values("_start").iloc[-1]
    for column in (
        "group_key",
        "primary_model_group",
        "primary_group",
        "sector_key",
    ):
        if column in row and pd.notna(row[column]):
            return str(row[column])
    return "unclassified"


def _build_rule_frame(snapshot):
    from research.evaluate_toprisk_comparison import build_comparison_frame
    from web.forecasts.decision import build_forecast_risk_context

    assignment_histories = {}
    for row in snapshot.assignments.to_dict(orient="records"):
        if pd.isna(row.get("effective_to")):
            row["effective_to"] = None
        assignment_histories.setdefault(str(row["ticker"]), []).append(row)
    context = build_forecast_risk_context(
        snapshot.histories,
        assignments=assignment_histories,
    )
    return build_comparison_frame(
        snapshot.histories,
        context=context,
        feature_frame=snapshot.feature_frame,
    )


def _load_real_snapshot(config):  # pragma: no cover - integration path
    from research.run_unified_downside_benchmark import (
        BenchmarkConfig,
        _attach_direction_targets,
        _build_rule_context,
        _load_real_inputs,
    )
    from web.market_groups import REFERENCE_TICKERS

    benchmark_config = BenchmarkConfig(
        database=config.research_database,
        start_date=config.start_date,
        max_tickers=max(config.max_tickers, config.candidate_tickers),
        horizons=config.horizons,
    )
    inputs = _load_real_inputs(benchmark_config)
    frame = _attach_direction_targets(inputs, benchmark_config)
    rule_frame = _build_rule_context(inputs)
    return ShadowInputSnapshot(
        feature_frame=frame,
        histories=inputs.histories or {},
        assignments=inputs.assignments,
        regimes=inputs.regimes,
        analysis_tickers=tuple(inputs.analysis_tickers),
        reference_tickers=tuple(
            ticker
            for ticker in REFERENCE_TICKERS
            if ticker in {"QQQ", "SPY"}
        ),
        rule_frame=rule_frame,
    )


def _validated_snapshot(value):
    if not isinstance(value, ShadowInputSnapshot):
        raise TypeError("load_inputs must return ShadowInputSnapshot")
    if not isinstance(value.feature_frame.index, pd.MultiIndex):
        raise ValueError("feature_frame requires a MultiIndex")
    if value.feature_frame.index.names != ["ticker", "observation_date"]:
        raise ValueError("feature_frame index names are invalid")
    if not value.analysis_tickers:
        raise ValueError("analysis_tickers must not be empty")
    if not value.reference_tickers:
        raise ValueError("reference_tickers must not be empty")
    return value


def _validate_config(config):
    if not isinstance(config, ShadowConfig):
        raise TypeError("config must be ShadowConfig")
    if config.max_tickers <= 0 or config.minimum_history <= 0:
        raise ValueError("ticker and history limits must be positive")
    if not config.horizons or any(
        horizon not in (5, 10, 20) for horizon in config.horizons
    ):
        raise ValueError("shadow horizons must be drawn from 5, 10, and 20")
    return config


def _aware_now(factory):
    value = factory()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("now must return a timezone-aware datetime")
    return value.isoformat()


def _aware_timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError("stored capture time is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored capture time must include a UTC offset")
    return parsed.isoformat()


def _canonical_scalar(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (tuple, list)):
        return [_canonical_scalar(item) for item in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        return None if pd.isna(value) else value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, np.generic):
        return value.item()
    return value


def _git_commit():
    result = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("freeze", "capture", "evaluate")
    )
    parser.add_argument("--research-database", type=Path, required=True)
    parser.add_argument("--shadow-database", type=Path, required=True)
    parser.add_argument("--model-artifact", type=Path, required=True)
    parser.add_argument(
        "--experiment-id", default="downside-shadow-v1"
    )
    return parser


def main(argv=None):
    arguments = _parser().parse_args(argv)
    config = ShadowConfig(
        research_database=arguments.research_database,
        shadow_database=arguments.shadow_database,
        model_artifact=arguments.model_artifact,
        experiment_id=arguments.experiment_id,
    )
    try:
        if arguments.command == "freeze":
            result = freeze_experiment(config)
        elif arguments.command == "capture":
            result = capture_latest(config)
        else:
            artifacts = evaluate_shadow(config)
            result = {
                "experiment_id": config.experiment_id,
                "inserted_outcomes": artifacts.manifest[
                    "inserted_outcomes"
                ],
                "mature_outcome_count": artifacts.manifest[
                    "mature_outcome_count"
                ],
                "online_authority": "none",
            }
    except (TypeError, ValueError, RuntimeError, OSError):
        print(json.dumps({"ok": False, "error_code": "shadow_command_failed"}))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
