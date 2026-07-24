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
        if isinstance(max_symbols, bool) or not isinstance(max_symbols, int) or not 1 <= max_symbols <= 30:
            raise ValueError("max_symbols must be a positive integer at most 30")
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
        object.__setattr__(self, "conditions", tuple(self.conditions))
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

    def __post_init__(self):
        _require_utc(self.start_ts, "start_ts")
        _require_utc(self.end_ts, "end_ts")
        _require_utc(self.received_ts, "received_ts")
        object.__setattr__(self, "symbol", _symbol(self.symbol))


MarketEvent = Union[TradeEvent, QuoteEvent, BarEvent]
EventSink = Callable[[MarketEvent], Awaitable[None]]


class MarketDataProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    async def stream_events(self, request: SubscriptionRequest, emit: EventSink) -> None: ...
    async def update_subscription(self, request: SubscriptionRequest) -> None: ...
    async def close(self) -> None: ...
