"""Run the unified point-in-time downside walk-forward benchmark."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from research.unified_downside_benchmark import (
    MODEL_KEY_COLUMNS,
    align_model_predictions,
    attach_next_open_path_targets,
    attach_point_in_time_strata,
    compare_folds,
    evaluate_unified_predictions,
)


STUDY_VERSION = "unified-downside-walkforward-v2"


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
    inputs = runtime.load_inputs(checked)
    if not isinstance(inputs, BenchmarkInputs):
        raise TypeError("load_inputs must return BenchmarkInputs")
    predictions = runtime.build_predictions(inputs, checked)
    if not isinstance(predictions, Mapping) or "ridge_down" not in predictions:
        raise ValueError("predictions require ridge_down anchor rows")
    anchor = _model_keys(predictions["ridge_down"])
    if anchor.empty:
        raise ValueError("ridge_down anchor rows must not be empty")
    targets = attach_next_open_path_targets(
        inputs.prices,
        horizons=checked.horizons,
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
    metrics = evaluate_unified_predictions(
        stratified,
        minimum_group_samples=checked.minimum_group_samples,
    )
    fold_comparisons = compare_folds(metrics, baseline="ridge_down")
    promotion_gate = _promotion_gate(metrics, fold_comparisons)
    manifest = _manifest(
        checked,
        inputs,
        labels,
        aligned,
        promotion_gate,
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
    _publish_atomic(artifacts)
    return artifacts


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


def default_dependencies():
    """Return production research adapters, imported lazily."""
    return BenchmarkDependencies(
        load_inputs=_load_real_inputs,
        build_predictions=_build_real_predictions,
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


def _manifest(config, inputs, labels, aligned, promotion_gate):
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


def _publish_atomic(artifacts):
    json_path, csv_path, markdown_path = artifacts.output_paths
    for path in artifacts.output_paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **artifacts.manifest,
        "metrics": _records(artifacts.metrics),
        "fold_comparisons": _records(artifacts.fold_comparisons),
        "ablations": _records(artifacts.ablations),
        "overlaps": _records(artifacts.overlaps),
    }
    temporary = tuple(
        path.with_name(f".{path.name}.tmp")
        for path in artifacts.output_paths
    )
    try:
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
        artifacts.metrics.to_csv(temporary[1], index=False)
        temporary[2].write_text(
            render_markdown(artifacts),
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
    ridge = anchor["predicted_direction"].astype(str).str.lower().eq("down")
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


def _build_real_predictions(inputs, config):  # pragma: no cover
    """Run every frozen model and adapt it to one benchmark contract."""
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
    outputs.update(
        _rule_prediction_frames(
            _build_rule_context(inputs),
            ridge_direction,
        )
    )
    return outputs


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
    )
    run_benchmark(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
