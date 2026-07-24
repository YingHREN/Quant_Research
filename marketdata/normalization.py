from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timezone
from math import isfinite
import re
from zoneinfo import ZoneInfo

from marketdata.base import QuoteEvent, TradeEvent


def _timestamp(value: str) -> datetime:
    nanosecond_timestamp = re.fullmatch(
        r"(.*\.\d{6})\d{1,3}(Z|[+-]\d{2}:\d{2})",
        value,
    )
    if nanosecond_timestamp is not None:
        value = "".join(nanosecond_timestamp.groups())
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S.%f%z")
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

    def clear_symbols(self, symbols):
        for value in symbols:
            symbol = str(value).upper()
            self._quotes.pop(symbol, None)
            self._previous_trade.pop(symbol, None)

    def reset(self):
        self._quotes.clear()
        self._previous_trade.clear()

    def _quote(self, payload, received_ts):
        try:
            bid_price = float(payload["bp"])
            bid_size = float(payload["bs"])
            ask_price = float(payload["ap"])
            ask_size = float(payload["as"])
            if not all(isfinite(value) for value in (bid_price, bid_size, ask_price, ask_size)):
                raise ValueError("quote values must be finite")
            quote = QuoteEvent(
                provider="alpaca",
                symbol=payload["S"],
                event_ts=_timestamp(payload["t"]),
                received_ts=received_ts.astimezone(timezone.utc),
                bid_price=bid_price,
                bid_size=bid_size,
                ask_price=ask_price,
                ask_size=ask_size,
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
            size = float(payload["s"])
            if not isfinite(price) or not isfinite(size):
                raise ValueError("trade price and size must be finite")
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
                size=size,
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
