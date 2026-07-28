"""Run the unified point-in-time downside walk-forward benchmark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Callable, Mapping, Optional

import numpy as np
import pandas as pd

from research.unified_benchmark_cache import (
    BenchmarkCacheArtifact,
    BenchmarkCacheIdentity,
    UnifiedBenchmarkCacheStore,
)
from research.unified_downside_benchmark import (
    MODEL_KEY_COLUMNS,
    align_model_predictions,
    attach_next_open_path_targets,
    attach_point_in_time_strata,
    compare_folds,
    evaluate_unified_predictions,
)


STUDY_VERSION = "unified-downside-walkforward-v2"
CACHE_SCHEMA_VERSION = "unified-benchmark-cache-v1"
_RULE_CACHE_SPECIFICATIONS = {
    "immediate_8",
    "memory_12",
    "toprisk_confirmed",
    "toprisk_stateful",
    "ridge_plus_toprisk",
}


@dataclass(frozen=True)
class BenchmarkConfig:
    database: Path
    start_date: str = "2018-01-01"
    max_tickers: int = 240
    folds: int = 5
    horizons: tuple[int, ...] = (5, 10, 20)
    minimum_training_samples: int = 1_000
    minimum_group_samples: int = 50
    output_directory: Path = Path("reports")
    cache_database: Path = Path("data/unified_benchmark_cache.db")
    cache_enabled: bool = True
    rebuild_cache: bool = False


@dataclass(frozen=True)
class BenchmarkInputs:
    prices: pd.DataFrame
    assignments: pd.DataFrame
    regimes: pd.DataFrame
    histories: Mapping[str, pd.DataFrame] | None = None
    feature_frame: pd.DataFrame | None = None
    analysis_tickers: tuple[str, ...] = ()


@dataclass(frozen=True)
class BenchmarkDependencies:
    load_inputs: Callable[[BenchmarkConfig], BenchmarkInputs]
    build_predictions: Callable[
        [BenchmarkInputs, BenchmarkConfig],
        Mapping[str, pd.DataFrame],
    ]
    build_rule_predictions: Optional[
        Callable[
            [BenchmarkInputs, BenchmarkConfig, Mapping[str, pd.DataFrame]],
            Mapping[str, pd.DataFrame],
        ]
    ] = None
    database_fingerprint: Optional[
        Callable[[BenchmarkInputs, BenchmarkConfig], str]
    ] = None
    assignment_fingerprint: Optional[Callable[[BenchmarkInputs], str]] = None
    code_fingerprint: Optional[Callable[[], tuple[str, bool]]] = None
    cache_store_factory: Callable[
        [Path], UnifiedBenchmarkCacheStore
    ] = UnifiedBenchmarkCacheStore
    monotonic: Callable[[], float] = time.monotonic
    progress: Callable[[str], None] = lambda message: print(
        message,
        file=sys.stderr,
    )


@dataclass(frozen=True)
class BenchmarkArtifacts:
    manifest: dict[str, object]
    metrics: pd.DataFrame
    fold_comparisons: pd.DataFrame
    ablations: pd.DataFrame
    overlaps: pd.DataFrame
    output_paths: tuple[Path, ...]


def run_benchmark(config, *, dependencies=None):
    """Build matched metrics and atomically publish all benchmark outputs."""
    checked = _validate_config(config)
    runtime = dependencies or default_dependencies()
    if not isinstance(runtime, BenchmarkDependencies):
        raise TypeError("dependencies must be BenchmarkDependencies")
    clock = runtime.monotonic
    total_started = clock()
    timings = {}
    inputs = _timed_stage(
        "load_inputs",
        lambda: runtime.load_inputs(checked),
        clock,
        runtime.progress,
        timings,
    )
    if not isinstance(inputs, BenchmarkInputs):
        raise TypeError("load_inputs must return BenchmarkInputs")
    cache = _prepare_cache(checked, inputs, runtime)
    statistical, statistical_artifact = _timed_stage(
        "build_statistical_predictions",
        lambda: _resolve_cached_stage(
            stage="statistical_predictions",
            inputs=inputs,
            config=checked,
            cache=cache,
            builder=lambda: runtime.build_predictions(inputs, checked),
        ),
        clock,
        runtime.progress,
        timings,
    )
    predictions = statistical
    if runtime.build_rule_predictions is None:
        _timed_stage(
            "build_rule_context",
            lambda: None,
            clock,
            runtime.progress,
            timings,
        )
        rule_artifact = None
        cache["manifest"]["rule_predictions"] = {
            "status": "not_configured",
            "artifact_key": None,
        }
    else:
        rule_predictions, rule_artifact = _timed_stage(
            "build_rule_context",
            lambda: _resolve_cached_stage(
                stage="rule_predictions",
                inputs=inputs,
                config=checked,
                cache=cache,
                dependency_artifact_key=cache["identities"][
                    "statistical_predictions"
                ].artifact_key
                if cache["identities"]
                else None,
                builder=lambda: runtime.build_rule_predictions(
                    inputs,
                    checked,
                    predictions,
                ),
            ),
            clock,
            runtime.progress,
            timings,
        )
        predictions = {**predictions, **rule_predictions}
    if not isinstance(predictions, Mapping) or "ridge_down" not in predictions:
        raise ValueError("predictions require ridge_down anchor rows")
    anchor = _model_keys(predictions["ridge_down"])
    if anchor.empty:
        raise ValueError("ridge_down anchor rows must not be empty")
    labels, aligned, stratified = _timed_stage(
        "label_and_align",
        lambda: _label_and_align(
            inputs,
            checked,
            anchor,
            predictions,
        ),
        clock,
        runtime.progress,
        timings,
    )
    metrics, fold_comparisons, promotion_gate = _timed_stage(
        "evaluate",
        lambda: _evaluate_aligned(stratified, checked),
        clock,
        runtime.progress,
        timings,
    )
    manifest = _manifest(
        checked,
        inputs,
        labels,
        aligned,
        promotion_gate,
        cache_manifest=cache["manifest"],
    )
    placeholder_paths = _output_paths(checked.output_directory)
    artifacts = BenchmarkArtifacts(
        manifest=manifest,
        metrics=metrics,
        fold_comparisons=fold_comparisons,
        ablations=pd.DataFrame(),
        overlaps=pd.DataFrame(),
        output_paths=placeholder_paths,
    )
    publish_started = clock()
    runtime.progress("stage=publish status=started")

    def finalize_manifest():
        timings["publish"] = max(0.0, clock() - publish_started)
        timings["total"] = max(0.0, clock() - total_started)
        manifest["stage_timings_seconds"] = dict(timings)
        runtime.progress(
            f"stage=publish status=completed seconds={timings['publish']:.6f}"
        )

    _publish_atomic(artifacts, finalize_manifest=finalize_manifest)
    pending = [
        artifact
        for artifact in (statistical_artifact, rule_artifact)
        if artifact is not None
    ]
    _commit_cache_after_publish(
        cache,
        pending,
        rebuild_cache=checked.rebuild_cache,
    )
    _refresh_published_manifest(artifacts)
    return artifacts


def _prepare_cache(config, inputs, runtime):
    manifest = {
        "mode": "enabled",
        "database": config.cache_database.name,
        "write_status": "not_needed",
    }
    context = {
        "store": None,
        "identities": {},
        "manifest": manifest,
        "read_enabled": False,
        "write_enabled": False,
        "stage_hits": {},
    }
    if not config.cache_enabled:
        manifest["mode"] = "disabled_by_flag"
        return context
    if runtime.code_fingerprint is None:
        manifest["mode"] = "disabled_custom_dependencies"
        return context

    code_provider = runtime.code_fingerprint
    try:
        code_fingerprint, dirty = code_provider()
        if not _is_sha256(code_fingerprint):
            raise ValueError("invalid code fingerprint")
        manifest["code_fingerprint"] = code_fingerprint
        manifest["dirty_worktree"] = bool(dirty)
        if dirty:
            manifest["mode"] = "disabled_dirty_worktree"
            return context
        database_provider = (
            runtime.database_fingerprint or _database_fingerprint
        )
        assignment_provider = (
            runtime.assignment_fingerprint or _assignment_fingerprint
        )
        database_fingerprint = database_provider(inputs, config)
        assignment_fingerprint = assignment_provider(inputs)
        if not _is_sha256(database_fingerprint) or not _is_sha256(
            assignment_fingerprint
        ):
            raise ValueError("invalid input fingerprint")
        identities = {}
        statistical = BenchmarkCacheIdentity(
            study_version=STUDY_VERSION,
            stage="statistical_predictions",
            database_fingerprint=database_fingerprint,
            assignment_fingerprint=assignment_fingerprint,
            config_fingerprint=_config_fingerprint(
                config,
                inputs,
                "statistical_predictions",
            ),
            code_fingerprint=code_fingerprint,
            dependency_artifact_key=None,
            schema_version=CACHE_SCHEMA_VERSION,
        )
        identities["statistical_predictions"] = statistical
        identities["rule_predictions"] = BenchmarkCacheIdentity(
            study_version=STUDY_VERSION,
            stage="rule_predictions",
            database_fingerprint=database_fingerprint,
            assignment_fingerprint=assignment_fingerprint,
            config_fingerprint=_config_fingerprint(
                config,
                inputs,
                "rule_predictions",
            ),
            code_fingerprint=code_fingerprint,
            dependency_artifact_key=statistical.artifact_key,
            schema_version=CACHE_SCHEMA_VERSION,
        )
        context["identities"] = identities
        context["store"] = runtime.cache_store_factory(config.cache_database)
        context["read_enabled"] = not config.rebuild_cache
        context["write_enabled"] = True
        manifest["mode"] = (
            "rebuild" if config.rebuild_cache else "enabled"
        )
        manifest["database_fingerprint"] = database_fingerprint
        manifest["assignment_fingerprint"] = assignment_fingerprint
    except Exception as error:
        manifest["mode"] = "disabled_fingerprint_error"
        manifest["reason"] = type(error).__name__
    return context


def _resolve_cached_stage(
    *,
    stage,
    inputs,
    config,
    cache,
    builder,
    dependency_artifact_key=None,
):
    identity = cache["identities"].get(stage)
    stage_manifest = {
        "status": "disabled",
        "artifact_key": (
            identity.artifact_key if identity is not None else None
        ),
    }
    cache["manifest"][stage] = stage_manifest
    read_status = None
    dependency_rebuilt = (
        stage == "rule_predictions"
        and cache["read_enabled"]
        and not cache["stage_hits"].get("statistical_predictions", False)
    )
    if dependency_rebuilt:
        read_status = "miss"
        stage_manifest["status"] = "dependency_rebuilt"
    elif cache["read_enabled"] and identity is not None:
        try:
            cached = cache["store"].read(identity)
            read_status = cached.status
            if cached.status == "hit":
                _validate_cached_predictions(
                    cached.frames,
                    stage=stage,
                    inputs=inputs,
                    config=config,
                )
                stage_manifest["status"] = "hit"
                cache["stage_hits"][stage] = True
                return cached.frames, None
            stage_manifest["status"] = cached.status
            if cached.reason:
                stage_manifest["reason"] = cached.reason
        except Exception as error:
            read_status = "miss_error"
            stage_manifest["status"] = "miss_error"
            stage_manifest["reason"] = type(error).__name__
    elif cache["manifest"]["mode"] == "rebuild":
        read_status = "rebuild"
        stage_manifest["status"] = "rebuild"

    frames = builder()
    cache["stage_hits"][stage] = False
    _validate_cached_predictions(
        frames,
        stage=stage,
        inputs=inputs,
        config=config,
    )
    if stage_manifest["status"] == "disabled":
        stage_manifest["status"] = "built_without_cache"
    elif stage_manifest["status"] in {"miss", "rebuild"}:
        stage_manifest["status"] = "built"
    else:
        stage_manifest["rebuilt_cold"] = True

    pending = None
    may_write = (
        cache["write_enabled"]
        and identity is not None
        and (
            read_status in {None, "miss", "rebuild"}
            or config.rebuild_cache
        )
    )
    if may_write:
        pending = BenchmarkCacheArtifact.from_frames(identity, frames)
        if (
            dependency_artifact_key is not None
            and identity.dependency_artifact_key != dependency_artifact_key
        ):
            raise ValueError("rule cache dependency identity mismatch")
        stage_manifest["write_pending"] = True
        cache["manifest"]["write_status"] = "pending_after_publish"
    return frames, pending


def _validate_cached_predictions(frames, *, stage, inputs, config):
    if not isinstance(frames, Mapping):
        raise ValueError("cached predictions must be a mapping")
    names = set(frames)
    if stage == "statistical_predictions":
        if "ridge_down" not in names:
            raise ValueError("statistical cache requires ridge_down")
    elif stage == "rule_predictions":
        missing = sorted(_RULE_CACHE_SPECIFICATIONS.difference(names))
        if missing:
            raise ValueError(
                "rule cache is missing specifications: {}".format(missing)
            )
    else:
        raise ValueError("unsupported cache stage")
    allowed_tickers = set(inputs.analysis_tickers)
    if not allowed_tickers and "ticker" in inputs.prices:
        allowed_tickers = set(
            inputs.prices["ticker"].astype(str).str.strip().str.upper()
        )
    required = {
        *MODEL_KEY_COLUMNS,
        "predicted_event",
        "predicted_score",
        "model_version",
    }
    for name, frame in frames.items():
        if not isinstance(name, str) or not isinstance(frame, pd.DataFrame):
            raise ValueError("cached prediction entries are invalid")
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(
                "{} cache frame is missing columns: {}".format(name, missing)
            )
        keys = _model_keys(frame)
        if not set(keys["ticker"]).issubset(allowed_tickers):
            raise ValueError("cached predictions contain ticker outside cohort")
        horizons = pd.to_numeric(keys["horizon"], errors="coerce")
        folds = pd.to_numeric(keys["fold"], errors="coerce")
        if (
            horizons.isna().any()
            or not set(horizons.astype(int)).issubset(set(config.horizons))
            or folds.isna().any()
            or (folds < 1).any()
            or (folds > config.folds).any()
        ):
            raise ValueError("cached predictions contain invalid horizon or fold")
        versions = frame["model_version"].astype("string")
        if versions.isna().any() or versions.str.strip().eq("").any():
            raise ValueError("cached predictions contain invalid model versions")
        try:
            frame["predicted_event"].astype("boolean")
            pd.to_numeric(frame["predicted_score"], errors="coerce")
        except (TypeError, ValueError) as error:
            raise ValueError("cached prediction values are invalid") from error
    return frames


def _commit_cache_after_publish(cache, artifacts, *, rebuild_cache):
    manifest = cache["manifest"]
    if not artifacts:
        if manifest["write_status"] == "pending_after_publish":
            manifest["write_status"] = "not_needed"
        return
    try:
        inserted = cache["store"].commit(
            artifacts,
            repair_corrupt=bool(rebuild_cache),
        )
        manifest["write_status"] = "committed"
        manifest["written_artifact_count"] = int(inserted)
        for stage in ("statistical_predictions", "rule_predictions"):
            stage_manifest = manifest.get(stage)
            if isinstance(stage_manifest, dict):
                stage_manifest.pop("write_pending", None)
    except Exception as error:
        manifest["write_status"] = "cache_write_failed"
        manifest["write_error"] = type(error).__name__


def _refresh_published_manifest(artifacts):
    if not artifacts.output_paths:
        return
    json_path = artifacts.output_paths[0]
    temporary = json_path.with_name(".{}.cache.tmp".format(json_path.name))
    try:
        payload = {
            **artifacts.manifest,
            "metrics": _records(artifacts.metrics),
            "fold_comparisons": _records(artifacts.fold_comparisons),
            "ablations": _records(artifacts.ablations),
            "overlaps": _records(artifacts.overlaps),
        }
        temporary.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(json_path)
    except (OSError, TypeError, ValueError):
        artifacts.manifest["cache"]["telemetry_refresh_failed"] = True
    finally:
        if temporary.exists():
            temporary.unlink()


def _timed_stage(name, operation, clock, progress, timings):
    progress(f"stage={name} status=started")
    started = clock()
    result = operation()
    elapsed = max(0.0, clock() - started)
    timings[name] = elapsed
    progress(f"stage={name} status=completed seconds={elapsed:.6f}")
    return result


def _label_and_align(inputs, config, anchor, predictions):
    targets = attach_next_open_path_targets(
        inputs.prices,
        horizons=config.horizons,
    ).reset_index()
    labels = anchor.merge(
        targets,
        on=["ticker", "observation_date", "horizon"],
        how="left",
        validate="many_to_one",
    )
    if labels["mature"].isna().any():
        raise ValueError("prediction keys are outside executable price labels")
    aligned = align_model_predictions(labels, predictions)
    stratified = attach_point_in_time_strata(
        aligned,
        inputs.assignments,
        inputs.regimes,
    )
    return labels, aligned, stratified


def _evaluate_aligned(stratified, config):
    metrics = evaluate_unified_predictions(
        stratified,
        minimum_group_samples=config.minimum_group_samples,
    )
    fold_comparisons = compare_folds(metrics, baseline="ridge_down")
    return (
        metrics,
        fold_comparisons,
        _promotion_gate(metrics, fold_comparisons),
    )


def render_markdown(artifacts):
    """Render an honest Chinese research report."""
    manifest = artifacts.manifest
    gate = manifest.get("promotion_gate") or {}
    reasons = gate.get("reasons") or []
    lines = [
        "# 统一向下风险走步基准 v2",
        "",
        f"- 数据区间：{manifest.get('start_date')} 至 {manifest.get('latest_date')}",
        f"- 股票数：{manifest.get('ticker_count', 0)}",
        f"- 完全相同的测试行：{manifest.get('matched_test_key_count', 0)}",
        "- 执行口径：观察日收盘生成信号，下一交易日开盘执行。",
        "- 权限：研究结果不具备线上否决权。",
        "",
        "## 晋级结论",
        "",
        (
            "- 冻结研究门槛通过，但仍只允许进入影子评估。"
            if gate.get("passed")
            else "- 冻结研究门槛未通过。"
        ),
        *(
            ["- 无失败原因。"]
            if not reasons
            else [f"- `{reason}`" for reason in reasons]
        ),
        "",
        "## 同池核心结果",
        "",
        _markdown_metrics(artifacts.metrics),
        "",
        "## 分层说明",
        "",
        "- `semiconductor`：半导体。",
        "- `software_cloud`：软件与云服务。",
        "- `unclassified`：观察日没有可用点时分类，不并入其他组。",
        "",
        "## 限制",
        "",
        "- 二元规则分数不是概率，未伪造 ROC/PR AUC。",
        "- 本报告不修改 Ridge、TOPRISK 或 forecast_decision_policy。",
        "",
    ]
    return "\n".join(lines)


def _sha256_json(value):
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _frame_content_fingerprint(frame):
    if frame is None:
        return None
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("fingerprinted inputs must be DataFrames")
    if not frame.columns.is_unique:
        raise ValueError("fingerprinted DataFrames require unique columns")
    metadata = {
        "columns": [
            {"name": str(column), "dtype": str(frame[column].dtype)}
            for column in frame.columns
        ],
        "index_class": type(frame.index).__name__,
        "index_names": [
            None if name is None else str(name)
            for name in frame.index.names
        ],
        "index_dtypes": (
            [str(level.dtype) for level in frame.index.levels]
            if isinstance(frame.index, pd.MultiIndex)
            else [str(frame.index.dtype)]
        ),
        "row_count": int(len(frame)),
    }
    digest = hashlib.sha256()
    digest.update(
        json.dumps(
            metadata,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    row_hashes = pd.util.hash_pandas_object(
        frame,
        index=True,
        categorize=False,
    ).to_numpy(dtype="<u8", copy=False)
    view = memoryview(row_hashes).cast("B")
    for offset in range(0, len(view), 8 * 65_536):
        digest.update(view[offset : offset + 8 * 65_536])
    return digest.hexdigest()


def _database_fingerprint(inputs, config=None):
    del config
    if not isinstance(inputs, BenchmarkInputs):
        raise TypeError("inputs must be BenchmarkInputs")
    histories = []
    for ticker, history in sorted((inputs.histories or {}).items()):
        histories.append(
            {
                "ticker": str(ticker),
                "content": _frame_content_fingerprint(history),
            }
        )
    return _sha256_json(
        {
            "prices": _frame_content_fingerprint(inputs.prices),
            "regimes": _frame_content_fingerprint(inputs.regimes),
            "histories": histories,
            "feature_frame": _frame_content_fingerprint(inputs.feature_frame),
            "analysis_tickers": list(inputs.analysis_tickers),
            "pandas_version": pd.__version__,
            "numpy_version": np.__version__,
        }
    )


def _assignment_fingerprint(inputs):
    if not isinstance(inputs, BenchmarkInputs):
        raise TypeError("inputs must be BenchmarkInputs")
    return _sha256_json(
        {
            "assignments": _frame_content_fingerprint(inputs.assignments),
            "analysis_tickers": list(inputs.analysis_tickers),
        }
    )


def _model_definition(stage):
    if stage == "rule_predictions":
        return {
            "specifications": sorted(_RULE_CACHE_SPECIFICATIONS),
            "versions": [
                "bearish_turn_immediate_v1",
                "bearish_turn_risk_rules_v2",
                "toprisk_v1",
                "forecast_decision_policy_toprisk_v1",
            ],
        }
    if stage != "statistical_predictions":
        raise ValueError("unsupported cache stage")
    from research.downside_specialist import PRESSURE_REGIMES
    from research.run_expanded_walkforward_study import expanded_feature_sets
    from research.run_pressure_downside_study import SPECIALIST_FEATURE_COLUMNS
    from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS

    expanded = expanded_feature_sets(RIDGE_V4_FEATURE_COLUMNS)
    return {
        "ridge_features": list(RIDGE_V4_FEATURE_COLUMNS),
        "general_logistic_features": list(expanded["ridge_decay_market"]),
        "specialist_features": list(SPECIALIST_FEATURE_COLUMNS),
        "pressure_regimes": list(PRESSURE_REGIMES),
        "versions": [
            "ridge_direction_v1",
            "general_logistic_v1",
            "pressure_downside_logistic_v1",
        ],
    }


def _config_fingerprint(config, inputs, stage):
    return _sha256_json(
        {
            "study_version": STUDY_VERSION,
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "stage": stage,
            "start_date": config.start_date,
            "analysis_tickers": list(inputs.analysis_tickers),
            "max_tickers": int(config.max_tickers),
            "folds": int(config.folds),
            "horizons": list(config.horizons),
            "minimum_training_samples": int(config.minimum_training_samples),
            "model_definition": _model_definition(stage),
        }
    )


def _code_fingerprint():
    repository = Path(__file__).resolve().parents[1]
    relevant_paths = [
        "research/run_unified_downside_benchmark.py",
        "research/unified_downside_benchmark.py",
        "research/benchmark_cache_codec.py",
        "research/unified_benchmark_cache.py",
        "research/market_direction_model.py",
        "research/downside_specialist.py",
        "research/run_pressure_downside_study.py",
        "research/evaluate_toprisk_comparison.py",
        "web/forecasts",
    ]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", *relevant_paths],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        if (
            len(commit) not in {40, 64}
            or any(character not in "0123456789abcdef" for character in commit)
        ):
            raise ValueError("git commit is not a hexadecimal object id")
        return _sha256_json(
            {
                "git_commit": commit,
                "study_version": STUDY_VERSION,
                "cache_schema_version": CACHE_SCHEMA_VERSION,
            }
        ), bool(status.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return _sha256_json({"git_state": "unavailable"}), True


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def default_dependencies():
    """Return production research adapters, imported lazily."""
    return BenchmarkDependencies(
        load_inputs=_load_real_inputs,
        build_predictions=_build_statistical_predictions,
        build_rule_predictions=_build_rule_predictions,
        database_fingerprint=_database_fingerprint,
        assignment_fingerprint=_assignment_fingerprint,
        code_fingerprint=_code_fingerprint,
    )


def _validate_config(config):
    if not isinstance(config, BenchmarkConfig):
        raise TypeError("config must be BenchmarkConfig")
    if config.folds < 2:
        raise ValueError("folds must be at least 2")
    if config.max_tickers <= 0:
        raise ValueError("max_tickers must be positive")
    if config.minimum_group_samples <= 0:
        raise ValueError("minimum_group_samples must be positive")
    if config.minimum_training_samples <= 0:
        raise ValueError("minimum_training_samples must be positive")
    if (
        not isinstance(config.cache_enabled, bool)
        or not isinstance(config.rebuild_cache, bool)
    ):
        raise TypeError("cache flags must be Boolean")
    if not config.cache_enabled and config.rebuild_cache:
        raise ValueError("--no-cache and --rebuild-cache are mutually exclusive")
    if not config.horizons or any(
        not isinstance(horizon, int)
        or isinstance(horizon, bool)
        or horizon <= 0
        for horizon in config.horizons
    ):
        raise ValueError("horizons must contain positive integers")
    if len(set(config.horizons)) != len(config.horizons):
        raise ValueError("horizons must not contain duplicates")
    return config


def _model_keys(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("model predictions must be a DataFrame")
    missing = sorted(set(MODEL_KEY_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError(f"ridge_down is missing keys: {missing}")
    keys = frame.loc[:, MODEL_KEY_COLUMNS].copy()
    keys["ticker"] = keys["ticker"].astype(str).str.strip().str.upper()
    keys["observation_date"] = pd.to_datetime(
        keys["observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    if keys.duplicated(list(MODEL_KEY_COLUMNS)).any():
        raise ValueError("ridge_down contains duplicate test keys")
    return keys


def _manifest(
    config,
    inputs,
    labels,
    aligned,
    promotion_gate,
    *,
    cache_manifest=None,
):
    mature = labels.loc[labels["mature"]]
    latest = (
        None
        if mature.empty
        else mature["observation_date"].max().date().isoformat()
    )
    return {
        "study_version": STUDY_VERSION,
        "online_authority": "none",
        "database": config.database.name,
        "start_date": config.start_date,
        "latest_date": latest,
        "ticker_count": int(labels["ticker"].nunique()),
        "matched_test_key_count": int(len(labels)),
        "aligned_prediction_row_count": int(len(aligned)),
        "horizons": list(config.horizons),
        "folds": int(config.folds),
        "minimum_group_samples": int(config.minimum_group_samples),
        "immature_row_count": int((~labels["mature"]).sum()),
        "group_assignment_row_count": int(len(inputs.assignments)),
        "promotion_gate": promotion_gate,
        "cache": cache_manifest or {"mode": "unavailable"},
    }


def _promotion_gate(metrics, comparisons):
    required_scopes = {"semiconductor", "software_cloud"}
    observed_scopes = set(
        metrics.loc[
            (metrics["status"] == "ok")
            & (metrics["sample_mode"] == "non_overlapping"),
            "scope",
        ]
    )
    reasons = []
    if not required_scopes.issubset(observed_scopes):
        reasons.append("required_group_evidence_missing")
    if comparisons.empty:
        reasons.append("paired_fold_evidence_missing")
    else:
        stable = comparisons.loc[
            (comparisons["sample_mode"] == "non_overlapping")
            & (comparisons["specification"] != "ridge_down")
            & (comparisons["comparable_fold_count"] >= 3)
            & (comparisons["fold_win_rate"] > 0.5)
        ]
        if stable.empty:
            reasons.append("fold_majority_not_won")
    return {"passed": not reasons, "reasons": reasons}


def _output_paths(directory):
    root = Path(directory)
    return (
        root / "unified-downside-benchmark-v2.json",
        root / "unified-downside-benchmark-v2.csv",
        root / "unified-downside-benchmark-v2.md",
    )


def _publish_atomic(artifacts, finalize_manifest=None):
    json_path, csv_path, markdown_path = artifacts.output_paths
    for path in artifacts.output_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tuple(
        path.with_name(f".{path.name}.tmp")
        for path in artifacts.output_paths
    )
    try:
        artifacts.metrics.to_csv(temporary[1], index=False)
        temporary[2].write_text(
            render_markdown(artifacts),
            encoding="utf-8",
        )
        if finalize_manifest is not None:
            finalize_manifest()
        payload = {
            **artifacts.manifest,
            "metrics": _records(artifacts.metrics),
            "fold_comparisons": _records(artifacts.fold_comparisons),
            "ablations": _records(artifacts.ablations),
            "overlaps": _records(artifacts.overlaps),
        }
        temporary[0].write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        for source, target in zip(temporary, artifacts.output_paths):
            source.replace(target)
    finally:
        for path in temporary:
            if path.exists():
                path.unlink()


def _records(frame):
    if frame.empty:
        return []
    checked = frame.astype(object).where(pd.notna(frame), None)
    return checked.to_dict(orient="records")


def _markdown_metrics(metrics):
    columns = (
        "scope",
        "regime_scope",
        "horizon",
        "sample_mode",
        "fold",
        "specification",
        "status",
        "sample_count",
        "precision",
        "recall",
        "specificity",
        "balanced_accuracy",
    )
    selected = metrics.loc[
        (metrics["sample_mode"] == "non_overlapping")
        & (metrics["fold"].astype(str) == "all")
        & (metrics["regime_scope"] == "all"),
        [column for column in columns if column in metrics],
    ].copy()
    if selected.empty:
        return "_无足够成熟样本。_"
    for column in selected.select_dtypes(include="number"):
        selected[column] = selected[column].map(
            lambda value: "" if pd.isna(value) else f"{value:.4f}"
        )
    header = list(selected.columns)
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for values in selected.astype(str).itertuples(index=False, name=None):
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _prediction_frame_from_direction(frame, *, model_version):
    """Convert existing direction output into the benchmark event contract."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("direction predictions must be a DataFrame")
    required = {*MODEL_KEY_COLUMNS, "predicted_direction"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"direction predictions are missing columns: {missing}")
    result = frame.loc[:, MODEL_KEY_COLUMNS].copy()
    direction = frame["predicted_direction"].astype(str).str.lower()
    result["predicted_event"] = direction.eq("down").astype("boolean")
    predicted_return = pd.to_numeric(
        frame.get("predicted_return"),
        errors="coerce",
    )
    result["predicted_score"] = -predicted_return
    result["model_version"] = str(model_version)
    return result


def _rule_prediction_frames(risk_context, anchor):
    """Align existing causal risk signals to the Ridge test keys."""
    if not isinstance(risk_context, pd.DataFrame):
        raise TypeError("risk_context must be a DataFrame")
    if not isinstance(risk_context.index, pd.MultiIndex):
        raise ValueError("risk_context requires ticker/date index")
    keys = _model_keys(anchor)
    context = risk_context.copy(deep=True)
    context.index = context.index.set_names(["ticker", "observation_date"])
    if context.index.has_duplicates:
        raise ValueError("risk_context contains duplicate point-in-time keys")
    merged = keys.merge(
        context.reset_index(),
        on=["ticker", "observation_date"],
        how="left",
        validate="many_to_one",
    )
    definitions = {
        "immediate_8": (
            "signal_immediate_8",
            "bearish_turn_score",
            "bearish_turn_immediate_v1",
        ),
        "memory_12": (
            "signal_memory_12",
            "individual_risk_score",
            "bearish_turn_risk_rules_v2",
        ),
        "toprisk_confirmed": (
            "signal_toprisk_confirmed",
            "high_level_distribution_score",
            "toprisk_v1",
        ),
        "toprisk_stateful": (
            "signal_toprisk_stateful",
            "high_level_distribution_score",
            "toprisk_v1",
        ),
    }
    output = {}
    for specification, (signal_column, score_column, version) in definitions.items():
        result = keys.copy()
        signal = (
            merged[signal_column]
            if signal_column in merged
            else pd.Series(pd.NA, index=merged.index, dtype="boolean")
        )
        result["predicted_event"] = signal.astype("boolean")
        result["predicted_score"] = pd.to_numeric(
            merged.get(score_column),
            errors="coerce",
        )
        result["model_version"] = version
        output[specification] = result
    if "predicted_direction" in anchor:
        ridge = (
            anchor["predicted_direction"]
            .astype(str)
            .str.lower()
            .eq("down")
        )
    elif "predicted_event" in anchor:
        ridge = anchor["predicted_event"].astype("boolean")
    else:
        raise ValueError(
            "rule anchor requires predicted_direction or predicted_event"
        )
    top = output["toprisk_stateful"]["predicted_event"]
    combined = pd.Series(pd.NA, index=keys.index, dtype="boolean")
    available = top.notna()
    combined.loc[available] = ridge.loc[available] | top.loc[available].astype(
        bool
    )
    combination = keys.copy()
    combination["predicted_event"] = combined
    combination["predicted_score"] = pd.NA
    combination["model_version"] = "forecast_decision_policy_toprisk_v1"
    output["ridge_plus_toprisk"] = combination
    return output


def _load_assignments(database):
    """Read and decode persisted effective-dated assignment rows."""
    path = Path(database)
    if not path.exists():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        available = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(group_assignments)"
            ).fetchall()
        }
        preferred = (
            "ticker",
            "effective_from",
            "effective_to",
            "sector_key",
            "sector_benchmark",
            "theme_keys_json",
            "theme_benchmarks_json",
            "primary_model_group",
            "classification_state",
            "source",
            "confidence",
        )
        selected = [column for column in preferred if column in available]
        required = {
            "ticker",
            "effective_from",
            "effective_to",
            "theme_keys_json",
            "primary_model_group",
            "classification_state",
            "source",
        }
        missing = sorted(required.difference(selected))
        if missing:
            raise ValueError(
                f"group_assignments is missing columns: {missing}"
            )
        query = (
            f"SELECT {', '.join(selected)} FROM group_assignments "
            "ORDER BY ticker, effective_from"
        )
        rows = pd.read_sql_query(query, connection)
    rows["effective_from"] = pd.to_datetime(
        rows["effective_from"],
        errors="raise",
    )
    rows["effective_to"] = pd.to_datetime(
        rows["effective_to"],
        errors="coerce",
    )
    rows["theme_keys"] = rows["theme_keys_json"].map(
        lambda value: tuple(json.loads(value))
    )
    if "theme_benchmarks_json" in rows:
        rows["theme_benchmarks"] = rows["theme_benchmarks_json"].map(
            lambda value: tuple(json.loads(value))
        )
    else:
        rows["theme_benchmarks"] = [tuple()] * len(rows)
    rows["state"] = rows["classification_state"].map(
        lambda value: (
            "assigned"
            if str(value).strip().lower() == "classified"
            else str(value).strip().lower()
        )
    )
    return rows.drop(
        columns=[
            column
            for column in ("theme_keys_json", "theme_benchmarks_json")
            if column in rows
        ]
    )


def _load_real_inputs(config):  # pragma: no cover - integration exercised later.
    """Load one deterministic cohort and every causal reference history."""
    from research.expanded_market_data import ExpandedMarketDataRepository
    from research.market_regime import build_market_regime_frame
    from research.run_expanded_walkforward_study import (
        classify_study_groups,
        prepare_expanded_frame,
        select_analysis_tickers,
    )
    from web.market_groups import REFERENCE_TICKERS

    repository = ExpandedMarketDataRepository(config.database)
    classifications = repository.load_classifications()
    groups = classify_study_groups(classifications)
    analysis_tickers = select_analysis_tickers(
        groups,
        max_tickers=config.max_tickers,
    )
    requested = tuple(
        sorted(set(analysis_tickers).union(REFERENCE_TICKERS))
    )
    histories = repository.load_universe_histories(tickers=requested)
    feature_frame = prepare_expanded_frame(
        histories,
        analysis_tickers=analysis_tickers,
        classifications=classifications,
        start_date=config.start_date,
        sector_mode="none",
    )
    price_frames = []
    for ticker in analysis_tickers:
        history = histories.get(ticker)
        if history is None or history.empty:
            continue
        selected = history.loc[:, ["Open", "High", "Low", "Close"]].copy()
        selected.insert(0, "observation_date", selected.index)
        selected.insert(0, "ticker", ticker)
        price_frames.append(selected.reset_index(drop=True))
    if not price_frames:
        raise ValueError("selected cohort has no price histories")
    regime_frame = build_market_regime_frame(histories)
    regimes = (
        regime_frame.loc[:, ["regime"]]
        .rename_axis("observation_date")
        .reset_index()
    )
    return BenchmarkInputs(
        prices=pd.concat(price_frames, ignore_index=True),
        assignments=_load_assignments(config.database),
        regimes=regimes,
        histories=histories,
        feature_frame=feature_frame,
        analysis_tickers=analysis_tickers,
    )


def _attach_direction_targets(inputs, config):
    from research.downside_specialist import attach_next_open_mae_targets
    from research.market_direction_model import attach_next_open_targets

    if inputs.feature_frame is None or inputs.histories is None:
        raise ValueError("real predictions require features and histories")
    frame = attach_next_open_targets(
        inputs.feature_frame,
        inputs.histories,
        horizons=config.horizons,
    )
    regime = inputs.regimes.copy(deep=True)
    regime["observation_date"] = pd.to_datetime(
        regime["observation_date"]
    ).dt.tz_localize(None)
    regime_by_date = regime.set_index("observation_date")["regime"]
    frame["regime"] = frame.index.get_level_values(
        "observation_date"
    ).map(regime_by_date)
    frame["regime_is_correction"] = (
        frame["regime"].astype(str).eq("correction").astype(float)
    )
    frame["regime_is_acute_selloff"] = (
        frame["regime"].astype(str).eq("acute_selloff").astype(float)
    )
    specialist_horizons = tuple(
        horizon for horizon in config.horizons if horizon in (5, 20)
    )
    if specialist_horizons:
        frame = attach_next_open_mae_targets(
            frame,
            inputs.histories,
            horizons=specialist_horizons,
        )
    return frame


def _ridge_predictions(frame, horizon, config):
    from research.market_direction_model import (
        walk_forward_ridge_predictions,
    )
    from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS

    return walk_forward_ridge_predictions(
        frame,
        horizon=horizon,
        feature_columns=RIDGE_V4_FEATURE_COLUMNS,
        n_folds=config.folds + 1,
        minimum_samples=config.minimum_training_samples,
        specification="ridge_current",
    )


def _general_logistic_predictions(frame, horizon, config):
    from research.market_direction_model import (
        walk_forward_direction_predictions,
    )
    from research.run_expanded_walkforward_study import expanded_feature_sets
    from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS

    return walk_forward_direction_predictions(
        frame,
        horizon=horizon,
        feature_sets={
            "general_logistic": expanded_feature_sets(
                RIDGE_V4_FEATURE_COLUMNS
            )["ridge_decay_market"]
        },
        n_folds=config.folds + 1,
        minimum_samples=config.minimum_training_samples,
    )


def _specialist_predictions(frame, horizon, config):
    if horizon not in (5, 20):
        return pd.DataFrame()
    from research.downside_specialist import (
        walk_forward_downside_predictions,
    )
    from research.run_pressure_downside_study import (
        SPECIALIST_FEATURE_COLUMNS,
    )

    return walk_forward_downside_predictions(
        frame,
        horizon=horizon,
        feature_columns=SPECIALIST_FEATURE_COLUMNS,
        n_folds=config.folds + 1,
        minimum_samples=config.minimum_training_samples,
    )


def _build_rule_context(inputs):
    from research.evaluate_toprisk_comparison import build_comparison_frame
    from web.forecasts.decision import build_forecast_risk_context

    if inputs.histories is None:
        raise ValueError("rule context requires histories")
    assignment_histories = {}
    for row in inputs.assignments.to_dict(orient="records"):
        if pd.isna(row.get("effective_to")):
            row["effective_to"] = None
        assignment_histories.setdefault(str(row["ticker"]), []).append(row)
    context = build_forecast_risk_context(
        inputs.histories,
        assignments=assignment_histories,
    )
    return build_comparison_frame(
        inputs.histories,
        context=context,
        feature_frame=inputs.feature_frame,
    )


def _build_statistical_predictions(inputs, config):  # pragma: no cover
    """Run the statistical models without constructing rule context."""
    frame = _attach_direction_targets(inputs, config)
    ridge_rows = []
    logistic_rows = []
    specialist_rows = []
    for horizon in config.horizons:
        ridge = _ridge_predictions(frame, horizon, config)
        if not ridge.empty:
            ridge_rows.append(ridge)
        logistic = _general_logistic_predictions(frame, horizon, config)
        if not logistic.empty:
            selected = (
                logistic.loc[
                    logistic["specification"] == "general_logistic"
                ]
                if "specification" in logistic
                else logistic
            )
            if not selected.empty:
                logistic_rows.append(selected)
        specialist = _specialist_predictions(frame, horizon, config)
        if not specialist.empty:
            specialist_rows.append(specialist)
    if not ridge_rows:
        raise ValueError("Ridge produced no walk-forward prediction rows")
    ridge_direction = pd.concat(ridge_rows, ignore_index=True)
    outputs = {
        "ridge_down": _prediction_frame_from_direction(
            ridge_direction,
            model_version="ridge_direction_v1",
        )
    }
    if logistic_rows:
        outputs["general_logistic_down"] = _prediction_frame_from_direction(
            pd.concat(logistic_rows, ignore_index=True),
            model_version="general_logistic_v1",
        )
    if specialist_rows:
        specialist = pd.concat(specialist_rows, ignore_index=True)
        if "predicted_event" not in specialist:
            adapted = _prediction_frame_from_direction(
                specialist,
                model_version="pressure_downside_logistic_v1",
            )
        else:
            adapted = specialist.loc[
                :,
                [
                    *MODEL_KEY_COLUMNS,
                    "predicted_event",
                    "predicted_score",
                ],
            ].assign(model_version="pressure_downside_logistic_v1")
        outputs["pressure_downside_logistic_v1"] = adapted
    return outputs


def _build_rule_predictions(inputs, config, predictions):  # pragma: no cover
    """Build the causal rules separately for observable stage timing."""
    del config
    return _rule_prediction_frames(
        _build_rule_context(inputs),
        predictions["ridge_down"],
    )


def _build_real_predictions(inputs, config):  # pragma: no cover
    """Compatibility adapter returning statistical and rule predictions."""
    statistical = _build_statistical_predictions(inputs, config)
    return {
        **statistical,
        **_build_rule_predictions(inputs, config, statistical),
    }


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--max-tickers", type=int, default=240)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--horizons", nargs="+", type=int, default=(5, 10, 20))
    parser.add_argument("--minimum-training-samples", type=int, default=1_000)
    parser.add_argument("--minimum-group-samples", type=int, default=50)
    parser.add_argument("--output-directory", default="reports")
    parser.add_argument(
        "--cache-database",
        default="data/unified_benchmark_cache.db",
    )
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--rebuild-cache", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    config = BenchmarkConfig(
        database=Path(args.database),
        start_date=args.start,
        max_tickers=args.max_tickers,
        folds=args.folds,
        horizons=tuple(args.horizons),
        minimum_training_samples=args.minimum_training_samples,
        minimum_group_samples=args.minimum_group_samples,
        output_directory=Path(args.output_directory),
        cache_database=Path(args.cache_database),
        cache_enabled=not args.no_cache,
        rebuild_cache=args.rebuild_cache,
    )
    run_benchmark(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
