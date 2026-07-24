from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import sqlite3

from marketdata.base import QuoteEvent, TradeEvent


SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_capability_snapshots (
    provider TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (provider, recorded_at)
);
CREATE TABLE IF NOT EXISTS subscription_intervals (
    interval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL, symbol TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS intraday_trades (
    provider TEXT NOT NULL, symbol TEXT NOT NULL, event_ts TEXT NOT NULL,
    received_ts TEXT NOT NULL, price REAL NOT NULL, size REAL NOT NULL,
    exchange_code TEXT, conditions TEXT NOT NULL, direction TEXT NOT NULL,
    direction_source TEXT NOT NULL, source_sequence TEXT NOT NULL,
    session TEXT NOT NULL,
    PRIMARY KEY (provider, symbol, source_sequence)
);
CREATE TABLE IF NOT EXISTS intraday_quotes (
    provider TEXT NOT NULL, symbol TEXT NOT NULL, event_ts TEXT NOT NULL,
    received_ts TEXT NOT NULL, bid_price REAL NOT NULL, bid_size REAL NOT NULL,
    ask_price REAL NOT NULL, ask_size REAL NOT NULL, source_sequence TEXT NOT NULL,
    session TEXT NOT NULL,
    PRIMARY KEY (provider, symbol, source_sequence)
);
"""


class IntradayStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(self):
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    @staticmethod
    def _sequence(event):
        if event.source_sequence is not None:
            return event.source_sequence
        if isinstance(event, TradeEvent):
            payload = {
                "event_ts": event.event_ts.isoformat(),
                "price": event.price,
                "size": event.size,
                "exchange": event.exchange,
                "conditions": event.conditions,
                "direction": event.direction,
                "direction_source": event.direction_source,
                "session": event.session,
            }
        elif isinstance(event, QuoteEvent):
            payload = {
                "event_ts": event.event_ts.isoformat(),
                "bid_price": event.bid_price,
                "bid_size": event.bid_size,
                "ask_price": event.ask_price,
                "ask_size": event.ask_size,
                "session": event.session,
            }
        else:
            raise TypeError("unsupported market event")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _require_utc(value, field):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be UTC-aware")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError(f"{field} must be UTC")

    def write_event(self, event):
        with self._connect() as connection:
            before = connection.total_changes
            if isinstance(event, TradeEvent):
                connection.execute(
                    "INSERT OR IGNORE INTO intraday_trades VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (event.provider, event.symbol, event.event_ts.isoformat(),
                     event.received_ts.isoformat(), event.price, event.size,
                     event.exchange, json.dumps(event.conditions),
                     event.direction, event.direction_source,
                     self._sequence(event), event.session),
                )
            elif isinstance(event, QuoteEvent):
                connection.execute(
                    "INSERT OR IGNORE INTO intraday_quotes VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (event.provider, event.symbol, event.event_ts.isoformat(),
                     event.received_ts.isoformat(), event.bid_price, event.bid_size,
                     event.ask_price, event.ask_size,
                     self._sequence(event), event.session),
                )
            else:
                raise TypeError("unsupported market event")
            return connection.total_changes > before

    def record_capabilities(self, capabilities, recorded_at):
        self._require_utc(recorded_at, "recorded_at")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO provider_capability_snapshots VALUES (?, ?, ?)",
                (capabilities.provider, recorded_at.isoformat(),
                 json.dumps(asdict(capabilities), sort_keys=True)),
            )

    def open_subscription(self, provider, symbols, started_at):
        self._require_utc(started_at, "started_at")
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO subscription_intervals (provider, symbol, started_at, finished_at) "
                "VALUES (?, ?, ?, NULL)",
                [(provider, symbol, started_at.isoformat()) for symbol in symbols],
            )

    def close_subscription(self, provider, symbols, finished_at):
        self._require_utc(finished_at, "finished_at")
        with self._connect() as connection:
            connection.executemany(
                "UPDATE subscription_intervals SET finished_at=? "
                "WHERE provider=? AND symbol=? AND finished_at IS NULL",
                [(finished_at.isoformat(), provider, symbol) for symbol in symbols],
            )

    def status(self):
        with self._connect() as connection:
            connection.execute("BEGIN")
            capability = connection.execute(
                "SELECT payload FROM provider_capability_snapshots "
                "ORDER BY recorded_at DESC LIMIT 1"
            ).fetchone()
            symbols = connection.execute(
                "SELECT DISTINCT symbol FROM subscription_intervals "
                "WHERE finished_at IS NULL ORDER BY symbol"
            ).fetchall()
            latest_trade = connection.execute(
                "SELECT MAX(received_ts) FROM intraday_trades"
            ).fetchone()[0]
            latest_quote = connection.execute(
                "SELECT MAX(received_ts) FROM intraday_quotes"
            ).fetchone()[0]
        result = {} if capability is None else json.loads(capability[0])
        result.update({
            "subscribed_symbols": [row[0] for row in symbols],
            "last_trade_received_at": latest_trade,
            "last_quote_received_at": latest_quote,
        })
        return result
