from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

from web.app import create_app
from web.services.market_data import (
    InvalidTicker,
    MarketDataUnavailable,
    UnknownTicker,
)
from web.services.update_jobs import UpdateAlreadyRunning


def price_history(periods=260, end="2026-07-21", offset=0.0):
    index = pd.bdate_range(end=end, periods=periods)
    close = np.linspace(100 + offset, 140 + offset, periods)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.linspace(1_000_000, 1_200_000, periods),
        },
        index=index,
    )


@dataclass(frozen=True)
class FakeSnapshot:
    state: str = "idle"

    def to_dict(self):
        return {
            "state": self.state,
            "started_at": None,
            "finished_at": None,
            "total": 0,
            "completed": 0,
            "updated": 0,
            "current_ticker": None,
            "error": None,
            "resumable": False,
        }


class FakeRepository:
    def __init__(self, failure=None, include_benchmark=True):
        self.failure = failure
        self.calls = []
        self.histories = {
            "AAA": price_history(offset=0),
            "BBB": price_history(offset=10),
        }
        if include_benchmark:
            self.histories["SPY"] = price_history(offset=20)

    def freshness(self):
        self.calls.append(("freshness",))
        if self.failure is not None:
            raise self.failure
        return {
            "latest_date": "2026-07-21",
            "by_date": [{"date": "2026-07-21", "tickers": len(self.histories)}],
        }

    def list_summaries(self):
        self.calls.append(("list_summaries",))
        if self.failure is not None:
            raise self.failure
        return [
            SimpleNamespace(
                ticker=ticker,
                latest_date=history.index[-1].date().isoformat(),
                lag_days=0,
                inactive=False,
            )
            for ticker, history in sorted(self.histories.items())
        ]

    def load_history(self, ticker, asof=None):
        self.calls.append(("load_history", ticker, asof))
        if self.failure is not None:
            raise self.failure
        if not isinstance(ticker, str) or not ticker or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for character in ticker
        ):
            raise InvalidTicker("unsafe detail /Users/alice/env.sh")
        try:
            history = self.histories[ticker]
        except KeyError as error:
            raise UnknownTicker("unsafe detail /Users/alice/prices.db") from error
        if asof is None:
            return history.copy()
        return history.loc[history.index <= pd.Timestamp(asof)].copy()


class FalseyRepository(FakeRepository):
    def __bool__(self):
        return False


class FakeManager:
    def __init__(self, start_error=None):
        self.start_error = start_error
        self.start_calls = 0
        self.snapshot_calls = 0

    def start(self):
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error
        return FakeSnapshot("running")

    def snapshot(self):
        self.snapshot_calls += 1
        return FakeSnapshot("idle")


class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()
        self.manager = FakeManager()
        self.app = create_app(
            {"TESTING": True}, self.repository, self.manager
        )
        self.client = self.app.test_client()

    def test_universe_schema_and_repository_calls(self):
        response = self.client.get("/api/universe")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json), {"asof", "freshness", "tickers", "factor_groups"}
        )
        self.assertEqual(response.json["asof"], "2026-07-21")
        self.assertEqual(
            set(response.json["tickers"][0]),
            {"ticker", "latest_date", "lag_days", "inactive"},
        )
        self.assertEqual(self.repository.calls.count(("freshness",)), 1)
        self.assertEqual(self.repository.calls.count(("list_summaries",)), 1)

    def test_factory_preserves_falsey_injected_dependencies(self):
        repository = FalseyRepository()
        manager = FakeManager()

        app = create_app({"TESTING": True}, repository, manager)
        response = app.test_client().get("/api/universe")

        self.assertEqual(response.status_code, 200)
        self.assertIs(app.extensions["dashboard_repository"], repository)
        self.assertIs(app.extensions["dashboard_update_manager"], manager)

    def test_stock_payload_has_one_consistent_observation_date(self):
        response = self.client.get("/api/stocks/AAA")
        payload = response.json

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(payload),
            {
                "ticker",
                "observation_date",
                "summary",
                "chart",
                "structures",
                "factors",
                "scenarios",
                "warnings",
            },
        )
        self.assertEqual(payload["ticker"], "AAA")
        self.assertEqual(payload["chart"][-1]["time"], payload["observation_date"])
        self.assertEqual(
            {factor["observation_date"] for factor in payload["factors"]},
            {payload["observation_date"]},
        )
        self.assertEqual(
            payload["scenarios"]["observation_date"], payload["observation_date"]
        )
        selected_loads = [
            call
            for call in self.repository.calls
            if call[0] == "load_history" and call[1] == "AAA"
        ]
        self.assertEqual(len(selected_loads), 1)

    def test_stock_ticker_is_normalized_before_repository_access(self):
        response = self.client.get("/api/stocks/%20aaa%20")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["ticker"], "AAA")
        self.assertIn(("load_history", "AAA", None), self.repository.calls)

    def test_missing_benchmark_degrades_to_warning(self):
        repository = FakeRepository(include_benchmark=False)
        client = create_app(
            {"TESTING": True}, repository, FakeManager()
        ).test_client()

        response = client.get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertIn("missing_benchmark", response.json["warnings"])

    def test_safe_unknown_ticker_error(self):
        response = self.client.get("/api/stocks/NOPE")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["error"]["code"], "unknown_ticker")
        self.assertNotIn("/Users/", response.get_data(as_text=True))

    def test_safe_invalid_ticker_error(self):
        response = self.client.get("/api/stocks/AAA%2Fetc")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"]["code"], "invalid_ticker")
        self.assertNotIn("/Users/", response.get_data(as_text=True))

    def test_market_data_failure_has_safe_service_unavailable_envelope(self):
        secret = "/Users/alice/prices.db?token=secret"
        repository = FakeRepository(MarketDataUnavailable())
        client = create_app(
            {"TESTING": True}, repository, FakeManager()
        ).test_client()

        response = client.get("/api/universe")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["error"]["code"], "market_data_unavailable")
        self.assertNotIn(secret, response.get_data(as_text=True))

    def test_unexpected_failure_is_redacted(self):
        secret = "/Users/alice/env.sh?token=secret"
        repository = FakeRepository(RuntimeError(secret))
        app = create_app({"TESTING": True}, repository, FakeManager())

        with self.assertLogs(app.logger.name, level="ERROR"):
            response = app.test_client().get("/api/universe")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json["error"]["code"], "internal_error")
        self.assertNotIn(secret, body)
        self.assertNotIn("Traceback", body)

    def test_update_start_and_status_use_injected_manager(self):
        started = self.client.post("/api/update")
        status = self.client.get("/api/update/status")

        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json["state"], "running")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json["state"], "idle")
        self.assertEqual(self.manager.start_calls, 1)
        self.assertEqual(self.manager.snapshot_calls, 1)

    def test_concurrent_update_has_conflict_envelope(self):
        manager = FakeManager(
            UpdateAlreadyRunning("unsafe detail /Users/alice/thread.log")
        )
        client = create_app(
            {"TESTING": True}, FakeRepository(), manager
        ).test_client()

        response = client.post("/api/update")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["error"]["code"], "update_in_progress")
        self.assertNotIn("/Users/", response.get_data(as_text=True))
        self.assertEqual(manager.start_calls, 1)

    def test_root_route_preserves_template_compatibility(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn("<html", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
