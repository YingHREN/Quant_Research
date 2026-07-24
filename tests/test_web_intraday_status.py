import unittest

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


if __name__ == "__main__":
    unittest.main()
