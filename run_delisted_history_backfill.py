"""Collect purified delisted-stock EOD histories with resumable checkpoints."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from research.delisted_history_backfill import (
    BACKFILL_VERSION,
    freeze_candidates,
    summarize_backfill,
)
from research.delisted_history_pilot import audit_history_rows


START_DATE = "2016-01-01"
FINISH_DATE = "2026-07-27"
RETRYABLE_HTTP_STATUSES = {429, 500, 502, 503, 504}
PATH_COMPONENT_RE = re.compile(r"^[A-Z0-9._-]+$")


def fetch_eod_history(ticker, start, finish, token, *, retries=4):
    """Fetch one EODHD daily history without exposing the request URL."""
    query = urlencode(
        {
            "api_token": token,
            "fmt": "json",
            "from": start,
            "to": finish,
            "period": "d",
            "order": "a",
        }
    )
    url = (
        "https://eodhd.com/api/eod/"
        + quote(str(ticker), safe=".-")
        + ".US?"
        + query
    )
    for attempt in range(max(1, int(retries))):
        try:
            with urlopen(url, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError("EODHD history response must be a list")
            return payload
        except HTTPError as exc:
            if (
                exc.code not in RETRYABLE_HTTP_STATUSES
                or attempt + 1 >= max(1, int(retries))
            ):
                raise
        except (URLError, TimeoutError, json.JSONDecodeError, ValueError):
            if attempt + 1 >= max(1, int(retries)):
                raise
        time.sleep(min(8, 2**attempt))
    raise RuntimeError("unreachable retry state")


def run_backfill(
    catalog_path,
    catalog_report_path,
    raw_root,
    report_csv,
    report_json,
    report_markdown,
    *,
    token=None,
    fetcher=None,
    workers=8,
    checkpoint_every=100,
    updated_at=None,
):
    """Freeze, download, resume, audit, and report the full candidate set."""
    catalog_path = Path(catalog_path)
    catalog_report_path = Path(catalog_report_path)
    raw_root = Path(raw_root)
    report_csv = Path(report_csv)
    report_json = Path(report_json)
    report_markdown = Path(report_markdown)
    workers = max(1, int(workers))
    checkpoint_every = max(1, int(checkpoint_every))
    updated_at = updated_at or datetime.now(timezone.utc).isoformat()
    fetcher = fetcher or fetch_eod_history
    token = str(token or os.environ.get("EODHD_API_TOKEN") or "")
    if fetcher is fetch_eod_history and not token:
        raise ValueError("EODHD_API_TOKEN is required")

    catalog_bytes = catalog_path.read_bytes()
    catalog_sha256 = hashlib.sha256(catalog_bytes).hexdigest()
    catalog = json.loads(catalog_bytes)
    catalog_report = json.loads(catalog_report_path.read_text())
    _validate_catalog_report(catalog_report, catalog_sha256)
    expected_candidates = freeze_candidates(
        catalog,
        catalog_sha256,
        START_DATE,
        FINISH_DATE,
    )
    expected_count = int(
        catalog_report.get("summary", {}).get("backfill_eligible_rows", -1)
    )
    if expected_count != expected_candidates["candidate_count"]:
        raise ValueError(
            "catalog report eligible count does not match frozen candidates"
        )

    raw_root.mkdir(parents=True, exist_ok=True)
    history_root = raw_root / "histories"
    history_root.mkdir(parents=True, exist_ok=True)
    candidates_path = raw_root / "candidates.json"
    if candidates_path.exists():
        existing = _read_json(candidates_path)
        if existing != expected_candidates:
            raise ValueError(
                "existing frozen candidates do not match catalog or window"
            )
    else:
        _atomic_json(candidates_path, expected_candidates)
    candidates = expected_candidates
    securities = tuple(candidates["securities"])

    previous_errors = _load_errors(raw_root / "errors.json")
    audits = {}
    jobs = {}
    completed_network = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for row in securities:
            key = _row_key(row)
            history_path = _history_path(history_root, row)
            cached = _cached_history(history_path)
            if cached is not None:
                audits[key] = audit_history_rows(
                    row,
                    cached,
                    raw_bytes=history_path.stat().st_size,
                )
                continue
            previous = previous_errors.get(key)
            if previous is not None and not bool(previous.get("retryable")):
                audits[key] = _error_audit(row, previous)
                continue
            future = pool.submit(
                fetcher,
                row["ticker"],
                START_DATE,
                FINISH_DATE,
                token,
            )
            jobs[future] = (row, history_path)

        for future in as_completed(jobs):
            row, history_path = jobs[future]
            key = _row_key(row)
            try:
                payload = future.result()
                if not isinstance(payload, list):
                    raise ValueError("history response must be a list")
                history_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_json(history_path, payload)
                audits[key] = audit_history_rows(
                    row,
                    payload,
                    raw_bytes=history_path.stat().st_size,
                )
            except Exception as exc:  # provider boundary
                error = _error_record(row, exc)
                audits[key] = _error_audit(row, error)
            completed_network += 1
            if completed_network % checkpoint_every == 0:
                result = _checkpoint(
                    candidates,
                    securities,
                    audits,
                    raw_root,
                    report_csv,
                    report_json,
                    report_markdown,
                    updated_at,
                )
                summary = result["summary"]
                print(
                    "backfill progress "
                    f"{summary['resolved_count']}/"
                    f"{summary['candidate_count']}: "
                    f"usable={summary['usable_histories']} "
                    f"empty={summary['empty_responses']} "
                    f"permanent={summary['permanent_errors']} "
                    f"retryable={summary['retryable_errors']}",
                    flush=True,
                )

    return _checkpoint(
        candidates,
        securities,
        audits,
        raw_root,
        report_csv,
        report_json,
        report_markdown,
        updated_at,
    )


def _checkpoint(
    candidates,
    securities,
    audits,
    raw_root,
    report_csv,
    report_json,
    report_markdown,
    updated_at,
):
    ordered = []
    resolved_count = 0
    for row in securities:
        key = _row_key(row)
        audit = audits.get(key)
        if audit is None:
            audit = _pending_audit(row)
        else:
            resolved_count += 1
        ordered.append(audit)
    summary = summarize_backfill(candidates, ordered)
    summary["resolved_count"] = resolved_count
    summary["completion_status"] = (
        "complete"
        if resolved_count == len(securities)
        and summary["retryable_errors"] == 0
        else "partial"
    )
    errors = [
        _persisted_error(row)
        for row in ordered
        if row["request_status"] not in {
            "success",
            "empty",
            "not_processed",
        }
    ]
    _atomic_json(raw_root / "errors.json", errors)
    manifest = {
        "schema_version": "delisted_history_backfill_manifest_v1",
        "backfill_version": BACKFILL_VERSION,
        "updated_at": str(updated_at),
        "catalog_sha256": candidates["catalog_sha256"],
        "start_date": candidates["start_date"],
        "finish_date": candidates["finish_date"],
        "candidate_count": len(securities),
        "resolved_count": resolved_count,
        "history_files": sum(
            path.is_file()
            for path in (raw_root / "histories").glob("*/*.json")
        ),
        "error_count": len(errors),
        "completion_status": summary["completion_status"],
    }
    _atomic_json(raw_root / "manifest.json", manifest)
    result = {
        "schema_version": "delisted_history_backfill_result_v1",
        "backfill_version": BACKFILL_VERSION,
        "updated_at": str(updated_at),
        "catalog_sha256": candidates["catalog_sha256"],
        "start_date": candidates["start_date"],
        "finish_date": candidates["finish_date"],
        "summary": summary,
        "audits": tuple(ordered),
    }
    _write_reports(
        result,
        report_csv,
        report_json,
        report_markdown,
    )
    return result


def _validate_catalog_report(report, catalog_sha256):
    if not isinstance(report, dict):
        raise TypeError("catalog report must be a mapping")
    if (
        report.get("schema_version")
        != "delisted_security_purification_report_v1"
    ):
        raise ValueError("catalog report schema does not match")
    if report.get("catalog_sha256") != catalog_sha256:
        raise ValueError("catalog hash does not match purification report")
    if report.get("rule_version") != "delisted_security_purification_v1":
        raise ValueError("catalog report rule does not match")


def _history_path(history_root, row):
    exchange = str(row["exchange"]).strip().upper().replace(" ", "_")
    ticker = str(row["ticker"]).strip().upper()
    if (
        not PATH_COMPONENT_RE.fullmatch(exchange)
        or not PATH_COMPONENT_RE.fullmatch(ticker)
    ):
        raise ValueError("unsafe exchange or ticker path component")
    return history_root / exchange / f"{ticker}.json"


def _cached_history(path):
    if not path.exists():
        return None
    try:
        payload = _read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, list) else None


def _load_errors(path):
    if not path.exists():
        return {}
    try:
        rows = _read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            result[_row_key(row)] = row
        except ValueError:
            continue
    return result


def _error_record(row, exc):
    http_status = exc.code if isinstance(exc, HTTPError) else None
    retryable = (
        http_status in RETRYABLE_HTTP_STATUSES
        if http_status is not None
        else True
    )
    return {
        "ticker": row["ticker"],
        "exchange": row["exchange"],
        "request_status": (
            "http_error" if isinstance(exc, HTTPError) else "fetch_error"
        ),
        "http_status": http_status,
        "error_type": type(exc).__name__,
        "retryable": retryable,
    }


def _error_audit(row, error):
    audit = audit_history_rows(row, [], raw_bytes=0)
    audit.update(
        {
            "request_status": error["request_status"],
            "quality_status": "unavailable",
            "http_status": error.get("http_status"),
            "error_type": error.get("error_type"),
            "retryable": bool(error.get("retryable")),
        }
    )
    return audit


def _pending_audit(row):
    audit = audit_history_rows(row, [], raw_bytes=0)
    audit.update(
        {
            "request_status": "not_processed",
            "quality_status": "unavailable",
            "http_status": None,
            "error_type": None,
            "retryable": True,
        }
    )
    return audit


def _persisted_error(audit):
    return {
        "ticker": audit["ticker"],
        "exchange": audit["exchange"],
        "request_status": audit["request_status"],
        "http_status": audit.get("http_status"),
        "error_type": audit.get("error_type"),
        "retryable": bool(audit.get("retryable")),
    }


def _write_reports(result, csv_path, json_path, markdown_path):
    for path in (csv_path, json_path, markdown_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    audits = list(result["audits"])
    fieldnames = [
        "ticker",
        "name",
        "exchange",
        "request_status",
        "quality_status",
        "raw_rows",
        "valid_rows",
        "duplicate_dates",
        "invalid_rows",
        "first_date",
        "last_date",
        "post_2018_valid_rows",
        "traded_since_2018",
        "raw_bytes",
        "suspicious_security_label",
        "http_status",
        "error_type",
        "retryable",
    ]
    temporary = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(audits)
    temporary.replace(csv_path)
    report = {
        key: value
        for key, value in result.items()
        if key != "audits"
    }
    _atomic_json(json_path, report)
    _atomic_text(markdown_path, _markdown_report(report))


def _markdown_report(report):
    summary = report["summary"]
    lines = [
        "# 退市普通股历史日线正式回填",
        "",
        f"- 更新时间：{report['updated_at']}",
        f"- 固定候选：{summary['candidate_count']:,} 只",
        f"- 已解析：{summary['resolved_count']:,} 只",
        f"- 查询窗口：{report['start_date']} 至 {report['finish_date']}",
        f"- 可用历史：{summary['usable_histories']:,} 只；空响应："
        f"{summary['empty_responses']:,} 只",
        f"- 永久失败：{summary['permanent_errors']:,} 只；可重试失败/待处理："
        f"{summary['retryable_errors']:,} 只",
        f"- 有效日线：{summary['valid_rows']:,} 行；原始体积："
        f"{_human_bytes(summary['raw_bytes'])}",
        f"- 状态：{summary['completion_status']}",
        "- 边界：本阶段只保存和审计原始 JSON，不写 research_prices.db；"
        "首末交易日不等于历史指数或板块成员区间。",
        "",
        "| 交易所 | 候选 | 可用 | 空响应 | 永久失败 | 可重试 | 有效行 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary["by_exchange"]:
        lines.append(
            f"| {row['exchange']} | {row['candidate_count']:,} | "
            f"{row['usable_histories']:,} | {row['empty_responses']:,} | "
            f"{row['permanent_errors']:,} | {row['retryable_errors']:,} | "
            f"{row['valid_rows']:,} |"
        )
    lines.append("")
    return "\n".join(lines)


def _human_bytes(value):
    number = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if number < 1024.0 or unit == "TiB":
            return f"{number:.2f} {unit}"
        number /= 1024.0


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_json(path, payload):
    _atomic_text(
        Path(path),
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def _atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(str(text), encoding="utf-8")
    temporary.replace(path)


def _row_key(row):
    exchange = str(row.get("exchange") or "").strip().upper()
    ticker = str(row.get("ticker") or "").strip().upper()
    if not exchange or not ticker:
        raise ValueError("row requires exchange and ticker")
    return exchange, ticker


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Resume the purified EODHD delisted-history backfill."
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--catalog-report", required=True)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--report-csv", required=True)
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--report-markdown", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--checkpoint-every", type=int, default=100)
    return parser.parse_args()


def main():
    args = _parse_args()
    result = run_backfill(
        args.catalog,
        args.catalog_report,
        args.raw_root,
        args.report_csv,
        args.report_json,
        args.report_markdown,
        workers=args.workers,
        checkpoint_every=args.checkpoint_every,
    )
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
