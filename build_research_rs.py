"""Build a precomputed cross-sectional RS snapshot in research SQLite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

import pandas as pd

from research.relative_strength import (
    MODEL_VERSION,
    build_relative_strength_snapshot,
    persist_relative_strength_snapshot,
)


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--asof")
    return parser


def _latest_date(connection):
    row = connection.execute("SELECT MAX(date) FROM daily_prices").fetchone()
    return None if row is None else row[0]


def _load_prices(database, asof):
    uri = f"{Path(database).resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        cutoff = asof or _latest_date(connection)
        if cutoff is None:
            return None, pd.DataFrame(
                columns=["ticker", "date", "adjusted_close"]
            )
        prices = pd.read_sql_query(
            """
            WITH ranked AS (
                SELECT ticker, date, adjusted_close,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker ORDER BY date DESC
                       ) AS recency_rank
                FROM daily_prices
                WHERE date <= ?
            )
            SELECT ticker, date, adjusted_close
            FROM ranked
            WHERE recency_rank <= 253
            ORDER BY ticker, date
            """,
            connection,
            params=(cutoff,),
        )
    return cutoff, prices


def main(argv=None):
    arguments = _parser().parse_args(argv)
    try:
        asof, prices = _load_prices(arguments.database, arguments.asof)
        if asof is None:
            raise ValueError("daily_prices has no observations")
        snapshot = build_relative_strength_snapshot(prices, asof)
        count = persist_relative_strength_snapshot(
            arguments.database,
            snapshot,
            MODEL_VERSION,
        )
    except (OSError, sqlite3.Error, TypeError, ValueError):
        print(json.dumps({"status": "failed"}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "asof": str(asof),
                "model_version": MODEL_VERSION,
                "row_count": count,
                "status": "completed",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
