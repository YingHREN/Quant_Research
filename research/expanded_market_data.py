"""Read-only adjusted OHLCV adapter for the expanded research database."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
from typing import Optional

import pandas as pd

from web.contracts import iso_date


TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class ExpandedMarketDataRepository:
    """Load the latest identity segment without mutating research storage."""

    def __init__(self, database):
        self.database = Path(database)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(
            f"{self.database.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        try:
            yield connection
        finally:
            connection.close()

    def load_universe_histories(
        self,
        *,
        asof=None,
        tickers: Optional[Sequence[str]] = None,
    ):
        checked_tickers = _validated_tickers(tickers)
        cutoff = iso_date(asof)
        query = """
            WITH latest_segments AS (
                SELECT ticker, MAX(segment_id) AS segment_id
                FROM daily_prices
                GROUP BY ticker
            )
            SELECT prices.ticker,
                   prices.date,
                   prices.adjusted_open,
                   prices.adjusted_high,
                   prices.adjusted_low,
                   prices.adjusted_close,
                   prices.volume,
                   prices.segment_id,
                   prices.provider,
                   prices.snapshot_date,
                   prices.imported_at,
                   prices.adjustment_method
            FROM daily_prices AS prices
            JOIN latest_segments AS latest
              ON latest.ticker = prices.ticker
             AND latest.segment_id = prices.segment_id
            WHERE 1 = 1
        """
        params = []
        if cutoff is not None:
            query += " AND prices.date <= ?"
            params.append(cutoff)
        if checked_tickers is not None:
            placeholders = ", ".join("?" for _ in checked_tickers)
            query += f" AND prices.ticker IN ({placeholders})"
            params.extend(checked_tickers)
        query += " ORDER BY prices.ticker, prices.date"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return _histories(rows)

    def load_classifications(
        self,
        *,
        asof=None,
        tickers: Optional[Sequence[str]] = None,
    ):
        """Return the latest point-in-time row for each ticker and taxonomy."""
        checked_tickers = _validated_tickers(tickers)
        cutoff = iso_date(asof)
        filters = []
        params = []
        if cutoff is not None:
            filters.append("asof <= ?")
            params.append(cutoff)
        if checked_tickers is not None:
            placeholders = ", ".join("?" for _ in checked_tickers)
            filters.append(f"ticker IN ({placeholders})")
            params.extend(checked_tickers)
        where = "WHERE " + " AND ".join(filters) if filters else ""
        query = f"""
            WITH ranked AS (
                SELECT ticker, taxonomy, sector_key, benchmark_ticker,
                       industry_code, industry_label, confidence, source,
                       rule_version, asof,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker, taxonomy
                           ORDER BY asof DESC, rule_version DESC
                       ) AS rank
                FROM sector_classifications
                {where}
            )
            SELECT ticker, taxonomy, sector_key, benchmark_ticker,
                   industry_code, industry_label, confidence, source,
                   rule_version, asof
            FROM ranked
            WHERE rank = 1
            ORDER BY ticker, taxonomy
        """
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        result = {}
        for (
            ticker,
            taxonomy,
            sector_key,
            benchmark_ticker,
            industry_code,
            industry_label,
            confidence,
            source,
            rule_version,
            classification_asof,
        ) in rows:
            result.setdefault(str(ticker), {})[str(taxonomy)] = {
                "sector_key": str(sector_key),
                "benchmark_ticker": (
                    None
                    if benchmark_ticker is None
                    else str(benchmark_ticker)
                ),
                "industry_code": (
                    None if industry_code is None else str(industry_code)
                ),
                "industry_label": (
                    None if industry_label is None else str(industry_label)
                ),
                "confidence": float(confidence),
                "source": str(source),
                "rule_version": str(rule_version),
                "asof": str(classification_asof),
            }
        return result


def _histories(rows):
    columns = (
        "Ticker",
        "Date",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Segment",
        "Provider",
        "SnapshotDate",
        "ImportedAt",
        "AdjustmentMethod",
    )
    frame = pd.DataFrame.from_records(rows, columns=columns)
    if frame.empty:
        return {}
    frame["Date"] = pd.to_datetime(frame["Date"])
    result = {}
    for ticker, selected in frame.groupby("Ticker", sort=True):
        selected = selected.sort_values("Date", kind="mergesort")
        metadata = selected.iloc[-1]
        history = selected.loc[
            :, ("Date", "Open", "High", "Low", "Close", "Volume")
        ].set_index("Date")
        history.index.name = "Date"
        history.attrs.update(
            {
                "segment_id": int(metadata["Segment"]),
                "provider": str(metadata["Provider"]),
                "snapshot_date": str(metadata["SnapshotDate"]),
                "imported_at": str(metadata["ImportedAt"]),
                "adjustment_method": str(metadata["AdjustmentMethod"]),
                "source_cutoff": history.index[-1].date().isoformat(),
            }
        )
        result[str(ticker)] = history
    return result


def _validated_tickers(tickers):
    if tickers is None:
        return None
    if isinstance(tickers, (str, bytes)):
        raise TypeError("tickers must be a sequence")
    checked = tuple(dict.fromkeys(str(ticker).upper() for ticker in tickers))
    if not checked:
        raise ValueError("tickers must not be empty")
    if any(not TICKER_RE.fullmatch(ticker) for ticker in checked):
        raise ValueError("tickers contain an unsupported symbol")
    return checked
