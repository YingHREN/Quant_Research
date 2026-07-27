from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from marketdata.base import QuoteEvent, TradeEvent
from marketdata.storage import IntradayStore
from web.app import create_app


class _Status:
    def snapshot(self):
        return {
            "state": "running",
            "provider": "alpaca",
            "coverage": "iex",
            "subscribed_symbols": ["SPY", "QQQ", "SOXX", "NVDA"],
            "last_event_received_at": "2026-07-27T14:31:30+00:00",
            "disconnect_count": 0,
            "error": None,
        }


class IntradayLiveApiTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "market.db"
        self.store = IntradayStore(self.path)
        self.store.initialize()
        app = create_app(
            {
                "TESTING": True,
                "MARKET_DATA_DATABASE": str(self.path),
                "INTRADAY_STATUS_SERVICE": _Status(),
            }
        )
        self.client = app.test_client()

    def tearDown(self):
        self.temporary.cleanup()

    def test_subscription_api_atomically_replaces_user_symbols(self):
        response = self.client.put(
            "/api/market-data/subscriptions",
            json={"symbols": ["NVDA", "AMD", "SPY"]},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["user_symbols"], ["NVDA", "AMD"])
        self.assertEqual(payload["pending_symbols"], ["AMD"])
        restored = self.client.get(
            "/api/market-data/subscriptions"
        ).get_json()
        self.assertEqual(restored["user_symbols"], ["NVDA", "AMD"])

    def test_snapshot_aggregates_directional_minute_trades_and_quote(self):
        self.client.put(
            "/api/market-data/subscriptions",
            json={"symbols": ["NVDA"]},
        )
        start = datetime(2026, 7, 27, 14, 30, 5, tzinfo=timezone.utc)
        for offset, price, size, direction in (
            (0, 100.0, 10.0, "buy"),
            (20, 101.0, 5.0, "sell"),
            (70, 102.0, 8.0, "unknown"),
        ):
            at = start + timedelta(seconds=offset)
            self.store.write_event(
                TradeEvent(
                    "alpaca", "NVDA", at, at, price, size, "V", (),
                    direction, "provider" if direction != "unknown" else "unknown",
                    f"trade-{offset}", "regular",
                )
            )
        quote_at = start + timedelta(seconds=80)
        self.store.write_event(
            QuoteEvent(
                "alpaca", "NVDA", quote_at, quote_at,
                101.9, 200.0, 102.1, 300.0, "quote-1", "regular",
            )
        )

        response = self.client.get("/api/intraday/NVDA?window=120")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["coverage"], "iex")
        self.assertEqual(len(payload["minutes"]), 2)
        self.assertEqual(payload["minutes"][0]["volume"], 15.0)
        self.assertEqual(payload["minutes"][0]["buy_volume"], 10.0)
        self.assertEqual(payload["minutes"][0]["sell_volume"], 5.0)
        self.assertEqual(payload["minutes"][0]["delta"], 5.0)
        self.assertEqual(payload["minutes"][1]["unknown_volume"], 8.0)
        self.assertEqual(payload["quote"]["bid_price"], 101.9)
        self.assertAlmostEqual(payload["quote"]["spread"], 0.2)
        self.assertAlmostEqual(payload["pressure"]["direction_coverage"], 15 / 23)

    def test_subscription_limit_returns_stable_error_without_partial_write(self):
        response = self.client.put(
            "/api/market-data/subscriptions",
            json={"symbols": [f"A{position:02d}" for position in range(28)]},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"]["code"],
            "subscription_limit_exceeded",
        )
        self.assertEqual(
            self.client.get("/api/market-data/subscriptions").get_json()[
                "user_symbols"
            ],
            [],
        )


if __name__ == "__main__":
    unittest.main()
