"""Atomic conversion from the audited EODHD research store to prices.db."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import sqlite3

import pandas as pd

from data.daily_history import persist_history
from data.research_store import ADJUSTMENT_METHOD


@dataclass(frozen=True)
class EODHDRebuildSummary:
    requested: int
    imported: int
    row_count: int
    first_date: str
    last_date: str
    integrity: str


def rebuild_from_eodhd(
    research_database,
    output_database,
    *,
    tickers=None,
    fetched_at=None,
):
    """Build a complete main-format database and replace the output atomically."""
    research_path = Path(research_database)
    output_path = Path(output_database)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    fetched_at = fetched_at or datetime.now(timezone.utc)

    source = sqlite3.connect(f"file:{research_path}?mode=ro", uri=True)
    destination = None
    try:
        symbols = (
            tuple(sorted(set(tickers)))
            if tickers is not None
            else tuple(
                row[0]
                for row in source.execute(
                    "SELECT ticker FROM security_master ORDER BY ticker"
                )
            )
        )
        temporary_path.unlink(missing_ok=True)
        destination = sqlite3.connect(temporary_path)
        for ticker in symbols:
            frame = _read_current_adjusted_history(source, ticker)
            persist_history(
                destination,
                ticker,
                frame,
                provider="eodhd",
                adjustment=ADJUSTMENT_METHOD,
                requested_start=date.fromisoformat(
                    frame.index[0].date().isoformat()
                ),
                fetched_at=fetched_at,
            )
        row_count, first_date, last_date = destination.execute(
            "SELECT COUNT(*), MIN(date), MAX(date) FROM prices"
        ).fetchone()
        imported = destination.execute(
            "SELECT COUNT(DISTINCT ticker) FROM prices"
        ).fetchone()[0]
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        destination.close()
        destination = None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.replace(output_path)
        return EODHDRebuildSummary(
            requested=len(symbols),
            imported=int(imported),
            row_count=int(row_count),
            first_date=str(first_date),
            last_date=str(last_date),
            integrity=str(integrity),
        )
    finally:
        if destination is not None:
            destination.close()
        source.close()


def _read_current_adjusted_history(connection, ticker):
    rows = connection.execute(
        """
        SELECT prices.date, prices.adjusted_open, prices.adjusted_high,
               prices.adjusted_low, prices.adjusted_close, prices.volume
        FROM daily_prices AS prices
        JOIN history_segments AS segments
          ON segments.ticker = prices.ticker
         AND segments.segment_id = prices.segment_id
         AND segments.is_current_segment = 1
        WHERE prices.ticker = ?
        ORDER BY prices.date
        """,
        (ticker,),
    ).fetchall()
    frame = pd.DataFrame(
        rows,
        columns=("Date", "Open", "High", "Low", "Close", "Volume"),
    )
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame.set_index("Date")
