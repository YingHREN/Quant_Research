"""Run the matched point-in-time audit of asymmetric tail-direction errors."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping
import json
import os
from pathlib import Path
import sys
import tempfile

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.run_asymmetric_tail_risk import build_tail_study_dataset
from research.run_hierarchical_recency_direction import (
    input_content_fingerprint,
)
from research.run_regime_threshold_direction import (
    _git_state,
    _manifest_value,
    _markdown_table,
    validate_report_payload,
)
from research.tail_direction_counterexample_audit import (
    AUDIT_FEATURE_TYPES,
    HIGH_DOWN_SCORE,
    admitted_feature_hypotheses,
    build_audit_population,
    match_extreme_up_to_terminal_down,
    paired_feature_evidence,
)


STUDY_VERSION = "tail_direction_counterexample_audit_v1"


def validate_audit_report_payload(payload):
    """Validate strict, finite, secret-free audit report content."""
    return validate_report_payload(payload)


def run_audit_from_dataset(
    dataset: Mapping,
    *,
    maximum_calendar_days=63,
    bootstrap_samples=2_000,
    bootstrap_block_days=20,
    seed=20260730,
):
    """Run the pure audit over an already-built point-in-time dataset."""
    if not isinstance(dataset, Mapping):
        raise TypeError("dataset must be a mapping")
    required = ("predictions", "feature_frame", "histories")
    missing = [key for key in required if key not in dataset]
    if missing:
        raise ValueError(f"dataset is missing keys: {missing}")
    population = build_audit_population(
        dataset["predictions"],
        dataset["feature_frame"],
        dataset["histories"],
    )
    pairs, coverage = match_extreme_up_to_terminal_down(
        population,
        maximum_calendar_days=maximum_calendar_days,
    )
    evidence = paired_feature_evidence(
        pairs,
        feature_types=AUDIT_FEATURE_TYPES,
        bootstrap_samples=bootstrap_samples,
        bootstrap_block_days=bootstrap_block_days,
        seed=seed,
    )
    admitted = admitted_feature_hypotheses(evidence)
    outcome_counts = Counter(population["outcome_state"].astype(str))
    decision = {
        "status": (
            "features_admitted"
            if admitted
            else "no_features_admitted"
        ),
        "admitted_features": list(admitted),
        "conditional_direction_challenger_supported": bool(admitted),
        "online_authority": "none",
        "authorized_consumers": ["offline_research_only"],
        "reason": (
            "准入特征只形成下一轮预注册假设；本轮不修改线上模型。"
            if admitted
            else "现有日线特征未通过冻结的匹配稳定性门槛。"
        ),
    }
    manifest = {
        "study_version": STUDY_VERSION,
        "model": {
            "lifecycle": "research",
            "online_authority": "none",
        },
        "configuration": {
            "high_down_score_threshold": HIGH_DOWN_SCORE,
            "maximum_calendar_days": int(maximum_calendar_days),
            "bootstrap_samples": int(bootstrap_samples),
            "bootstrap_block_days": int(bootstrap_block_days),
            "bootstrap_seed": int(seed),
            "matching": (
                "same_outer_fold_group_regime_one_to_one_without_replacement"
            ),
        },
        "population_count": int(len(population)),
        "outcome_counts": {
            name: int(outcome_counts.get(name, 0))
            for name in (
                "terminal_down",
                "extreme_up",
                "path_only_stress",
                "other",
            )
        },
        "pair_count": int(len(pairs)),
        "data_availability": {
            "earnings_proximity": "unavailable",
            "earnings_proximity_reason": (
                "point_in_time_earnings_calendar_has_zero_coverage"
            ),
            "market_cap": "unavailable",
            "market_cap_reason": "true_point_in_time_market_cap_not_loaded",
        },
        "decision": decision,
        "coverage": _records(coverage),
        "feature_evidence": _records(evidence),
    }
    validate_audit_report_payload(manifest)
    return pairs, coverage, evidence, manifest


def run_audit(
    *,
    database="data/research_prices.db",
    start_date="2018-01-01",
    max_tickers=240,
    study_seed=20260726,
    minimum_samples=1_000,
    maximum_calendar_days=63,
    bootstrap_samples=2_000,
    bootstrap_block_days=20,
    bootstrap_seed=20260730,
):
    """Build frozen inputs and return strict matched audit evidence."""
    dataset = build_tail_study_dataset(
        database=database,
        start_date=start_date,
        max_tickers=max_tickers,
        seed=study_seed,
        minimum_samples=minimum_samples,
    )
    pairs, coverage, evidence, manifest = run_audit_from_dataset(
        dataset,
        maximum_calendar_days=maximum_calendar_days,
        bootstrap_samples=bootstrap_samples,
        bootstrap_block_days=bootstrap_block_days,
        seed=bootstrap_seed,
    )
    source_commit, dirty_worktree = _git_state()
    manifest.update(
        {
            "latest_date": dataset["metadata"]["latest_date"],
            "start_date": str(start_date),
            "ticker_count": int(len(dataset["analysis_tickers"])),
            "prediction_count": int(len(dataset["predictions"])),
            "database": Path(database).name,
            "database_content_fingerprint": input_content_fingerprint(
                dataset["target_frame"],
                dataset["histories"],
                dataset["analysis_tickers"],
            ),
            "source_commit": source_commit,
            "dirty_worktree": dirty_worktree,
            "study_configuration": {
                "cohort_seed": int(study_seed),
                "maximum_tickers": int(max_tickers),
                "minimum_samples": int(minimum_samples),
            },
        }
    )
    validate_audit_report_payload(manifest)
    return pairs, coverage, evidence, manifest


def render_audit_report(evidence, coverage, manifest):
    """Render the Chinese research-only counterexample audit."""
    admitted = tuple(
        manifest.get("decision", {}).get("admitted_features", ())
    )
    conclusion = (
        "通过准入门槛的特征：" + "、".join(admitted)
        if admitted
        else "没有特征通过冻结的准入门槛。"
    )
    summary_columns = [
        column
        for column in (
            "feature",
            "pair_count",
            "case_availability",
            "control_availability",
            "standardized_difference",
            "ci_low",
            "ci_high",
            "consistent_folds",
            "consistent_large_groups",
            "gate_passed",
        )
        if column in evidence
    ]
    overall = coverage.loc[
        coverage.get("scope_type") == "overall"
    ] if not coverage.empty else coverage
    return "\n".join(
        (
            "# 尾部方向误报匹配审计",
            "",
            "> 离线研究；`online_authority=none`。不修改 Ridge、方向策略、"
            "风险否决权或 UI。",
            "",
            "## 结论",
            "",
            f"- {conclusion}",
            f"- 高分审计总体：{manifest.get('population_count', 0)} 条。",
            f"- 一对一匹配：{manifest.get('pair_count', 0)} 对。",
            "- 终点下跌、极端上涨、路径压力和普通样本使用互斥标签。",
            "",
            "## 匹配覆盖",
            "",
            _markdown_table(overall),
            "",
            "## 特征证据",
            "",
            _markdown_table(evidence.loc[:, summary_columns]),
            "",
            "## 数据边界",
            "",
            "- 财报临近：不可用；点时财报日历当前覆盖率为零。",
            "- 真实市值：不可用；成交额只作为流动性代理。",
            "- 所有未来收益保持原始、未截尾，且不参与观察日特征。",
            "",
        )
    )


def publish_audit_reports(
    prefix,
    pairs,
    coverage,
    evidence,
    manifest,
    report,
):
    """Atomically publish strict JSON and three audit tables."""
    validate_audit_report_payload(manifest)
    checked_prefix = Path(prefix)
    checked_prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": checked_prefix.with_suffix(".json"),
        "pairs_csv": checked_prefix.with_name(
            checked_prefix.name + "-pairs"
        ).with_suffix(".csv"),
        "coverage_csv": checked_prefix.with_name(
            checked_prefix.name + "-coverage"
        ).with_suffix(".csv"),
        "features_csv": checked_prefix.with_name(
            checked_prefix.name + "-features"
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
        "pairs_csv": pairs.to_csv(index=False),
        "coverage_csv": coverage.to_csv(index=False),
        "features_csv": evidence.to_csv(index=False),
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument("--start-date", default="2018-01-01")
    parser.add_argument("--max-tickers", type=int, default=240)
    parser.add_argument("--study-seed", type=int, default=20260726)
    parser.add_argument("--minimum-samples", type=int, default=1_000)
    parser.add_argument("--maximum-calendar-days", type=int, default=63)
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    parser.add_argument("--bootstrap-block-days", type=int, default=20)
    parser.add_argument("--bootstrap-seed", type=int, default=20260730)
    parser.add_argument(
        "--output-prefix",
        default="reports/tail-direction-counterexample-audit",
    )
    args = parser.parse_args(argv)
    pairs, coverage, evidence, manifest = run_audit(
        database=args.database,
        start_date=args.start_date,
        max_tickers=args.max_tickers,
        study_seed=args.study_seed,
        minimum_samples=args.minimum_samples,
        maximum_calendar_days=args.maximum_calendar_days,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_block_days=args.bootstrap_block_days,
        bootstrap_seed=args.bootstrap_seed,
    )
    report = render_audit_report(evidence, coverage, manifest)
    paths = publish_audit_reports(
        args.output_prefix,
        pairs,
        coverage,
        evidence,
        manifest,
        report,
    )
    print(
        json.dumps(
            {name: str(path) for name, path in paths.items()},
            sort_keys=True,
        )
    )
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
