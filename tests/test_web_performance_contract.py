"""Deterministic operation-count contracts for dashboard request performance."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from web.app import create_app
from web.factors.builtin import build_chart_rows
from web.factors.registry import FactorRegistry
from web.services.forecast_artifacts import ForecastArtifactStore
from web.services.forecasts import ForecastService

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
