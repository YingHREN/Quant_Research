from __future__ import annotations

from collections import Counter
from datetime import datetime, time, timezone
import re
from zoneinfo import ZoneInfo

from marketdata.base import (
    QuoteEvent,
    TradeCancelEvent,
    TradeCorrectionEvent,
    TradeEvent,
    _datetime_to_ns,
)


QUOTE_MID_MAX_AGE_NS_V1 = 5_000_000_000
RFC3339_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
    r"(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$"
)


def _provider_timestamp(value: str) -> tuple[datetime, int]:
    if not isinstance(value, str):
        raise ValueError("provider timestamp is invalid")
    match = RFC3339_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("provider timestamp is invalid")
    base, fraction, offset = match.groups()
    fraction_ns = int((fraction or "").ljust(9, "0"))
    microsecond = fraction_ns // 1_000
    parsed = datetime.fromisoformat(
        f"{base}{'+00:00' if offset == 'Z' else offset}"
    )
    parsed = parsed.replace(microsecond=microsecond).astimezone(timezone.utc)
    exact_ns = _datetime_to_ns(parsed.replace(microsecond=0)) + fraction_ns
    return parsed, exact_ns


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
        self.quality_counts = Counter()
        self.control_counts = Counter()

    def ingest(self, payload, received_ts):
        message_type = payload.get("T")
        if message_type == "q":
            return self._quote(payload, received_ts)
        if message_type == "t":
            return self._trade(payload, received_ts)
        if message_type == "c":
            return self._correction(payload, received_ts)
        if message_type == "x":
            return self._cancel(payload, received_ts)
        if message_type in ("success", "subscription", "error"):
            self.control_counts[message_type] += 1
        else:
            self.control_counts["unknown"] += 1
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
            bid_size = float(payload["bs"]) * 100.0
            ask_price = float(payload["ap"])
            ask_size = float(payload["as"]) * 100.0
            event_ts, event_ts_ns = _provider_timestamp(payload["t"])
            quote = QuoteEvent(
                provider="alpaca",
                symbol=payload["S"],
                event_ts=event_ts,
                received_ts=received_ts.astimezone(timezone.utc),
                bid_price=bid_price,
                bid_size=bid_size,
                ask_price=ask_price,
                ask_size=ask_size,
                source_sequence=None if payload.get("i") is None else str(payload["i"]),
                session=_session(event_ts),
                event_ts_ns=event_ts_ns,
                size_unit="shares",
                lot_size=100,
            )
        except (KeyError, TypeError, ValueError):
            self.drop_counts["invalid_quote"] += 1
            return None
        previous = self._quotes.get(quote.symbol)
        if previous is None or quote.event_ts_ns >= previous.event_ts_ns:
            self._quotes[quote.symbol] = quote
        else:
            self.quality_counts["out_of_order_quote"] += 1
        return quote

    def _trade(self, payload, received_ts):
        try:
            symbol = str(payload["S"]).upper()
            price = float(payload["p"])
            size = float(payload["s"])
            event_ts, event_ts_ns = _provider_timestamp(payload["t"])
            previous = self._previous_trade.get(symbol)
            quote = self._quotes.get(symbol)
            out_of_order = (
                previous is not None and event_ts_ns < previous[0]
            )
            if out_of_order:
                self.quality_counts["out_of_order_trade"] += 1
            direction, source = self._direction(
                price,
                event_ts_ns,
                quote,
                None if out_of_order else previous,
            )
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
                event_ts_ns=event_ts_ns,
                size_unit="shares",
            )
        except (KeyError, TypeError, ValueError):
            self.drop_counts["invalid_trade"] += 1
            return None
        if previous is None or event_ts_ns >= previous[0]:
            self._previous_trade[symbol] = (event_ts_ns, price)
        return trade

    def _direction(self, price, event_ts_ns, quote, previous):
        if quote is not None:
            quote_age_ns = event_ts_ns - quote.event_ts_ns
            if quote_age_ns < 0:
                self.quality_counts["future_quote"] += 1
            elif quote_age_ns > QUOTE_MID_MAX_AGE_NS_V1:
                self.quality_counts["stale_quote"] += 1
            else:
                midpoint = (quote.bid_price + quote.ask_price) / 2.0
                if price > midpoint:
                    return "buy", "quote_mid"
                if price < midpoint:
                    return "sell", "quote_mid"
        previous_price = None if previous is None else previous[1]
        if previous_price is not None and price > previous_price:
            return "buy", "tick_rule"
        if previous_price is not None and price < previous_price:
            return "sell", "tick_rule"
        return "unknown", "unknown"

    def _correction(self, payload, received_ts):
        try:
            event_ts, event_ts_ns = _provider_timestamp(payload["t"])
            provider_trade_id = payload.get("oi", payload.get("i"))
            if provider_trade_id is None:
                raise ValueError("missing provider trade id")
            replacement_trade_id = (
                None
                if payload.get("oi") is None or payload.get("i") is None
                else str(payload["i"])
            )
            return TradeCorrectionEvent(
                provider="alpaca",
                symbol=payload["S"],
                event_ts=event_ts,
                received_ts=received_ts.astimezone(timezone.utc),
                provider_trade_id=str(provider_trade_id),
                replacement_trade_id=replacement_trade_id,
                price=float(payload["p"]),
                size=float(payload["s"]),
                exchange=payload.get("x"),
                conditions=tuple(str(value) for value in payload.get("c", ())),
                session=_session(event_ts),
                event_ts_ns=event_ts_ns,
                size_unit="shares",
            )
        except (KeyError, TypeError, ValueError):
            self.drop_counts["invalid_correction"] += 1
            return None

    def _cancel(self, payload, received_ts):
        try:
            event_ts, event_ts_ns = _provider_timestamp(payload["t"])
            provider_trade_id = payload.get("oi", payload.get("i"))
            if provider_trade_id is None:
                raise ValueError("missing provider trade id")
            return TradeCancelEvent(
                provider="alpaca",
                symbol=payload["S"],
                event_ts=event_ts,
                received_ts=received_ts.astimezone(timezone.utc),
                provider_trade_id=str(provider_trade_id),
                cancel_code=str(payload.get("a") or "cancel"),
                session=_session(event_ts),
                event_ts_ns=event_ts_ns,
            )
        except (KeyError, TypeError, ValueError):
            self.drop_counts["invalid_cancel"] += 1
            return None
