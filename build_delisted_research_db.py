"""Build an isolated SQLite staging database for audited delisted histories."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from data.research_store import ADJUSTMENT_METHOD, normalize_daily_rows
from research.delisted_history_staging import (
    STAGING_IMPORT_VERSION,
    STAGING_SCHEMA_VERSION,
    partition_history_rows,
)


def build_database(
    candidates_path,
    audit_csv_path,
    raw_root,
    output_path,
    report_json,
    report_markdown,
    *,
    imported_at=None,
):
    candidates_path = Path(candidates_path)
    audit_csv_path = Path(audit_csv_path)
    raw_root = Path(raw_root)
    output_path = Path(output_path)
    report_json = Path(report_json)
    report_markdown = Path(report_markdown)
    imported_at = imported_at or datetime.now(timezone.utc).isoformat()
    candidates = _read_json(candidates_path)
    manifest = _read_json(raw_root / "manifest.json")
    securities = _validate_inputs(candidates, manifest)
    audit_by_key = _read_audits(audit_csv_path)
    expected_keys = {
        (row["exchange"], row["ticker"]) for row in securities
    }
    if set(audit_by_key) != expected_keys:
        raise ValueError("audit CSV does not match frozen candidates")

    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    temporary.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(temporary)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = OFF")
    connection.execute("PRAGMA synchronous = OFF")
    connection.execute("PRAGMA temp_store = MEMORY")
    started_at = datetime.now(timezone.utc).isoformat()
    totals = Counter()
    reasons = Counter()
    try:
        _initialize(connection)
        for security in securities:
            key = (security["exchange"], security["ticker"])
            audit = audit_by_key[key]
            history_path = _history_path(raw_root, security)
            payload = _read_json(history_path)
            if not isinstance(payload, list):
                raise ValueError(f"history is not a list: {security['ticker']}")
            valid, rejected = partition_history_rows(payload)
            _validate_security_audit(security, audit, payload, valid, rejected)
            normalized, segments = normalize_daily_rows(valid)
            with connection:
                _insert_security(
                    connection,
                    security,
                    audit,
                    normalized,
                    segments,
                    rejected,
                    history_path.relative_to(raw_root).as_posix(),
                    candidates,
                    imported_at,
                )
            totals["security_count"] += 1
            totals["raw_rows"] += len(payload)
            totals["daily_rows"] += len(normalized)
            totals["rejected_rows"] += len(rejected)
            totals["empty_responses"] += audit["request_status"] == "empty"
            totals["segment_count"] += len(segments)
            reasons.update(row.reason for row in rejected)

        _create_indexes(connection)
        _validate_totals(connection, candidates, audit_by_key, totals)
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_errors:
            raise ValueError("SQLite foreign key check failed")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        completed_at = datetime.now(timezone.utc).isoformat()
        with connection:
            connection.execute(
                """
                INSERT INTO import_runs (
                    schema_version, import_version, catalog_sha256,
                    snapshot_date, imported_at, started_at, completed_at,
                    security_count, raw_row_count, daily_row_count,
                    rejected_row_count, empty_response_count, segment_count,
                    integrity_status, database_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', 0)
                """,
                (
                    STAGING_SCHEMA_VERSION,
                    STAGING_IMPORT_VERSION,
                    candidates["catalog_sha256"],
                    candidates["finish_date"],
                    imported_at,
                    started_at,
                    completed_at,
                    totals["security_count"],
                    totals["raw_rows"],
                    totals["daily_rows"],
                    totals["rejected_rows"],
                    totals["empty_responses"],
                    totals["segment_count"],
                ),
            )
            database_bytes = (
                connection.execute("PRAGMA page_count").fetchone()[0]
                * connection.execute("PRAGMA page_size").fetchone()[0]
            )
            connection.execute(
                "UPDATE import_runs SET database_bytes = ?",
                (database_bytes,),
            )
        connection.close()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(output_path)
        result = {
            "schema_version": "delisted_history_staging_import_report_v1",
            "database_schema_version": STAGING_SCHEMA_VERSION,
            "import_version": STAGING_IMPORT_VERSION,
            "catalog_sha256": candidates["catalog_sha256"],
            "snapshot_date": candidates["finish_date"],
            "imported_at": imported_at,
            "security_count": totals["security_count"],
            "raw_rows": totals["raw_rows"],
            "daily_rows": totals["daily_rows"],
            "rejected_rows": totals["rejected_rows"],
            "empty_responses": totals["empty_responses"],
            "segment_count": totals["segment_count"],
            "reason_counts": dict(sorted(reasons.items())),
            "database_bytes": output_path.stat().st_size,
            "integrity": "ok",
            "foreign_key_errors": 0,
        }
        _atomic_json(report_json, result)
        _atomic_text(report_markdown, _markdown(result))
        return result
    except Exception:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise


def _validate_inputs(candidates, manifest):
    if (
        candidates.get("schema_version")
        != "delisted_history_backfill_candidates_v1"
        or candidates.get("backfill_version")
        != "delisted_history_backfill_v1"
    ):
        raise ValueError("frozen candidate contract does not match")
    securities = candidates.get("securities")
    if not isinstance(securities, list):
        raise ValueError("frozen securities must be a list")
    if int(candidates.get("candidate_count") or -1) != len(securities):
        raise ValueError("frozen candidate count does not match")
    if (
        manifest.get("schema_version")
        != "delisted_history_backfill_manifest_v1"
        or manifest.get("catalog_sha256") != candidates.get("catalog_sha256")
        or int(manifest.get("candidate_count") or -1) != len(securities)
        or int(manifest.get("history_files") or -1) != len(securities)
        or manifest.get("completion_status") != "complete"
        or int(manifest.get("error_count") or 0) != 0
    ):
        raise ValueError("backfill manifest does not match frozen candidates")
    keys = []
    tickers = []
    for row in securities:
        exchange = str(row.get("exchange") or "").strip().upper()
        ticker = str(row.get("ticker") or "").strip().upper()
        if (
            row.get("classification") != "accepted_common"
            or row.get("backfill_eligible") is not True
            or not exchange
            or not ticker
        ):
            raise ValueError("invalid frozen security")
        keys.append((exchange, ticker))
        tickers.append(ticker)
    if len(set(keys)) != len(keys) or len(set(tickers)) != len(tickers):
        raise ValueError("duplicate frozen ticker")
    return securities


def _read_audits(path):
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    result = {}
    for row in rows:
        key = (
            str(row.get("exchange") or "").strip().upper(),
            str(row.get("ticker") or "").strip().upper(),
        )
        if not all(key) or key in result:
            raise ValueError("audit CSV contains duplicate or incomplete key")
        result[key] = row
    return result


def _validate_security_audit(security, audit, payload, valid, rejected):
    ticker = security["ticker"]
    expected_status = "empty" if not payload else "success"
    if audit.get("request_status") != expected_status:
        raise ValueError(f"audit request status mismatch: {ticker}")
    expected = {
        "raw_rows": len(payload),
        "valid_rows": len(valid),
        "rejected_rows": len(rejected),
    }
    actual_rejected = _audit_int(audit, "invalid_rows") + _audit_int(
        audit, "duplicate_dates"
    )
    if (
        _audit_int(audit, "raw_rows") != expected["raw_rows"]
        or _audit_int(audit, "valid_rows") != expected["valid_rows"]
        or actual_rejected != expected["rejected_rows"]
    ):
        raise ValueError(f"audit row counts mismatch: {ticker}")
    valid_dates = sorted(str(row["date"]) for row in valid)
    if (audit.get("first_date") or None) != (
        valid_dates[0] if valid_dates else None
    ) or (audit.get("last_date") or None) != (
        valid_dates[-1] if valid_dates else None
    ):
        raise ValueError(f"audit date range mismatch: {ticker}")


def _initialize(connection):
    connection.executescript(
        """
        CREATE TABLE security_master (
            ticker TEXT PRIMARY KEY, name TEXT NOT NULL, exchange TEXT NOT NULL,
            currency TEXT, provider_isin TEXT, identity_status TEXT NOT NULL,
            identity_key TEXT, classification TEXT NOT NULL,
            active INTEGER NOT NULL CHECK(active=0),
            is_delisted INTEGER NOT NULL CHECK(is_delisted=1),
            provider TEXT NOT NULL, catalog_sha256 TEXT NOT NULL,
            snapshot_date TEXT NOT NULL, imported_at TEXT NOT NULL
        );
        CREATE TABLE daily_prices (
            ticker TEXT NOT NULL REFERENCES security_master(ticker),
            date TEXT NOT NULL, raw_open REAL NOT NULL, raw_high REAL NOT NULL,
            raw_low REAL NOT NULL, raw_close REAL NOT NULL,
            adjusted_open REAL NOT NULL, adjusted_high REAL NOT NULL,
            adjusted_low REAL NOT NULL, adjusted_close REAL NOT NULL,
            adjustment_factor REAL NOT NULL, volume REAL NOT NULL,
            segment_id INTEGER NOT NULL, provider TEXT NOT NULL,
            snapshot_date TEXT NOT NULL, imported_at TEXT NOT NULL,
            adjustment_method TEXT NOT NULL, PRIMARY KEY(ticker,date)
        );
        CREATE TABLE history_segments (
            ticker TEXT NOT NULL REFERENCES security_master(ticker),
            segment_id INTEGER NOT NULL, first_date TEXT NOT NULL,
            last_date TEXT NOT NULL, row_count INTEGER NOT NULL,
            break_before_days INTEGER, is_current_segment INTEGER NOT NULL,
            PRIMARY KEY(ticker,segment_id)
        );
        CREATE TABLE security_audits (
            ticker TEXT PRIMARY KEY REFERENCES security_master(ticker),
            exchange TEXT NOT NULL, request_status TEXT NOT NULL,
            quality_status TEXT NOT NULL, raw_rows INTEGER NOT NULL,
            valid_rows INTEGER NOT NULL, rejected_rows INTEGER NOT NULL,
            duplicate_dates INTEGER NOT NULL, invalid_rows INTEGER NOT NULL,
            first_date TEXT, last_date TEXT, raw_bytes INTEGER NOT NULL,
            response_path TEXT NOT NULL, backfill_version TEXT NOT NULL,
            catalog_sha256 TEXT NOT NULL, imported_at TEXT NOT NULL
        );
        CREATE TABLE rejected_daily_rows (
            ticker TEXT NOT NULL REFERENCES security_master(ticker),
            source_index INTEGER NOT NULL, reason TEXT NOT NULL,
            raw_json TEXT NOT NULL, PRIMARY KEY(ticker,source_index)
        );
        CREATE TABLE import_runs (
            run_id INTEGER PRIMARY KEY, schema_version TEXT NOT NULL,
            import_version TEXT NOT NULL, catalog_sha256 TEXT NOT NULL,
            snapshot_date TEXT NOT NULL, imported_at TEXT NOT NULL,
            started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
            security_count INTEGER NOT NULL, raw_row_count INTEGER NOT NULL,
            daily_row_count INTEGER NOT NULL, rejected_row_count INTEGER NOT NULL,
            empty_response_count INTEGER NOT NULL, segment_count INTEGER NOT NULL,
            integrity_status TEXT NOT NULL, database_bytes INTEGER NOT NULL
        );
        """
    )


def _insert_security(
    connection, security, audit, normalized, segments, rejected,
    response_path, candidates, imported_at,
):
    ticker = security["ticker"]
    connection.execute(
        """
        INSERT INTO security_master VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, 'eodhd', ?, ?, ?)
        """,
        (
            ticker, security.get("name") or ticker, security["exchange"],
            security.get("currency"), security.get("provider_isin"),
            security.get("identity_status") or "ticker_only",
            security.get("identity_key"), security["classification"],
            candidates["catalog_sha256"], candidates["finish_date"], imported_at,
        ),
    )
    connection.executemany(
        """
        INSERT INTO daily_prices VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'eodhd', ?, ?, ?)
        """,
        [
            (
                ticker, row["date"], row["raw_open"], row["raw_high"],
                row["raw_low"], row["raw_close"], row["adjusted_open"],
                row["adjusted_high"], row["adjusted_low"],
                row["adjusted_close"], row["adjustment_factor"], row["volume"],
                row["segment_id"], candidates["finish_date"], imported_at,
                ADJUSTMENT_METHOD,
            )
            for row in normalized
        ],
    )
    connection.executemany(
        "INSERT INTO history_segments VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                ticker, row["segment_id"], row["first_date"], row["last_date"],
                row["row_count"], row["break_before_days"],
                int(row["is_current_segment"]),
            )
            for row in segments
        ],
    )
    connection.execute(
        """
        INSERT INTO security_audits VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticker, security["exchange"], audit["request_status"],
            audit["quality_status"], _audit_int(audit, "raw_rows"),
            _audit_int(audit, "valid_rows"), len(rejected),
            _audit_int(audit, "duplicate_dates"),
            _audit_int(audit, "invalid_rows"), audit.get("first_date") or None,
            audit.get("last_date") or None, _audit_int(audit, "raw_bytes"),
            response_path, candidates["backfill_version"],
            candidates["catalog_sha256"], imported_at,
        ),
    )
    connection.executemany(
        "INSERT INTO rejected_daily_rows VALUES (?, ?, ?, ?)",
        [
            (ticker, row.source_index, row.reason, row.raw_json)
            for row in rejected
        ],
    )


def _create_indexes(connection):
    connection.executescript(
        """
        CREATE INDEX idx_daily_prices_date ON daily_prices(date);
        CREATE INDEX idx_daily_prices_segment
            ON daily_prices(ticker,segment_id,date);
        CREATE INDEX idx_security_audits_status
            ON security_audits(request_status,quality_status);
        CREATE INDEX idx_rejected_reason ON rejected_daily_rows(reason);
        """
    )


def _validate_totals(connection, candidates, audits, totals):
    expected_raw = sum(_audit_int(row, "raw_rows") for row in audits.values())
    expected_valid = sum(
        _audit_int(row, "valid_rows") for row in audits.values()
    )
    expected_rejected = sum(
        _audit_int(row, "invalid_rows")
        + _audit_int(row, "duplicate_dates")
        for row in audits.values()
    )
    expected_empty = sum(
        row["request_status"] == "empty" for row in audits.values()
    )
    expected = (
        len(candidates["securities"]), expected_raw, expected_valid,
        expected_rejected, expected_empty,
    )
    actual = (
        totals["security_count"], totals["raw_rows"], totals["daily_rows"],
        totals["rejected_rows"], totals["empty_responses"],
    )
    if actual != expected or expected_raw != expected_valid + expected_rejected:
        raise ValueError("staging import totals do not conserve audited input")
    database_counts = (
        connection.execute("SELECT COUNT(*) FROM security_master").fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0],
        connection.execute(
            "SELECT COUNT(*) FROM rejected_daily_rows"
        ).fetchone()[0],
        connection.execute("SELECT COUNT(*) FROM security_audits").fetchone()[0],
    )
    if database_counts != (
        expected[0], expected[2], expected[3], expected[0]
    ):
        raise ValueError("database counts do not match audited input")


def _history_path(raw_root, security):
    exchange = str(security["exchange"]).replace(" ", "_")
    return raw_root / "histories" / exchange / f"{security['ticker']}.json"


def _audit_int(row, field):
    value = int(row.get(field) or 0)
    if value < 0:
        raise ValueError(f"invalid audit {field}")
    return value


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _atomic_json(path, payload):
    _atomic_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _markdown(result):
    return "\n".join(
        [
            "# 退市股票历史日线暂存研究库导入",
            "",
            f"- 证券：{result['security_count']:,}",
            f"- 有效日线：{result['daily_rows']:,}",
            f"- 拒绝行：{result['rejected_rows']:,}",
            f"- 空响应：{result['empty_responses']:,}",
            f"- 历史段：{result['segment_count']:,}",
            f"- 数据库字节：{result['database_bytes']:,}",
            f"- SQLite 完整性：{result['integrity']}",
            "- 边界：独立暂存库，不代表历史指数或板块成员关系。",
            "",
        ]
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidates",
        default=(
            "data/cache/eodhd_delisted_history_backfill/"
            "2026-07-27/candidates.json"
        ),
    )
    parser.add_argument(
        "--audit-csv", default="reports/delisted-history-backfill.csv"
    )
    parser.add_argument(
        "--raw-root",
        default="data/cache/eodhd_delisted_history_backfill/2026-07-27",
    )
    parser.add_argument(
        "--output", default="data/delisted_research_prices.db"
    )
    parser.add_argument(
        "--report-json",
        default="reports/delisted-history-staging-import.json",
    )
    parser.add_argument(
        "--report-markdown",
        default="reports/delisted-history-staging-import.md",
    )
    args = parser.parse_args()
    result = build_database(
        args.candidates,
        args.audit_csv,
        args.raw_root,
        args.output,
        args.report_json,
        args.report_markdown,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
