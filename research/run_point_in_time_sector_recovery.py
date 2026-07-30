"""Audit leakage-safe sector-relative features on frozen tail-risk pairs."""

from __future__ import annotations

import argparse
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.expanded_market_data import ExpandedMarketDataRepository
from research.point_in_time_sector_features import (
    ASSIGNMENT_RULE_VERSION,
    FEATURE_COLUMNS,
    PIT_SECTOR_CANDIDATES,
    build_monthly_behavior_assignments,
    build_point_in_time_sector_features,
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
from research.tail_direction_counterexample_audit import (
    paired_feature_evidence,
)


STUDY_VERSION = "point_in_time_sector_recovery_v1"
AUDIT_FEATURE_TYPES = OrderedDict(
    (
        ("pit_sector_relative_strength_20", "numeric"),
        ("pit_stock_sector_relative_strength_20", "numeric"),
        ("pit_sector_assignment_age_days", "numeric"),
        ("pit_sector_residual_correlation", "numeric"),
    )
)
DIRECTIONAL_FEATURES = frozenset(
    {
        "pit_sector_relative_strength_20",
        "pit_stock_sector_relative_strength_20",
    }
)
FOLD_AVAILABILITY_GATE = 0.85


def attach_recovered_features_to_pairs(pairs, features):
    """Attach exact case/control feature rows without changing frozen pairs."""
    checked_pairs = _validated_pairs(pairs)
    checked_features = _validated_features(features)
    requested = []
    for row in checked_pairs.itertuples(index=False):
        requested.extend(
            (
                (row.case_ticker, row.case_observation_date),
                (row.control_ticker, row.control_observation_date),
            )
        )
    requested_index = pd.MultiIndex.from_tuples(
        requested,
        names=["ticker", "observation_date"],
    )
    missing = requested_index.unique().difference(checked_features.index)
    if len(missing):
        raise ValueError(
            f"feature frame is missing {len(missing)} frozen keys"
        )
    enriched = checked_pairs.copy(deep=True)
    for side in ("case", "control"):
        side_index = pd.MultiIndex.from_arrays(
            (
                enriched[f"{side}_ticker"],
                enriched[f"{side}_observation_date"],
            ),
            names=["ticker", "observation_date"],
        )
        selected = checked_features.reindex(side_index)
        for feature in AUDIT_FEATURE_TYPES:
            enriched[f"{side}_{feature}"] = pd.to_numeric(
                selected[feature],
                errors="coerce",
            ).to_numpy(dtype=float)
        enriched[f"{side}_pit_sector_unavailable_reason"] = selected[
            "pit_sector_unavailable_reason"
        ].fillna("missing_feature_row").astype(str).to_numpy()
    return enriched


def evaluate_sector_recovery(
    pairs,
    features,
    *,
    bootstrap_samples=2_000,
    bootstrap_block_days=20,
    seed=20260730,
):
    """Evaluate recovered features while preserving offline-only authority."""
    enriched = attach_recovered_features_to_pairs(pairs, features)
    evidence = paired_feature_evidence(
        enriched,
        feature_types=AUDIT_FEATURE_TYPES,
        bootstrap_samples=bootstrap_samples,
        bootstrap_block_days=bootstrap_block_days,
        seed=seed,
    )
    coverage = _fold_coverage(enriched)
    evidence = apply_fold_availability_gate(evidence, coverage)
    unavailable_reasons = _unavailable_reason_records(enriched)
    admitted = tuple(
        sorted(
            evidence.loc[
                evidence["final_gate_passed"],
                "feature",
            ].astype(str)
        )
    )
    manifest = {
        "study_version": STUDY_VERSION,
        "model": {
            "lifecycle": "research",
            "online_authority": "none",
        },
        "configuration": {
            "pair_cohort": "frozen_tail_direction_8199_pairs",
            "matching": "reuse_frozen_pairs_without_rematching",
            "assignment_rule_version": ASSIGNMENT_RULE_VERSION,
            "assignment_refresh": "completed_month_end",
            "assignment_effective": "next_recorded_stock_session",
            "maximum_assignment_age_calendar_days": 45,
            "return_window_stock_sessions": 20,
            "return_endpoint_policy": "exact_dates_no_adjacent_fill",
            "minimum_fold_pair_availability": FOLD_AVAILABILITY_GATE,
            "bootstrap_samples": int(bootstrap_samples),
            "bootstrap_block_days": int(bootstrap_block_days),
            "bootstrap_seed": int(seed),
            "candidate_proxies": dict(PIT_SECTOR_CANDIDATES),
        },
        "pair_count": int(len(enriched)),
        "decision": {
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
                "通过的方向特征只进入下一轮预注册挑战者研究。"
                if admitted
                else "未有方向特征同时通过全局与逐折覆盖门槛。"
            ),
        },
        "coverage": _records(coverage),
        "unavailable_reasons": unavailable_reasons,
        "feature_evidence": _records(evidence),
    }
    validate_report_payload(manifest)
    return evidence, coverage, manifest


def apply_fold_availability_gate(evidence, coverage):
    """Add the frozen five-fold coverage and diagnostic-only gates."""
    if not isinstance(evidence, pd.DataFrame):
        raise TypeError("evidence must be a DataFrame")
    if not isinstance(coverage, pd.DataFrame):
        raise TypeError("coverage must be a DataFrame")
    required_evidence = {"feature", "gate_passed", "gate_reasons"}
    required_coverage = {"feature", "fold", "pair_availability"}
    if not required_evidence.issubset(evidence):
        raise ValueError("evidence is missing gate columns")
    if not required_coverage.issubset(coverage):
        raise ValueError("coverage is missing fold columns")
    result = evidence.copy(deep=True)
    minimums = (
        coverage.groupby("feature", sort=False)["pair_availability"]
        .min()
        .to_dict()
    )
    fold_counts = coverage.groupby("feature", sort=False)["fold"].nunique()
    final_reasons = []
    for row in result.itertuples(index=False):
        feature = str(row.feature)
        raw_reasons = row.gate_reasons
        if isinstance(raw_reasons, str):
            reasons = [raw_reasons] if raw_reasons else []
        else:
            reasons = list(raw_reasons)
        if feature not in DIRECTIONAL_FEATURES:
            reasons.append("diagnostic_only_not_directional")
        if int(fold_counts.get(feature, 0)) != 5:
            reasons.append("missing_outer_fold_coverage")
        if float(minimums.get(feature, 0.0)) < FOLD_AVAILABILITY_GATE:
            reasons.append("fold_pair_availability_below_gate")
        final_reasons.append(tuple(dict.fromkeys(reasons)))
    result["minimum_fold_pair_availability"] = result["feature"].map(
        minimums
    ).fillna(0.0)
    result["fold_availability_gate_passed"] = (
        result["minimum_fold_pair_availability"]
        >= FOLD_AVAILABILITY_GATE
    )
    result["final_gate_reasons"] = final_reasons
    result["final_gate_passed"] = [
        bool(original) and not reasons
        for original, reasons in zip(
            result["gate_passed"],
            final_reasons,
        )
    ]
    return result


def run_recovery(
    *,
    database="data/research_prices.db",
    pairs_path="reports/tail-direction-counterexample-audit-pairs.csv",
    bootstrap_samples=2_000,
    bootstrap_block_days=20,
    seed=20260730,
):
    """Load frozen pairs, compute causal histories and return audit outputs."""
    pairs = pd.read_csv(pairs_path)
    checked_pairs = _validated_pairs(pairs)
    analysis_tickers = tuple(
        sorted(
            set(checked_pairs["case_ticker"])
            | set(checked_pairs["control_ticker"])
        )
    )
    requested = tuple(
        sorted(
            set(analysis_tickers)
            | {"SPY", "QQQ"}
            | set(PIT_SECTOR_CANDIDATES.values())
        )
    )
    repository = ExpandedMarketDataRepository(database)
    histories = repository.load_universe_histories(tickers=requested)
    earliest = min(
        checked_pairs["case_observation_date"].min(),
        checked_pairs["control_observation_date"].min(),
    )
    assignments = build_monthly_behavior_assignments(
        histories,
        analysis_tickers,
        start_date=earliest,
    )
    observation_index = pd.MultiIndex.from_tuples(
        [
            (ticker, date)
            for ticker, date in zip(
                pd.concat(
                    (
                        checked_pairs["case_ticker"],
                        checked_pairs["control_ticker"],
                    ),
                    ignore_index=True,
                ),
                pd.concat(
                    (
                        checked_pairs["case_observation_date"],
                        checked_pairs["control_observation_date"],
                    ),
                    ignore_index=True,
                ),
            )
        ],
        names=["ticker", "observation_date"],
    ).unique()
    features = build_point_in_time_sector_features(
        histories,
        assignments,
        observation_index,
    )
    evidence, coverage, manifest = evaluate_sector_recovery(
        checked_pairs,
        features,
        bootstrap_samples=bootstrap_samples,
        bootstrap_block_days=bootstrap_block_days,
        seed=seed,
    )
    source_commit, dirty_worktree = _git_state()
    manifest.update(
        {
            "database": Path(database).name,
            "database_content_fingerprint": input_content_fingerprint(
                pd.DataFrame(),
                histories,
                analysis_tickers,
            ),
            "pair_cohort_fingerprint": _frame_fingerprint(checked_pairs),
            "source_commit": source_commit,
            "dirty_worktree": dirty_worktree,
            "ticker_count": int(len(analysis_tickers)),
            "assignment_count": int(len(assignments)),
        }
    )
    validate_report_payload(manifest)
    return assignments, coverage, evidence, manifest


def render_recovery_report(evidence, coverage, manifest):
    admitted = manifest.get("decision", {}).get("admitted_features", ())
    conclusion = (
        "通过准入门槛：" + "、".join(admitted)
        if admitted
        else "没有方向特征通过冻结的全部准入门槛。"
    )
    columns = [
        column
        for column in (
            "feature",
            "pair_count",
            "case_availability",
            "control_availability",
            "standardized_difference",
            "ci_low",
            "ci_high",
            "minimum_fold_pair_availability",
            "final_gate_passed",
        )
        if column in evidence
    ]
    return "\n".join(
        (
            "# 历史时点板块特征覆盖恢复审计",
            "",
            "> 离线研究；`online_authority=none`。不修改 Ridge、策略、"
            "风险否决权或 UI。",
            "",
            "## 结论",
            "",
            f"- {conclusion}",
            f"- 固定匹配样本：{manifest.get('pair_count', 0)} 对；未重新匹配。",
            "- 月末价格行为分类从下一股票交易日起生效，最多使用 45 天。",
            "- 20 日相对收益要求股票、板块 ETF 与 QQQ 使用完全相同日期。",
            "",
            "## 特征证据",
            "",
            _markdown_table(evidence.loc[:, columns]),
            "",
            "## 逐折覆盖",
            "",
            _markdown_table(coverage),
            "",
        )
    )


def publish_recovery_reports(
    prefix,
    assignments,
    coverage,
    evidence,
    manifest,
):
    """Atomically publish strict recovery evidence."""
    validate_report_payload(manifest)
    checked_prefix = Path(prefix)
    checked_prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": checked_prefix.with_suffix(".json"),
        "assignments_csv": checked_prefix.with_name(
            checked_prefix.name + "-assignments"
        ).with_suffix(".csv"),
        "coverage_csv": checked_prefix.with_name(
            checked_prefix.name + "-coverage"
        ).with_suffix(".csv"),
        "features_csv": checked_prefix.with_name(
            checked_prefix.name + "-features"
        ).with_suffix(".csv"),
        "md": checked_prefix.with_suffix(".md"),
    }
    report = render_recovery_report(evidence, coverage, manifest)
    payloads = {
        "json": json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        "assignments_csv": assignments.to_csv(index=False),
        "coverage_csv": coverage.to_csv(index=False),
        "features_csv": evidence.to_csv(index=False),
        "md": report,
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


def _validated_pairs(pairs):
    if not isinstance(pairs, pd.DataFrame):
        raise TypeError("pairs must be a DataFrame")
    required = (
        "pair_id",
        "case_key",
        "control_key",
        "case_ticker",
        "case_observation_date",
        "control_ticker",
        "control_observation_date",
        "fold",
        "group",
        "regime",
    )
    missing = [column for column in required if column not in pairs]
    if missing:
        raise ValueError(f"pairs are missing columns: {missing}")
    checked = pairs.copy(deep=True)
    for side in ("case", "control"):
        checked[f"{side}_ticker"] = (
            checked[f"{side}_ticker"].astype(str).str.strip().str.upper()
        )
        checked[f"{side}_observation_date"] = pd.to_datetime(
            checked[f"{side}_observation_date"],
            errors="raise",
        ).dt.tz_localize(None).dt.normalize()
        expected = (
            checked[f"{side}_ticker"]
            + "|"
            + checked[f"{side}_observation_date"].dt.strftime("%Y-%m-%d")
        )
        if not expected.equals(checked[f"{side}_key"].astype(str)):
            raise ValueError(f"{side} frozen keys do not match ticker/date")
        if checked[f"{side}_key"].duplicated().any():
            raise ValueError(f"pairs contain duplicate {side} keys")
    if checked["pair_id"].duplicated().any():
        raise ValueError("pairs contain duplicate pair ids")
    return checked.reset_index(drop=True)


def _validated_features(features):
    if not isinstance(features, pd.DataFrame):
        raise TypeError("features must be a DataFrame")
    if not isinstance(features.index, pd.MultiIndex):
        raise TypeError("features must use a MultiIndex")
    missing = set(FEATURE_COLUMNS) - set(features.columns)
    if missing:
        raise ValueError(
            "features are missing columns: " + ", ".join(sorted(missing))
        )
    if features.index.has_duplicates:
        raise ValueError("features contain duplicate keys")
    tuples = [
        (str(ticker).strip().upper(), pd.Timestamp(date).normalize())
        for ticker, date in features.index.tolist()
    ]
    checked = features.loc[:, FEATURE_COLUMNS].copy(deep=True)
    checked.index = pd.MultiIndex.from_tuples(
        tuples,
        names=["ticker", "observation_date"],
    )
    return checked


def _fold_coverage(enriched):
    rows = []
    for feature in AUDIT_FEATURE_TYPES:
        for fold in range(1, 6):
            selected = enriched.loc[enriched["fold"] == fold]
            both = (
                pd.to_numeric(
                    selected[f"case_{feature}"],
                    errors="coerce",
                ).notna()
                & pd.to_numeric(
                    selected[f"control_{feature}"],
                    errors="coerce",
                ).notna()
            )
            rows.append(
                {
                    "feature": feature,
                    "fold": fold,
                    "pair_count": int(len(selected)),
                    "both_available_count": int(both.sum()),
                    "pair_availability": (
                        float(both.mean()) if len(selected) else 0.0
                    ),
                }
            )
    return pd.DataFrame(rows)


def _unavailable_reason_records(enriched):
    records = []
    for side in ("case", "control"):
        column = f"{side}_pit_sector_unavailable_reason"
        reasons = enriched[column].replace("", "available").value_counts(
            dropna=False
        )
        for reason, count in reasons.sort_index().items():
            records.append(
                {
                    "side": side,
                    "reason": str(reason),
                    "count": int(count),
                    "rate": float(count / len(enriched))
                    if len(enriched)
                    else 0.0,
                }
            )
    return records


def _records(frame):
    return [
        {
            str(key): _manifest_value(value)
            for key, value in row.items()
        }
        for row in frame.to_dict(orient="records")
    ]


def _frame_fingerprint(frame):
    canonical = frame.to_csv(index=False, lineterminator="\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument(
        "--pairs",
        default="reports/tail-direction-counterexample-audit-pairs.csv",
    )
    parser.add_argument("--bootstrap-samples", type=int, default=2_000)
    parser.add_argument("--bootstrap-block-days", type=int, default=20)
    parser.add_argument("--bootstrap-seed", type=int, default=20260730)
    parser.add_argument(
        "--output-prefix",
        default="reports/point-in-time-sector-recovery",
    )
    args = parser.parse_args(argv)
    assignments, coverage, evidence, manifest = run_recovery(
        database=args.database,
        pairs_path=args.pairs,
        bootstrap_samples=args.bootstrap_samples,
        bootstrap_block_days=args.bootstrap_block_days,
        seed=args.bootstrap_seed,
    )
    paths = publish_recovery_reports(
        args.output_prefix,
        assignments,
        coverage,
        evidence,
        manifest,
    )
    print(
        json.dumps(
            {name: str(path) for name, path in paths.items()},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
