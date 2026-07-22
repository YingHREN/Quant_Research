"""Read-only access to the dashboard's local price database."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3

import numpy as np
import pandas as pd

from web.contracts import iso_date


TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
INACTIVE_LAG_DAYS = 20


class InvalidTicker(ValueError):
    """Raised when a ticker cannot safely be queried."""


class UnknownTicker(LookupError):
    """Raised when a syntactically valid ticker has no local history."""


class MarketDataUnavailable(RuntimeError):
    """Raised when the local market-data database cannot be read safely."""

    def __init__(self):
        super().__init__("Market data is unavailable")


@dataclass(frozen=True)
class TickerSummary:
    ticker: str
    latest_date: str
    lag_days: int
    inactive: bool


class MarketDataRepository:
    """Repository for read-only queries and explicit per-ticker price writes."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)

    @contextmanager
    def _connect(self):
        try:
            connection = sqlite3.connect(
                f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True
            )
            try:
                yield connection
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise MarketDataUnavailable() from error

    @contextmanager
    def _connect_writable(self):
        try:
            connection = sqlite3.connect(
                f"{self.db_path.resolve().as_uri()}?mode=rw", uri=True
            )
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        except sqlite3.Error as error:
            raise MarketDataUnavailable() from error

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

    def load_universe_histories(self, asof=None):
        """Load every ticker through one date with one read-only database query."""
        asof_date = iso_date(asof)
        query = """
            SELECT ticker AS Ticker,
                   date AS Date,
                   open AS Open,
                   high AS High,
                   low AS Low,
                   close AS Close,
                   volume AS Volume
            FROM prices
        """
        params = ()
        if asof_date is not None:
            query += " WHERE date <= ?"
            params = (asof_date,)
        query += " ORDER BY ticker ASC, date ASC"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        frame = pd.DataFrame.from_records(
            rows,
            columns=("Ticker", "Date", "Open", "High", "Low", "Close", "Volume"),
        )
        if frame.empty:
            return {}

        frame["Date"] = pd.to_datetime(frame["Date"])
        histories = {}
        for ticker, ticker_frame in frame.groupby("Ticker", sort=False):
            history = ticker_frame.drop(columns="Ticker").set_index("Date")
            history.index.name = "Date"
            histories[str(ticker)] = history
        return histories

    def upsert_history(self, ticker, frame):
        """Validate and commit one ticker's OHLCV frame in one transaction."""
        self._validate_ticker(ticker)
        columns = ("Open", "High", "Low", "Close", "Volume")
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("History must be a pandas DataFrame")
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise ValueError("History must contain OHLCV columns")
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans:
            raise ValueError("History must have a valid DatetimeIndex")
        if frame.index.has_duplicates:
            raise ValueError("History dates must be unique")
        for column in columns:
            values = frame[column]
            if not pd.api.types.is_numeric_dtype(values):
                raise ValueError("History OHLCV values must be numeric")
            if not np.isfinite(values.to_numpy(dtype=float)).all():
                raise ValueError("History OHLCV values must be finite")

        rows = [
            (
                ticker,
                index.date().isoformat(),
                float(row.Open),
                float(row.High),
                float(row.Low),
                float(row.Close),
                float(row.Volume),
            )
            for index, row in frame.loc[:, columns].iterrows()
        ]
        with self._connect_writable() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO prices
                    (ticker, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
