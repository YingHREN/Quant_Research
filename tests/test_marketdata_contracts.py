from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import math
import unittest

from marketdata.base import (
    BarEvent,
    ProviderCapabilities,
    QuoteEvent,
    SubscriptionRequest,
    TradeCancelEvent,
    TradeCorrectionEvent,
    TradeEvent,
)


UTC = timezone.utc


class MarketDataContractsTest(unittest.TestCase):
    def test_subscription_normalizes_deduplicates_and_rejects_more_than_limit(self):
        request = SubscriptionRequest(("spy", "AMD", "SPY"), max_symbols=30)
        self.assertEqual(request.symbols, ("SPY", "AMD"))
        with self.assertRaisesRegex(ValueError, "at most 2"):
            SubscriptionRequest(("A", "B", "C"), max_symbols=2)

    def test_subscription_cannot_exceed_global_limit_and_requires_positive_integer_limit(self):
        with self.assertRaisesRegex(ValueError, "at most 30"):
            SubscriptionRequest(tuple(str(index) for index in range(31)), max_symbols=31)
        for max_symbols in (0, -1, 1.5, True):
            with self.subTest(max_symbols=max_symbols):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    SubscriptionRequest(("AMD",), max_symbols=max_symbols)

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

    def test_trade_conditions_are_stored_as_an_immutable_tuple(self):
        conditions = ["@"]
        event = TradeEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
            received_ts=datetime(2026, 7, 24, 14, 30, 1, tzinfo=UTC),
            price=150.25,
            size=100.0,
            exchange="V",
            conditions=conditions,
            direction="unknown",
            direction_source="unknown",
            source_sequence="42",
            session="regular",
        )
        conditions.append("I")
        self.assertEqual(event.conditions, ("@",))
        self.assertIsInstance(event.conditions, tuple)

    def test_bar_normalizes_symbol_and_requires_utc_timestamps(self):
        bar = BarEvent(
            provider="alpaca",
            symbol=" amd ",
            start_ts=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
            end_ts=datetime(2026, 7, 24, 14, 31, tzinfo=UTC),
            received_ts=datetime(2026, 7, 24, 14, 31, 1, tzinfo=UTC),
            interval="1m",
            open=150.0,
            high=151.0,
            low=149.0,
            close=150.5,
            volume=1000.0,
            trade_count=10,
            vwap=150.25,
            locally_aggregated=False,
        )
        self.assertEqual(bar.symbol, "AMD")
        with self.assertRaisesRegex(ValueError, "UTC"):
            BarEvent(
                provider="alpaca",
                symbol="AMD",
                start_ts=datetime(2026, 7, 24, 14, 30),
                end_ts=datetime(2026, 7, 24, 14, 31, tzinfo=UTC),
                received_ts=datetime(2026, 7, 24, 14, 31, 1, tzinfo=UTC),
                interval="1m",
                open=150.0,
                high=151.0,
                low=149.0,
                close=150.5,
                volume=1000.0,
                trade_count=10,
                vwap=150.25,
                locally_aggregated=False,
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

    def test_provider_neutral_events_reject_non_finite_numbers(self):
        trade_arguments = dict(
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
        quote_arguments = dict(
            provider="alpaca",
            symbol="AMD",
            event_ts=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
            received_ts=datetime(2026, 7, 24, 14, 30, 1, tzinfo=UTC),
            bid_price=150.0,
            bid_size=10.0,
            ask_price=150.1,
            ask_size=12.0,
            source_sequence=None,
            session="regular",
        )
        for field in ("price", "size"):
            for invalid in (math.nan, math.inf, -math.inf):
                with self.subTest(event="trade", field=field, invalid=invalid):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        TradeEvent(**{**trade_arguments, field: invalid})
        for field in ("bid_price", "bid_size", "ask_price", "ask_size"):
            for invalid in (math.nan, math.inf, -math.inf):
                with self.subTest(event="quote", field=field, invalid=invalid):
                    with self.assertRaisesRegex(ValueError, "finite"):
                        QuoteEvent(**{**quote_arguments, field: invalid})

    def test_bar_validates_shape_prices_counts_and_timestamp_order(self):
        arguments = dict(
            provider="alpaca",
            symbol="AMD",
            start_ts=datetime(2026, 7, 24, 14, 30, tzinfo=UTC),
            end_ts=datetime(2026, 7, 24, 14, 31, tzinfo=UTC),
            received_ts=datetime(2026, 7, 24, 14, 31, 1, tzinfo=UTC),
            interval="1m",
            open=150.0,
            high=151.0,
            low=149.0,
            close=150.5,
            volume=1000.0,
            trade_count=10,
            vwap=150.25,
            locally_aggregated=False,
        )
        invalid_cases = (
            ("timestamp", {"end_ts": arguments["start_ts"]}),
            ("interval", {"interval": "minute"}),
            ("OHLC", {"high": 149.5}),
            ("OHLC", {"low": 150.25}),
            ("finite", {"close": math.nan}),
            ("volume", {"volume": -1.0}),
            ("trade_count", {"trade_count": -1}),
            ("trade_count", {"trade_count": 1.5}),
            ("finite", {"vwap": math.inf}),
        )
        for message, changes in invalid_cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, message):
                    BarEvent(**{**arguments, **changes})

    def test_exact_timestamp_and_trade_adjustments_are_immutable(self):
        event_ts = datetime(2026, 7, 24, 14, 30, tzinfo=UTC)
        correction = TradeCorrectionEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=event_ts,
            received_ts=event_ts,
            event_ts_ns=1784903400000000800,
            provider_trade_id="original-1",
            replacement_trade_id="replacement-1",
            price=151.0,
            size=200.0,
            exchange="V",
            conditions=("@",),
            session="regular",
        )
        cancel = TradeCancelEvent(
            provider="alpaca",
            symbol="AMD",
            event_ts=event_ts,
            received_ts=event_ts,
            event_ts_ns=1784903400000000900,
            provider_trade_id="original-1",
            cancel_code="cancel",
            session="regular",
        )
        self.assertEqual(correction.trading_date, "2026-07-24")
        self.assertEqual(cancel.trading_date, "2026-07-24")
        with self.assertRaises(FrozenInstanceError):
            correction.price = 1.0


if __name__ == "__main__":
    unittest.main()
