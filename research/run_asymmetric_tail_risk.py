"""Run the causal asymmetric five-session tail-risk research study."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.asymmetric_tail_risk import (
    attach_asymmetric_tail_targets,
    audit_extreme_counterexamples,
    evaluate_tail_predictions,
    tail_promotion_decision,
    walk_forward_asymmetric_tail_predictions,
)
from research.expanded_market_data import ExpandedMarketDataRepository
from research.market_direction_model import (
    attach_next_open_targets,
    walk_forward_direction_predictions,
)
from research.market_regime import REGIME_VERSION, build_market_regime_frame
from research.run_expanded_walkforward_study import (
    classify_study_groups,
    prepare_expanded_frame,
    select_analysis_tickers,
)
from research.run_hierarchical_recency_direction import (
    input_content_fingerprint,
)
from research.run_regime_threshold_direction import (
    _git_state,
    _manifest_value,
    _markdown_table,
    validate_report_payload,
)
from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS
from web.market_groups import REFERENCE_TICKERS


STUDY_VERSION = "asymmetric_tail_risk_v1"
BASELINE_SPECIFICATION = "logistic_reference"


def validate_tail_report_payload(payload):
    """Validate strict, secret-free report content."""
    return validate_report_payload(payload)


def render_tail_report(metrics, manifest):
    """Render the research decision and raw economic evidence in Chinese."""
    decision = manifest.get("decision", {})
    selected = metrics.loc[
        (metrics.get("sample_mode") == "non_overlapping")
        & (metrics.get("scope_type") == "overall")
    ] if not metrics.empty else metrics
    summary_columns = [
        column
        for column in (
            "row_count",
            "risk_count",
            "coverage",
            "down_precision",
            "baseline_down_precision",
            "down_precision_gain",
            "mean_terminal_return",
            "risk_rebound_rate",
            "all_rebound_rate",
        )
        if column in selected
    ]
    reasons = decision.get("reasons") or ()
    reason_lines = (
        "\n".join(f"- `{reason}`" for reason in reasons)
        if reasons
        else "- 无"
    )
    candidate_evidence = _candidate_evidence_frame(manifest)
    return "\n".join(
        (
            "# 不对称五日尾部风险研究",
            "",
            "> 离线研究模型；`online_authority=none`。不修改 Ridge、最终方向、"
            "否决策略或 UI。",
            "",
            "## 结论",
            "",
            f"- 研究门槛：`{decision.get('status', 'unavailable')}`",
            f"- 是否通过：`{bool(decision.get('promoted', False))}`",
            "- 所有经济收益均使用未截尾、未 Winsorize、未删除暴涨样本的"
            "真实五日终点收益。",
            "",
            "## 非重叠总体结果",
            "",
            _markdown_table(selected.loc[:, summary_columns]),
            "",
            "## 训练内候选边界",
            "",
            _markdown_table(candidate_evidence),
            "",
            "## 未通过原因",
            "",
            reason_lines,
            "",
            "## 方法边界",
            "",
            "- 入场为观察日后下一交易日开盘，退出为第五个未来交易日收盘。",
            "- 下跌事件、收益中位数、20% 下分位数和极端反弹概率由四个"
            "独立模型头估计。",
            "- 概率校准和组合边界只使用外层训练集内部净化 OOF 结果。",
            "- 极端反弹作为反证保留，不得为了改善均值而删除。",
            "",
        )
    )


def publish_tail_reports(
    prefix,
    metrics,
    counterexamples,
    manifest,
    report,
):
    """Atomically publish strict JSON, metrics, counterexamples and Markdown."""
    validate_tail_report_payload(manifest)
    checked_prefix = Path(prefix)
    checked_prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": checked_prefix.with_suffix(".json"),
        "csv": checked_prefix.with_suffix(".csv"),
        "counterexamples_csv": checked_prefix.with_name(
            checked_prefix.name + "-counterexamples"
        ).with_suffix(".csv"),
        "md": checked_prefix.with_suffix(".md"),
    }
    payloads = {
        "json": json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        "csv": metrics.to_csv(index=False),
        "counterexamples_csv": counterexamples.to_csv(index=False),
        "md": str(report),
    }
    temporary = {}
    try:
        for name, destination in paths.items():
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{destination.name}.",
                suffix=".tmp",
                dir=str(destination.parent),
            )
            temporary[name] = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payloads[name])
                handle.flush()
                os.fsync(handle.fileno())
        for name, destination in paths.items():
            temporary[name].replace(destination)
    finally:
        for temporary_path in temporary.values():
            temporary_path.unlink(missing_ok=True)
    return paths


def run_study(
    *,
    database="data/research_prices.db",
    start_date="2018-01-01",
    max_tickers=240,
    seed=20260726,
    minimum_samples=1_000,
):
    """Build predictions, audits and a strict manifest from frozen inputs."""
    repository = ExpandedMarketDataRepository(database)
    classifications = repository.load_classifications()
    groups = classify_study_groups(classifications)
    analysis_tickers = select_analysis_tickers(
        groups,
        max_tickers=max_tickers,
        seed=seed,
    )
    requested = tuple(sorted(set(analysis_tickers) | set(REFERENCE_TICKERS)))
    histories = repository.load_universe_histories(tickers=requested)
    feature_frame = prepare_expanded_frame(
        histories,
        analysis_tickers=analysis_tickers,
        classifications=classifications,
        start_date=start_date,
        sector_mode="none",
    )
    frame = attach_asymmetric_tail_targets(feature_frame, histories)
    predictions = walk_forward_asymmetric_tail_predictions(
        frame,
        feature_columns=RIDGE_V4_FEATURE_COLUMNS,
        n_test_folds=6,
        minimum_samples=minimum_samples,
    )
    if predictions.empty:
        raise RuntimeError(
            "no asymmetric tail predictions were produced: "
            + predictions.attrs.get("reason", "unavailable")
        )
    nested_fold_evidence = predictions.attrs.get("fold_evidence", ())

    baseline_frame = attach_next_open_targets(
        feature_frame,
        histories,
        horizons=(5,),
    )
    baseline = walk_forward_direction_predictions(
        baseline_frame,
        horizon=5,
        feature_sets={
            BASELINE_SPECIFICATION: RIDGE_V4_FEATURE_COLUMNS,
        },
        n_folds=6,
        minimum_samples=minimum_samples,
    )
    baseline = baseline.loc[
        baseline["specification"] == BASELINE_SPECIFICATION,
        ["ticker", "observation_date", "fold", "predicted_direction"],
    ].rename(
        columns={
            "predicted_direction": "_baseline_direction",
        }
    )
    predictions = predictions.merge(
        baseline,
        on=["ticker", "observation_date", "fold"],
        how="left",
        validate="one_to_one",
    )
    if predictions["_baseline_direction"].isna().any():
        raise RuntimeError("baseline and challenger outer keys are not aligned")
    predictions["baseline_predicted_down"] = (
        predictions.pop("_baseline_direction") == "down"
    )

    regimes = build_market_regime_frame(histories)
    observation_dates = pd.DatetimeIndex(
        pd.to_datetime(predictions["observation_date"])
    ).tz_localize(None)
    predictions["regime"] = regimes["regime"].reindex(
        observation_dates
    ).fillna("unavailable").to_numpy()
    normalized_groups = {
        str(ticker).strip().upper(): str(group)
        for ticker, group in groups.items()
    }
    predictions["group"] = predictions["ticker"].map(
        lambda ticker: normalized_groups.get(
            str(ticker).strip().upper(),
            "other",
        )
    )
    predictions = _attach_counterexample_context(
        predictions,
        frame,
        histories,
    )
    metrics = evaluate_tail_predictions(
        predictions,
        group_map=normalized_groups,
    )
    causal_audit = {
        "outer_training_labels_end_before_test_start": bool(
            (
                pd.to_datetime(predictions["training_label_end_max"])
                < pd.to_datetime(predictions["test_start"])
            ).all()
        ),
        "five_outer_test_folds": predictions["fold"].nunique() == 5,
        "unique_outer_test_keys": not predictions.duplicated(
            ["ticker", "observation_date"]
        ).any(),
        "calibration_source": "outer_training_inner_purged_oof_only",
        "boundary_source": "calibrated_inner_oof_only",
    }
    causal_audit["passed"] = all(
        value is True
        for key, value in causal_audit.items()
        if key not in ("calibration_source", "boundary_source")
    )
    decision = tail_promotion_decision(metrics, causal_audit)
    if not (predictions["boundary_status"] == "available").any():
        decision = dict(decision)
        decision["reasons"] = (
            "all_outer_boundaries_unavailable",
            *tuple(decision["reasons"]),
        )
        decision["status"] = "rejected"
        decision["promoted"] = False
        conditions = dict(decision["conditions"])
        conditions["outer_boundary_available"] = False
        decision["conditions"] = conditions
    counterexamples = audit_extreme_counterexamples(predictions)
    fold_diagnostics = (
        predictions.loc[
            :,
            [
                "fold",
                "test_start",
                "training_samples",
                "training_label_end_max",
                "down_calibration_samples",
                "down_calibration_positive_count",
                "rebound_calibration_samples",
                "rebound_calibration_positive_count",
                "boundary_status",
                "boundary_reason",
                "down_threshold",
                "rebound_cap",
                "boundary_inner_down_precision",
                "boundary_inner_coverage",
                "boundary_inner_mean_terminal_return",
            ],
        ]
        .drop_duplicates()
        .sort_values("fold")
        .reset_index(drop=True)
    )
    source_commit, dirty_worktree = _git_state()
    fingerprint = input_content_fingerprint(
        frame,
        histories,
        analysis_tickers,
    )
    manifest = {
        "study_version": STUDY_VERSION,
        "latest_date": pd.Timestamp(
            frame.index.get_level_values("observation_date").max()
        ).date().isoformat(),
        "start_date": str(start_date),
        "ticker_count": len(analysis_tickers),
        "row_count": len(frame),
        "prediction_count": len(predictions),
        "database": Path(database).name,
        "database_content_fingerprint": fingerprint,
        "source_commit": source_commit,
        "dirty_worktree": dirty_worktree,
        "configuration": {
            "cohort_seed": int(seed),
            "maximum_tickers": int(max_tickers),
            "minimum_samples": int(minimum_samples),
            "outer_test_folds": 5,
            "inner_oof_test_folds": 3,
            "horizon": 5,
            "entry": "next_session_open",
            "exit": "fifth_future_session_close",
            "down_terminal_threshold": -0.05,
            "down_path_threshold": -0.07,
            "extreme_rebound_threshold": 0.10,
            "conditional_quantiles": [0.20, 0.50],
            "market_regime_version": REGIME_VERSION,
        },
        "model": {
            "name": STUDY_VERSION,
            "heads": [
                "down_event_logistic",
                "terminal_return_quantile_0.50",
                "terminal_return_quantile_0.20",
                "extreme_rebound_logistic",
            ],
            "lifecycle": "research",
            "online_authority": "none",
        },
        "decision": _json_safe(decision),
        "causal_audit": _json_safe(causal_audit),
        "fold_diagnostics": _records(fold_diagnostics),
        "nested_fold_evidence": _json_safe(nested_fold_evidence),
        "metrics": _records(metrics),
        "counterexamples": _records(counterexamples),
    }
    validate_tail_report_payload(manifest)
    return {
        "manifest": manifest,
        "metrics": metrics,
        "counterexamples": counterexamples,
        "predictions": predictions,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument("--start-date", default="2018-01-01")
    parser.add_argument("--max-tickers", type=int, default=240)
    parser.add_argument("--seed", type=int, default=20260726)
    parser.add_argument("--minimum-samples", type=int, default=1_000)
    parser.add_argument(
        "--output-prefix",
        default="reports/asymmetric-tail-risk",
    )
    args = parser.parse_args(argv)
    result = run_study(
        database=args.database,
        start_date=args.start_date,
        max_tickers=args.max_tickers,
        seed=args.seed,
        minimum_samples=args.minimum_samples,
    )
    report = render_tail_report(
        result["metrics"],
        result["manifest"],
    )
    paths = publish_tail_reports(
        args.output_prefix,
        result["metrics"],
        result["counterexamples"],
        result["manifest"],
        report,
    )
    print(
        json.dumps(
            {key: str(value) for key, value in paths.items()},
            sort_keys=True,
        )
    )
    return 0


def _attach_counterexample_context(predictions, frame, histories):
    result = predictions.copy(deep=True)
    keys = pd.MultiIndex.from_arrays(
        (
            result["ticker"].astype(str),
            pd.to_datetime(result["observation_date"]),
        ),
        names=("ticker", "observation_date"),
    )
    result["realized_volatility"] = frame["realized_vol_63"].reindex(
        keys
    ).to_numpy(dtype=float)
    result["opening_gap"] = np.nan
    result["dollar_volume"] = np.nan
    for ticker, positions in result.groupby("ticker", sort=False).groups.items():
        history = histories.get(str(ticker))
        if history is None or history.empty:
            continue
        ordered = history.sort_index().copy()
        ordered.index = pd.DatetimeIndex(ordered.index).tz_localize(None)
        previous_close = pd.to_numeric(
            ordered["Close"],
            errors="coerce",
        ).shift(1)
        opening_gap = (
            pd.to_numeric(ordered["Open"], errors="coerce")
            / previous_close.replace(0.0, np.nan)
            - 1.0
        )
        dollar_volume = (
            pd.to_numeric(ordered["Close"], errors="coerce")
            * pd.to_numeric(ordered["Volume"], errors="coerce")
        ).rolling(20, min_periods=20).mean()
        dates = pd.DatetimeIndex(
            pd.to_datetime(result.loc[positions, "observation_date"])
        )
        result.loc[positions, "opening_gap"] = opening_gap.reindex(
            dates
        ).to_numpy()
        result.loc[positions, "dollar_volume"] = dollar_volume.reindex(
            dates
        ).to_numpy()
    result["earnings_proximity"] = None
    return result


def _records(frame):
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("report records must come from a DataFrame")
    return [
        {
            str(key): _manifest_value(value)
            for key, value in row.items()
        }
        for row in frame.to_dict(orient="records")
    ]


def _candidate_evidence_frame(manifest):
    rows = []
    for fold_evidence in manifest.get("nested_fold_evidence", ()):
        if not isinstance(fold_evidence, Mapping):
            continue
        for candidate in fold_evidence.get("boundary_candidates", ()):
            if not isinstance(candidate, Mapping):
                continue
            rows.append(
                {
                    "fold": fold_evidence.get("fold"),
                    "down_threshold": candidate.get("down_threshold"),
                    "rebound_cap": candidate.get("rebound_cap"),
                    "risk_count": candidate.get("risk_count"),
                    "coverage": candidate.get("coverage"),
                    "down_precision": candidate.get("down_precision"),
                    "mean_terminal_return": candidate.get(
                        "mean_terminal_return"
                    ),
                    "reasons": ", ".join(candidate.get("reasons", ())),
                }
            )
    return pd.DataFrame(
        rows,
        columns=(
            "fold",
            "down_threshold",
            "rebound_cap",
            "risk_count",
            "coverage",
            "down_precision",
            "mean_terminal_return",
            "reasons",
        ),
    )


def _json_safe(value):
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return _manifest_value(value)


if __name__ == "__main__":
    raise SystemExit(main())
