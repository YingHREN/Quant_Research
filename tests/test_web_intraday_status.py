import unittest
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path

from marketdata.storage import IntradayStore
from web.app import create_app


class FakeStatusService:
    def __init__(self):
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return {
            "state": "running",
            "provider": "alpaca",
            "coverage": "iex",
            "subscribed_symbols": ["AMD", "QQQ", "SOXX", "SPY"],
            "last_event_received_at": "2026-07-24T14:30:00+00:00",
            "disconnect_count": 0,
            "error": None,
        }


class IntradayStatusApiTest(unittest.TestCase):
    def test_status_is_read_only_and_keeps_iex_label(self):
        service = FakeStatusService()
        app = create_app({"TESTING": True, "INTRADAY_STATUS_SERVICE": service})
        response = app.test_client().get("/api/market-data/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["coverage"], "iex")
        self.assertEqual(service.calls, 1)

    def test_default_without_collector_is_typed_unavailable(self):
        app = create_app({"TESTING": True})
        payload = app.test_client().get("/api/market-data/status").get_json()
        self.assertEqual(payload["state"], "unavailable")
        self.assertEqual(payload["error"], "collector_not_configured")

    def test_separate_store_writer_and_default_flask_reader_share_lifecycle(self):
        at = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.db"
            writer = IntradayStore(path)
            writer.initialize()
            writer.write_collector_status(
                session_id="session-1",
                provider="alpaca",
                coverage="iex",
                state="running",
                confirmed_symbols=("SPY", "AMD"),
                last_event_received_at=at,
                disconnect_count=1,
                error=None,
                heartbeat_at=at,
                queue_depth=2,
                queue_high_water=4,
                dropped_event_count=0,
                undrained_event_count=0,
            )
            app = create_app(
                {
                    "TESTING": True,
                    "MARKET_DATA_DATABASE": str(path),
                    "INTRADAY_STATUS_STALE_AFTER_SECONDS": 30,
                }
            )
            client = app.test_client()
            running = client.get("/api/market-data/status").get_json()
            self.assertEqual(running["state"], "running")
            self.assertEqual(running["subscribed_symbols"], ["SPY", "AMD"])
            self.assertEqual(running["session_id"], "session-1")

            writer.write_collector_status(
                session_id="session-1",
                provider="alpaca",
                coverage="iex",
                state="running",
                confirmed_symbols=("SPY", "AMD"),
                last_event_received_at=at,
                disconnect_count=1,
                error=None,
                heartbeat_at=at - timedelta(minutes=1),
                queue_depth=0,
                queue_high_water=4,
                dropped_event_count=0,
                undrained_event_count=0,
            )
            stale = client.get("/api/market-data/status").get_json()
            self.assertEqual(stale["state"], "stale")
            self.assertEqual(stale["error"], "collector_stale")

            writer.write_collector_status(
                session_id="session-1",
                provider="alpaca",
                coverage="iex",
                state="stopped",
                confirmed_symbols=(),
                last_event_received_at=at,
                disconnect_count=1,
                error=None,
                heartbeat_at=datetime.now(timezone.utc),
                queue_depth=0,
                queue_high_water=4,
                dropped_event_count=0,
                undrained_event_count=0,
            )
            stopped = client.get("/api/market-data/status").get_json()
            self.assertEqual(stopped["state"], "stopped")
            self.assertEqual(stopped["subscribed_symbols"], [])

    def test_persisted_missing_credentials_is_distinct_from_never_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.db"
            writer = IntradayStore(path)
            writer.initialize()
            at = datetime.now(timezone.utc)
            writer.write_collector_status(
                session_id="session-1",
                provider="alpaca",
                coverage="iex",
                state="unavailable",
                confirmed_symbols=(),
                last_event_received_at=None,
                disconnect_count=0,
                error="missing_credentials",
                heartbeat_at=at,
                queue_depth=0,
                queue_high_water=0,
                dropped_event_count=0,
                undrained_event_count=0,
            )
            app = create_app(
                {
                    "TESTING": True,
                    "MARKET_DATA_DATABASE": str(path),
                }
            )
            payload = app.test_client().get(
                "/api/market-data/status"
            ).get_json()
            self.assertEqual(payload["state"], "unavailable")
            self.assertEqual(payload["error"], "missing_credentials")


if __name__ == "__main__":
    unittest.main()
