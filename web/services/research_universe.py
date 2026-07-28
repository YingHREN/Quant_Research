"""Bounded, read-only access to the point-in-time research universe."""

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
DEFAULT_DETAIL_SESSIONS = 260


class UnknownResearchTicker(LookupError):
    """Raised when a ticker is not a research-universe member at the cutoff."""


class InvalidResearchTicker(ValueError):
    """Raised when a research ticker cannot safely be queried."""


class ResearchUniverseDataError(RuntimeError):
    """Raised when stored research price rows violate the repository contract."""


@dataclass(frozen=True)
class ResearchUniverseMember:
    ticker: str
    latest_date: str | None
    stale: bool
    name: str | None = None
    exchange: str | None = None
    market_cap: float | None = None
    market_cap_asof: str | None = None


@dataclass(frozen=True)
class ResearchUniverseSnapshot:
    status: str
    asof: str | None
    revision: int | None
    members: tuple[ResearchUniverseMember, ...]
    histories: dict[str, pd.DataFrame]
    reason: str | None = None


@dataclass(frozen=True)
class ResearchDetailSnapshot:
    ticker: str
    asof: str
    revision: int | None
    histories: dict[str, pd.DataFrame]
    stale: bool


class ResearchUniverseRepository:
    """Read point-in-time members without loading the full research database."""

    def __init__(self, database_path):
        self.database_path = Path(database_path)

    def revision(self):
        try:
            return self.database_path.stat().st_mtime_ns
        except OSError:
            return None

    @contextmanager
    def _connect(self):
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only = ON")
            yield connection
        finally:
            connection.close()

    def snapshot(self, asof=None, sessions=260):
        revision = self.revision()
        if revision is None:
            return _unavailable_snapshot(None, revision, "database_missing")
        try:
            session_count = _validate_sessions(sessions)
            cutoff = iso_date(asof)
            with self._connect() as connection:
                observation_date = cutoff or _latest_observation_date(connection)
                if observation_date is None:
                    return _unavailable_snapshot(
                        None, revision, "no_price_observations"
                    )
                price_frame = pd.read_sql_query(
                    _SNAPSHOT_SQL,
                    connection,
                    params=(
                        observation_date,
                        observation_date,
                        observation_date,
                        session_count,
                    ),
                )
                metadata = connection.execute(
                    _MEMBER_METADATA_SQL,
                    (observation_date, observation_date),
                ).fetchall()
            histories = _histories_from_frame(price_frame)
            members = _members_from_metadata(metadata, histories, observation_date)
            return ResearchUniverseSnapshot(
                status="available",
                asof=observation_date,
                revision=revision,
                members=members,
                histories=histories,
            )
        except (OSError, sqlite3.Error, ValueError, ResearchUniverseDataError) as error:
            return _unavailable_snapshot(
                iso_date(asof), revision, _reason_for_error(error)
            )

    def load_detail_snapshot(
        self,
        ticker,
        asof=None,
        benchmark_tickers=("SPY", "QQQ"),
    ):
        selected = _validate_ticker(ticker)
        benchmarks = tuple(
            dict.fromkeys(
                _validate_ticker(value)
                for value in ("SPY", "QQQ", *tuple(benchmark_tickers))
                if str(value).strip().upper() != selected
            )
        )
        try:
            with self._connect() as connection:
                observation_date = iso_date(asof) or _latest_observation_date(connection)
                if observation_date is None or not _is_member(
                    connection, selected, observation_date
                ):
                    raise UnknownResearchTicker(
                        f"Ticker is not a research member at the cutoff: {selected}"
                    )
                requested = (selected, *benchmarks)
                placeholders = ",".join("?" for _ in requested)
                price_frame = pd.read_sql_query(
                    _DETAIL_SQL.format(placeholders=placeholders),
                    connection,
                    params=(
                        *requested,
                        observation_date,
                        DEFAULT_DETAIL_SESSIONS,
                    ),
                )
        except sqlite3.Error as error:
            raise ResearchUniverseDataError("research_database_unavailable") from error
        histories = _histories_from_frame(price_frame)
        if selected not in histories:
            raise UnknownResearchTicker(f"No research history found: {selected}")
        latest = histories[selected].index.max().date().isoformat()
        return ResearchDetailSnapshot(
            ticker=selected,
            asof=observation_date,
            revision=self.revision(),
            histories=histories,
            stale=latest != observation_date,
        )

    def load_market_cap(self, ticker, asof=None):
        """Read one ticker's effective company market cap without loading prices."""
        selected = _validate_ticker(ticker)
        try:
            with self._connect() as connection:
                observation_date = iso_date(asof) or _latest_observation_date(connection)
                if observation_date is None:
                    return None, None
                row = connection.execute(
                    """
                    SELECT market_cap, effective_from
                    FROM universe_memberships
                    WHERE ticker = ?
                      AND effective_from <= ?
                      AND (effective_to IS NULL OR ? < effective_to)
                    ORDER BY effective_from DESC
                    LIMIT 1
                    """,
                    (selected, observation_date, observation_date),
                ).fetchone()
        except sqlite3.Error as error:
            raise ResearchUniverseDataError("research_database_unavailable") from error
        if row is None:
            return None, None
        try:
            market_cap = (
                None
                if row["market_cap"] is None
                else float(row["market_cap"])
            )
        except (TypeError, ValueError):
            market_cap = None
        return market_cap, iso_date(row["effective_from"])


def _validate_sessions(sessions):
    if isinstance(sessions, bool) or not isinstance(sessions, int):
        raise ValueError("sessions must be an integer")
    if not 1 <= sessions <= 1000:
        raise ValueError("sessions must be between 1 and 1000")
    return sessions


def _validate_ticker(ticker):
    normalized = str(ticker).strip().upper()
    if not TICKER_RE.fullmatch(normalized):
        raise InvalidResearchTicker("Ticker must match the supported symbol format")
    return normalized


def _latest_observation_date(connection):
    row = connection.execute("SELECT MAX(date) FROM daily_prices").fetchone()
    return iso_date(row[0]) if row and row[0] is not None else None


def _is_member(connection, ticker, asof):
    return (
        connection.execute(
            """
            SELECT 1
            FROM universe_memberships
            WHERE ticker = ?
              AND effective_from <= ?
              AND (effective_to IS NULL OR ? < effective_to)
            LIMIT 1
            """,
            (ticker, asof, asof),
        ).fetchone()
        is not None
    )


def _histories_from_frame(frame):
    required = {"Ticker", "Date", "Open", "High", "Low", "Close", "Volume"}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns):
        raise ResearchUniverseDataError("invalid_price_schema")
    if frame.duplicated(("Ticker", "Date")).any():
        raise ResearchUniverseDataError("duplicate_price_date")
    normalized = frame.loc[
        :, ("Ticker", "Date", "Open", "High", "Low", "Close", "Volume")
    ].copy()
    try:
        normalized["Date"] = pd.to_datetime(normalized["Date"], errors="raise")
    except (TypeError, ValueError) as error:
        raise ResearchUniverseDataError("invalid_price_dates") from error
    try:
        for column in ("Open", "High", "Low", "Close", "Volume"):
            normalized[column] = pd.to_numeric(
                normalized[column],
                errors="raise",
            )
    except (TypeError, ValueError) as error:
        raise ResearchUniverseDataError("malformed_numeric_price") from error
    numeric = normalized[["Open", "High", "Low", "Close", "Volume"]]
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ResearchUniverseDataError("malformed_numeric_price")
    if (numeric[["Open", "High", "Low", "Close"]] <= 0).any().any():
        raise ResearchUniverseDataError("invalid_price_value")
    if (numeric["Volume"] < 0).any():
        raise ResearchUniverseDataError("invalid_price_value")
    histories = {}
    for ticker, ticker_frame in normalized.groupby("Ticker", sort=False):
        history = ticker_frame.drop(columns="Ticker").set_index("Date")
        history.index.name = "Date"
        if not history.index.is_monotonic_increasing:
            raise ResearchUniverseDataError("invalid_price_dates")
        histories[str(ticker)] = history
    return histories


def _members_from_metadata(rows, histories, asof):
    members = []
    for row in rows:
        ticker = str(row["ticker"])
        history = histories.get(ticker)
        latest = (
            history.index.max().date().isoformat()
            if history is not None and not history.empty
            else None
        )
        members.append(
            ResearchUniverseMember(
                ticker=ticker,
                latest_date=latest,
                stale=latest != asof,
                name=row["name"],
                exchange=row["exchange"],
                market_cap=(
                    float(row["market_cap"])
                    if row["market_cap"] is not None
                    else None
                ),
                market_cap_asof=iso_date(row["market_cap_asof"]),
            )
        )
    return tuple(members)


def _reason_for_error(error):
    if isinstance(error, ResearchUniverseDataError):
        return str(error)
    if isinstance(error, ValueError):
        return "invalid_request"
    return "database_unavailable"


def _unavailable_snapshot(asof, revision, reason):
    return ResearchUniverseSnapshot(
        status="unavailable",
        asof=asof,
        revision=revision,
        members=(),
        histories={},
        reason=reason,
    )


_SNAPSHOT_SQL = """
WITH eligible AS (
    SELECT DISTINCT ticker
    FROM universe_memberships
    WHERE effective_from <= ?
      AND (effective_to IS NULL OR ? < effective_to)
)
SELECT prices.ticker AS Ticker,
       prices.date AS Date,
       prices.adjusted_open AS Open,
       prices.adjusted_high AS High,
       prices.adjusted_low AS Low,
       prices.adjusted_close AS Close,
       prices.volume AS Volume
FROM eligible
JOIN daily_prices AS prices
  ON prices.rowid IN (
      SELECT candidate.rowid
      FROM daily_prices AS candidate
      WHERE candidate.ticker = eligible.ticker
        AND candidate.date <= ?
      ORDER BY candidate.date DESC
      LIMIT ?
  )
ORDER BY prices.ticker, prices.date
"""


_MEMBER_METADATA_SQL = """
WITH ranked_memberships AS (
    SELECT ticker,
           market_cap,
           effective_from,
           ROW_NUMBER() OVER (
               PARTITION BY ticker
               ORDER BY effective_from DESC
           ) AS membership_rank
    FROM universe_memberships
    WHERE effective_from <= ?
      AND (effective_to IS NULL OR ? < effective_to)
)
SELECT membership.ticker AS ticker,
       security_master.name AS name,
       security_master.exchange AS exchange,
       membership.market_cap AS market_cap,
       membership.effective_from AS market_cap_asof
FROM ranked_memberships AS membership
LEFT JOIN security_master ON security_master.ticker = membership.ticker
WHERE membership.membership_rank = 1
ORDER BY membership.ticker
"""


_DETAIL_SQL = """
WITH ranked AS (
    SELECT ticker AS Ticker,
           date AS Date,
           adjusted_open AS Open,
           adjusted_high AS High,
           adjusted_low AS Low,
           adjusted_close AS Close,
           volume AS Volume,
           ROW_NUMBER() OVER (
               PARTITION BY ticker
               ORDER BY date DESC
           ) AS recent_rank
    FROM daily_prices
    WHERE ticker IN ({placeholders})
      AND date <= ?
)
SELECT Ticker, Date, Open, High, Low, Close, Volume
FROM ranked
WHERE recent_rank <= ?
ORDER BY Ticker, Date
"""
