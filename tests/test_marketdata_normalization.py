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
