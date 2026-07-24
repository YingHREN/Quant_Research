from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import unittest

from marketdata.base import (
    BarEvent,
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


if __name__ == "__main__":
    unittest.main()
