"""Import and audit an immutable point-in-time universe snapshot."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3

from data.point_in_time_universe import (
    normalize_historical_components,
    normalize_symbol_changes,
)
from data.research_store import ResearchPriceStore


UNIVERSE_KEY = "sp500_historical_eodhd_v1"


def import_snapshot(
    database,
    snapshot_root,
    *,
    report_json,
    report_markdown,
    imported_at=None,
):
    """Import one validated snapshot and write annual coverage evidence."""
    database = Path(database)
    snapshot_root = Path(snapshot_root)
    report_json = Path(report_json)
    report_markdown = Path(report_markdown)
    imported_at = imported_at or datetime.now(timezone.utc).isoformat()
    manifest = json.loads((snapshot_root / "manifest.json").read_text())
    components = json.loads(
        (snapshot_root / "historical_components.json").read_text()
    )
    changes = json.loads((snapshot_root / "symbol_changes.json").read_text())
    memberships = normalize_historical_components(components)
    symbol_changes = normalize_symbol_changes(changes)
    snapshot_date = date.fromisoformat(
        str(manifest["snapshot_date"])
    ).isoformat()

    with sqlite3.connect(database) as connection:
        store = ResearchPriceStore(connection)
        store.initialize()
        store.replace_universe_memberships(
            UNIVERSE_KEY,
            memberships,
            snapshot_date=snapshot_date,
            imported_at=imported_at,
        )
        store.upsert_symbol_changes(
            symbol_changes,
            snapshot_date=snapshot_date,
            imported_at=imported_at,
        )
        coverage = [
            _coverage_row(connection, observation_date)
            for observation_date in _audit_dates(
                memberships,
                snapshot_date,
            )
        ]
        integrity = connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity check failed: {integrity}")

    report = {
        "schema_version": "point_in_time_universe_audit_v1",
        "universe_key": UNIVERSE_KEY,
        "snapshot_date": snapshot_date,
        "imported_at": str(imported_at),
        "membership_intervals": len(memberships),
        "unique_tickers": len({row.ticker for row in memberships}),
        "delisted_tickers": len(
            {row.ticker for row in memberships if row.is_delisted}
        ),
        "symbol_changes": len(symbol_changes),
        "earliest_effective_from": min(
            row.effective_from for row in memberships
        ),
        "latest_effective_from": max(
            row.effective_from for row in memberships
        ),
        "coverage_by_date": coverage,
        "integrity": integrity,
        "source_manifest": manifest,
    }
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_markdown.parent.mkdir(parents=True, exist_ok=True)
    _atomic_text(
        report_json,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_text(report_markdown, _markdown_report(report))
    return report


def _audit_dates(memberships, snapshot_date):
    first_year = max(
        2018,
        min(int(row.effective_from[:4]) for row in memberships),
    )
    snapshot = date.fromisoformat(snapshot_date)
    values = [
        f"{year}-12-31"
        for year in range(first_year, snapshot.year)
    ]
    if snapshot.month == 12 and snapshot.day == 31:
        values.append(snapshot_date)
    else:
        values.append(snapshot_date)
    return tuple(values)


def _coverage_row(connection, observation_date):
    members = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT ticker
            FROM universe_memberships
            WHERE universe_key = ?
              AND effective_from <= ?
              AND (effective_to IS NULL OR ? < effective_to)
            """,
            (UNIVERSE_KEY, observation_date, observation_date),
        )
    }
    covered = {
        str(row[0])
        for row in connection.execute(
            """
            SELECT DISTINCT memberships.ticker
            FROM universe_memberships AS memberships
            JOIN daily_prices AS prices
              ON prices.ticker = memberships.ticker
            WHERE memberships.universe_key = ?
              AND memberships.effective_from <= ?
              AND (
                    memberships.effective_to IS NULL
                    OR ? < memberships.effective_to
                  )
              AND prices.date BETWEEN date(?, '-10 day') AND ?
            """,
            (
                UNIVERSE_KEY,
                observation_date,
                observation_date,
                observation_date,
                observation_date,
            ),
        )
    }
    return {
        "observation_date": observation_date,
        "member_count": len(members),
        "price_covered": len(covered),
        "missing_price_count": len(members - covered),
        "coverage_rate": (
            None if not members else len(covered) / float(len(members))
        ),
    }


def _markdown_report(report):
    rows = [
        "# 历史点时股票池覆盖审计",
        "",
        f"- 股票池：`{report['universe_key']}`",
        f"- 数据快照：{report['snapshot_date']}",
        f"- 成员区间：{report['membership_intervals']}",
        f"- 唯一代码：{report['unique_tickers']}",
        f"- 退市代码：{report['delisted_tickers']}",
        f"- 代码变更：{report['symbol_changes']}",
        f"- SQLite 完整性：`{report['integrity']}`",
        "",
        "| 观察日期 | 有效成员 | 价格覆盖 | 缺失价格 | 覆盖率 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for item in report["coverage_by_date"]:
        rate = item["coverage_rate"]
        rows.append(
            "| "
            f"{item['observation_date']} | {item['member_count']} | "
            f"{item['price_covered']} | {item['missing_price_count']} | "
            f"{'—' if rate is None else f'{rate:.1%}'} |"
        )
    rows.extend(
        (
            "",
            "历史成员已按半开区间读取；价格缺失代码不会被当前股票替代。",
            "",
        )
    )
    return "\n".join(rows)


def _atomic_text(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="data/research_prices.db")
    parser.add_argument("--snapshot-root", required=True)
    parser.add_argument(
        "--report-json",
        default="reports/point-in-time-universe.json",
    )
    parser.add_argument(
        "--report-markdown",
        default="reports/point-in-time-universe.md",
    )
    args = parser.parse_args(argv)
    report = import_snapshot(
        args.database,
        args.snapshot_root,
        report_json=args.report_json,
        report_markdown=args.report_markdown,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
