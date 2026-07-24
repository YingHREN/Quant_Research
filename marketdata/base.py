from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
import re
from typing import Awaitable, Callable, Protocol, Sequence, Tuple, Union
from zoneinfo import ZoneInfo


DIRECTIONS = frozenset(("buy", "sell", "neutral", "unknown"))
DIRECTION_SOURCES = frozenset(("provider", "quote_mid", "tick_rule", "unknown"))
SESSIONS = frozenset(("pre", "regular", "post", "unknown"))
SIZE_UNITS = frozenset(("shares",))
INTERVAL_PATTERN = re.compile(r"^[1-9][0-9]*[smhd]$")
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


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


def _datetime_to_ns(value: datetime) -> int:
    _require_utc(value, "timestamp")
    delta = value - EPOCH
    return (
        delta.days * 86_400_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _event_timestamp_ns(value: datetime, exact_ns: int | None) -> int:
    truncated_ns = _datetime_to_ns(value)
    if exact_ns is None:
        return truncated_ns
    if isinstance(exact_ns, bool) or not isinstance(exact_ns, int):
        raise ValueError("event_ts_ns must be an integer")
    if not truncated_ns <= exact_ns < truncated_ns + 1_000:
        raise ValueError("event_ts_ns must match event_ts")
    return exact_ns


def _trading_date(value: datetime, trading_date: str | None) -> str:
    expected = value.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    if trading_date is None:
        return expected
    if trading_date != expected:
        raise ValueError("trading_date must match event timestamp")
    return trading_date


def _finite(*values: float) -> bool:
    for value in values:
        if isinstance(value, bool):
            return False
        try:
            if not isfinite(value):
                return False
        except TypeError:
            return False
    return True


def _validate_size_unit(size_unit: str, lot_size: int) -> None:
    if size_unit not in SIZE_UNITS:
        raise ValueError("size_unit is invalid")
    if isinstance(lot_size, bool) or not isinstance(lot_size, int) or lot_size <= 0:
        raise ValueError("lot_size must be a positive integer")


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
class SubscriptionConfirmation:
    trades: Tuple[str, ...]
    quotes: Tuple[str, ...]

    def __init__(self, trades: Sequence[str], quotes: Sequence[str]):
        normalized_trades = SubscriptionRequest(trades).symbols
        normalized_quotes = SubscriptionRequest(quotes).symbols
        if set(normalized_trades) != set(normalized_quotes):
            raise ValueError("confirmed trade and quote symbols must match")
        object.__setattr__(self, "trades", normalized_trades)
        object.__setattr__(self, "quotes", normalized_quotes)

    @property
    def symbols(self) -> Tuple[str, ...]:
        return self.trades


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
    event_ts_ns: int | None = None
    trading_date: str | None = None
    size_unit: str = "shares"

    def __post_init__(self):
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.received_ts, "received_ts")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "conditions", tuple(self.conditions))
        object.__setattr__(
            self,
            "event_ts_ns",
            _event_timestamp_ns(self.event_ts, self.event_ts_ns),
        )
        object.__setattr__(
            self,
            "trading_date",
            _trading_date(self.event_ts, self.trading_date),
        )
        _validate_size_unit(self.size_unit, 1)
        if not _finite(self.price, self.size):
            raise ValueError("trade price and size must be finite")
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
    event_ts_ns: int | None = None
    trading_date: str | None = None
    size_unit: str = "shares"
    lot_size: int = 1

    def __post_init__(self):
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.received_ts, "received_ts")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(
            self,
            "event_ts_ns",
            _event_timestamp_ns(self.event_ts, self.event_ts_ns),
        )
        object.__setattr__(
            self,
            "trading_date",
            _trading_date(self.event_ts, self.trading_date),
        )
        _validate_size_unit(self.size_unit, self.lot_size)
        if not _finite(
            self.bid_price,
            self.bid_size,
            self.ask_price,
            self.ask_size,
        ):
            raise ValueError("quote values must be finite")
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
        if self.end_ts <= self.start_ts:
            raise ValueError("bar timestamp ordering is invalid")
        if not isinstance(self.interval, str) or INTERVAL_PATTERN.fullmatch(
            self.interval
        ) is None:
            raise ValueError("bar interval shape is invalid")
        prices = (self.open, self.high, self.low, self.close)
        finite_values = prices + (() if self.vwap is None else (self.vwap,))
        if not _finite(*finite_values):
            raise ValueError("bar values must be finite")
        if min(prices) <= 0 or (
            self.vwap is not None and self.vwap <= 0
        ):
            raise ValueError("bar prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(
            self.open,
            self.close,
        ) or self.low > self.high:
            raise ValueError("bar OHLC values are invalid")
        if not _finite(self.volume) or self.volume < 0:
            raise ValueError("bar volume must be finite and nonnegative")
        if (
            isinstance(self.trade_count, bool)
            or not isinstance(self.trade_count, int)
            or self.trade_count < 0
        ):
            raise ValueError("bar trade_count must be a nonnegative integer")


@dataclass(frozen=True)
class TradeCorrectionEvent:
    provider: str
    symbol: str
    event_ts: datetime
    received_ts: datetime
    provider_trade_id: str
    replacement_trade_id: str | None
    price: float
    size: float
    exchange: str | None
    conditions: Tuple[str, ...]
    session: str
    event_ts_ns: int | None = None
    trading_date: str | None = None
    size_unit: str = "shares"

    def __post_init__(self):
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.received_ts, "received_ts")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(self, "conditions", tuple(self.conditions))
        object.__setattr__(
            self,
            "event_ts_ns",
            _event_timestamp_ns(self.event_ts, self.event_ts_ns),
        )
        object.__setattr__(
            self,
            "trading_date",
            _trading_date(self.event_ts, self.trading_date),
        )
        _validate_size_unit(self.size_unit, 1)
        if not str(self.provider_trade_id).strip():
            raise ValueError("provider_trade_id is required")
        if not _finite(self.price, self.size):
            raise ValueError("trade correction values must be finite")
        if self.price <= 0 or self.size <= 0:
            raise ValueError("trade correction price and size must be positive")
        if self.session not in SESSIONS:
            raise ValueError("session is invalid")


@dataclass(frozen=True)
class TradeCancelEvent:
    provider: str
    symbol: str
    event_ts: datetime
    received_ts: datetime
    provider_trade_id: str
    cancel_code: str
    session: str
    event_ts_ns: int | None = None
    trading_date: str | None = None

    def __post_init__(self):
        _require_utc(self.event_ts, "event_ts")
        _require_utc(self.received_ts, "received_ts")
        object.__setattr__(self, "symbol", _symbol(self.symbol))
        object.__setattr__(
            self,
            "event_ts_ns",
            _event_timestamp_ns(self.event_ts, self.event_ts_ns),
        )
        object.__setattr__(
            self,
            "trading_date",
            _trading_date(self.event_ts, self.trading_date),
        )
        if not str(self.provider_trade_id).strip():
            raise ValueError("provider_trade_id is required")
        if not str(self.cancel_code).strip():
            raise ValueError("cancel_code is required")
        if self.session not in SESSIONS:
            raise ValueError("session is invalid")


MarketEvent = Union[
    TradeEvent,
    QuoteEvent,
    BarEvent,
    TradeCorrectionEvent,
    TradeCancelEvent,
]
EventSink = Callable[[MarketEvent], Awaitable[None]]


class MarketDataProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...
    async def stream_events(
        self,
        request: SubscriptionRequest,
        emit: EventSink,
        on_confirmed=None,
    ) -> None: ...
    async def update_subscription(
        self,
        request: SubscriptionRequest,
    ) -> SubscriptionConfirmation | None: ...
    async def close(self) -> None: ...
