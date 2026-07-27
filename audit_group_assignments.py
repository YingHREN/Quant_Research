"""Audit point-in-time group coverage for every active common stock."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sqlite3

from data.group_assignments import REVIEW_SECTOR_KEY
from web.market_groups import MARKET_GROUPS, SECTOR_ETFS


def audit_database(database_path, *, asof=None):
    """Return a deterministic, read-only assignment audit."""
    path = Path(database_path)
    connection = _readonly_connection(path)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "security_master" not in tables:
            raise ValueError("security_master table is missing")
        observation_date = (
            _normalize_date(asof)
            if asof is not None
            else _latest_observation_date(connection, tables)
        )
        active_tickers = tuple(
            row["ticker"]
            for row in connection.execute(
                """
                SELECT ticker
                FROM security_master
                WHERE active = 1 AND security_type = 'Common Stock'
                ORDER BY ticker
                """
            )
        )
        rows = (
            _assignment_rows(connection)
            if "group_assignments" in tables
            else ()
        )
    finally:
        connection.close()

    active = set(active_tickers)
    rows = tuple(row for row in rows if row["ticker"] in active)
    conflicts = _range_conflicts(rows)
    current_by_ticker = _current_assignments(rows, observation_date)
    selected = {
        ticker: candidates[-1]
        for ticker, candidates in current_by_ticker.items()
    }
    conflicts.extend(_current_conflicts(current_by_ticker))

    needs_review = sorted(
        ticker
        for ticker, row in selected.items()
        if row["sector_key"] == REVIEW_SECTOR_KEY
    )
    missing = sorted(active - selected.keys())
    invalid_benchmarks = []
    theme_counts = {}
    for ticker, row in sorted(selected.items()):
        themes, theme_benchmarks, json_conflicts = _assignment_json(row)
        conflicts.extend(json_conflicts)
        invalid_benchmarks.extend(
            _benchmark_findings(ticker, row, themes, theme_benchmarks)
        )
        for theme in themes:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        conflicts.extend(_state_conflicts(ticker, row))

    assigned = len(selected)
    total = len(active_tickers)
    result = {
        "active_common_stocks": total,
        "asof": observation_date,
        "assigned": assigned,
        "coverage": assigned / total if total else 1.0,
        "needs_review": {
            "count": len(needs_review),
            "tickers": needs_review,
        },
        "missing": {
            "count": len(missing),
            "tickers": missing,
        },
        "invalid_benchmarks": sorted(
            _sorted_unique(invalid_benchmarks),
            key=lambda finding: (
                finding["ticker"],
                0 if finding["kind"] == "theme" else 1,
                finding["group"],
            ),
        ),
        "theme_counts": dict(sorted(theme_counts.items())),
        "conflicts": _sorted_unique(conflicts),
    }
    return result


def strict_failure(result):
    """Return whether an audit result fails the publication gate."""
    return bool(
        result["coverage"] < 1.0
        or result["missing"]["count"]
        or result["invalid_benchmarks"]
        or result["conflicts"]
    )


def _readonly_connection(path):
    uri = f"{path.resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _normalize_date(value):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_asof_date") from exc


def _latest_observation_date(connection, tables):
    sources = ["SELECT MAX(observed_at) FROM security_master"]
    if "group_assignments" in tables:
        sources.append("SELECT MAX(observed_at) FROM group_assignments")
    values = [
        row[0]
        for query in sources
        for row in (connection.execute(query).fetchone(),)
        if row[0] is not None
    ]
    if not values:
        raise ValueError("audit asof is unavailable")
    return _normalize_date(max(values))


def _assignment_rows(connection):
    return tuple(
        connection.execute(
            """
            SELECT
                ticker, rule_version, effective_from, effective_to,
                sector_key, sector_benchmark, theme_keys_json,
                theme_benchmarks_json, primary_model_group,
                classification_state
            FROM group_assignments
            ORDER BY ticker, effective_from, rule_version
            """
        )
    )


def _current_assignments(rows, asof):
    current = {}
    for row in rows:
        if (
            row["effective_from"] <= asof
            and (row["effective_to"] is None or asof < row["effective_to"])
        ):
            current.setdefault(row["ticker"], []).append(row)
    for candidates in current.values():
        candidates.sort(key=lambda row: (row["effective_from"], row["rule_version"]))
    return current


def _range_conflicts(rows):
    findings = []
    by_ticker = {}
    for row in rows:
        by_ticker.setdefault(row["ticker"], []).append(row)
    for ticker, records in sorted(by_ticker.items()):
        records.sort(key=lambda row: (row["effective_from"], row["rule_version"]))
        previous = None
        for current in records:
            if previous is not None and (
                previous["effective_to"] is None
                or current["effective_from"] < previous["effective_to"]
            ):
                findings.append(
                    {
                        "kind": "overlapping_effective_ranges",
                        "ticker": ticker,
                        "previous_rule_version": previous["rule_version"],
                        "previous_effective_to": previous["effective_to"],
                        "current_rule_version": current["rule_version"],
                        "current_effective_from": current["effective_from"],
                    }
                )
            if (
                previous is None
                or previous["effective_to"] is None
                or current["effective_to"] is None
                or previous["effective_to"] < current["effective_to"]
            ):
                previous = current
    return findings


def _current_conflicts(current_by_ticker):
    return [
        {
            "kind": "multiple_effective_assignments",
            "ticker": ticker,
            "rule_versions": sorted(row["rule_version"] for row in rows),
        }
        for ticker, rows in sorted(current_by_ticker.items())
        if len(rows) > 1
    ]


def _assignment_json(row):
    ticker = row["ticker"]
    findings = []
    try:
        themes = json.loads(row["theme_keys_json"])
    except (TypeError, json.JSONDecodeError):
        themes = []
        findings.append(
            {
                "kind": "invalid_assignment_json",
                "field": "theme_keys_json",
                "ticker": ticker,
            }
        )
    try:
        benchmarks = json.loads(row["theme_benchmarks_json"])
    except (TypeError, json.JSONDecodeError):
        benchmarks = {}
        findings.append(
            {
                "kind": "invalid_assignment_json",
                "field": "theme_benchmarks_json",
                "ticker": ticker,
            }
        )
    if not isinstance(themes, list) or any(
        not isinstance(theme, str) for theme in themes
    ):
        themes = []
        findings.append(
            {
                "kind": "invalid_assignment_json",
                "field": "theme_keys_json",
                "ticker": ticker,
            }
        )
    if not isinstance(benchmarks, dict):
        benchmarks = {}
        findings.append(
            {
                "kind": "invalid_assignment_json",
                "field": "theme_benchmarks_json",
                "ticker": ticker,
            }
        )
    if len(themes) != len(set(themes)):
        findings.append({"kind": "duplicate_themes", "ticker": ticker})
    return themes, benchmarks, findings


def _benchmark_findings(ticker, row, themes, theme_benchmarks):
    findings = []
    sector = row["sector_key"]
    actual_sector_benchmark = row["sector_benchmark"]
    expected_sector_benchmark = SECTOR_ETFS.get(sector)
    if sector == REVIEW_SECTOR_KEY:
        if actual_sector_benchmark is not None:
            findings.append(
                {
                    "actual": actual_sector_benchmark,
                    "expected": None,
                    "group": sector,
                    "kind": "sector",
                    "ticker": ticker,
                }
            )
    elif sector not in SECTOR_ETFS:
        findings.append(
            {
                "actual": actual_sector_benchmark,
                "expected": None,
                "group": sector,
                "kind": "sector",
                "reason": "unknown_group",
                "ticker": ticker,
            }
        )
    elif actual_sector_benchmark != expected_sector_benchmark:
        findings.append(
            {
                "actual": actual_sector_benchmark,
                "expected": expected_sector_benchmark,
                "group": sector,
                "kind": "sector",
                "ticker": ticker,
            }
        )

    expected_themes = {
        key: list(group.benchmark_tickers)
        for key, group in MARKET_GROUPS.items()
        if key not in SECTOR_ETFS
    }
    for theme in sorted(set(themes) | set(theme_benchmarks)):
        actual = theme_benchmarks.get(theme)
        expected = expected_themes.get(theme)
        if theme not in expected_themes:
            findings.append(
                {
                    "actual": actual,
                    "expected": None,
                    "group": theme,
                    "kind": "theme",
                    "reason": "unknown_group",
                    "ticker": ticker,
                }
            )
        elif theme not in themes or actual != expected:
            findings.append(
                {
                    "actual": actual,
                    "expected": expected,
                    "group": theme,
                    "kind": "theme",
                    "ticker": ticker,
                }
            )
    return findings


def _state_conflicts(ticker, row):
    explicit_review = row["sector_key"] == REVIEW_SECTOR_KEY
    review_state = row["classification_state"] == "needs_review"
    if explicit_review == review_state:
        return []
    return [{"kind": "inconsistent_review_state", "ticker": ticker}]


def _sorted_unique(findings):
    by_json = {
        json.dumps(finding, ensure_ascii=False, sort_keys=True): finding
        for finding in findings
    }
    return [by_json[key] for key in sorted(by_json)]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit full-universe stock group assignment coverage."
    )
    parser.add_argument(
        "--database",
        default="data/research_prices.db",
        help="SQLite research database (opened read-only)",
    )
    parser.add_argument("--asof", help="Point-in-time date (YYYY-MM-DD)")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when assignment publication requirements fail",
    )
    args = parser.parse_args(argv)
    result = audit_database(args.database, asof=args.asof)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.strict and strict_failure(result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
