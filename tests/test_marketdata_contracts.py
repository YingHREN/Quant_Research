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
