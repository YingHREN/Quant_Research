from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from marketdata.storage import IntradayStore
from web.services.intraday_subscriptions import (
    IntradaySubscriptionService,
    SubscriptionLimitExceeded,
)


class IntradaySubscriptionControlTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "intraday.db"
        self.store = IntradayStore(self.path)
        self.store.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_store_persists_normalized_request_and_increments_revision(self):
        initial = self.store.read_subscription_request()
        self.assertEqual(initial["revision"], 0)
        self.assertEqual(initial["user_symbols"], [])

        first = self.store.replace_subscription_request(
            [" nvda ", "AMD", "NVDA"],
            datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        )
        second_store = IntradayStore(self.path)
        restored = second_store.read_subscription_request()

        self.assertEqual(first["revision"], 1)
        self.assertEqual(restored["user_symbols"], ["NVDA", "AMD"])
        self.assertEqual(restored["updated_at"], "2026-07-27T12:00:00+00:00")

        second = second_store.replace_subscription_request(
            ["SNDK"],
            datetime(2026, 7, 27, 12, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["user_symbols"], ["SNDK"])

    def test_service_excludes_fixed_symbols_and_reports_pending(self):
        self.store.replace_subscription_request(
            ["SPY", "NVDA", "QQQ", "AMD"],
            datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
        )
        service = IntradaySubscriptionService(
            self.store,
            status_service=_StatusService(
                {
                    "state": "running",
                    "provider": "alpaca",
                    "coverage": "iex",
                    "subscribed_symbols": ["SPY", "QQQ", "SOXX", "NVDA"],
                    "last_event_received_at": "2026-07-27T12:00:01+00:00",
                    "error": None,
                }
            ),
        )

        payload = service.snapshot()

        self.assertEqual(payload["fixed_symbols"], ["SPY", "QQQ", "SOXX"])
        self.assertEqual(payload["user_symbols"], ["NVDA", "AMD"])
        self.assertEqual(
            payload["requested_symbols"],
            ["SPY", "QQQ", "SOXX", "NVDA", "AMD"],
        )
        self.assertEqual(payload["confirmed_symbols"], ["SPY", "QQQ", "SOXX", "NVDA"])
        self.assertEqual(payload["pending_symbols"], ["AMD"])
        self.assertEqual(payload["capacity"], {"used": 5, "limit": 30, "remaining": 25})

    def test_service_rejects_more_than_twenty_seven_user_symbols_atomically(self):
        service = IntradaySubscriptionService(self.store)
        symbols = [f"A{position:02d}" for position in range(28)]

        with self.assertRaises(SubscriptionLimitExceeded):
            service.replace(symbols)

        self.assertEqual(self.store.read_subscription_request()["revision"], 0)


class _StatusService:
    def __init__(self, payload):
        self.payload = payload

    def snapshot(self):
        return dict(self.payload)


if __name__ == "__main__":
    unittest.main()
