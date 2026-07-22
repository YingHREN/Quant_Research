from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

from web.app import create_app
from web.factors.registry import FactorRegistry
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

    def load_universe_histories(self, asof=None):
        self.calls.append(("load_universe_histories", asof))
        if self.failure is not None:
            raise self.failure
        return {
            ticker: (
                history.copy()
                if asof is None
                else history.loc[history.index <= pd.Timestamp(asof)].copy()
            )
            for ticker, history in self.histories.items()
        }


class FalseyRepository(FakeRepository):
    def __bool__(self):
        return False


class StaleSelectionRepository(FakeRepository):
    def __init__(self):
        super().__init__()
        self.histories = {
            "OLD": price_history(end="2026-07-15", offset=0),
            **{
                f"P{number}": price_history(
                    end="2026-07-21", offset=number * 10
                )
                for number in range(1, 6)
            },
            "SPY": price_history(end="2026-07-21", offset=70),
        }

    def list_summaries(self):
        summaries = super().list_summaries()
        return [
            SimpleNamespace(
                ticker=summary.ticker,
                latest_date=summary.latest_date,
                lag_days=6 if summary.ticker == "OLD" else 0,
                inactive=summary.ticker == "OLD",
            )
            for summary in summaries
        ]


class MappedFactor:
    def __init__(self, key, group, direction, values):
        self.key = key
        self.label = key.replace("_", " ").title()
        self.group = group
        self.direction = direction
        self.description = "API cohort fixture"
        self.version = "test-v1"
        self.values = values

    def compute(self, context):
        return self.values.get(context.ticker)

    def format(self, value):
        return str(value)


class UniverseCohortRepository(FakeRepository):
    def __init__(self):
        super().__init__()
        current = ("AAA", "BBB", "CCC", "DDD", "EEE", "SPY")
        self.histories = {
            ticker: price_history(offset=number * 10)
            for number, ticker in enumerate(current)
        }
        self.histories["OLD"] = price_history(end="2026-06-01", offset=70)

    def list_summaries(self):
        self.calls.append(("list_summaries",))
        return [
            SimpleNamespace(
                ticker=ticker,
                latest_date=history.index[-1].date().isoformat(),
                lag_days=50 if ticker == "OLD" else 0,
                inactive=ticker == "OLD",
            )
            for ticker, history in sorted(self.histories.items())
        ]

    def freshness(self):
        self.calls.append(("freshness",))
        return {
            "latest_date": "2026-07-21",
            "by_date": [
                {"date": "2026-07-21", "tickers": 6},
                {"date": "2026-06-01", "tickers": 1},
            ],
        }


def universe_registry():
    current = ("AAA", "BBB", "CCC", "DDD", "EEE", "SPY")
    return FactorRegistry(
        [
            MappedFactor(
                "strict_vcp",
                "structure",
                "neutral",
                {
                    ticker: {"reject_reason": None if ticker == "AAA" else "none"}
                    for ticker in current
                },
            ),
            MappedFactor(
                "tight_platform",
                "structure",
                "neutral",
                {
                    ticker: {"is_platform": ticker == "BBB"}
                    for ticker in current
                },
            ),
            MappedFactor(
                "pivot_distance_pct",
                "structure",
                "neutral",
                {ticker: 2.0 if ticker == "CCC" else 12.0 for ticker in current},
            ),
            MappedFactor(
                "mom_12_1",
                "momentum",
                "higher",
                {
                    ticker: value
                    for ticker, value in zip(current, (60, 50, 40, 30, 20, 10))
                },
            ),
            MappedFactor(
                "realized_vol_63",
                "risk",
                "lower",
                {
                    ticker: value
                    for ticker, value in zip(
                        current, (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
                    )
                },
            ),
        ]
    )


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
            {
                "ticker",
                "latest_date",
                "lag_days",
                "inactive",
                "fresh",
                "strict_vcp",
                "tight_platform",
                "near_pivot",
                "shape_state",
                "momentum_percentile",
                "momentum_factor_key",
                "momentum_percentile_unit",
                "volatility",
                "volatility_factor_key",
                "volatility_unit",
            },
        )
        self.assertEqual(self.repository.calls.count(("freshness",)), 1)
        self.assertEqual(self.repository.calls.count(("list_summaries",)), 1)
        self.assertEqual(
            self.repository.calls.count(
                ("load_universe_histories", pd.Timestamp("2026-07-21"))
            ),
            1,
        )
        self.assertFalse(
            any(call[0] == "load_history" for call in self.repository.calls)
        )

    def test_universe_diagnostics_feed_real_filter_and_sort_pipeline(self):
        repository = UniverseCohortRepository()
        app = create_app(
            {"TESTING": True, "FACTOR_REGISTRY": universe_registry()},
            repository,
            FakeManager(),
        )

        response = app.test_client().get("/api/universe")

        self.assertEqual(response.status_code, 200)
        by_ticker = {row["ticker"]: row for row in response.json["tickers"]}
        self.assertEqual(by_ticker["AAA"]["shape_state"], "strict_vcp")
        self.assertEqual(by_ticker["BBB"]["shape_state"], "tight_platform")
        self.assertEqual(by_ticker["CCC"]["shape_state"], "near_pivot")
        self.assertEqual(by_ticker["DDD"]["shape_state"], "none")
        self.assertEqual(by_ticker["OLD"]["shape_state"], "inactive")
        self.assertEqual(by_ticker["AAA"]["momentum_percentile"], 100.0)
        self.assertEqual(by_ticker["AAA"]["momentum_factor_key"], "mom_12_1")
        self.assertEqual(
            by_ticker["AAA"]["momentum_percentile_unit"], "percentile_0_100"
        )
        self.assertEqual(by_ticker["AAA"]["volatility"], 10.0)
        self.assertEqual(
            by_ticker["AAA"]["volatility_unit"], "annualized_percent"
        )
        self.assertIsNone(by_ticker["OLD"]["momentum_percentile"])
        self.assertIsNone(by_ticker["OLD"]["volatility"])

        module_uri = (
            Path(__file__).resolve().parents[1] / "web/static/js/universe.js"
        ).as_uri()
        script = f"""
            import {{ filterTickers, sortTickers }} from {json.dumps(module_uri)};
            const rows = {json.dumps(response.json["tickers"])};
            console.log(JSON.stringify({{
              strict: filterTickers(rows, '', {{strictVcp: true}}).map(row => row.ticker),
              tight: filterTickers(rows, '', {{tightPlatform: true}}).map(row => row.ticker),
              near: filterTickers(rows, '', {{nearPivot: true}}).map(row => row.ticker),
              momentum: sortTickers(rows, 'momentum_percentile', 'desc').map(row => row.ticker),
              volatility: sortTickers(rows, 'volatility', 'asc').map(row => row.ticker)
            }}));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        actual = json.loads(result.stdout)
        self.assertEqual(actual["strict"], ["AAA"])
        self.assertEqual(actual["tight"], ["BBB"])
        self.assertEqual(actual["near"], ["CCC"])
        self.assertEqual(actual["momentum"][:3], ["AAA", "BBB", "CCC"])
        self.assertEqual(actual["volatility"][:3], ["AAA", "BBB", "CCC"])

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

    def test_stale_stock_uses_peers_truncated_to_its_observation_date(self):
        repository = StaleSelectionRepository()
        client = create_app(
            {"TESTING": True}, repository, FakeManager()
        ).test_client()

        response = client.get("/api/stocks/OLD")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["observation_date"], "2026-07-15")
        factor = next(
            factor
            for factor in response.json["factors"]
            if factor["key"] == "close_vs_ema20_pct"
        )
        self.assertEqual(factor["observation_date"], "2026-07-15")
        self.assertIsNotNone(factor["percentile"])
        self.assertIn("inactive_ticker", response.json["warnings"])
        for ticker in ("P1", "P2", "P3", "P4", "P5"):
            self.assertIn(
                ("load_history", ticker, pd.Timestamp("2026-07-15")),
                repository.calls,
            )

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
