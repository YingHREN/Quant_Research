"""Read-only access to the dashboard's local price database."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

import pandas as pd

from web.contracts import iso_date


TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
INACTIVE_LAG_DAYS = 20


class InvalidTicker(ValueError):
    """Raised when a ticker cannot safely be queried."""


class UnknownTicker(LookupError):
    """Raised when a syntactically valid ticker has no local history."""


@dataclass(frozen=True)
class TickerSummary:
    ticker: str
    latest_date: str
    lag_days: int
    inactive: bool


class MarketDataRepository:
    """Repository for short-lived, read-only SQLite price queries."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)

    def _connect(self):
        return sqlite3.connect(self.db_path)

    @staticmethod
    def _validate_ticker(ticker):
        if not isinstance(ticker, str) or not TICKER_RE.fullmatch(ticker):
            raise InvalidTicker("Ticker must match the supported symbol format")

    def freshness(self):
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT latest_date AS date, COUNT(*) AS tickers
                FROM (
                    SELECT ticker AS ticker, MAX(date) AS latest_date
                    FROM prices
                    GROUP BY ticker
                )
                GROUP BY latest_date
                ORDER BY latest_date DESC
                """
            ).fetchall()
        by_date = [
            {"date": iso_date(date_value), "tickers": ticker_count}
            for date_value, ticker_count in rows
        ]
        return {"latest_date": by_date[0]["date"] if by_date else None, "by_date": by_date}

    def list_summaries(self):
        with self._connect() as connection:
            rows = connection.execute(
                """
                WITH latest AS (
                    SELECT ticker AS ticker, MAX(date) AS latest_date
                    FROM prices
                    GROUP BY ticker
                ), database_latest AS (
                    SELECT MAX(latest_date) AS latest_date FROM latest
                )
                SELECT latest.ticker AS ticker,
                       latest.latest_date AS latest_date,
                       CAST(julianday(database_latest.latest_date)
                            - julianday(latest.latest_date) AS INTEGER) AS lag_days
                FROM latest
                CROSS JOIN database_latest
                ORDER BY latest.ticker ASC
                """
            ).fetchall()
        return [
            TickerSummary(
                ticker=ticker,
                latest_date=iso_date(latest_date),
                lag_days=lag_days,
                inactive=lag_days > INACTIVE_LAG_DAYS,
            )
            for ticker, latest_date, lag_days in rows
        ]

    def load_history(self, ticker, asof=None):
        self._validate_ticker(ticker)
        asof_date = iso_date(asof)
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM prices WHERE ticker = ? LIMIT 1", (ticker,)
            ).fetchone()
            if exists is None:
                raise UnknownTicker(f"Ticker not found: {ticker}")
            query = """
                SELECT date AS Date,
                       open AS Open,
                       high AS High,
                       low AS Low,
                       close AS Close,
                       volume AS Volume
                FROM prices
                WHERE ticker = ?
            """
            params = (ticker,)
            if asof_date is not None:
                query += " AND date <= ?"
                params = (ticker, asof_date)
            query += " ORDER BY date ASC"
            frame = pd.read_sql_query(query, connection, params=params)
        frame.index = pd.to_datetime(frame.pop("Date"))
        frame.index.name = "Date"
        return frame
