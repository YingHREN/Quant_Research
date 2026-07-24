from datetime import datetime, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from marketdata.base import ProviderCapabilities, TradeEvent
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


if __name__ == "__main__":
    unittest.main()
