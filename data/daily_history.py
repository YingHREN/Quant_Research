"""Audited, non-destructive persistence for adjusted daily OHLCV history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import sqlite3

import numpy as np
import pandas as pd


PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
EIGHT_YEAR_DAYS = 8 * 365.2425


class InvalidDailyHistory(ValueError):
    """Raised before persistence when a provider frame is unsafe."""


@dataclass(frozen=True)
class DailyHistoryAudit:
    row_count: int
    duplicate_dates: int
    invalid_rows: int
    suspicious_returns: int
    long_gaps: int

    @property
    def quality_status(self):
        if self.duplicate_dates or self.invalid_rows:
            return "invalid"
        if self.suspicious_returns or self.long_gaps:
            return "warning"
        return "ok"


@dataclass(frozen=True)
class PriceCoverage:
    ticker: str
    first_date: str
    last_date: str
    source_cutoff: str
    row_count: int
    coverage_years: float
    meets_eight_year_floor: bool
    provider: str
    adjustment: str
    fetched_at: str
    revision: str
    quality_status: str
    suspicious_returns: int
    long_gaps: int


def history_start(asof: date, years: int = 10) -> date:
    """Return a calendar-year lookback, clamping leap day to February 28."""
    if not isinstance(years, int) or years <= 0:
        raise ValueError("years must be a positive integer")
    try:
        return asof.replace(year=asof.year - years)
    except ValueError:
        return asof.replace(year=asof.year - years, day=28)


def audit_history(frame: pd.DataFrame) -> DailyHistoryAudit:
    """Inspect one provider frame without mutating or silently repairing it."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return DailyHistoryAudit(0, 0, 1, 0, 0)
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans:
        return DailyHistoryAudit(len(frame), 0, len(frame), 0, 0)
    if any(column not in frame.columns for column in PRICE_COLUMNS):
        return DailyHistoryAudit(len(frame), 0, len(frame), 0, 0)

    duplicate_dates = int(frame.index.duplicated(keep="first").sum())
    numeric = frame.loc[:, PRICE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    finite = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1)
    prices = numeric.loc[:, ("Open", "High", "Low", "Close")]
    structurally_valid = (
        finite
        & (prices > 0).all(axis=1)
        & (numeric["Volume"] >= 0)
        & (numeric["High"] >= prices.max(axis=1))
        & (numeric["Low"] <= prices.min(axis=1))
    )
    invalid_rows = int((~structurally_valid).sum())

    chronological = numeric.assign(_date=frame.index).drop_duplicates(
        "_date", keep="last"
    ).sort_values("_date")
    close_returns = chronological["Close"].pct_change()
    suspicious_returns = int((close_returns.abs() > 0.50).sum())
    day_gaps = chronological["_date"].diff().dt.total_seconds().div(86_400)
    long_gaps = int((day_gaps > 10).sum())
    return DailyHistoryAudit(
        row_count=len(frame),
        duplicate_dates=duplicate_dates,
        invalid_rows=invalid_rows,
        suspicious_returns=suspicious_returns,
        long_gaps=long_gaps,
    )


def _ensure_schema(connection: sqlite3.Connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS prices(
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY(ticker, date)
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_ticker ON prices(ticker)")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS price_ingestions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            requested_start TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            source_start TEXT NOT NULL,
            source_cutoff TEXT NOT NULL,
            source_rows INTEGER NOT NULL,
            provider TEXT NOT NULL,
            adjustment TEXT NOT NULL,
            revision TEXT NOT NULL,
            quality_status TEXT NOT NULL,
            suspicious_returns INTEGER NOT NULL,
            long_gaps INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS price_coverage(
            ticker TEXT PRIMARY KEY,
            first_date TEXT NOT NULL,
            last_date TEXT NOT NULL,
            source_cutoff TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            coverage_years REAL NOT NULL,
            meets_eight_year_floor INTEGER NOT NULL,
            provider TEXT NOT NULL,
            adjustment TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            revision TEXT NOT NULL,
            quality_status TEXT NOT NULL,
            suspicious_returns INTEGER NOT NULL,
            long_gaps INTEGER NOT NULL
        )
        """
    )


def _frame_revision(frame: pd.DataFrame) -> str:
    canonical = frame.loc[:, PRICE_COLUMNS].sort_index()
    rows = [
        [
            timestamp.date().isoformat(),
            *[float(value) for value in row],
        ]
        for timestamp, row in canonical.iterrows()
    ]
    payload = json.dumps(rows, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def persist_history(
    connection: sqlite3.Connection,
    ticker: str,
    frame: pd.DataFrame,
    *,
    provider: str,
    adjustment: str,
    requested_start: date,
    fetched_at: datetime,
) -> PriceCoverage:
    """Validate and upsert one response without deleting absent historical rows."""
    audit = audit_history(frame)
    if audit.duplicate_dates:
        raise InvalidDailyHistory("provider history contains duplicate dates")
    if audit.invalid_rows or audit.row_count == 0:
        raise InvalidDailyHistory("provider history contains invalid OHLCV rows")
    if not ticker or not provider or not adjustment:
        raise ValueError("ticker, provider and adjustment are required")
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")

    normalized = frame.loc[:, PRICE_COLUMNS].astype(float).sort_index()
    revision = _frame_revision(normalized)
    fetched_iso = fetched_at.isoformat()
    source_start = normalized.index[0].date().isoformat()
    source_cutoff = normalized.index[-1].date().isoformat()
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
        for index, row in normalized.iterrows()
    ]

    _ensure_schema(connection)
    with connection:
        connection.executemany(
            """
            INSERT OR REPLACE INTO prices
                (ticker, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        first_date, last_date, row_count = connection.execute(
            """
            SELECT MIN(date), MAX(date), COUNT(*)
            FROM prices WHERE ticker=?
            """,
            (ticker,),
        ).fetchone()
        coverage_years = (
            pd.Timestamp(last_date) - pd.Timestamp(first_date)
        ).days / 365.2425
        meets_floor = coverage_years * 365.2425 >= EIGHT_YEAR_DAYS
        connection.execute(
            """
            INSERT INTO price_ingestions(
                ticker, requested_start, fetched_at, source_start, source_cutoff,
                source_rows, provider, adjustment, revision, quality_status,
                suspicious_returns, long_gaps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticker,
                requested_start.isoformat(),
                fetched_iso,
                source_start,
                source_cutoff,
                len(normalized),
                provider,
                adjustment,
                revision,
                audit.quality_status,
                audit.suspicious_returns,
                audit.long_gaps,
            ),
        )
        connection.execute(
            """
            INSERT INTO price_coverage(
                ticker, first_date, last_date, source_cutoff, row_count,
                coverage_years, meets_eight_year_floor, provider, adjustment,
                fetched_at, revision, quality_status, suspicious_returns, long_gaps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                first_date=excluded.first_date,
                last_date=excluded.last_date,
                source_cutoff=excluded.source_cutoff,
                row_count=excluded.row_count,
                coverage_years=excluded.coverage_years,
                meets_eight_year_floor=excluded.meets_eight_year_floor,
                provider=excluded.provider,
                adjustment=excluded.adjustment,
                fetched_at=excluded.fetched_at,
                revision=excluded.revision,
                quality_status=excluded.quality_status,
                suspicious_returns=excluded.suspicious_returns,
                long_gaps=excluded.long_gaps
            """,
            (
                ticker,
                first_date,
                last_date,
                source_cutoff,
                row_count,
                coverage_years,
                int(meets_floor),
                provider,
                adjustment,
                fetched_iso,
                revision,
                audit.quality_status,
                audit.suspicious_returns,
                audit.long_gaps,
            ),
        )
    return _coverage_row(
        connection.execute(
            """
            SELECT ticker, first_date, last_date, source_cutoff, row_count,
                   coverage_years, meets_eight_year_floor, provider, adjustment,
                   fetched_at, revision, quality_status, suspicious_returns, long_gaps
            FROM price_coverage WHERE ticker=?
            """,
            (ticker,),
        ).fetchone()
    )


def _coverage_row(row) -> PriceCoverage:
    return PriceCoverage(
        ticker=row[0],
        first_date=row[1],
        last_date=row[2],
        source_cutoff=row[3],
        row_count=int(row[4]),
        coverage_years=float(row[5]),
        meets_eight_year_floor=bool(row[6]),
        provider=row[7],
        adjustment=row[8],
        fetched_at=row[9],
        revision=row[10],
        quality_status=row[11],
        suspicious_returns=int(row[12]),
        long_gaps=int(row[13]),
    )


def coverage_report(connection: sqlite3.Connection) -> list[PriceCoverage]:
    _ensure_schema(connection)
    rows = connection.execute(
        """
        SELECT ticker, first_date, last_date, source_cutoff, row_count,
               coverage_years, meets_eight_year_floor, provider, adjustment,
               fetched_at, revision, quality_status, suspicious_returns, long_gaps
        FROM price_coverage ORDER BY ticker
        """
    ).fetchall()
    return [_coverage_row(row) for row in rows]


def completed_ingestions(
    connection: sqlite3.Connection,
    *,
    provider: str,
    requested_start: date,
) -> set[str]:
    """Return symbols already committed for one resumable provider window."""
    _ensure_schema(connection)
    rows = connection.execute(
        """
        SELECT DISTINCT ticker
        FROM price_ingestions
        WHERE provider=? AND requested_start=?
        """,
        (provider, requested_start.isoformat()),
    ).fetchall()
    return {str(row[0]) for row in rows}
