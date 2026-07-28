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
    common = None
    for ticker in snapshot.reference_tickers:
        history = snapshot.histories.get(ticker)
        if not isinstance(history, pd.DataFrame) or history.empty:
            raise ValueError(f"required reference history is missing: {ticker}")
        dates = set(
            pd.DatetimeIndex(history.index).tz_localize(None).normalize()
        )
        common = dates if common is None else common.intersection(dates)
    if not common:
        raise ValueError("required references have no common market session")
    return max(common).date().isoformat()


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
    if isinstance(value, pd.Timestamp):
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
    parser.add_argument("command", choices=("freeze", "capture"))
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
        result = (
            freeze_experiment(config)
            if arguments.command == "freeze"
            else capture_latest(config)
        )
    except (TypeError, ValueError, RuntimeError, OSError):
        print(json.dumps({"ok": False, "error_code": "shadow_command_failed"}))
        return 1
    print(json.dumps({"ok": True, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
