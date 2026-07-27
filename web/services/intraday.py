"""Read-only status and corrected minute snapshots for intraday data."""

from collections import OrderedDict
from datetime import timedelta

from marketdata.base import SubscriptionRequest


class IntradayStatusService:
    """Expose collector state without starting or changing collection."""

    def __init__(
        self,
        collector=None,
        store=None,
        stale_after_seconds=30,
    ):
        self._collector = collector
        self._store = store
        self._stale_after_seconds = stale_after_seconds

    def snapshot(self):
        if self._collector is not None:
            return self._collector.snapshot()
        if self._store is not None:
            return self._store.read_collector_status(
                stale_after_seconds=self._stale_after_seconds,
            )
        return {
            "state": "unavailable",
            "provider": None,
            "coverage": None,
            "subscribed_symbols": [],
            "last_event_received_at": None,
            "disconnect_count": 0,
            "error": "collector_not_configured",
        }


class IntradaySnapshotService:
    def __init__(self, store, subscription_service, provider="alpaca"):
        self._store = store
        self._subscriptions = subscription_service
        self._provider = provider

    def snapshot(self, ticker, window_minutes=120):
        symbol = SubscriptionRequest((ticker,), max_symbols=1).symbols[0]
        if (
            isinstance(window_minutes, bool)
            or not isinstance(window_minutes, int)
            or not 1 <= window_minutes <= 390
        ):
            raise ValueError("window must be between 1 and 390 minutes")
        subscription = self._subscriptions.snapshot()
        requested = set(subscription["requested_symbols"])
        confirmed = set(subscription["confirmed_symbols"])
        trades = self._store.read_effective_trades(
            provider=self._provider,
            symbol=symbol,
        )
        latest_date = max(
            (trade.trading_date for trade in trades),
            default=None,
        )
        selected = [
            trade
            for trade in trades
            if latest_date is not None and trade.trading_date == latest_date
        ]
        if selected:
            cutoff = selected[-1].event_ts - timedelta(minutes=window_minutes)
            selected = [trade for trade in selected if trade.event_ts >= cutoff]
        minutes = self._aggregate_minutes(selected)
        quote = self._store.read_latest_quote(self._provider, symbol)
        buy = sum(row["buy_volume"] for row in minutes)
        sell = sum(row["sell_volume"] for row in minutes)
        unknown = sum(row["unknown_volume"] for row in minutes)
        directed = buy + sell
        total = directed + unknown
        collector = subscription["collector"]
        if symbol not in requested:
            state = "not_subscribed"
        elif symbol not in confirmed:
            state = "pending"
        elif collector.get("state") == "running":
            state = "live" if selected or quote is not None else "stale"
        elif collector.get("state") == "stale":
            state = "stale"
        else:
            state = "collector_disconnected"
        return {
            "ticker": symbol,
            "state": state,
            "provider": self._provider,
            "coverage": collector.get("coverage") or "iex",
            "coverage_label": "IEX partial market",
            "latest_trade": (
                None
                if not selected
                else {
                    "price": selected[-1].price,
                    "size": selected[-1].size,
                    "time": selected[-1].event_ts.isoformat(),
                    "direction": selected[-1].direction,
                }
            ),
            "quote": self._quote_payload(quote),
            "minutes": minutes,
            "pressure": {
                "score": (
                    None
                    if directed == 0
                    else round(100.0 * (buy - sell) / directed, 2)
                ),
                "buy_volume": buy,
                "sell_volume": sell,
                "unknown_volume": unknown,
                "direction_coverage": 0.0 if total == 0 else directed / total,
            },
            "last_event_received_at": collector.get(
                "last_event_received_at"
            ),
        }

    @staticmethod
    def _aggregate_minutes(trades):
        grouped = OrderedDict()
        for trade in trades:
            minute = trade.event_ts.replace(second=0, microsecond=0)
            key = minute.isoformat()
            row = grouped.setdefault(
                key,
                {
                    "time": key,
                    "open": trade.price,
                    "high": trade.price,
                    "low": trade.price,
                    "close": trade.price,
                    "volume": 0.0,
                    "buy_volume": 0.0,
                    "sell_volume": 0.0,
                    "unknown_volume": 0.0,
                    "delta": 0.0,
                },
            )
            row["high"] = max(row["high"], trade.price)
            row["low"] = min(row["low"], trade.price)
            row["close"] = trade.price
            row["volume"] += trade.size
            if trade.direction == "buy":
                row["buy_volume"] += trade.size
                row["delta"] += trade.size
            elif trade.direction == "sell":
                row["sell_volume"] += trade.size
                row["delta"] -= trade.size
            else:
                row["unknown_volume"] += trade.size
        return list(grouped.values())

    @staticmethod
    def _quote_payload(quote):
        if quote is None:
            return None
        return {
            "time": quote.event_ts.isoformat(),
            "bid_price": quote.bid_price,
            "bid_size": quote.bid_size,
            "ask_price": quote.ask_price,
            "ask_size": quote.ask_size,
            "spread": quote.ask_price - quote.bid_price,
        }
