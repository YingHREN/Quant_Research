"""Publish a point-in-time audit of asymmetric-tail counterexamples."""

from __future__ import annotations

import argparse
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

from research.asymmetric_tail_counterexample_audit import (
    attach_point_in_time_context,
    fixed_band_definitions,
    preregistered_feature_hypotheses,
    resolve_point_in_time_groups,
    summarize_counterexamples,
)
from research.asymmetric_tail_risk import attach_asymmetric_tail_targets
from research.expanded_market_data import ExpandedMarketDataRepository
from research.run_expanded_walkforward_study import (
    classify_study_groups,
    prepare_expanded_frame,
    select_analysis_tickers,
)
from research.run_hierarchical_recency_direction import (
    input_content_fingerprint,
)
from research.run_historical_demand_support_study import (
    load_group_assignment_intervals,
)
from research.run_regime_threshold_direction import (
    _git_state,
    _manifest_value,
    _markdown_table,
    validate_report_payload,
)
from web.market_groups import REFERENCE_TICKERS


AUDIT_VERSION = "asymmetric_tail_counterexample_audit_v1"
IDENTITY_COLUMNS = (
    "ticker",
    "observation_date",
    "calibrated_down_probability",
    "actual_terminal_return",
)
NUMERIC_SOURCE_COLUMNS = {
    "fold",
    "calibrated_down_probability",
    "calibrated_rebound_probability",
    "actual_terminal_return",
    "actual_path_mae",
    "opening_gap",
    "realized_volatility",
    "dollar_volume",
}


def validate_source_identity(source_manifest, counterexamples):
    """Require the CSV to be the exact sample embedded in the source JSON."""
    if not isinstance(source_manifest, dict):
        raise TypeError("source_manifest must be a dictionary")
    if not isinstance(counterexamples, pd.DataFrame):
        raise TypeError("counterexamples must be a DataFrame")
    model = source_manifest.get("model", {})
    if (
        model.get("lifecycle") != "research"
        or model.get("online_authority") != "none"
    ):
        raise ValueError("source model authority is not research-only")
    source_records = source_manifest.get("counterexamples")
    if not isinstance(source_records, list):
        raise ValueError("source counterexample identity is unavailable")
    embedded = pd.DataFrame(source_records)
    missing_embedded = [
        column for column in IDENTITY_COLUMNS if column not in embedded
    ]
    missing_csv = [
        column for column in IDENTITY_COLUMNS if column not in counterexamples
    ]
    if missing_embedded or missing_csv:
        raise ValueError(
            "source counterexample identity columns are unavailable"
        )
    source_columns = tuple(str(column) for column in embedded.columns)
    csv_columns = tuple(str(column) for column in counterexamples.columns)
    if (
        len(csv_columns) != len(source_columns)
        or set(csv_columns) != set(source_columns)
    ):
        raise ValueError("source counterexample schema mismatch")
    expected = _normalized_source_rows(embedded, source_columns)
    actual = _normalized_source_rows(counterexamples, source_columns)
    if len(expected) != len(actual):
        raise ValueError("source counterexample identity count mismatch")
    if not expected.loc[:, IDENTITY_COLUMNS[:2]].equals(
        actual.loc[:, IDENTITY_COLUMNS[:2]]
    ):
        raise ValueError("source counterexample identity key mismatch")
    for column in source_columns:
        if column in IDENTITY_COLUMNS[:2]:
            continue
        if column in NUMERIC_SOURCE_COLUMNS:
            equal = np.allclose(
                expected[column].to_numpy(dtype=float),
                actual[column].to_numpy(dtype=float),
                rtol=1e-12,
                atol=1e-12,
                equal_nan=True,
            )
        else:
            equal = expected[column].equals(actual[column])
        if not equal:
            raise ValueError(
                f"source counterexample identity mismatch for {column}"
            )
    return None


def _published_source_view(source_manifest, audit_rows):
    """Restore source fields intentionally replaced during the audit."""
    source_view = audit_rows.copy()
    if "published_group" in source_view.columns:
        source_view["group"] = source_view["published_group"]
    source_records = source_manifest.get("counterexamples", ())
    source_columns = tuple(pd.DataFrame(source_records).columns)
    missing = [column for column in source_columns if column not in source_view]
    if missing:
        return source_view
    return source_view.loc[:, source_columns]


def build_audit_manifest(
    source_manifest,
    audit_rows,
    summary,
    *,
    source_counterexamples_file,
):
    """Build strict research-only audit metadata and descriptive evidence."""
    validate_source_identity(
        source_manifest,
        _published_source_view(source_manifest, audit_rows),
    )
    if not isinstance(summary, pd.DataFrame):
        raise TypeError("summary must be a DataFrame")
    source_file = Path(source_counterexamples_file).name
    if not source_file:
        raise ValueError("source_counterexamples_file must not be empty")
    manifest = {
        "audit_version": AUDIT_VERSION,
        "source_study_version": source_manifest.get("study_version"),
        "source_commit": source_manifest.get("source_commit"),
        "source_database": source_manifest.get("database"),
        "source_database_content_fingerprint": source_manifest.get(
            "database_content_fingerprint"
        ),
        "source_counterexamples_file": source_file,
        "source_sample_definition": {
            "calibrated_down_probability_minimum": 0.40,
            "raw_terminal_return_minimum": 0.10,
        },
        "source_sample_count": len(
            source_manifest.get("counterexamples", ())
        ),
        "audit_row_count": len(audit_rows),
        "model": {
            "name": AUDIT_VERSION,
            "lifecycle": "research",
            "online_authority": "none",
        },
        "audit_code_provenance": _audit_code_provenance(),
        "point_in_time_contract": {
            "feature_join": "exact_ticker_observation_date",
            "percentile_reference": (
                "same_observation_date_frozen_study_cohort"
            ),
            "future_outcome_use": "source_sample_definition_only",
            "ticker_specific_tuning": False,
        },
        "fixed_band_definitions": _json_safe(fixed_band_definitions()),
        "data_availability": {
            "opening_gap_available": _finite_count(
                audit_rows,
                "opening_gap",
            ),
            "realized_volatility_available": _finite_count(
                audit_rows,
                "realized_volatility",
            ),
            "realized_volatility_percentile_available": _finite_count(
                audit_rows,
                "realized_volatility_percentile",
            ),
            "atr20_percentile_available": _finite_count(
                audit_rows,
                "atr20_percentile",
            ),
            "price_available": _finite_count(audit_rows, "price"),
            "dollar_volume_available": _finite_count(
                audit_rows,
                "dollar_volume",
            ),
            "earnings_proximity_available": int(
                (
                    audit_rows.get(
                        "earnings_proximity_status",
                        pd.Series(dtype=object),
                    )
                    != "unavailable"
                ).sum()
            ),
            "point_in_time_market_cap_available": 0,
            "point_in_time_group_available": int(
                (
                    audit_rows.get(
                        "point_in_time_group_status",
                        pd.Series(dtype=object),
                    )
                    == "available"
                ).sum()
            ),
        },
        "limitations": (
            "descriptive_counterexample_only_no_reference_rate",
            "earnings_proximity_unavailable_without_point_in_time_calendar",
            "point_in_time_market_cap_unavailable_price_liquidity_proxies_only",
            "published_group_snapshot_not_used_as_point_in_time_evidence",
            "no_model_threshold_feature_or_authority_change",
        ),
        "preregistered_feature_hypotheses": _json_safe(
            preregistered_feature_hypotheses()
        ),
        "summary": _records(summary),
    }
    validate_report_payload(manifest)
    return manifest


def render_audit_report(summary, manifest):
    """Render descriptive strata and frozen next-study hypotheses in Chinese."""
    if not isinstance(summary, pd.DataFrame):
        raise TypeError("summary must be a DataFrame")
    display_columns = [
        column
        for column in (
            "dimension",
            "stratum",
            "row_count",
            "share",
            "mean_terminal_return",
            "median_terminal_return",
            "median_path_mae",
            "median_down_probability",
            "median_rebound_probability",
        )
        if column in summary
    ]
    availability = manifest.get("data_availability", {})
    hypotheses = manifest.get("preregistered_feature_hypotheses", ())
    hypothesis_lines = "\n".join(
        "- `{name}`：{test}".format(
            name=item.get("name", "unavailable"),
            test=item.get("test", "unavailable"),
        )
        for item in hypotheses
    )
    return "\n".join(
        (
            "# 不对称尾部高分暴涨反例点时审计",
            "",
            "> 描述性离线审计；`online_authority=none`。本报告不修改模型、"
            "阈值、Ridge、最终方向、否决策略或 UI。",
            "",
            "## 样本与数据边界",
            "",
            f"- 原始反例：{manifest.get('source_sample_count', 0):,} 条；"
            f"审计保留：{manifest.get('audit_row_count', 0):,} 条。",
            "- 反例固定为校准下跌概率至少 0.40 且未经截尾的五日终点收益"
            "至少 +10%。",
            "- 波动率与 ATR 百分位仅在相同观察日的冻结研究队列内计算。",
            f"- 财报邻近度不可用：可靠点时财报日历覆盖 "
            f"{availability.get('earnings_proximity_available', 0)} 条。",
            "- 点时市值不可用；本轮仅报告观察日价格与过去 20 日平均成交额"
            "代理，不用当前市值回填历史。",
            f"- 点时板块可用 "
            f"{availability.get('point_in_time_group_available', 0)} 条；"
            "已发布的当前分类快照仅保留审计，不参与板块分层。",
            "- 本审计没有非反例参考率，分层占比只能形成下一轮预注册假设，"
            "不能解释为特征增量或因果效应。",
            "",
            "## 分层结果",
            "",
            _markdown_table(summary.loc[:, display_columns]),
            "",
            "## 下一轮预注册特征假设",
            "",
            hypothesis_lines or "- 无",
            "",
            "## 权限结论",
            "",
            "- 所有假设保持 `research`、`online_authority=none`。",
            "- 在新的嵌套走步实验通过预注册门槛前，不接入模型或线上决策。",
            "",
        )
    )


def publish_audit_reports(prefix, audit_rows, manifest, report):
    """Atomically publish strict JSON, detailed CSV and Markdown."""
    if not isinstance(audit_rows, pd.DataFrame):
        raise TypeError("audit_rows must be a DataFrame")
    validate_report_payload(manifest)
    checked_prefix = Path(prefix)
    checked_prefix.parent.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": checked_prefix.with_suffix(".json"),
        "csv": checked_prefix.with_suffix(".csv"),
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
        "csv": audit_rows.to_csv(index=False),
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


def run_audit(
    *,
    database="data/research_prices.db",
    source_prefix="reports/asymmetric-tail-risk",
):
    """Reconstruct the frozen cohort and audit the published sample."""
    source_path = Path(source_prefix)
    source_json = source_path.with_suffix(".json")
    source_csv = source_path.with_name(
        source_path.name + "-counterexamples"
    ).with_suffix(".csv")
    source_manifest = json.loads(source_json.read_text(encoding="utf-8"))
    validate_report_payload(source_manifest)
    counterexamples = pd.read_csv(source_csv)
    validate_source_identity(source_manifest, counterexamples)

    configuration = source_manifest.get("configuration", {})
    seed = int(configuration.get("cohort_seed"))
    max_tickers = int(configuration.get("maximum_tickers"))
    start_date = str(source_manifest.get("start_date"))
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
    fingerprint_frame = attach_asymmetric_tail_targets(
        feature_frame,
        histories,
    )
    fingerprint = input_content_fingerprint(
        fingerprint_frame,
        histories,
        analysis_tickers,
    )
    if fingerprint != source_manifest.get("database_content_fingerprint"):
        raise RuntimeError(
            "source database content fingerprint no longer matches"
        )

    audit_rows = attach_point_in_time_context(
        counterexamples,
        feature_frame,
        point_in_time_groups=resolve_point_in_time_groups(
            counterexamples,
            load_group_assignment_intervals(database),
        ),
    )
    summary = summarize_counterexamples(audit_rows)
    manifest = build_audit_manifest(
        source_manifest,
        audit_rows,
        summary,
        source_counterexamples_file=source_csv.name,
    )
    manifest["reconstruction"] = {
        "database_content_fingerprint": fingerprint,
        "matched_source_fingerprint": True,
        "ticker_count": len(analysis_tickers),
        "start_date": start_date,
        "cohort_seed": seed,
        "group_assignment_evidence_fingerprint": (
            _frame_content_fingerprint(
                load_group_assignment_intervals(database)
            )
        ),
    }
    validate_report_payload(manifest)
    return {
        "manifest": manifest,
        "audit_rows": audit_rows,
        "summary": summary,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument(
        "--source-prefix",
        default="reports/asymmetric-tail-risk",
    )
    parser.add_argument(
        "--output-prefix",
        default="reports/asymmetric-tail-risk-counterexample-audit",
    )
    args = parser.parse_args(argv)
    result = run_audit(
        database=args.database,
        source_prefix=args.source_prefix,
    )
    report = render_audit_report(
        result["summary"],
        result["manifest"],
    )
    paths = publish_audit_reports(
        args.output_prefix,
        result["audit_rows"],
        result["manifest"],
        report,
    )
    print(
        json.dumps(
            {name: str(path) for name, path in paths.items()},
            sort_keys=True,
        )
    )
    return 0


def _normalized_source_rows(frame, columns):
    checked = frame.loc[:, columns].copy()
    checked["ticker"] = checked["ticker"].astype(str).str.strip().str.upper()
    checked["observation_date"] = pd.to_datetime(
        checked["observation_date"],
        errors="raise",
    ).dt.tz_localize(None)
    for column in columns:
        if column in IDENTITY_COLUMNS[:2]:
            continue
        if column in NUMERIC_SOURCE_COLUMNS:
            checked[column] = pd.to_numeric(checked[column], errors="raise")
        else:
            checked[column] = (
                checked[column]
                .where(checked[column].notna(), "<missing>")
                .astype(str)
                .str.strip()
                .str.casefold()
            )
    if checked.duplicated(list(IDENTITY_COLUMNS[:2])).any():
        raise ValueError("source counterexample identity contains duplicates")
    return checked.sort_values(
        list(IDENTITY_COLUMNS[:2]),
        kind="mergesort",
    ).reset_index(drop=True)


def _audit_code_provenance():
    source_commit, dirty_worktree = _git_state()
    digest = hashlib.sha256()
    for path in (
        Path(__file__).with_name("asymmetric_tail_counterexample_audit.py"),
        Path(__file__),
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return {
        "source_commit": source_commit,
        "dirty_worktree": dirty_worktree,
        "content_fingerprint": digest.hexdigest(),
    }


def _frame_content_fingerprint(frame):
    digest = hashlib.sha256()
    digest.update(
        pd.util.hash_pandas_object(
            frame,
            index=True,
            categorize=False,
        ).to_numpy(dtype="<u8", copy=False).tobytes()
    )
    return digest.hexdigest()


def _finite_count(frame, column):
    if column not in frame:
        return 0
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(
        dtype=float
    )
    return int(np.isfinite(values).sum())


def _records(frame):
    return [
        {
            str(key): _manifest_value(value)
            for key, value in row.items()
        }
        for row in frame.to_dict(orient="records")
    ]


def _json_safe(value):
    return _manifest_value(value)


if __name__ == "__main__":
    raise SystemExit(main())
