# Market Data Foundation and Dynamic Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provider-neutral, point-in-time-safe intraday data foundation that can collect Alpaca free IEX trades and quotes for a stable dynamic pool of at most 30 symbols.

**Architecture:** A new `marketdata` package owns immutable event contracts, normalization, SQLite persistence, subscription selection, and the Alpaca WebSocket adapter. A collector coordinates one combined WebSocket stream, updates subscriptions only when the selected ticker or scheduled candidate set changes, and exposes a read-only status snapshot to Flask. This plan intentionally stops before order-flow scores and UI rendering; those consume the contracts created here in separate plans.

**Tech Stack:** Python 3.9, standard-library `dataclasses`/`asyncio`/`sqlite3`, installed `websockets==15.0.1`, Flask 3.1, `unittest`.

## Global Constraints

- Preserve the existing Tiingo daily-price database and its `MarketDataRepository` API.
- Use Alpaca free IEX only in this phase; always expose `coverage="iex"` and never describe it as full-market order flow.
- Keep the logical subscription pool at or below 30 unique symbols.
- Fixed symbols are `SPY`, `QQQ`, and `SOXX`; the currently selected ticker has the next priority.
- Chart hover must never mutate the subscription pool.
- Store timestamps in UTC and preserve provider timestamp plus receive timestamp.
- Persist raw normalized events before computing derived features.
- Missing credentials must leave the dashboard runnable and report a typed unavailable status.
- Do not log API secrets or authorization payloads.
- Do not install the uSMART or Webull SDK; only the provider-neutral contracts are reserved for them.
- Use strict test-first steps and one focused commit per task.

---

## File Structure

### New production files

- `marketdata/__init__.py`: public exports for provider contracts and normalized events.
- `marketdata/base.py`: event dataclasses, capability declaration, provider protocol, and subscription request.
- `marketdata/normalization.py`: payload validation, UTC timestamps, quote-mid/tick-rule direction inference.
- `marketdata/storage.py`: schema creation, idempotent event writes, subscription intervals, and status reads.
- `marketdata/subscriptions.py`: deterministic 30-symbol pool selection and change planning.
- `marketdata/alpaca.py`: authenticated combined Alpaca IEX WebSocket stream and payload conversion.
- `marketdata/collector.py`: connection lifecycle, desired-pool updates, persistence, retry, and status snapshot.
- `collect_intraday.py`: explicit CLI for starting the collector; the Flask process does not silently start network collection.
- `web/services/intraday.py`: dashboard-safe status service and unavailable fallback.

### Modified production files

- `web/app.py`: configure the intraday status service and add `GET /api/market-data/status`.
- `.env.example`: document Alpaca variables without real credentials.
- `docs/dashboard.md`: document collector startup, IEX limitation, and status endpoint.

### New tests

- `tests/test_marketdata_contracts.py`
- `tests/test_marketdata_normalization.py`
- `tests/test_marketdata_storage.py`
- `tests/test_marketdata_subscriptions.py`
- `tests/test_marketdata_alpaca.py`
- `tests/test_marketdata_collector.py`
- `tests/test_web_intraday_status.py`
- `tests/test_collect_intraday.py`

---

### Task 1: Standard Event and Provider Contracts

**Files:**
- Create: `marketdata/__init__.py`
- Create: `marketdata/base.py`
- Test: `tests/test_marketdata_contracts.py`

**Interfaces:**
- Produces: `ProviderCapabilities`, `SubscriptionRequest`, `TradeEvent`, `QuoteEvent`, `BarEvent`, `MarketEvent`, `EventSink`, and `MarketDataProvider`.
- Consumers: Tasks 2–8 import these names without redefining event fields.

- [ ] **Step 1: Write the failing contract tests**

```python
# tests/test_marketdata_contracts.py
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import unittest

from marketdata.base import (
    ProviderCapabilities,
    QuoteEvent,
    SubscriptionRequest,
    TradeEvent,
)


UTC = timezone.utc


class MarketDataContractsTest(unittest.TestCase):
    def test_subscription_normalizes_deduplicates_and_rejects_more_than_limit(self):
        request = SubscriptionRequest(("spy", "AMD", "SPY"), max_symbols=30)
        self.assertEqual(request.symbols, ("SPY", "AMD"))
        with self.assertRaisesRegex(ValueError, "at most 2"):
            SubscriptionRequest(("A", "B", "C"), max_symbols=2)

    def test_events_are_immutable_and_require_utc_timestamps(self):
        event = TradeEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
            received_ts=datetime(2026, 7, 24, 14, 30, 1, tzinfo=UTC),
            price=150.25,
            size=100.0,
            exchange="V",
            conditions=("@",),
            direction="unknown",
            direction_source="unknown",
            source_sequence="42",
            session="regular",
        )
        with self.assertRaises(FrozenInstanceError):
            event.price = 1.0
        with self.assertRaisesRegex(ValueError, "UTC"):
            QuoteEvent(
                provider="alpaca",
                symbol="AMD",
                event_ts=datetime(2026, 7, 24, 14, 30),
                received_ts=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
                bid_price=150.0,
                bid_size=10.0,
                ask_price=150.1,
                ask_size=12.0,
                source_sequence=None,
                session="regular",
            )

    def test_capabilities_serialize_without_credentials(self):
        value = ProviderCapabilities(
            provider="alpaca",
            coverage="iex",
            realtime=True,
            max_symbols=30,
            historical_trades=False,
            historical_quotes=False,
            historical_bars=True,
            provider_direction=False,
            order_book_depth=False,
            unavailable_reason=None,
        )
        self.assertEqual(value.to_dict()["coverage"], "iex")
        self.assertNotIn("api_key", value.to_dict())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and verify the import failure**

Run: `./venv/bin/python -m unittest tests.test_marketdata_contracts -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'marketdata'`.

- [ ] **Step 3: Implement immutable contracts and validation**

```python
# marketdata/base.py
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Awaitable, Callable, Protocol, Sequence, Tuple, Union


DIRECTIONS = frozenset(("buy", "sell", "neutral", "unknown"))
DIRECTION_SOURCES = frozenset(("provider", "quote_mid", "tick_rule", "unknown"))
SESSIONS = frozenset(("pre", "regular", "post", "unknown"))


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be UTC-aware")
    if value.utcoffset().total_seconds() != 0:
        raise ValueError(f"{field} must be UTC")


def _symbol(value: str) -> str:
    normalized = str(value).strip().upper()
    if not normalized or len(normalized) > 10:
        raise ValueError("symbol is invalid")
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for character in normalized):
        raise ValueError("symbol is invalid")
    return normalized


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    coverage: str
    realtime: bool
    max_symbols: int
    historical_trades: bool
    historical_quotes: bool
    historical_bars: bool
    provider_direction: bool
    order_book_depth: bool
    unavailable_reason: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SubscriptionRequest:
    symbols: Tuple[str, ...]
    max_symbols: int = 30

    def __init__(self, symbols: Sequence[str], max_symbols: int = 30):
        normalized = tuple(dict.fromkeys(_symbol(value) for value in symbols))
        if len(normalized) > max_symbols:
            raise ValueError(f"subscription supports at most {max_symbols} symbols")
        object.__setattr__(self, "symbols", normalized)
        object.__setattr__(self, "max_symbols", max_symbols)


@dataclass(frozen=True)
class TradeEvent:
    provider: str
    symbol: str
    event_ts: datetime
    received_ts: datetime
    price: float
    size: float
    exchange: str | None
    conditions: Tuple[str, ...]
    direction: str
    direction_source: str
    source_sequence: str | None
    session: str

    def __post_init__(self):
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.received_ts, "received_ts")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if self.price <= 0 or self.size <= 0:
            raise ValueError("trade price and size must be positive")
        if self.direction not in DIRECTIONS or self.direction_source not in DIRECTION_SOURCES:
            raise ValueError("trade direction metadata is invalid")
        if self.session not in SESSIONS:
            raise ValueError("session is invalid")


@dataclass(frozen=True)
class QuoteEvent:
    provider: str
    symbol: str
    event_ts: datetime
    received_ts: datetime
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    source_sequence: str | None
    session: str

    def __post_init__(self):
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.received_ts, "received_ts")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        if min(self.bid_price, self.ask_price) <= 0 or min(self.bid_size, self.ask_size) < 0:
            raise ValueError("quote values are invalid")
        if self.bid_price > self.ask_price:
            raise ValueError("crossed quote is invalid")
        if self.session not in SESSIONS:
            raise ValueError("session is invalid")


@dataclass(frozen=True)
class BarEvent:
    provider: str
    symbol: str
    start_ts: datetime
    end_ts: datetime
    received_ts: datetime
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int
    vwap: float | None
    locally_aggregated: bool


MarketEvent = Union[TradeEvent, QuoteEvent, BarEvent]
EventSink = Callable[[MarketEvent], Awaitable[None]]


class MarketDataProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    async def stream_events(self, request: SubscriptionRequest, emit: EventSink) -> None: ...
    async def update_subscription(self, request: SubscriptionRequest) -> None: ...
    async def close(self) -> None: ...
```

```python
# marketdata/__init__.py
from marketdata.base import (
    BarEvent,
    EventSink,
    MarketDataProvider,
    MarketEvent,
    ProviderCapabilities,
    QuoteEvent,
    SubscriptionRequest,
    TradeEvent,
)

__all__ = [
    "BarEvent",
    "EventSink",
    "MarketDataProvider",
    "MarketEvent",
    "ProviderCapabilities",
    "QuoteEvent",
    "SubscriptionRequest",
    "TradeEvent",
]
```

- [ ] **Step 4: Run the contract tests**

Run: `./venv/bin/python -m unittest tests.test_marketdata_contracts -v`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add marketdata/__init__.py marketdata/base.py tests/test_marketdata_contracts.py
git commit -m "feat: define intraday market data contracts"
```

---

### Task 2: Normalize Alpaca Events and Infer Trade Direction

**Files:**
- Create: `marketdata/normalization.py`
- Test: `tests/test_marketdata_normalization.py`

**Interfaces:**
- Consumes: `TradeEvent` and `QuoteEvent` from Task 1.
- Produces: `AlpacaEventNormalizer.ingest(payload: dict, received_ts: datetime) -> MarketEvent | None`.
- State: latest valid quote and previous trade price are tracked per symbol.

- [ ] **Step 1: Write failing tests for quote-mid, tick-rule, and invalid payloads**

```python
# tests/test_marketdata_normalization.py
from datetime import datetime, timezone
import unittest

from marketdata.base import QuoteEvent, TradeEvent
from marketdata.normalization import AlpacaEventNormalizer


UTC = timezone.utc
NOW = datetime(2026, 7, 24, 14, 30, 1, tzinfo=UTC)


class AlpacaEventNormalizerTest(unittest.TestCase):
    def setUp(self):
        self.normalizer = AlpacaEventNormalizer()

    def test_quote_then_trade_uses_contemporaneous_midpoint(self):
        quote = self.normalizer.ingest(
            {"T": "q", "S": "AMD", "t": "2026-07-24T14:30:00Z",
             "bp": 150.00, "bs": 10, "ap": 150.10, "as": 12},
            NOW,
        )
        trade = self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:00.5Z",
             "p": 150.10, "s": 100, "x": "V", "c": ["@"], "i": 42},
            NOW,
        )
        self.assertIsInstance(quote, QuoteEvent)
        self.assertIsInstance(trade, TradeEvent)
        self.assertEqual((trade.direction, trade.direction_source), ("buy", "quote_mid"))

    def test_midpoint_trade_falls_back_to_tick_rule(self):
        for price in (100.0, 100.5):
            trade = self.normalizer.ingest(
                {"T": "t", "S": "AAA", "t": "2026-07-24T14:30:00Z",
                 "p": price, "s": 1, "i": str(price)},
                NOW,
            )
        self.assertEqual((trade.direction, trade.direction_source), ("buy", "tick_rule"))

    def test_unknown_message_and_crossed_quote_are_dropped_with_reason(self):
        self.assertIsNone(self.normalizer.ingest({"T": "success"}, NOW))
        self.assertIsNone(
            self.normalizer.ingest(
                {"T": "q", "S": "AMD", "t": "2026-07-24T14:30:00Z",
                 "bp": 151, "bs": 1, "ap": 150, "as": 1},
                NOW,
            )
        )
        self.assertEqual(self.normalizer.drop_counts["invalid_quote"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests and verify failure**

Run: `./venv/bin/python -m unittest tests.test_marketdata_normalization -v`
Expected: FAIL because `marketdata.normalization` does not exist.

- [ ] **Step 3: Implement normalization and inference**

Implement `marketdata/normalization.py` with these exact rules:

```python
from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from marketdata.base import QuoteEvent, TradeEvent


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def _session(timestamp: datetime) -> str:
    local_time = timestamp.astimezone(ZoneInfo("America/New_York")).time()
    if time(4, 0) <= local_time < time(9, 30):
        return "pre"
    if time(9, 30) <= local_time < time(16, 0):
        return "regular"
    if time(16, 0) <= local_time < time(20, 0):
        return "post"
    return "unknown"


class AlpacaEventNormalizer:
    def __init__(self):
        self._quotes = {}
        self._previous_trade = {}
        self.drop_counts = Counter()

    def ingest(self, payload, received_ts):
        message_type = payload.get("T")
        if message_type == "q":
            return self._quote(payload, received_ts)
        if message_type == "t":
            return self._trade(payload, received_ts)
        return None

    def _quote(self, payload, received_ts):
        try:
            quote = QuoteEvent(
                provider="alpaca",
                symbol=payload["S"],
                event_ts=_timestamp(payload["t"]),
                received_ts=received_ts.astimezone(timezone.utc),
                bid_price=float(payload["bp"]),
                bid_size=float(payload["bs"]),
                ask_price=float(payload["ap"]),
                ask_size=float(payload["as"]),
                source_sequence=None if payload.get("i") is None else str(payload["i"]),
                session=_session(_timestamp(payload["t"])),
            )
        except (KeyError, TypeError, ValueError):
            self.drop_counts["invalid_quote"] += 1
            return None
        self._quotes[quote.symbol] = quote
        return quote

    def _trade(self, payload, received_ts):
        try:
            symbol = str(payload["S"]).upper()
            price = float(payload["p"])
            previous = self._previous_trade.get(symbol)
            quote = self._quotes.get(symbol)
            direction, source = self._direction(price, quote, previous)
            event_ts = _timestamp(payload["t"])
            trade = TradeEvent(
                provider="alpaca",
                symbol=symbol,
                event_ts=event_ts,
                received_ts=received_ts.astimezone(timezone.utc),
                price=price,
                size=float(payload["s"]),
                exchange=payload.get("x"),
                conditions=tuple(str(value) for value in payload.get("c", ())),
                direction=direction,
                direction_source=source,
                source_sequence=None if payload.get("i") is None else str(payload["i"]),
                session=_session(event_ts),
            )
        except (KeyError, TypeError, ValueError):
            self.drop_counts["invalid_trade"] += 1
            return None
        self._previous_trade[symbol] = price
        return trade

    @staticmethod
    def _direction(price, quote, previous):
        if quote is not None:
            midpoint = (quote.bid_price + quote.ask_price) / 2.0
            if price > midpoint:
                return "buy", "quote_mid"
            if price < midpoint:
                return "sell", "quote_mid"
        if previous is not None and price > previous:
            return "buy", "tick_rule"
        if previous is not None and price < previous:
            return "sell", "tick_rule"
        return "unknown", "unknown"
```

- [ ] **Step 4: Run normalization and contract tests**

Run: `./venv/bin/python -m unittest tests.test_marketdata_contracts tests.test_marketdata_normalization -v`
Expected: 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add marketdata/normalization.py tests/test_marketdata_normalization.py
git commit -m "feat: normalize intraday trades and quotes"
```

---

### Task 3: Persist Raw Events and Subscription Coverage

**Files:**
- Create: `marketdata/storage.py`
- Test: `tests/test_marketdata_storage.py`

**Interfaces:**
- Consumes: `TradeEvent`, `QuoteEvent`, and `ProviderCapabilities`.
- Produces: `IntradayStore.initialize()`, `write_event(event) -> bool`, `record_capabilities(value)`, `open_subscription(provider, symbols, started_at)`, `close_subscription(provider, symbols, finished_at)`, and `status() -> dict`.
- Guarantee: duplicate provider sequences are idempotent; no existing `prices` table is changed.

- [ ] **Step 1: Write failing SQLite tests**

```python
# tests/test_marketdata_storage.py
from datetime import datetime, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from marketdata.base import ProviderCapabilities, TradeEvent
from marketdata.storage import IntradayStore


UTC = timezone.utc
AT = datetime(2026, 7, 24, 14, 30, tzinfo=UTC)


def trade(sequence="1"):
    return TradeEvent(
        provider="alpaca", symbol="AMD", event_ts=AT, received_ts=AT,
        price=150.0, size=100.0, exchange="V", conditions=("@",),
        direction="buy", direction_source="quote_mid",
        source_sequence=sequence, session="regular",
    )


class IntradayStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "market.db"
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE prices (ticker TEXT)")
        self.store = IntradayStore(self.path)
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_event_is_idempotent_and_prices_table_survives(self):
        self.assertTrue(self.store.write_event(trade()))
        self.assertFalse(self.store.write_event(trade()))
        with sqlite3.connect(self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM intraday_trades").fetchone()[0]
            prices = connection.execute(
                "SELECT name FROM sqlite_master WHERE name='prices'"
            ).fetchone()
        self.assertEqual(count, 1)
        self.assertIsNotNone(prices)

    def test_status_reports_capability_and_subscription_coverage(self):
        capability = ProviderCapabilities(
            "alpaca", "iex", True, 30, False, False, True, False, False, None
        )
        self.store.record_capabilities(capability, AT)
        self.store.open_subscription("alpaca", ("SPY", "AMD"), AT)
        status = self.store.status()
        self.assertEqual(status["provider"], "alpaca")
        self.assertEqual(status["coverage"], "iex")
        self.assertEqual(status["subscribed_symbols"], ["AMD", "SPY"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `./venv/bin/python -m unittest tests.test_marketdata_storage -v`
Expected: FAIL because `marketdata.storage` does not exist.

- [ ] **Step 3: Implement schema and transactional writes**

Create `marketdata/storage.py` with:

```python
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import sqlite3

from marketdata.base import QuoteEvent, TradeEvent


SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_capability_snapshots (
    provider TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (provider, recorded_at)
);
CREATE TABLE IF NOT EXISTS subscription_intervals (
    provider TEXT NOT NULL, symbol TEXT NOT NULL, started_at TEXT NOT NULL,
    finished_at TEXT, PRIMARY KEY (provider, symbol, started_at)
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
        return "|".join(
            (event.event_ts.isoformat(), str(getattr(event, "price", "")),
             str(getattr(event, "size", "")), str(getattr(event, "bid_price", "")),
             str(getattr(event, "ask_price", "")))
        )

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
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO provider_capability_snapshots VALUES (?, ?, ?)",
                (capabilities.provider, recorded_at.isoformat(),
                 json.dumps(asdict(capabilities), sort_keys=True)),
            )

    def open_subscription(self, provider, symbols, started_at):
        with self._connect() as connection:
            connection.executemany(
                "INSERT OR IGNORE INTO subscription_intervals VALUES (?, ?, ?, NULL)",
                [(provider, symbol, started_at.isoformat()) for symbol in symbols],
            )

    def close_subscription(self, provider, symbols, finished_at):
        with self._connect() as connection:
            connection.executemany(
                "UPDATE subscription_intervals SET finished_at=? "
                "WHERE provider=? AND symbol=? AND finished_at IS NULL",
                [(finished_at.isoformat(), provider, symbol) for symbol in symbols],
            )

    def status(self):
        with self._connect() as connection:
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
```

- [ ] **Step 4: Run storage tests**

Run: `./venv/bin/python -m unittest tests.test_marketdata_storage -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add marketdata/storage.py tests/test_marketdata_storage.py
git commit -m "feat: persist normalized intraday events"
```

---

### Task 4: Deterministic Dynamic Subscription Pool

**Files:**
- Create: `marketdata/subscriptions.py`
- Test: `tests/test_marketdata_subscriptions.py`

**Interfaces:**
- Produces: `build_pool(selected, peers, candidates, fixed=("SPY","QQQ","SOXX"), limit=30) -> tuple[str, ...]`.
- Produces: `plan_change(current, desired) -> SubscriptionChange` with `subscribe` ordered before `unsubscribe`.
- Constraint: no function accepts hover time or chart coordinates.

- [ ] **Step 1: Write failing priority and change-plan tests**

```python
# tests/test_marketdata_subscriptions.py
import unittest

from marketdata.subscriptions import build_pool, plan_change


class DynamicSubscriptionPoolTest(unittest.TestCase):
    def test_priority_deduplication_and_limit(self):
        peers = [f"P{number}" for number in range(1, 25)]
        candidates = ["AMD", "P1"] + [f"C{number}" for number in range(1, 20)]
        pool = build_pool("amd", peers, candidates, limit=30)
        self.assertEqual(pool[:4], ("SPY", "QQQ", "SOXX", "AMD"))
        self.assertEqual(len(pool), 30)
        self.assertEqual(len(set(pool)), 30)
        self.assertEqual(pool[4:20], tuple(f"P{number}" for number in range(1, 17)))

    def test_change_subscribes_new_symbols_before_unsubscribing_old(self):
        change = plan_change(("SPY", "QQQ", "SOXX", "AMD"),
                             ("SPY", "QQQ", "SOXX", "NVDA"))
        self.assertEqual(change.subscribe, ("NVDA",))
        self.assertEqual(change.unsubscribe, ("AMD",))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `./venv/bin/python -m unittest tests.test_marketdata_subscriptions -v`
Expected: FAIL because `marketdata.subscriptions` does not exist.

- [ ] **Step 3: Implement pure pool selection**

```python
# marketdata/subscriptions.py
from dataclasses import dataclass

from marketdata.base import SubscriptionRequest


@dataclass(frozen=True)
class SubscriptionChange:
    subscribe: tuple[str, ...]
    unsubscribe: tuple[str, ...]


def build_pool(selected, peers, candidates, fixed=("SPY", "QQQ", "SOXX"), limit=30):
    ordered = []

    def append_unique(values, group_limit):
        added = 0
        for value in values:
            normalized = SubscriptionRequest((value,), max_symbols=1).symbols[0]
            if normalized in ordered:
                continue
            ordered.append(normalized)
            added += 1
            if added == group_limit or len(ordered) == limit:
                break

    append_unique(fixed, len(fixed))
    if selected and len(ordered) < limit:
        append_unique((selected,), 1)
    if len(ordered) < limit:
        append_unique(peers, 16)
    if len(ordered) < limit:
        append_unique(candidates, 10)
    return tuple(ordered[:limit])


def plan_change(current, desired):
    current_set = set(current)
    desired_set = set(desired)
    return SubscriptionChange(
        subscribe=tuple(symbol for symbol in desired if symbol not in current_set),
        unsubscribe=tuple(symbol for symbol in current if symbol not in desired_set),
    )
```

- [ ] **Step 4: Run tests**

Run: `./venv/bin/python -m unittest tests.test_marketdata_subscriptions -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add marketdata/subscriptions.py tests/test_marketdata_subscriptions.py
git commit -m "feat: select dynamic intraday symbol pool"
```

---

### Task 5: Alpaca IEX WebSocket Adapter

**Files:**
- Create: `marketdata/alpaca.py`
- Test: `tests/test_marketdata_alpaca.py`

**Interfaces:**
- Consumes: `SubscriptionRequest`, `AlpacaEventNormalizer`, and async `EventSink`.
- Produces: `AlpacaIEXProvider(api_key, api_secret, connect=None)`.
- Uses one combined stream for trades and quotes at `wss://stream.data.alpaca.markets/v2/iex`.
- `update_subscription()` sends subscribe additions first and unsubscribe removals second.

- [ ] **Step 1: Write failing protocol tests with a fake socket**

```python
# tests/test_marketdata_alpaca.py
import asyncio
import json
import unittest

from marketdata.alpaca import AlpacaIEXProvider
from marketdata.base import SubscriptionRequest, TradeEvent


class FakeSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    async def send(self, value):
        self.sent.append(json.loads(value))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return json.dumps(next(self.messages))
        except StopIteration:
            raise StopAsyncIteration


class FakeConnection:
    def __init__(self, socket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *_args):
        return False


class AlpacaIEXProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_auth_subscribe_normalize_and_emit(self):
        socket = FakeSocket([
            [{"T": "success", "msg": "authenticated"}],
            [{"T": "subscription", "trades": ["AMD"], "quotes": ["AMD"]}],
            [{"T": "t", "S": "AMD", "t": "2026-07-24T14:30:00Z",
              "p": 150, "s": 10, "i": 1}],
        ])
        provider = AlpacaIEXProvider(
            "key", "secret", connect=lambda _url: FakeConnection(socket)
        )
        events = []

        async def emit(event):
            events.append(event)

        await provider.stream_events(SubscriptionRequest(("AMD",)), emit)

        self.assertEqual(socket.sent[0], {"action": "auth", "key": "key", "secret": "secret"})
        self.assertEqual(socket.sent[1],
                         {"action": "subscribe", "trades": ["AMD"], "quotes": ["AMD"]})
        self.assertIsInstance(events[0], TradeEvent)
        self.assertEqual(provider.capabilities().coverage, "iex")

    async def test_missing_credentials_fail_before_connect(self):
        provider = AlpacaIEXProvider("", "")
        with self.assertRaisesRegex(ValueError, "credentials"):
            await provider.stream_events(SubscriptionRequest(("AMD",)), lambda _event: None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `./venv/bin/python -m unittest tests.test_marketdata_alpaca -v`
Expected: FAIL because `marketdata.alpaca` does not exist.

- [ ] **Step 3: Implement the adapter with injected connection**

Create `marketdata/alpaca.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import json

from websockets.asyncio.client import connect as websocket_connect

from marketdata.base import ProviderCapabilities
from marketdata.normalization import AlpacaEventNormalizer
from marketdata.subscriptions import plan_change


STREAM_URL = "wss://stream.data.alpaca.markets/v2/iex"


class AlpacaIEXProvider:
    def __init__(self, api_key, api_secret, connect=None):
        self._api_key = api_key
        self._api_secret = api_secret
        self._connect = websocket_connect if connect is None else connect
        self._socket = None
        self._symbols = ()
        self._normalizer = AlpacaEventNormalizer()
        self._write_lock = asyncio.Lock()

    def capabilities(self):
        reason = None if self._api_key and self._api_secret else "missing_credentials"
        return ProviderCapabilities(
            "alpaca", "iex", True, 30, False, False, True,
            False, False, reason,
        )

    async def stream_events(self, request, emit):
        if not self._api_key or not self._api_secret:
            raise ValueError("Alpaca credentials are required")
        async with self._connect(STREAM_URL) as socket:
            self._socket = socket
            await self._send({"action": "auth", "key": self._api_key,
                              "secret": self._api_secret})
            authenticated = False
            subscribed = False
            async for raw in socket:
                messages = json.loads(raw)
                for payload in messages:
                    if payload.get("T") == "success" and payload.get("msg") == "authenticated":
                        authenticated = True
                        await self._replace_subscription(request.symbols)
                        continue
                    if payload.get("T") == "subscription":
                        subscribed = True
                        continue
                    if payload.get("T") == "error":
                        raise RuntimeError("alpaca_stream_error")
                    event = self._normalizer.ingest(
                        payload, datetime.now(timezone.utc)
                    )
                    if event is not None:
                        await emit(event)
            if not authenticated or not subscribed:
                raise RuntimeError("alpaca_stream_closed_before_subscription")

    async def update_subscription(self, request):
        await self._replace_subscription(request.symbols)

    async def _replace_subscription(self, desired):
        change = plan_change(self._symbols, desired)
        if change.subscribe:
            await self._send({"action": "subscribe",
                              "trades": list(change.subscribe),
                              "quotes": list(change.subscribe)})
        if change.unsubscribe:
            await self._send({"action": "unsubscribe",
                              "trades": list(change.unsubscribe),
                              "quotes": list(change.unsubscribe)})
        self._symbols = tuple(desired)

    async def _send(self, payload):
        if self._socket is None:
            raise RuntimeError("alpaca_stream_not_connected")
        async with self._write_lock:
            await self._socket.send(json.dumps(payload))

    async def close(self):
        socket, self._socket = self._socket, None
        if socket is not None:
            await socket.close()
```

- [ ] **Step 4: Run adapter tests**

Run: `./venv/bin/python -m unittest tests.test_marketdata_alpaca -v`
Expected: 2 tests PASS and no external network call.

- [ ] **Step 5: Commit**

```bash
git add marketdata/alpaca.py tests/test_marketdata_alpaca.py
git commit -m "feat: add Alpaca IEX stream adapter"
```

---

### Task 6: Collector Lifecycle, Retry, and Pool Updates

**Files:**
- Create: `marketdata/collector.py`
- Test: `tests/test_marketdata_collector.py`

**Interfaces:**
- Consumes: provider, `IntradayStore`, and pure `build_pool`.
- Produces: `IntradayCollector.set_selection(selected, peers, candidates)`, `run()`, `stop()`, and `snapshot()`.
- Guarantee: pool updates come only from explicit selection/scheduled candidate calls, never hover.

- [ ] **Step 1: Write failing lifecycle tests**

```python
# tests/test_marketdata_collector.py
import asyncio
import unittest

from marketdata.collector import IntradayCollector


class FakeStore:
    def __init__(self):
        self.events = []
        self.opened = []
        self.closed = []
    def initialize(self): pass
    def record_capabilities(self, _capability, _at): pass
    def write_event(self, event): self.events.append(event); return True
    def open_subscription(self, provider, symbols, at): self.opened.append((provider, symbols))
    def close_subscription(self, provider, symbols, at): self.closed.append((provider, symbols))


class FakeProvider:
    def __init__(self):
        self.requests = []
        self.updated = []
    def capabilities(self):
        from marketdata.base import ProviderCapabilities
        return ProviderCapabilities("fake", "iex", True, 30, False, False,
                                    True, False, False, None)
    async def stream_events(self, request, emit):
        self.requests.append(request.symbols)
        await asyncio.Event().wait()
    async def update_subscription(self, request):
        self.updated.append(request.symbols)
    async def close(self): pass


class IntradayCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def test_selection_builds_expected_pool_and_updates_connected_provider(self):
        provider, store = FakeProvider(), FakeStore()
        collector = IntradayCollector(provider, store, retry_delays=(0,))
        collector.set_selection("AMD", ["NVDA", "AVGO"], ["NBIS"])
        task = asyncio.create_task(collector.run())
        await asyncio.sleep(0)
        self.assertEqual(provider.requests[0][:4], ("SPY", "QQQ", "SOXX", "AMD"))

        collector.set_selection("NBIS", ["AMD"], [])
        await asyncio.sleep(0)
        self.assertEqual(provider.updated[-1][:4], ("SPY", "QQQ", "SOXX", "NBIS"))
        await collector.stop()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_snapshot_never_contains_credentials(self):
        collector = IntradayCollector(FakeProvider(), FakeStore())
        value = collector.snapshot()
        self.assertEqual(value["coverage"], "iex")
        self.assertNotIn("secret", str(value).lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `./venv/bin/python -m unittest tests.test_marketdata_collector -v`
Expected: FAIL because `marketdata.collector` does not exist.

- [ ] **Step 3: Implement collector state**

Create `marketdata/collector.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
import asyncio

from marketdata.base import SubscriptionRequest
from marketdata.subscriptions import build_pool, plan_change


class IntradayCollector:
    def __init__(self, provider, store, retry_delays=(1, 2, 5, 10, 30)):
        self._provider = provider
        self._store = store
        self._retry_delays = tuple(retry_delays) or (0,)
        limit = provider.capabilities().max_symbols
        self._desired = SubscriptionRequest(
            build_pool(None, (), (), limit=limit), max_symbols=limit
        )
        self._active = SubscriptionRequest((), max_symbols=limit)
        self._state = "idle"
        self._last_event_received_at = None
        self._disconnect_count = 0
        self._error = None
        self._stop_requested = False
        self._update_tasks = set()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    def set_selection(self, selected, peers, candidates):
        limit = self._provider.capabilities().max_symbols
        desired = build_pool(selected, peers, candidates, limit=limit)
        self._desired = SubscriptionRequest(desired, max_symbols=limit)
        if self._state == "running":
            task = asyncio.create_task(self._apply_desired())
            self._update_tasks.add(task)
            task.add_done_callback(self._update_tasks.discard)

    async def _apply_desired(self):
        if self._desired == self._active:
            return
        previous = self._active
        change = plan_change(previous.symbols, self._desired.symbols)
        await self._provider.update_subscription(self._desired)
        changed_at = self._now()
        if change.subscribe:
            self._store.open_subscription(
                self._provider.capabilities().provider,
                change.subscribe,
                changed_at,
            )
        if change.unsubscribe:
            self._store.close_subscription(
                self._provider.capabilities().provider,
                change.unsubscribe,
                changed_at,
            )
        self._active = self._desired

    async def _emit(self, event):
        self._store.write_event(event)
        self._last_event_received_at = event.received_ts

    async def run(self):
        self._store.initialize()
        capabilities = self._provider.capabilities()
        self._store.record_capabilities(capabilities, self._now())
        if capabilities.unavailable_reason is not None:
            self._state = "unavailable"
            self._error = capabilities.unavailable_reason
            return

        retry_index = 0
        while not self._stop_requested:
            started_at = self._now()
            self._state = "connecting"
            self._error = None
            self._active = self._desired
            self._store.open_subscription(
                capabilities.provider, self._active.symbols, started_at
            )
            try:
                self._state = "running"
                await self._provider.stream_events(self._active, self._emit)
                if not self._stop_requested:
                    raise RuntimeError("provider_stream_ended")
            except asyncio.CancelledError:
                raise
            except Exception:
                self._disconnect_count += 1
                self._error = "provider_error"
                self._state = "retrying"
                self._store.close_subscription(
                    capabilities.provider, self._active.symbols, self._now()
                )
                delay = self._retry_delays[
                    min(retry_index, len(self._retry_delays) - 1)
                ]
                retry_index += 1
                await asyncio.sleep(delay)
        self._state = "stopped"

    async def stop(self):
        self._stop_requested = True
        for task in tuple(self._update_tasks):
            task.cancel()
        if self._active.symbols:
            self._store.close_subscription(
                self._provider.capabilities().provider,
                self._active.symbols,
                self._now(),
            )
        await self._provider.close()
        self._state = "stopped"

    def snapshot(self):
        capabilities = self._provider.capabilities()
        return {
            "state": self._state,
            "provider": capabilities.provider,
            "coverage": capabilities.coverage,
            "subscribed_symbols": list(self._active.symbols),
            "desired_symbols": list(self._desired.symbols),
            "last_event_received_at": (
                None
                if self._last_event_received_at is None
                else self._last_event_received_at.isoformat()
            ),
            "disconnect_count": self._disconnect_count,
            "error": self._error,
        }
```

- [ ] **Step 4: Run collector tests**

Run: `./venv/bin/python -m unittest tests.test_marketdata_collector -v`
Expected: 2 tests PASS without sleeping beyond one event-loop turn.

- [ ] **Step 5: Commit**

```bash
git add marketdata/collector.py tests/test_marketdata_collector.py
git commit -m "feat: coordinate intraday collection lifecycle"
```

---

### Task 7: Read-Only Flask Status Endpoint

**Files:**
- Create: `web/services/intraday.py`
- Modify: `web/app.py`
- Test: `tests/test_web_intraday_status.py`

**Interfaces:**
- Consumes: optional collector and optional `IntradayStore`.
- Produces: `IntradayStatusService.snapshot() -> dict`.
- Produces: `GET /api/market-data/status`.
- Constraint: calling the endpoint never starts or changes collection.

- [ ] **Step 1: Write failing API tests**

```python
# tests/test_web_intraday_status.py
import unittest

from web.app import create_app


class FakeStatusService:
    def __init__(self):
        self.calls = 0
    def snapshot(self):
        self.calls += 1
        return {
            "state": "running", "provider": "alpaca", "coverage": "iex",
            "subscribed_symbols": ["AMD", "QQQ", "SOXX", "SPY"],
            "last_event_received_at": "2026-07-24T14:30:00+00:00",
            "disconnect_count": 0, "error": None,
        }


class IntradayStatusApiTest(unittest.TestCase):
    def test_status_is_read_only_and_keeps_iex_label(self):
        service = FakeStatusService()
        app = create_app({"TESTING": True, "INTRADAY_STATUS_SERVICE": service})
        response = app.test_client().get("/api/market-data/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["coverage"], "iex")
        self.assertEqual(service.calls, 1)

    def test_default_without_collector_is_typed_unavailable(self):
        app = create_app({"TESTING": True})
        payload = app.test_client().get("/api/market-data/status").get_json()
        self.assertEqual(payload["state"], "unavailable")
        self.assertEqual(payload["error"], "collector_not_configured")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `./venv/bin/python -m unittest tests.test_web_intraday_status -v`
Expected: FAIL because the endpoint returns 404.

- [ ] **Step 3: Add the service and route**

Create `web/services/intraday.py`:

```python
class IntradayStatusService:
    def __init__(self, collector=None):
        self._collector = collector

    def snapshot(self):
        if self._collector is None:
            return {
                "state": "unavailable",
                "provider": None,
                "coverage": None,
                "subscribed_symbols": [],
                "last_event_received_at": None,
                "disconnect_count": 0,
                "error": "collector_not_configured",
            }
        return self._collector.snapshot()
```

Modify `web/app.py`:

```python
from web.services.intraday import IntradayStatusService
```

Inside `create_app`, after scenario-provider setup:

```python
intraday_status_service = flask_app.config.get("INTRADAY_STATUS_SERVICE")
if intraday_status_service is None:
    intraday_status_service = IntradayStatusService()
flask_app.extensions["dashboard_intraday_status_service"] = intraday_status_service
```

Add the route before error handlers:

```python
@flask_app.get("/api/market-data/status")
def market_data_status():
    return _json_response(intraday_status_service.snapshot())
```

- [ ] **Step 4: Run focused and existing API tests**

Run: `./venv/bin/python -m unittest tests.test_web_intraday_status tests.test_web_api -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add web/services/intraday.py web/app.py tests/test_web_intraday_status.py
git commit -m "feat: expose intraday market data status"
```

---

### Task 8: Explicit Collector CLI and Operator Documentation

**Files:**
- Create: `collect_intraday.py`
- Create: `.env.example`
- Modify: `docs/dashboard.md`
- Test: `tests/test_collect_intraday.py`

**Interfaces:**
- Consumes: Alpaca credentials from `ALPACA_API_KEY` and `ALPACA_API_SECRET`.
- Consumes: `--selected`, repeatable `--peer`, repeatable `--candidate`, and `--database`.
- Produces: an explicit foreground collector process with no Flask side effects.

- [ ] **Step 1: Write failing CLI construction tests**

```python
# tests/test_collect_intraday.py
import unittest
from unittest import mock

import collect_intraday


class CollectIntradayCliTest(unittest.TestCase):
    def test_missing_credentials_exits_without_printing_secret(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(SystemExit, "Alpaca credentials"):
                collect_intraday.build_collector(["--selected", "AMD"])

    def test_arguments_build_expected_initial_pool(self):
        with mock.patch.dict(
            "os.environ",
            {"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"},
            clear=True,
        ):
            collector = collect_intraday.build_collector(
                ["--selected", "AMD", "--peer", "NVDA",
                 "--candidate", "NBIS", "--database", "data/prices.db"]
            )
        snapshot = collector.snapshot()
        self.assertEqual(snapshot["desired_symbols"][:4],
                         ["SPY", "QQQ", "SOXX", "AMD"])
        self.assertNotIn("secret", str(snapshot).lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run: `./venv/bin/python -m unittest tests.test_collect_intraday -v`
Expected: FAIL because `collect_intraday` does not exist.

- [ ] **Step 3: Implement argument parsing and foreground execution**

Create `collect_intraday.py`:

```python
from __future__ import annotations

import argparse
import asyncio
import os

from marketdata.alpaca import AlpacaIEXProvider
from marketdata.collector import IntradayCollector
from marketdata.storage import IntradayStore


def build_collector(argv=None):
    parser = argparse.ArgumentParser(description="Collect free Alpaca IEX intraday events")
    parser.add_argument("--selected", default="SPY")
    parser.add_argument("--peer", action="append", default=[])
    parser.add_argument("--candidate", action="append", default=[])
    parser.add_argument("--database", default="data/prices.db")
    args = parser.parse_args(argv)
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        raise SystemExit("Alpaca credentials are required")
    collector = IntradayCollector(
        AlpacaIEXProvider(key, secret),
        IntradayStore(args.database),
    )
    collector.set_selection(args.selected, args.peer, args.candidate)
    return collector


def main():
    asyncio.run(build_collector().run())


if __name__ == "__main__":
    main()
```

Create `.env.example`:

```bash
# Free Alpaca IEX market-data credentials. Never commit real values.
ALPACA_API_KEY=
ALPACA_API_SECRET=
```

Add to `docs/dashboard.md`:

````markdown
## Free intraday collector

The collector is a separate foreground process so opening or hovering the
dashboard cannot change subscriptions:

```bash
source env.sh
export ALPACA_API_KEY="..."
export ALPACA_API_SECRET="..."
./venv/bin/python collect_intraday.py \
  --selected AMD --peer NVDA --peer AVGO --candidate NBIS
```

This phase uses Alpaca's free IEX feed, not the full US consolidated market.
Trade direction is inferred from the contemporaneous quote midpoint and then
the tick rule; it is not exchange-provided aggressor direction. Inspect
`GET /api/market-data/status` for coverage, active symbols, freshness, and
disconnects.
````

- [ ] **Step 4: Run CLI and all market-data tests**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_marketdata_contracts \
  tests.test_marketdata_normalization \
  tests.test_marketdata_storage \
  tests.test_marketdata_subscriptions \
  tests.test_marketdata_alpaca \
  tests.test_marketdata_collector \
  tests.test_web_intraday_status \
  tests.test_collect_intraday -v
```

Expected: all new tests PASS with zero network requests.

- [ ] **Step 5: Run the full existing suite**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: all Python tests PASS; no database fixture or existing dashboard contract changes.

- [ ] **Step 6: Verify the Flask status endpoint locally**

Run:

```bash
./venv/bin/python -c "from web.app import create_app; c=create_app({'TESTING': True}).test_client(); r=c.get('/api/market-data/status'); print(r.status_code, r.get_json())"
```

Expected:

```text
200 {'coverage': None, 'disconnect_count': 0, 'error': 'collector_not_configured', 'last_event_received_at': None, 'provider': None, 'state': 'unavailable', 'subscribed_symbols': []}
```

- [ ] **Step 7: Commit**

```bash
git add collect_intraday.py .env.example docs/dashboard.md tests/test_collect_intraday.py
git commit -m "docs: add free intraday collector workflow"
```

---

## Completion Gate

Before declaring this plan complete:

1. Run `git diff --check`.
2. Run `./venv/bin/python -m unittest discover -s tests -v`.
3. Confirm `git status --short` contains no plan-generated files other than intentionally ignored runtime data.
4. Start the collector only if credentials are present; otherwise verify typed unavailable behavior without requesting credentials in logs.
5. Verify that the active pool contains at most 30 symbols and begins with `SPY`, `QQQ`, `SOXX`, and the selected ticker.
6. Verify that no hover handler, chart module, or frontend file imports or calls subscription code.
7. Request a code review before merging or starting the scoring/model plan.

## Follow-On Plans

After this plan is implemented and reviewed:

1. `intraday-order-flow-features-and-evaluation`: minute aggregation, OFI/delta, absorption, selling pressure, six display scores, quality shrinkage, and rolling out-of-sample research.
2. `intraday-dashboard-point-in-time-ui`: API series/snapshot contracts, scheme-A score cards, factor explanations, unified hover timestamp, fixed forecast overlays, and browser regression tests.
3. `broker-market-data-adapters`: only after uSMART/Webull credentials and permissions are available.
