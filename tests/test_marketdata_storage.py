from dataclasses import replace
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from marketdata.base import ProviderCapabilities, QuoteEvent, TradeEvent
from marketdata.storage import IntradayStore


UTC = timezone.utc
AT = datetime(2026, 7, 24, 14, 30, tzinfo=UTC)


def trade(sequence="1"):
    return TradeEvent(
        provider="alpaca", symbol="AMD", event_ts=AT, received_ts=AT,
        price=150.0, size=100.0, exchange="V", conditions=("@",),
        direction="buy", direction_source="quote_mid",
        source_sequence=sequence, session="regular",
    )


class IntradayStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "market.db"
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE prices (ticker TEXT)")
        self.store = IntradayStore(self.path)
        self.store.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_event_is_idempotent_and_prices_table_survives(self):
        self.assertTrue(self.store.write_event(trade()))
        self.assertFalse(self.store.write_event(trade()))
        with sqlite3.connect(self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM intraday_trades").fetchone()[0]
            prices = connection.execute(
                "SELECT name FROM sqlite_master WHERE name='prices'"
            ).fetchone()
        self.assertEqual(count, 1)
        self.assertIsNotNone(prices)

    def test_status_reports_capability_and_subscription_coverage(self):
        capability = ProviderCapabilities(
            "alpaca", "iex", True, 30, False, False, True, False, False, None
        )
        self.store.record_capabilities(capability, AT)
        self.store.open_subscription("alpaca", ("SPY", "AMD"), AT)
        status = self.store.status()
        self.assertEqual(status["provider"], "alpaca")
        self.assertEqual(status["coverage"], "iex")
        self.assertEqual(status["subscribed_symbols"], ["AMD", "SPY"])

    def test_fallback_trade_sequence_distinguishes_metadata_and_is_idempotent(self):
        first = trade(sequence=None)
        different_metadata = replace(first, conditions=("@", "I"))
        self.assertTrue(self.store.write_event(first))
        self.assertFalse(self.store.write_event(first))
        self.assertTrue(self.store.write_event(different_metadata))
        with sqlite3.connect(self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM intraday_trades").fetchone()[0]
        self.assertEqual(count, 2)

    def test_fallback_quote_sequence_distinguishes_size_and_is_idempotent(self):
        first = QuoteEvent(
            "alpaca", "AMD", AT, AT, 149.0, 10.0, 151.0, 12.0, None, "regular"
        )
        different_size = replace(first, bid_size=11.0)
        self.assertTrue(self.store.write_event(first))
        self.assertFalse(self.store.write_event(first))
        self.assertTrue(self.store.write_event(different_size))
        with sqlite3.connect(self.path) as connection:
            count = connection.execute("SELECT COUNT(*) FROM intraday_quotes").fetchone()[0]
        self.assertEqual(count, 2)

    def test_close_and_reopen_at_same_timestamp_preserves_active_interval(self):
        self.store.open_subscription("alpaca", ("AMD",), AT)
        self.store.close_subscription("alpaca", ("AMD",), AT)
        self.store.open_subscription("alpaca", ("AMD",), AT)
        with sqlite3.connect(self.path) as connection:
            intervals = connection.execute(
                "SELECT finished_at FROM subscription_intervals WHERE provider='alpaca' AND symbol='AMD'"
            ).fetchall()
        self.assertEqual(len(intervals), 2)
        self.assertEqual(sum(finished_at is None for (finished_at,) in intervals), 1)
        self.assertEqual(self.store.status()["subscribed_symbols"], ["AMD"])

    def test_lifecycle_timestamps_require_utc(self):
        capability = ProviderCapabilities(
            "alpaca", "iex", True, 30, False, False, True, False, False, None
        )
        naive = datetime(2026, 7, 24, 14, 30)
        non_utc = datetime(2026, 7, 24, 22, 30, tzinfo=timezone(timedelta(hours=8)))
        with self.assertRaises(ValueError):
            self.store.record_capabilities(capability, naive)
        with self.assertRaises(ValueError):
            self.store.open_subscription("alpaca", ("AMD",), non_utc)
        with self.assertRaises(ValueError):
            self.store.close_subscription("alpaca", ("AMD",), naive)


if __name__ == "__main__":
    unittest.main()
