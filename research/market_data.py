from __future__ import annotations

from collections.abc import Sequence
import sqlite3

import pandas as pd


def load_price_panel(db_path: str, tickers: Sequence[str]) -> dict[str, pd.DataFrame]:
    """Load requested ticker histories without filling missing observations."""
    panel: dict[str, pd.DataFrame] = {}
    with sqlite3.connect(db_path) as connection:
        for ticker in tickers:
            frame = pd.read_sql_query(
                "SELECT date,open,high,low,close,volume "
                "FROM prices WHERE ticker=? ORDER BY date",
                connection,
                params=(ticker,),
            )
            if frame.empty:
                continue
            frame["date"] = pd.to_datetime(frame["date"])
            frame = frame.set_index("date")
            frame.columns = ["Open", "High", "Low", "Close", "Volume"]
            frame.index.name = "Date"
            panel[ticker] = frame
    return panel


def bar_on(frame: pd.DataFrame, date: pd.Timestamp) -> pd.Series | None:
    """Return the bar exactly on date; never substitute an older bar."""
    timestamp = pd.Timestamp(date)
    if timestamp not in frame.index:
        return None
    return frame.loc[timestamp]


def next_bar(
    frame: pd.DataFrame, after: pd.Timestamp
) -> tuple[pd.Timestamp, pd.Series] | None:
    """Return the first actual bar strictly after the supplied date."""
    later = frame.index[frame.index > pd.Timestamp(after)]
    if len(later) == 0:
        return None
    date = pd.Timestamp(later[0])
    return date, frame.loc[date]
