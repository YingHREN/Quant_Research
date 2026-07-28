"""Build the isolated research price database from immutable raw snapshots."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from data.group_assignments import (
    audit_assignments,
    historical_group_assignment_intervals,
)
from data.market_behavior import classify_market_behavior, write_market_behavior
from data.research_store import (
    ResearchPriceStore,
    SCHEMA_VERSION,
    load_json_list,
)
from web.market_groups import MARKET_GROUPS, REFERENCE_TICKERS, SECTOR_ETFS


def _price_history(connection, ticker, asof):
    return connection.execute(
        """
        SELECT date, adjusted_close
        FROM daily_prices
        WHERE ticker = ? AND date <= ?
          AND segment_id = (
              SELECT segment_id
              FROM history_segments
              WHERE ticker = ? AND is_current_segment = 1
          )
        ORDER BY date
        """,
        (ticker, asof, ticker),
    ).fetchall()


def _required_group_reference_tickers():
    return frozenset(
        ticker
        for group in MARKET_GROUPS.values()
        for ticker in (
            *group.benchmark_tickers,
            *group.fallback_benchmark_tickers,
        )
    )


def _validate_reference_assets():
    missing = sorted(
        _required_group_reference_tickers() - set(REFERENCE_TICKERS)
    )
    if missing:
        raise ValueError(
            "missing standard reference ETF mappings: " + ", ".join(missing)
        )


def _validate_persisted_reference_assets(connection):
    rows = connection.execute(
        """
        SELECT ticker
        FROM security_master
        WHERE security_type = 'ETF'
        """
    )
    persisted = {str(row[0]) for row in rows}
    missing = sorted(_required_group_reference_tickers() - persisted)
    if missing:
        raise ValueError(
            "missing persisted reference ETF assets: " + ", ".join(missing)
        )


def _audit_failure(audit):
    findings = {
        key: audit.get(key, [])
        for key in (
            "invalid_benchmarks",
            "invalid_benchmark_mappings",
            "duplicate_themes",
            "conflicting_assignments",
            "invalid_classification_states",
            "invalid_primary_model_groups",
        )
        if audit.get(key)
    }
    return findings or None


def _catalog_common_stock_tickers(catalog):
    tickers = tuple(
        str(security.get("ticker") or "").strip().upper()
        for security in catalog.get("securities", ())
    )
    duplicates = sorted(
        ticker for ticker in set(tickers) if tickers.count(ticker) > 1
    )
    if duplicates:
        raise ValueError(
            "duplicate catalog common-stock tickers: " + ", ".join(duplicates)
        )
    return tickers


def _assignment_evidence_start(connection, ticker):
    row = connection.execute(
        """
        SELECT COALESCE(
            (
                SELECT first_date
                FROM history_segments
                WHERE ticker = ? AND is_current_segment = 1
            ),
            (
                SELECT observed_at
                FROM security_master
                WHERE ticker = ?
            )
        )
        """,
        (ticker, ticker),
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"group assignment evidence is missing: {ticker}")
    return row[0]


def build_database(catalog_path, raw_root, output_path, *, imported_at=None):
    catalog_path = Path(catalog_path)
    raw_root = Path(raw_root)
    output_path = Path(output_path)
    imported_at = imported_at or datetime.now(timezone.utc).isoformat()
    catalog = json.loads(catalog_path.read_text())
    snapshot_date = raw_root.name
    catalog_tickers = _catalog_common_stock_tickers(catalog)
    _validate_reference_assets()
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    store = ResearchPriceStore(connection)
    store.initialize()
    summaries = []
    errors = []
    try:
        for security in catalog["securities"]:
            ticker = security["ticker"]
            try:
                summary = store.import_security(
                    security,
                    load_json_list(raw_root / f"{ticker}.json"),
                    load_json_list(raw_root / "splits" / f"{ticker}.json"),
                    load_json_list(raw_root / "dividends" / f"{ticker}.json"),
                    snapshot_date=snapshot_date,
                    imported_at=imported_at,
                    include_group_assignment=False,
                )
                summaries.append(summary)
            except Exception as exc:
                errors.append(
                    {
                        "ticker": ticker,
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
        for ticker in REFERENCE_TICKERS:
            try:
                summary = store.import_security(
                    {
                        "ticker": ticker,
                        "name": f"{ticker} reference ETF",
                        "exchange": "US",
                        "asof": catalog["asof"],
                        "selection_rule": "reference_assets_v1",
                    },
                    load_json_list(raw_root / f"{ticker}.json"),
                    load_json_list(raw_root / "splits" / f"{ticker}.json"),
                    load_json_list(raw_root / "dividends" / f"{ticker}.json"),
                    snapshot_date=snapshot_date,
                    imported_at=imported_at,
                    security_type="ETF",
                    include_membership=False,
                    include_group_assignment=False,
                )
                summaries.append(summary)
            except Exception as exc:
                errors.append(
                    {
                        "ticker": ticker,
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                )
        if errors:
            raise ValueError(
                f"{len(errors)} securities failed: "
                + json.dumps(errors[:10], ensure_ascii=False)
            )
        _validate_persisted_reference_assets(connection)

        reference_histories = {
            ticker: _price_history(connection, ticker, catalog["asof"])
            for ticker in ("SPY", *SECTOR_ETFS.values())
        }
        behavior_count = 0
        assignments = []
        with connection:
            for security in catalog["securities"]:
                ticker = security["ticker"]
                histories = dict(reference_histories)
                histories[ticker] = _price_history(
                    connection, ticker, catalog["asof"]
                )
                result = classify_market_behavior(
                    histories,
                    ticker,
                    SECTOR_ETFS,
                    sec_sector=(security.get("classification") or {}).get(
                        "sector_key"
                    ),
                    asof=catalog["asof"],
                )
                market_behavior = None
                if result is not None:
                    write_market_behavior(connection, ticker, result)
                    behavior_count += 1
                    market_behavior = {
                        "sector_key": result.sector_key,
                        "benchmark_ticker": result.benchmark_ticker,
                        "confidence": result.confidence,
                        "rule_version": result.rule_version,
                        "observed_at": result.asof,
                    }
                ticker_assignments = historical_group_assignment_intervals(
                    ticker,
                    {
                        "sec": security.get("classification") or {},
                        "market_behavior": market_behavior,
                    },
                    observed_at=catalog["asof"],
                    evidence_start=_assignment_evidence_start(
                        connection,
                        ticker,
                    ),
                )
                for assignment in ticker_assignments:
                    store.persist_group_assignment(
                        assignment,
                        observed_at=catalog["asof"],
                    )
                    assignments.append(assignment)
            assignment_audit = audit_assignments(assignments)
            audit_failure = _audit_failure(assignment_audit)
            if audit_failure:
                raise ValueError(
                    "group assignment audit failed: "
                    + json.dumps(audit_failure, ensure_ascii=False, sort_keys=True)
                )
            persisted_assignment_count = connection.execute(
                "SELECT COUNT(DISTINCT ticker) FROM group_assignments"
            ).fetchone()[0]
            if persisted_assignment_count != len(catalog_tickers):
                raise ValueError(
                    "persisted group assignment count mismatch: "
                    f"expected {len(catalog_tickers)}, got {persisted_assignment_count}"
                )
            connection.execute(
                """
                INSERT INTO import_runs
                    (schema_version, universe_key, snapshot_date, imported_at,
                     security_count, daily_row_count, error_count, errors_json)
                VALUES (?, ?, ?, ?, ?, ?, 0, '[]')
                """,
                (
                    SCHEMA_VERSION,
                    catalog["universe_key"],
                    snapshot_date,
                    imported_at,
                    len(summaries),
                    sum(summary.daily_rows for summary in summaries),
                ),
            )
        from audit_group_assignments import audit_database, strict_failure

        persisted_audit = audit_database(
            temporary,
            asof=catalog["asof"],
        )
        if strict_failure(persisted_audit):
            raise ValueError(
                "persisted group assignment audit failed: "
                + json.dumps(
                    {
                        "coverage": persisted_audit["coverage"],
                        "historical_coverage": persisted_audit[
                            "historical_coverage"
                        ],
                        "invalid_benchmarks": persisted_audit[
                            "invalid_benchmarks"
                        ],
                        "conflicts": persisted_audit["conflicts"],
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")
        counts = {
            "securities": connection.execute(
                "SELECT COUNT(*) FROM security_master"
            ).fetchone()[0],
            "daily_rows": connection.execute(
                "SELECT COUNT(*) FROM daily_prices"
            ).fetchone()[0],
            "segments": connection.execute(
                "SELECT COUNT(*) FROM history_segments"
            ).fetchone()[0],
            "splits": connection.execute(
                "SELECT COUNT(*) FROM splits"
            ).fetchone()[0],
            "dividends": connection.execute(
                "SELECT COUNT(*) FROM dividends"
            ).fetchone()[0],
            "market_behavior": behavior_count,
            "group_assignment_count": persisted_assignment_count,
            "group_assignment_review_count": assignment_audit[
                "needs_review_count"
            ],
            "group_assignment_coverage": assignment_audit["coverage"],
            "integrity": integrity,
        }
        connection.close()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary.replace(output_path)
        return counts
    except Exception:
        connection.close()
        if temporary.exists():
            temporary.unlink()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        default="data/cache/research_universe_liquid100m_v1.json",
    )
    parser.add_argument(
        "--raw-root", default="data/cache/eodhd_raw/2026-07-26"
    )
    parser.add_argument("--output", default="data/research_prices.db")
    args = parser.parse_args()
    result = build_database(args.catalog, args.raw_root, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
