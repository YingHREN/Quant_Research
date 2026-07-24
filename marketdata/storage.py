from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import sqlite3
from zoneinfo import ZoneInfo

from marketdata.base import (
    QuoteEvent,
    SubscriptionRequest,
    TradeCancelEvent,
    TradeCorrectionEvent,
    TradeEvent,
    _datetime_to_ns,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_capability_snapshots (
    provider TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (provider, recorded_at)
);
CREATE TABLE IF NOT EXISTS subscription_intervals (
    interval_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL, symbol TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, session_id TEXT
);
CREATE TABLE IF NOT EXISTS intraday_trades (
    provider TEXT NOT NULL, symbol TEXT NOT NULL, event_ts TEXT NOT NULL,
    event_ts_ns INTEGER NOT NULL, received_ts TEXT NOT NULL,
    trading_date TEXT NOT NULL, price REAL NOT NULL, size REAL NOT NULL,
    size_unit TEXT NOT NULL, exchange_code TEXT, conditions TEXT NOT NULL,
    direction TEXT NOT NULL, direction_source TEXT NOT NULL,
    source_sequence TEXT NOT NULL, session TEXT NOT NULL,
    PRIMARY KEY (provider, symbol, source_sequence)
);
CREATE TABLE IF NOT EXISTS intraday_quotes (
    provider TEXT NOT NULL, symbol TEXT NOT NULL, event_ts TEXT NOT NULL,
    event_ts_ns INTEGER NOT NULL, received_ts TEXT NOT NULL,
    trading_date TEXT NOT NULL, bid_price REAL NOT NULL, bid_size REAL NOT NULL,
    ask_price REAL NOT NULL, ask_size REAL NOT NULL, size_unit TEXT NOT NULL,
    lot_size INTEGER NOT NULL, source_sequence TEXT NOT NULL,
    session TEXT NOT NULL,
    PRIMARY KEY (provider, symbol, source_sequence)
);
CREATE TABLE IF NOT EXISTS intraday_trade_corrections (
    provider TEXT NOT NULL, symbol TEXT NOT NULL, event_identity TEXT NOT NULL,
    event_ts TEXT NOT NULL, event_ts_ns INTEGER NOT NULL,
    received_ts TEXT NOT NULL, trading_date TEXT NOT NULL,
    provider_trade_id TEXT NOT NULL, replacement_trade_id TEXT,
    price REAL NOT NULL, size REAL NOT NULL, size_unit TEXT NOT NULL,
    exchange_code TEXT, conditions TEXT NOT NULL, session TEXT NOT NULL,
    PRIMARY KEY (provider, symbol, event_identity)
);
CREATE TABLE IF NOT EXISTS intraday_trade_cancels (
    provider TEXT NOT NULL, symbol TEXT NOT NULL, event_identity TEXT NOT NULL,
    event_ts TEXT NOT NULL, event_ts_ns INTEGER NOT NULL,
    received_ts TEXT NOT NULL, trading_date TEXT NOT NULL,
    provider_trade_id TEXT NOT NULL, cancel_code TEXT NOT NULL,
    session TEXT NOT NULL,
    PRIMARY KEY (provider, symbol, event_identity)
);
CREATE TABLE IF NOT EXISTS collector_status (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    session_id TEXT NOT NULL, provider TEXT NOT NULL, coverage TEXT NOT NULL,
    state TEXT NOT NULL, confirmed_symbols TEXT NOT NULL,
    last_event_received_at TEXT, disconnect_count INTEGER NOT NULL,
    error TEXT, heartbeat_at TEXT NOT NULL, queue_depth INTEGER NOT NULL,
    queue_high_water INTEGER NOT NULL, dropped_event_count INTEGER NOT NULL,
    undrained_event_count INTEGER NOT NULL
);
"""


MIGRATION_COLUMNS = {
    "subscription_intervals": (
        ("session_id", "TEXT"),
    ),
    "intraday_trades": (
        ("event_ts_ns", "INTEGER"),
        ("trading_date", "TEXT"),
        ("size_unit", "TEXT"),
    ),
    "intraday_quotes": (
        ("event_ts_ns", "INTEGER"),
        ("trading_date", "TEXT"),
        ("size_unit", "TEXT"),
        ("lot_size", "INTEGER"),
    ),
}


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must be UTC-aware")
    return parsed.astimezone(timezone.utc)


def _local_date(value: datetime) -> str:
    return value.astimezone(ZoneInfo("America/New_York")).date().isoformat()


class IntradayStore:
    def __init__(self, db_path):
        self.db_path = Path(db_path)

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _connect_readonly(self):
        connection = sqlite3.connect(
            f"file:{self.db_path.resolve()}?mode=ro",
            uri=True,
        )
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def initialize(self):
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_preceding_schema(connection)

    @staticmethod
    def _migrate_preceding_schema(connection):
        added = {}
        for table, columns in MIGRATION_COLUMNS.items():
            existing = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            added[table] = set()
            for name, sql_type in columns:
                if name in existing:
                    continue
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"
                )
                added[table].add(name)

        for table in ("intraday_trades", "intraday_quotes"):
            rows = connection.execute(
                f"SELECT rowid, event_ts FROM {table} "
                "WHERE event_ts_ns IS NULL OR trading_date IS NULL"
            ).fetchall()
            for rowid, raw_timestamp in rows:
                timestamp = _parse_utc(raw_timestamp)
                connection.execute(
                    f"UPDATE {table} SET event_ts_ns=COALESCE(event_ts_ns, ?), "
                    "trading_date=COALESCE(trading_date, ?) WHERE rowid=?",
                    (_datetime_to_ns(timestamp), _local_date(timestamp), rowid),
                )

        if "lot_size" in added["intraday_quotes"]:
            connection.execute(
                "UPDATE intraday_quotes SET bid_size=bid_size * 100, "
                "ask_size=ask_size * 100, lot_size=100 "
                "WHERE provider='alpaca' AND lot_size IS NULL"
            )
            connection.execute(
                "UPDATE intraday_quotes SET lot_size=1 WHERE lot_size IS NULL"
            )
        connection.execute(
            "UPDATE intraday_trades SET size_unit='shares' "
            "WHERE size_unit IS NULL"
        )
        connection.execute(
            "UPDATE intraday_quotes SET size_unit='shares' "
            "WHERE size_unit IS NULL"
        )

    @staticmethod
    def _canonical_identity(payload):
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=lambda value: (
                value.isoformat()
                if isinstance(value, datetime)
                else str(value)
            ),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _sequence(cls, event):
        if event.source_sequence is not None:
            return event.source_sequence
        if isinstance(event, TradeEvent):
            payload = {
                "event_ts_ns": event.event_ts_ns,
                "price": event.price,
                "size": event.size,
                "size_unit": event.size_unit,
                "exchange": event.exchange,
                "conditions": event.conditions,
                "direction": event.direction,
                "direction_source": event.direction_source,
                "session": event.session,
                "trading_date": event.trading_date,
            }
        elif isinstance(event, QuoteEvent):
            payload = {
                "event_ts_ns": event.event_ts_ns,
                "bid_price": event.bid_price,
                "bid_size": event.bid_size,
                "ask_price": event.ask_price,
                "ask_size": event.ask_size,
                "size_unit": event.size_unit,
                "lot_size": event.lot_size,
                "session": event.session,
                "trading_date": event.trading_date,
            }
        else:
            raise TypeError("unsupported market event")
        return cls._canonical_identity(payload)

    @classmethod
    def _adjustment_identity(cls, event):
        payload = asdict(event)
        payload.pop("received_ts", None)
        return cls._canonical_identity(payload)

    @staticmethod
    def _require_utc(value, field):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{field} must be UTC-aware")
        if value.utcoffset().total_seconds() != 0:
            raise ValueError(f"{field} must be UTC")

    def write_event(self, event):
        return self.write_events((event,)) == 1

    def write_events(self, events):
        values = tuple(events)
        if not values:
            return 0
        with self._connect() as connection:
            before = connection.total_changes
            for event in values:
                self._insert_event(connection, event)
            return connection.total_changes - before

    def _insert_event(self, connection, event):
        if isinstance(event, TradeEvent):
            connection.execute(
                """
                INSERT OR IGNORE INTO intraday_trades (
                    provider, symbol, event_ts, event_ts_ns, received_ts,
                    trading_date, price, size, size_unit, exchange_code,
                    conditions, direction, direction_source, source_sequence,
                    session
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.provider,
                    event.symbol,
                    event.event_ts.isoformat(),
                    event.event_ts_ns,
                    event.received_ts.isoformat(),
                    event.trading_date,
                    event.price,
                    event.size,
                    event.size_unit,
                    event.exchange,
                    json.dumps(event.conditions),
                    event.direction,
                    event.direction_source,
                    self._sequence(event),
                    event.session,
                ),
            )
        elif isinstance(event, QuoteEvent):
            connection.execute(
                """
                INSERT OR IGNORE INTO intraday_quotes (
                    provider, symbol, event_ts, event_ts_ns, received_ts,
                    trading_date, bid_price, bid_size, ask_price, ask_size,
                    size_unit, lot_size, source_sequence, session
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.provider,
                    event.symbol,
                    event.event_ts.isoformat(),
                    event.event_ts_ns,
                    event.received_ts.isoformat(),
                    event.trading_date,
                    event.bid_price,
                    event.bid_size,
                    event.ask_price,
                    event.ask_size,
                    event.size_unit,
                    event.lot_size,
                    self._sequence(event),
                    event.session,
                ),
            )
        elif isinstance(event, TradeCorrectionEvent):
            connection.execute(
                """
                INSERT OR IGNORE INTO intraday_trade_corrections (
                    provider, symbol, event_identity, event_ts, event_ts_ns,
                    received_ts, trading_date, provider_trade_id,
                    replacement_trade_id, price, size, size_unit, exchange_code,
                    conditions, session
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.provider,
                    event.symbol,
                    self._adjustment_identity(event),
                    event.event_ts.isoformat(),
                    event.event_ts_ns,
                    event.received_ts.isoformat(),
                    event.trading_date,
                    event.provider_trade_id,
                    event.replacement_trade_id,
                    event.price,
                    event.size,
                    event.size_unit,
                    event.exchange,
                    json.dumps(event.conditions),
                    event.session,
                ),
            )
        elif isinstance(event, TradeCancelEvent):
            connection.execute(
                """
                INSERT OR IGNORE INTO intraday_trade_cancels (
                    provider, symbol, event_identity, event_ts, event_ts_ns,
                    received_ts, trading_date, provider_trade_id, cancel_code,
                    session
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.provider,
                    event.symbol,
                    self._adjustment_identity(event),
                    event.event_ts.isoformat(),
                    event.event_ts_ns,
                    event.received_ts.isoformat(),
                    event.trading_date,
                    event.provider_trade_id,
                    event.cancel_code,
                    event.session,
                ),
            )
        else:
            raise TypeError("unsupported market event")

    def read_effective_trades(self, provider=None, symbol=None):
        clauses = []
        parameters = []
        if provider is not None:
            clauses.append("provider=?")
            parameters.append(provider)
        if symbol is not None:
            clauses.append("symbol=?")
            parameters.append(symbol)
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            trades = connection.execute(
                "SELECT * FROM intraday_trades" + where + " ORDER BY event_ts_ns",
                parameters,
            ).fetchall()
            corrections = connection.execute(
                "SELECT * FROM intraday_trade_corrections"
                + where
                + " ORDER BY event_ts_ns",
                parameters,
            ).fetchall()
            cancels = connection.execute(
                "SELECT * FROM intraday_trade_cancels" + where,
                parameters,
            ).fetchall()

        latest_corrections = {
            (
                row["provider"],
                row["symbol"],
                row["provider_trade_id"],
            ): row
            for row in corrections
        }
        cancelled_ids = {
            (row["provider"], row["symbol"], row["provider_trade_id"])
            for row in cancels
        }
        effective = []
        for row in trades:
            source_sequence = row["source_sequence"]
            provider_trade_key = (
                row["provider"],
                row["symbol"],
                source_sequence,
            )
            if provider_trade_key in cancelled_ids:
                continue
            correction = latest_corrections.get(provider_trade_key)
            if correction is None:
                effective.append(self._trade_from_row(row))
            else:
                effective.append(
                    TradeEvent(
                        provider=row["provider"],
                        symbol=row["symbol"],
                        event_ts=_parse_utc(correction["event_ts"]),
                        received_ts=_parse_utc(correction["received_ts"]),
                        price=correction["price"],
                        size=correction["size"],
                        exchange=correction["exchange_code"],
                        conditions=tuple(json.loads(correction["conditions"])),
                        direction="unknown",
                        direction_source="unknown",
                        source_sequence=source_sequence,
                        session=correction["session"],
                        event_ts_ns=correction["event_ts_ns"],
                        trading_date=correction["trading_date"],
                        size_unit=correction["size_unit"],
                    )
                )
        return effective

    @staticmethod
    def _trade_from_row(row):
        return TradeEvent(
            provider=row["provider"],
            symbol=row["symbol"],
            event_ts=_parse_utc(row["event_ts"]),
            received_ts=_parse_utc(row["received_ts"]),
            price=row["price"],
            size=row["size"],
            exchange=row["exchange_code"],
            conditions=tuple(json.loads(row["conditions"])),
            direction=row["direction"],
            direction_source=row["direction_source"],
            source_sequence=row["source_sequence"],
            session=row["session"],
            event_ts_ns=row["event_ts_ns"],
            trading_date=row["trading_date"],
            size_unit=row["size_unit"],
        )

    def record_capabilities(self, capabilities, recorded_at):
        self._require_utc(recorded_at, "recorded_at")
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO provider_capability_snapshots "
                "VALUES (?, ?, ?)",
                (
                    capabilities.provider,
                    recorded_at.isoformat(),
                    json.dumps(asdict(capabilities), sort_keys=True),
                ),
            )

    def open_subscription(
        self,
        provider,
        symbols,
        started_at,
        session_id=None,
    ):
        self._require_utc(started_at, "started_at")
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO subscription_intervals "
                "(provider, symbol, started_at, finished_at, session_id) "
                "VALUES (?, ?, ?, NULL, ?)",
                [
                    (provider, symbol, started_at.isoformat(), session_id)
                    for symbol in symbols
                ],
            )

    def close_subscription(
        self,
        provider,
        symbols,
        finished_at,
        session_id=None,
    ):
        self._require_utc(finished_at, "finished_at")
        session_clause = (
            "" if session_id is None else " AND session_id=?"
        )
        with self._connect() as connection:
            connection.executemany(
                "UPDATE subscription_intervals SET finished_at=? "
                "WHERE provider=? AND symbol=? AND finished_at IS NULL"
                + session_clause,
                [
                    (
                        finished_at.isoformat(),
                        provider,
                        symbol,
                        *((session_id,) if session_id is not None else ()),
                    )
                    for symbol in symbols
                ],
            )

    @staticmethod
    def _never_configured_status():
        return {
            "state": "unavailable",
            "provider": None,
            "coverage": None,
            "subscribed_symbols": [],
            "last_event_received_at": None,
            "disconnect_count": 0,
            "error": "collector_not_configured",
            "heartbeat_at": None,
            "session_id": None,
            "queue_depth": 0,
            "queue_high_water": 0,
            "dropped_event_count": 0,
            "undrained_event_count": 0,
        }

    def begin_collector_session(
        self,
        *,
        session_id,
        provider,
        coverage,
        started_at,
        stale_after_seconds=30,
    ):
        self._require_utc(started_at, "started_at")
        if not str(session_id).strip():
            raise ValueError("session_id is required")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT session_id, state, heartbeat_at FROM collector_status "
                "WHERE singleton_id=1"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "UPDATE subscription_intervals SET finished_at=? "
                    "WHERE finished_at IS NULL",
                    (started_at.isoformat(),),
                )
            elif existing[0] != session_id:
                heartbeat = _parse_utc(existing[2])
                age = (started_at - heartbeat).total_seconds()
                active = existing[1] in (
                    "connecting",
                    "running",
                    "retrying",
                )
                if active and age <= stale_after_seconds:
                    raise RuntimeError("collector_session_active")
                connection.execute(
                    "UPDATE subscription_intervals SET finished_at=? "
                    "WHERE finished_at IS NULL AND "
                    "(session_id IS NULL OR session_id != ?)",
                    (started_at.isoformat(), session_id),
                )
            self._write_collector_status_connection(
                connection,
                session_id=session_id,
                provider=provider,
                coverage=coverage,
                state="connecting",
                confirmed_symbols=(),
                last_event_received_at=None,
                disconnect_count=0,
                error=None,
                heartbeat_at=started_at,
                queue_depth=0,
                queue_high_water=0,
                dropped_event_count=0,
                undrained_event_count=0,
            )

    def write_collector_status(
        self,
        *,
        session_id,
        provider,
        coverage,
        state,
        confirmed_symbols,
        last_event_received_at,
        disconnect_count,
        error,
        heartbeat_at,
        queue_depth,
        queue_high_water,
        dropped_event_count,
        undrained_event_count,
    ):
        self._require_utc(heartbeat_at, "heartbeat_at")
        if last_event_received_at is not None:
            self._require_utc(
                last_event_received_at,
                "last_event_received_at",
            )
        with self._connect() as connection:
            self._write_collector_status_connection(
                connection,
                session_id=session_id,
                provider=provider,
                coverage=coverage,
                state=state,
                confirmed_symbols=confirmed_symbols,
                last_event_received_at=last_event_received_at,
                disconnect_count=disconnect_count,
                error=error,
                heartbeat_at=heartbeat_at,
                queue_depth=queue_depth,
                queue_high_water=queue_high_water,
                dropped_event_count=dropped_event_count,
                undrained_event_count=undrained_event_count,
            )

    @staticmethod
    def _write_collector_status_connection(
        connection,
        *,
        session_id,
        provider,
        coverage,
        state,
        confirmed_symbols,
        last_event_received_at,
        disconnect_count,
        error,
        heartbeat_at,
        queue_depth,
        queue_high_water,
        dropped_event_count,
        undrained_event_count,
    ):
        symbols = SubscriptionRequest(confirmed_symbols).symbols
        connection.execute(
            """
            INSERT OR REPLACE INTO collector_status (
                singleton_id, session_id, provider, coverage, state,
                confirmed_symbols, last_event_received_at, disconnect_count,
                error, heartbeat_at, queue_depth, queue_high_water,
                dropped_event_count, undrained_event_count
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                provider,
                coverage,
                state,
                json.dumps(symbols),
                (
                    None
                    if last_event_received_at is None
                    else last_event_received_at.isoformat()
                ),
                int(disconnect_count),
                error,
                heartbeat_at.isoformat(),
                int(queue_depth),
                int(queue_high_water),
                int(dropped_event_count),
                int(undrained_event_count),
            ),
        )

    def read_collector_status(
        self,
        *,
        now=None,
        stale_after_seconds=30,
    ):
        if not self.db_path.exists():
            return self._never_configured_status()
        if now is None:
            now = datetime.now(timezone.utc)
        self._require_utc(now, "now")
        try:
            with self._connect_readonly() as connection:
                row = connection.execute(
                    """
                    SELECT session_id, provider, coverage, state,
                           confirmed_symbols, last_event_received_at,
                           disconnect_count, error, heartbeat_at, queue_depth,
                           queue_high_water, dropped_event_count,
                           undrained_event_count
                    FROM collector_status WHERE singleton_id=1
                    """
                ).fetchone()
        except sqlite3.OperationalError:
            return self._never_configured_status()
        if row is None:
            return self._never_configured_status()
        (
            session_id,
            provider,
            coverage,
            state,
            confirmed_symbols,
            last_event_received_at,
            disconnect_count,
            error,
            heartbeat_at,
            queue_depth,
            queue_high_water,
            dropped_event_count,
            undrained_event_count,
        ) = row
        heartbeat = _parse_utc(heartbeat_at)
        if state in ("connecting", "running", "retrying") and (
            now - heartbeat
        ).total_seconds() > stale_after_seconds:
            state = "stale"
            error = "collector_stale"
        return {
            "state": state,
            "provider": provider,
            "coverage": coverage,
            "subscribed_symbols": list(json.loads(confirmed_symbols)),
            "last_event_received_at": last_event_received_at,
            "disconnect_count": disconnect_count,
            "error": error,
            "heartbeat_at": heartbeat_at,
            "session_id": session_id,
            "queue_depth": queue_depth,
            "queue_high_water": queue_high_water,
            "dropped_event_count": dropped_event_count,
            "undrained_event_count": undrained_event_count,
        }

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
        result.update(
            {
                "subscribed_symbols": [row[0] for row in symbols],
                "last_trade_received_at": latest_trade,
                "last_quote_received_at": latest_quote,
            }
        )
        return result
