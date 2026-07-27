"""Persistent user overrides for the dashboard research pool."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from threading import RLock


TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


class InvalidResearchPoolTicker(ValueError):
    """Raised when a user-supplied research-pool ticker is unsafe."""


def normalize_research_pool_ticker(ticker):
    normalized = str(ticker).strip().upper()
    if not TICKER_RE.fullmatch(normalized):
        raise InvalidResearchPoolTicker(
            "Ticker must match the supported symbol format"
        )
    return normalized


class ResearchPoolMembershipStore:
    """Store explicit include/exclude choices separately from provider data."""

    def __init__(self, database_path=None):
        self.database_path = (
            None if database_path is None else Path(database_path)
        )
        self._memory = {}
        self._lock = RLock()

    def resolve(self, ticker, default=False):
        normalized = normalize_research_pool_ticker(ticker)
        with self._lock:
            state = self._read_state(normalized)
        if state is None:
            return bool(default)
        return state == "included"

    def set_membership(self, ticker, included):
        normalized = normalize_research_pool_ticker(ticker)
        state = "included" if bool(included) else "excluded"
        with self._lock:
            if self.database_path is None:
                self._memory[normalized] = state
            else:
                self.database_path.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(self.database_path) as connection:
                    _ensure_schema(connection)
                    connection.execute(
                        """
                        INSERT INTO research_pool_overrides (
                            ticker, state, updated_at
                        ) VALUES (?, ?, ?)
                        ON CONFLICT(ticker) DO UPDATE SET
                            state = excluded.state,
                            updated_at = excluded.updated_at
                        """,
                        (
                            normalized,
                            state,
                            datetime.now(timezone.utc).isoformat(),
                        ),
                    )
        return state == "included"

    def _read_state(self, ticker):
        if self.database_path is None:
            return self._memory.get(ticker)
        if not self.database_path.exists():
            return None
        with sqlite3.connect(self.database_path) as connection:
            _ensure_schema(connection)
            row = connection.execute(
                """
                SELECT state
                FROM research_pool_overrides
                WHERE ticker = ?
                """,
                (ticker,),
            ).fetchone()
        return None if row is None else str(row[0])


def apply_research_pool_membership(payload, membership_store):
    """Apply user choices without removing provider-catalog rows."""
    result = deepcopy(payload)
    rows = result.get("tickers")
    if not isinstance(rows, list):
        return result
    for row in rows:
        membership = dict(row.get("pool_membership") or {})
        catalog_member = bool(
            membership.get("research_catalog", membership.get("research"))
        )
        membership["active"] = bool(membership.get("active"))
        membership["research_catalog"] = catalog_member
        membership["research"] = membership_store.resolve(
            row.get("ticker"),
            default=False,
        )
        row["pool_membership"] = membership
    result["pool_summary"] = {
        "active_count": sum(
            bool(row["pool_membership"]["active"]) for row in rows
        ),
        "research_count": sum(
            bool(row["pool_membership"]["research"]) for row in rows
        ),
        "overlap_count": sum(
            bool(row["pool_membership"]["active"])
            and bool(row["pool_membership"]["research"])
            for row in rows
        ),
    }
    return result


def apply_stock_research_pool_membership(payload, membership_store):
    """Apply the same membership contract to a stock-detail payload."""
    result = deepcopy(payload)
    membership = dict(result.get("pool_membership") or {})
    catalog_member = bool(
        membership.get("research_catalog", membership.get("research"))
    )
    membership["active"] = bool(membership.get("active"))
    membership["research_catalog"] = catalog_member
    membership["research"] = membership_store.resolve(
        result.get("ticker"),
        default=False,
    )
    result["pool_membership"] = membership
    return result


def _ensure_schema(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS research_pool_overrides (
            ticker TEXT PRIMARY KEY,
            state TEXT NOT NULL CHECK (state IN ('included', 'excluded')),
            updated_at TEXT NOT NULL
        )
        """
    )
