"""Build an audited, deterministic catalog of delisted common-stock candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from data.delisted_security_catalog import (
    CATALOG_SCHEMA_VERSION,
    RULE_VERSION,
    build_delisted_catalog,
    summarize_delisted_catalog,
)


REPORT_SCHEMA_VERSION = "delisted_security_purification_report_v1"
MANIFEST_SCHEMA_VERSION = "delisted_security_purification_manifest_v1"


def build_catalog_files(
    input_path,
    *,
    output_catalog,
    manifest_path,
    report_json,
    report_markdown,
    observed_at,
):
    """Purify one raw provider snapshot and atomically write audit artifacts."""
    input_path = Path(input_path)
    output_catalog = Path(output_catalog)
    manifest_path = Path(manifest_path)
    report_json = Path(report_json)
    report_markdown = Path(report_markdown)
    observed_at = str(observed_at or "").strip()
    if not observed_at:
        raise ValueError("observed_at must not be empty")
    source_bytes = input_path.read_bytes()
    rows = json.loads(source_bytes)
    catalog = build_delisted_catalog(rows)
    summary = summarize_delisted_catalog(catalog)
    catalog_bytes = _canonical_json_bytes(catalog)
    input_sha256 = hashlib.sha256(source_bytes).hexdigest()
    catalog_sha256 = hashlib.sha256(catalog_bytes).hexdigest()

    _atomic_bytes(output_catalog, catalog_bytes)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "input_sha256": input_sha256,
        "catalog_sha256": catalog_sha256,
        "summary": summary,
    }
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "observed_at": observed_at,
        "input_rows": summary["input_rows"],
        "input_sha256": input_sha256,
        "catalog_sha256": catalog_sha256,
        "report_status": "completed",
    }
    _atomic_bytes(report_json, _pretty_json_bytes(report))
    _atomic_bytes(manifest_path, _pretty_json_bytes(manifest))
    _atomic_text(report_markdown, _markdown_report(report))
    return report


def _markdown_report(report):
    summary = report["summary"]
    classifications = summary["classification_counts"]
    rows = [
        "# 退市证券类型净化审计",
        "",
        f"- 规则版本：`{report['rule_version']}`",
        f"- 原始目录：{summary['input_rows']:,} 行",
        f"- 主交易所范围内：{summary['in_scope_rows']:,} 行",
        f"- 可进入日线回填候选：{summary['backfill_eligible_rows']:,} 行",
        f"- 输入 SHA-256：`{report['input_sha256']}`",
        f"- 净化目录 SHA-256：`{report['catalog_sha256']}`",
        "- 边界：该目录不是指数成员区间；无稳定身份键时不拼接价格序列。",
        "",
        "## 分类结果",
        "",
        "| 分类 | 数量 |",
        "| --- | ---: |",
    ]
    for key in (
        "accepted_common",
        "rejected_non_common",
        "needs_review",
        "out_of_scope",
    ):
        rows.append(f"| `{key}` | {classifications.get(key, 0):,} |")
    rows.extend(
        (
            "",
            "## 身份覆盖",
            "",
            "| 身份状态 | 全目录 | 主交易所范围内 |",
            "| --- | ---: | ---: |",
        )
    )
    identity_keys = sorted(
        set(summary["identity_status_counts"])
        | set(summary["in_scope_identity_status_counts"])
    )
    for key in identity_keys:
        rows.append(
            f"| `{key}` | {summary['identity_status_counts'].get(key, 0):,} | "
            f"{summary['in_scope_identity_status_counts'].get(key, 0):,} |"
        )
    rows.extend(
        (
            "",
            "## 原因审计",
            "",
            "| 原因码 | 数量 | 样例代码 |",
            "| --- | ---: | --- |",
        )
    )
    for key, value in summary["reason_counts"].items():
        samples = ", ".join(summary["reason_samples"].get(key, ())) or "—"
        rows.append(f"| `{key}` | {value:,} | {samples} |")
    rows.append("")
    return "\n".join(rows)


def _canonical_json_bytes(payload):
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(payload):
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    ).encode("utf-8")


def _atomic_text(path, content):
    _atomic_bytes(Path(path), content.encode("utf-8"))


def _atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-catalog", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--report-json",
        default="reports/delisted-security-purification.json",
    )
    parser.add_argument(
        "--report-markdown",
        default="reports/delisted-security-purification.md",
    )
    parser.add_argument("--observed-at", required=True)
    args = parser.parse_args(argv)
    report = build_catalog_files(
        args.input,
        output_catalog=args.output_catalog,
        manifest_path=args.manifest,
        report_json=args.report_json,
        report_markdown=args.report_markdown,
        observed_at=args.observed_at,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
