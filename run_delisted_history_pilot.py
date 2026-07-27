"""Run a resumable, fixed-sample EODHD delisted-history pilot."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from research.delisted_history_pilot import (
    SAMPLE_VERSION,
    audit_history_rows,
    select_stratified_sample,
    summarize_pilot,
)


DEFAULT_QUOTAS = {"NASDAQ": 100, "NYSE": 100, "NYSE MKT": 50}
START_DATE = "2016-01-01"
FINISH_DATE = "2026-07-27"


def fetch_eod_history(ticker, start, finish, token, *, retries=4):
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
    for attempt in range(int(retries)):
        try:
            with urlopen(url, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError("EODHD history response must be a list")
            return payload
        except HTTPError as exc:
            if exc.code not in {429, 500, 502, 503, 504}:
                raise
            if attempt + 1 >= int(retries):
                raise
        except (URLError, TimeoutError, json.JSONDecodeError):
            if attempt + 1 >= int(retries):
                raise
        time.sleep(min(8, 2**attempt))


def run_pilot(
    catalog_path,
    *,
    raw_root,
    report_csv,
    report_json,
    report_markdown,
    quotas=None,
    token=None,
    fetcher=None,
    workers=8,
    collected_at=None,
):
    """Freeze a sample, collect or reuse histories, and render evidence."""
    catalog_path = Path(catalog_path)
    raw_root = Path(raw_root)
    report_csv = Path(report_csv)
    report_json = Path(report_json)
    report_markdown = Path(report_markdown)
    quotas = dict(quotas or DEFAULT_QUOTAS)
    collected_at = collected_at or datetime.now(timezone.utc).isoformat()
    fetcher = fetcher or fetch_eod_history
    token = str(token or os.environ.get("EODHD_API_TOKEN") or "")
    if fetcher is fetch_eod_history and not token:
        raise ValueError("EODHD_API_TOKEN is required")
    catalog = json.loads(catalog_path.read_text())
    if not isinstance(catalog, list):
        raise ValueError("delisted catalog must be a list")

    raw_root.mkdir(parents=True, exist_ok=True)
    history_root = raw_root / "histories"
    history_root.mkdir(parents=True, exist_ok=True)
    _atomic_json(raw_root / "catalog.json", catalog)
    sample_path = raw_root / "sample.json"
    if sample_path.exists():
        sample_payload = json.loads(sample_path.read_text())
        if sample_payload.get("sample_version") != SAMPLE_VERSION:
            raise ValueError("cached sample version does not match")
        if sample_payload.get("quotas") != quotas:
            raise ValueError("cached sample quotas do not match")
        sample = tuple(sample_payload["securities"])
    else:
        sample = select_stratified_sample(catalog, quotas)
        sample_payload = {
            "sample_version": SAMPLE_VERSION,
            "quotas": quotas,
            "start_date": START_DATE,
            "finish_date": FINISH_DATE,
            "securities": sample,
        }
        _atomic_json(sample_path, sample_payload)

    error_path = raw_root / "errors.json"
    previous_errors = (
        json.loads(error_path.read_text()) if error_path.exists() else []
    )
    permanent_errors = {
        str(row["ticker"]): row
        for row in previous_errors
        if not bool(row.get("retryable"))
    }
    audits = {}
    jobs = {}
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for row in sample:
            ticker = str(row["ticker"])
            history_path = history_root / f"{ticker}.json"
            cached = _cached_history(history_path)
            if cached is not None:
                audits[ticker] = audit_history_rows(
                    row,
                    cached,
                    raw_bytes=history_path.stat().st_size,
                )
                continue
            if ticker in permanent_errors:
                audits[ticker] = _error_audit(row, permanent_errors[ticker])
                continue
            future = pool.submit(
                fetcher,
                ticker,
                START_DATE,
                FINISH_DATE,
                token,
            )
            jobs[future] = (row, history_path)
        for future in as_completed(jobs):
            row, history_path = jobs[future]
            ticker = str(row["ticker"])
            try:
                payload = future.result()
                if not isinstance(payload, list):
                    raise ValueError("history response must be a list")
                _atomic_json(history_path, payload)
                audits[ticker] = audit_history_rows(
                    row,
                    payload,
                    raw_bytes=history_path.stat().st_size,
                )
            except Exception as exc:  # external provider boundary
                error = _error_record(ticker, exc)
                permanent_errors[ticker] = error
                audits[ticker] = _error_audit(row, error)

    ordered_audits = tuple(audits[str(row["ticker"])] for row in sample)
    error_rows = [
        {
            "ticker": row["ticker"],
            "request_status": row["request_status"],
            "http_status": row.get("http_status"),
            "error_type": row.get("error_type"),
            "retryable": row.get("retryable", False),
        }
        for row in ordered_audits
        if row["request_status"] not in {"success", "empty"}
    ]
    _atomic_json(error_path, error_rows)
    summary = summarize_pilot(sample, ordered_audits, catalog)
    result = {
        "schema_version": "delisted_history_pilot_report_v1",
        "sample_version": SAMPLE_VERSION,
        "collected_at": str(collected_at),
        "start_date": START_DATE,
        "finish_date": FINISH_DATE,
        "quotas": quotas,
        "sample": sample,
        "audits": ordered_audits,
        "summary": summary,
    }
    manifest = {
        "schema_version": "delisted_history_pilot_manifest_v1",
        "sample_version": SAMPLE_VERSION,
        "collected_at": str(collected_at),
        "sample_count": len(sample),
        "history_files": len(list(history_root.glob("*.json"))),
        "error_count": len(error_rows),
        "report_status": "completed",
    }
    _atomic_json(raw_root / "manifest.json", manifest)
    _write_reports(result, report_csv, report_json, report_markdown)
    return result


def _cached_history(path):
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, list) else None


def _error_record(ticker, exc):
    http_status = exc.code if isinstance(exc, HTTPError) else None
    return {
        "ticker": str(ticker),
        "request_status": (
            "http_error" if isinstance(exc, HTTPError) else "fetch_error"
        ),
        "http_status": http_status,
        "error_type": type(exc).__name__,
        "retryable": (
            http_status in {429, 500, 502, 503, 504}
            if http_status is not None
            else True
        ),
    }


def _error_audit(sample_row, error):
    audit = audit_history_rows(sample_row, [], raw_bytes=0)
    audit.update(error)
    return audit


def _write_reports(result, csv_path, json_path, markdown_path):
    for path in (csv_path, json_path, markdown_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    audits = list(result["audits"])
    temporary_csv = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with temporary_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(audits[0]) if audits else ("ticker",),
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(audits)
    temporary_csv.replace(csv_path)
    _atomic_json(json_path, result)
    _atomic_text(markdown_path, _markdown_report(result))


def _markdown_report(result):
    summary = result["summary"]
    rows = [
        "# 退市普通股历史日线分层试验",
        "",
        f"- 固定样本：{summary['sample_count']} 只",
        f"- 查询窗口：{result['start_date']} 至 {result['finish_date']}",
        "- 结论边界：交易首末日期不是指数成员区间，不写生产数据库。",
        "",
        "| 交易所 | 目录候选 | 样本 | 可用率 | 2018后交易率 | "
        "预计可回填 | 平均体积估计 | P90体积上界 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary["by_exchange"]:
        rows.append(
            "| "
            f"{item['exchange']} | {item['eligible_catalog']} | "
            f"{item['sample_count']} | {item['success_rate']:.1%} | "
            f"{item['traded_since_2018_rate']:.1%} | "
            f"{item['estimated_successful_tickers']} | "
            f"{_human_bytes(item['estimated_raw_bytes_mean'])} | "
            f"{_human_bytes(item['estimated_raw_bytes_p90'])} |"
        )
    rows.extend(
        (
            "",
            f"- 主交易所预计可回填："
            f"{summary['estimated_successful_tickers']:,} 只",
            f"- 未压缩 JSON 平均估计："
            f"{_human_bytes(summary['estimated_raw_bytes_mean'])}",
            f"- 未压缩 JSON P90 上界："
            f"{_human_bytes(summary['estimated_raw_bytes_p90'])}",
            "",
        )
    )
    return "\n".join(rows)


def _human_bytes(value):
    number = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if number < 1024.0 or unit == "TiB":
            return f"{number:.2f} {unit}"
        number /= 1024.0


def _atomic_json(path, payload):
    _atomic_text(
        path,
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )


def _atomic_text(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument(
        "--report-csv",
        default="reports/delisted-history-pilot.csv",
    )
    parser.add_argument(
        "--report-json",
        default="reports/delisted-history-pilot.json",
    )
    parser.add_argument(
        "--report-markdown",
        default="reports/delisted-history-pilot.md",
    )
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    result = run_pilot(
        args.catalog,
        raw_root=args.raw_root,
        report_csv=args.report_csv,
        report_json=args.report_json,
        report_markdown=args.report_markdown,
        workers=args.workers,
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
