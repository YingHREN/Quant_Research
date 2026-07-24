from datetime import datetime, timedelta, timezone
import unittest

from marketdata.base import (
    QuoteEvent,
    TradeCancelEvent,
    TradeCorrectionEvent,
    TradeEvent,
)
from marketdata.normalization import (
    QUOTE_MID_MAX_AGE_NS_V1,
    AlpacaEventNormalizer,
)


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

    def test_nanosecond_timestamp_is_preserved_alongside_utc_datetime(self):
        trade = self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:00.639713735Z",
             "p": 150.10, "s": 100, "i": 42},
            NOW,
        )
        self.assertIsInstance(trade, TradeEvent)
        self.assertEqual(
            trade.event_ts,
            datetime(2026, 7, 24, 14, 30, 0, 639713, tzinfo=UTC),
        )
        self.assertEqual(trade.event_ts_ns, 1784903400639713735)
        self.assertEqual(trade.trading_date, "2026-07-24")
        self.assertIs(trade.event_ts.tzinfo, UTC)

    def test_nan_trade_is_dropped_without_polluting_previous_trade(self):
        first = self.normalizer.ingest(
            {"T": "t", "S": "AAA", "t": "2026-07-24T14:30:00Z",
             "p": 100.0, "s": 1},
            NOW,
        )
        invalid = self.normalizer.ingest(
            {"T": "t", "S": "AAA", "t": "2026-07-24T14:30:00.1Z",
             "p": float("nan"), "s": 1},
            NOW,
        )
        after = self.normalizer.ingest(
            {"T": "t", "S": "AAA", "t": "2026-07-24T14:30:00.2Z",
             "p": 100.5, "s": 1},
            NOW,
        )
        self.assertIsInstance(first, TradeEvent)
        self.assertIsNone(invalid)
        self.assertEqual(self.normalizer.drop_counts["invalid_trade"], 1)
        self.assertEqual((after.direction, after.direction_source), ("buy", "tick_rule"))

    def test_infinite_quote_is_dropped_without_polluting_latest_quote(self):
        valid = self.normalizer.ingest(
            {"T": "q", "S": "AMD", "t": "2026-07-24T14:30:00Z",
             "bp": 100.0, "bs": 1, "ap": 101.0, "as": 1},
            NOW,
        )
        invalid = self.normalizer.ingest(
            {"T": "q", "S": "AMD", "t": "2026-07-24T14:30:00.1Z",
             "bp": 100.0, "bs": 1, "ap": float("inf"), "as": 1},
            NOW,
        )
        trade = self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:00.2Z",
             "p": 102.0, "s": 1},
            NOW,
        )
        self.assertIsInstance(valid, QuoteEvent)
        self.assertIsNone(invalid)
        self.assertEqual(self.normalizer.drop_counts["invalid_quote"], 1)
        self.assertEqual((trade.direction, trade.direction_source), ("buy", "quote_mid"))

    def test_future_quote_falls_back_without_using_future_information(self):
        self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:00Z",
             "p": 100.0, "s": 1},
            NOW,
        )
        self.normalizer.ingest(
            {"T": "q", "S": "AMD", "t": "2026-07-24T14:30:02Z",
             "bp": 90.0, "bs": 1, "ap": 91.0, "as": 1},
            NOW,
        )
        trade = self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:01Z",
             "p": 101.0, "s": 1},
            NOW,
        )
        self.assertEqual((trade.direction, trade.direction_source), ("buy", "tick_rule"))
        self.assertEqual(self.normalizer.quality_counts["future_quote"], 1)

    def test_stale_quote_falls_back_to_tick_rule(self):
        self.normalizer.ingest(
            {"T": "q", "S": "AMD", "t": "2026-07-24T14:30:00Z",
             "bp": 90.0, "bs": 1, "ap": 91.0, "as": 1},
            NOW,
        )
        self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:00Z",
             "p": 100.0, "s": 1},
            NOW,
        )
        stale_time = datetime(2026, 7, 24, 14, 30, tzinfo=UTC) + timedelta(
            microseconds=(QUOTE_MID_MAX_AGE_NS_V1 // 1000) + 1
        )
        trade = self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": stale_time.isoformat(),
             "p": 101.0, "s": 1},
            NOW,
        )
        self.assertEqual((trade.direction, trade.direction_source), ("buy", "tick_rule"))
        self.assertEqual(self.normalizer.quality_counts["stale_quote"], 1)

    def test_out_of_order_trade_never_regresses_tick_rule_state(self):
        latest = self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:02Z",
             "p": 102.0, "s": 1},
            NOW,
        )
        late = self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:01Z",
             "p": 90.0, "s": 1},
            NOW,
        )
        after = self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:03Z",
             "p": 101.0, "s": 1},
            NOW,
        )
        self.assertIsInstance(latest, TradeEvent)
        self.assertEqual((late.direction, late.direction_source), ("unknown", "unknown"))
        self.assertEqual((after.direction, after.direction_source), ("sell", "tick_rule"))
        self.assertEqual(self.normalizer.quality_counts["out_of_order_trade"], 1)

    def test_alpaca_quote_round_lots_are_normalized_to_shares(self):
        quote = self.normalizer.ingest(
            {"T": "q", "S": "AMD", "t": "2026-07-24T14:30:00Z",
             "bp": 150.0, "bs": 3, "ap": 150.1, "as": 4},
            NOW,
        )
        trade = self.normalizer.ingest(
            {"T": "t", "S": "AMD", "t": "2026-07-24T14:30:00.1Z",
             "p": 150.1, "s": 3},
            NOW,
        )
        self.assertEqual((quote.bid_size, quote.ask_size), (300.0, 400.0))
        self.assertEqual((quote.size_unit, quote.lot_size), ("shares", 100))
        self.assertEqual((trade.size, trade.size_unit), (3.0, "shares"))

    def test_trade_correction_and_cancel_messages_are_normalized(self):
        correction = self.normalizer.ingest(
            {"T": "c", "S": "AMD", "t": "2026-07-24T14:30:01.000000100Z",
             "oi": "trade-1", "op": 150.0, "os": 10, "oc": ["@"],
             "ci": "trade-2", "cp": 151.0, "cs": 20, "cc": ["T"],
             "x": "V"},
            NOW,
        )
        cancel = self.normalizer.ingest(
            {"T": "x", "S": "AMD", "t": "2026-07-24T14:30:02.000000200Z",
             "i": "trade-1", "a": "cancel"},
            NOW,
        )
        self.assertIsInstance(correction, TradeCorrectionEvent)
        self.assertEqual(correction.provider_trade_id, "trade-1")
        self.assertEqual(correction.replacement_trade_id, "trade-2")
        self.assertEqual(
            (correction.original_price, correction.original_size),
            (150.0, 10.0),
        )
        self.assertEqual(correction.original_conditions, ("@",))
        self.assertEqual((correction.price, correction.size), (151.0, 20.0))
        self.assertEqual(correction.conditions, ("T",))
        self.assertEqual(correction.event_ts_ns, 1784903401000000100)
        self.assertIsInstance(cancel, TradeCancelEvent)
        self.assertEqual(cancel.provider_trade_id, "trade-1")
        self.assertEqual(cancel.event_ts_ns, 1784903402000000200)

    def test_unknown_control_messages_are_counted_by_explicit_type(self):
        self.assertIsNone(self.normalizer.ingest({"T": "success"}, NOW))
        self.assertIsNone(self.normalizer.ingest({"T": "mystery"}, NOW))
        self.assertEqual(self.normalizer.control_counts["success"], 1)
        self.assertEqual(self.normalizer.control_counts["unknown"], 1)


if __name__ == "__main__":
    unittest.main()
