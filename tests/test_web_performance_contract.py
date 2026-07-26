"""Deterministic operation-count contracts for dashboard request performance."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd

from web.app import create_app
from web.factors.builtin import build_chart_rows
from web.factors.registry import FactorRegistry
from web.services.forecast_artifacts import ForecastArtifactStore
from web.services.forecast_warmup import ForecastCacheWarmer
from web.services.forecasts import ForecastService
from web.services.update_jobs import UpdateJobManager
from web.services.entry_signals import (
    EntrySignalArtifactStore,
    EntrySignalService,
)

from tests.test_web_api import (
    FakeManager,
    FakeRepository,
    price_history,
    test_config,
)


class ConstantFactor:
    key = "constant"
    label = "Constant"
    group = "test"
    direction = "higher"
    description = "Operation-count fixture."
    methodology = "Returns a constant without constructing a chart."
    overview = True
    version = "test-v1"

    def compute(self, context):
        return 1.0

    def format(self, value):
        return str(value)


class WebPerformanceContractTest(unittest.TestCase):
    def test_new_entry_service_uses_persistent_rows_without_replaying_history(self):
        history = price_history(periods=80)
        rows = [
            {
                "time": timestamp.date().isoformat(),
                "strict_vcp_active": False,
            }
            for timestamp in history.index
        ]
        with tempfile.TemporaryDirectory() as temporary:
            store = EntrySignalArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            with patch(
                "web.services.entry_signals.build_entry_signal_rows",
                return_value=rows,
            ) as build:
                EntrySignalService(artifact_store=store).build(
                    "AAA",
                    history,
                )
                EntrySignalService(artifact_store=store).build(
                    "AAA",
                    history.copy(),
                )

        self.assertEqual(build.call_count, 1)

    def test_selected_stock_builds_one_chart_regardless_of_peer_count(self):
        small_count = self._chart_build_count(peer_count=2)
        large_count = self._chart_build_count(peer_count=40)

        self.assertEqual(small_count, 1)
        self.assertEqual(large_count, 1)

    def test_new_service_uses_persistent_artifact_without_rebuilding(self):
        histories = {
            "AAA": price_history(periods=80),
            "BBB": price_history(periods=80, offset=10),
        }
        with tempfile.TemporaryDirectory() as temporary:
            store = ForecastArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            ForecastService(artifact_store=store).prewarm(histories)

            with patch(
                "web.services.forecasts.build_feature_frame",
                side_effect=AssertionError("persistent hit rebuilt features"),
            ), patch(
                "web.services.forecasts.build_forecast_risk_context",
                side_effect=AssertionError("persistent hit rebuilt risk"),
            ):
                result = ForecastService(artifact_store=store).prewarm(
                    histories
                )

            self.assertGreater(result["row_count"], 0)
            self.assertEqual(store.entry_count(), 1)

    def test_price_update_invalidates_then_persists_reusable_warm_artifact(self):
        class UpdatingRepository:
            def __init__(self):
                self.histories = {
                    "AAA": price_history(periods=80, end="2026-07-21"),
                    "BBB": price_history(
                        periods=80,
                        end="2026-07-21",
                        offset=10,
                    ),
                }

            def list_summaries(self):
                return [
                    SimpleNamespace(
                        ticker=ticker,
                        latest_date=history.index[-1].date().isoformat(),
                        inactive=False,
                    )
                    for ticker, history in sorted(self.histories.items())
                ]

            def upsert_history(self, ticker, frame):
                self.histories[ticker] = frame.copy()

            def load_universe_histories(self, asof=None):
                cutoff = None if asof is None else pd.Timestamp(asof)
                return {
                    ticker: (
                        history.copy()
                        if cutoff is None
                        else history.loc[history.index <= cutoff].copy()
                    )
                    for ticker, history in self.histories.items()
                }

        class UpdatingProvider:
            def fetch_history(self, ticker):
                offset = 0 if ticker == "AAA" else 10
                return price_history(
                    periods=81,
                    end="2026-07-22",
                    offset=offset,
                )

        repository = UpdatingRepository()
        events = []
        with tempfile.TemporaryDirectory() as temporary:
            store = ForecastArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            service = ForecastService(artifact_store=store)
            warmer = ForecastCacheWarmer(repository, service)

            def invalidate():
                events.append("invalidate")
                service.invalidate()

            def warm():
                events.append("warm")
                return warmer()

            manager = UpdateJobManager(
                repository,
                UpdatingProvider(),
                on_success=invalidate,
                on_cache_warmup=warm,
            )
            snapshot = manager.run_synchronously_for_test()

            self.assertEqual(events, ["invalidate", "warm"])
            self.assertEqual(snapshot.state, "completed")
            self.assertEqual(snapshot.cache_warmup_state, "ready")
            self.assertEqual(snapshot.cache_warmup_cohorts, ("2026-07-22",))

            histories = repository.load_universe_histories(
                pd.Timestamp("2026-07-22")
            )
            with patch(
                "web.services.forecasts.build_feature_frame",
                side_effect=AssertionError("updated artifact rebuilt features"),
            ), patch(
                "web.services.forecasts.build_forecast_risk_context",
                side_effect=AssertionError("updated artifact rebuilt risk"),
            ):
                restored = ForecastService(artifact_store=store).prewarm(
                    histories
                )

            self.assertGreater(restored["row_count"], 0)
            self.assertEqual(store.entry_count(), 1)

    @staticmethod
    def _chart_build_count(peer_count):
        repository = FakeRepository()
        repository.histories.update(
            {
                f"P{position:02d}": price_history(offset=position)
                for position in range(peer_count)
            }
        )
        app = create_app(
            test_config(FACTOR_REGISTRY=FactorRegistry([ConstantFactor()])),
            repository,
            FakeManager(),
        )

        with patch("web.app.build_chart_rows", wraps=build_chart_rows) as build:
            response = app.test_client().get("/api/stocks/AAA")

        if response.status_code != 200:
            raise AssertionError(response.get_json())
        return build.call_count


if __name__ == "__main__":
    unittest.main()
