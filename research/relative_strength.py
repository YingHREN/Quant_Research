"""Point-in-time cross-sectional relative-strength snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd


MODEL_VERSION = "cross_sectional_rs_v1"
RETURN_WINDOWS = (63, 126, 189, 252)
MINIMUM_OBSERVATIONS = 253


def build_relative_strength_snapshot(prices, asof):
    """Build one causal 40/20/20/20 RS snapshot from adjusted closes."""
    required = {"ticker", "date", "adjusted_close"}
    if not isinstance(prices, pd.DataFrame):
        raise TypeError("prices must be a DataFrame")
    if not required.issubset(prices.columns):
        raise ValueError("prices are missing required columns")
    frame = prices.loc[:, ["ticker", "date", "adjusted_close"]].copy()
    frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    if frame["date"].dt.tz is not None:
        raise ValueError("price dates must be timezone-naive")
    if frame.duplicated(["ticker", "date"]).any():
        raise ValueError("prices contain duplicate ticker/date rows")
    cutoff = pd.Timestamp(asof).normalize()
    if cutoff.tz is not None:
        raise ValueError("asof must be timezone-naive")
    frame = frame.loc[frame["date"] <= cutoff].sort_values(
        ["ticker", "date"],
        kind="mergesort",
    )

    records = []
    for ticker, history in frame.groupby("ticker", sort=True):
        tail = history.tail(MINIMUM_OBSERVATIONS)
        if len(tail) < MINIMUM_OBSERVATIONS:
            continue
        if pd.Timestamp(tail["date"].iloc[-1]).normalize() != cutoff:
            continue
        values = pd.to_numeric(
            tail["adjusted_close"], errors="coerce"
        ).to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            continue
        latest = values[-1]
        returns = {
            window: latest / values[-(window + 1)] - 1.0
            for window in RETURN_WINDOWS
        }
        composite = (
            0.4 * returns[63]
            + 0.2 * returns[126]
            + 0.2 * returns[189]
            + 0.2 * returns[252]
        )
        records.append(
            {
                "ticker": ticker,
                "asof": cutoff.date().isoformat(),
                **{
                    f"return_{window}": returns[window]
                    for window in RETURN_WINDOWS
                },
                "composite": composite,
            }
        )

    columns = [
        "ticker",
        "asof",
        "return_63",
        "return_126",
        "return_189",
        "return_252",
        "composite",
        "rank_pct",
        "rs_rating",
        "sample_count",
        "model_version",
    ]
    if not records:
        return pd.DataFrame(columns=columns)
    result = pd.DataFrame.from_records(records)
    result["rank_pct"] = result["composite"].rank(
        method="average",
        pct=True,
    )
    result["rs_rating"] = (
        1 + 98 * result["rank_pct"]
    ).round().clip(1, 99).astype(int)
    result["sample_count"] = len(result)
    result["model_version"] = MODEL_VERSION
    return result.loc[:, columns].sort_values("ticker").reset_index(drop=True)


def persist_relative_strength_snapshot(
    database,
    snapshot,
    model_version=MODEL_VERSION,
):
    """Replace the supplied as-of/model snapshots in one SQLite transaction."""
    if not isinstance(snapshot, pd.DataFrame):
        raise TypeError("snapshot must be a DataFrame")
    required = {
        "ticker",
        "asof",
        "return_63",
        "return_126",
        "return_189",
        "return_252",
        "composite",
        "rs_rating",
        "sample_count",
    }
    if not required.issubset(snapshot.columns):
        raise ValueError("snapshot is missing required columns")
    database = Path(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).isoformat()
    rows = [
        (
            str(row.asof),
            str(row.ticker).strip().upper(),
            float(row.return_63),
            float(row.return_126),
            float(row.return_189),
            float(row.return_252),
            float(row.composite),
            int(row.rs_rating),
            int(row.sample_count),
            str(model_version),
            created_at,
        )
        for row in snapshot.itertuples(index=False)
    ]
    with sqlite3.connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS relative_strength_snapshots (
                asof TEXT NOT NULL,
                ticker TEXT NOT NULL,
                return_63 REAL NOT NULL,
                return_126 REAL NOT NULL,
                return_189 REAL NOT NULL,
                return_252 REAL NOT NULL,
                composite REAL NOT NULL,
                rs_rating INTEGER NOT NULL,
                sample_count INTEGER NOT NULL,
                model_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (asof, ticker, model_version)
            )
            """
        )
        for asof in sorted({row[0] for row in rows}):
            connection.execute(
                """
                DELETE FROM relative_strength_snapshots
                WHERE asof = ? AND model_version = ?
                """,
                (asof, model_version),
            )
        connection.executemany(
            """
            INSERT INTO relative_strength_snapshots (
                asof, ticker, return_63, return_126, return_189,
                return_252, composite, rs_rating, sample_count,
                model_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)
