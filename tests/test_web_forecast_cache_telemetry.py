from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from web.app import _unavailable_cache_status
from web.services.forecast_artifacts import ForecastArtifactStore
from web.services.forecasts import ForecastService


class _Provider:
    model_key = "telemetry_test_direction"
    model_version = "v1"


class _ProviderFactory:
    model_key = _Provider.model_key
    model_version = _Provider.model_version

    def __init__(self, failures=0):
        self._failures = failures
        self.call_count = 0

    def __call__(self, _frame):
        self.call_count += 1
        if self.call_count <= self._failures:
            raise ValueError("persisted frame is incompatible")
        return _Provider()


def _price_history(*, offset=0.0):
    index = pd.bdate_range(end="2026-07-21", periods=80)
    close = np.linspace(100.0 + offset, 140.0 + offset, len(index))
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.linspace(1_000_000.0, 1_200_000.0, len(index)),
        },
        index=index,
    )


class ForecastCacheTelemetryTest(unittest.TestCase):
    def test_store_reported_failure_is_authoritative_without_double_counting(self):
        class CountingFailingStore:
            def __init__(self):
                self.failure_count = 0

            def load(self, _identity, _market_signature):
                self.failure_count += 1
                raise RuntimeError("store boundary failed")

            def save(self, _identity, _market_signature, _artifact):
                return True

            def status(self):
                return {"failure_count": self.failure_count}

        service = ForecastService(
            provider_factory=_ProviderFactory(),
            artifact_store=CountingFailingStore(),
        )

        service.prewarm(
            {
                "AAA": _price_history(),
                "BBB": _price_history(offset=10.0),
            }
        )

        self.assertEqual(service.cache_status()["failure_count"], 1)

    def test_store_without_status_uses_stable_service_boundary_failures(self):
        class BoundaryFailingStore:
            def load(self, _identity, _market_signature):
                raise RuntimeError("store boundary failed")

            def save(self, _identity, _market_signature, _artifact):
                return True

        service = ForecastService(
            provider_factory=_ProviderFactory(),
            artifact_store=BoundaryFailingStore(),
        )

        service.prewarm(
            {
                "AAA": _price_history(),
                "BBB": _price_history(offset=10.0),
            }
        )

        self.assertEqual(service.cache_status()["failure_count"], 1)
        self.assertEqual(service.cache_status()["failure_count"], 1)

    def test_malformed_store_failure_count_keeps_direct_status_safe(self):
        class MalformedStatusStore:
            def load(self, _identity, _market_signature):
                return None

            def save(self, _identity, _market_signature, _artifact):
                return True

            def status(self):
                return {"failure_count": "not-a-count"}

        status = ForecastService(
            provider_factory=_ProviderFactory(),
            artifact_store=MalformedStatusStore(),
        ).cache_status()

        self.assertEqual(status["failure_count"], 0)
        self.assertEqual(status["access_count"], 0)

    def test_invalid_disk_artifact_provider_falls_back_to_counted_rebuild(self):
        histories = {
            "AAA": _price_history(),
            "BBB": _price_history(offset=10.0),
        }
        with tempfile.TemporaryDirectory() as temporary:
            store = ForecastArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            ForecastService(
                provider_factory=_ProviderFactory(),
                artifact_store=store,
            ).prewarm(histories)
            restoring_factory = _ProviderFactory(failures=1)
            restoring_service = ForecastService(
                provider_factory=restoring_factory,
                artifact_store=store,
            )

            restoring_service.prewarm(histories)
            status = restoring_service.cache_status()

            self.assertEqual(restoring_factory.call_count, 2)
            self.assertEqual(status["access_count"], 1)
            self.assertEqual(status["disk_hit_count"], 0)
            self.assertEqual(status["rebuild_count"], 1)
            self.assertEqual(status["rebuild_failure_count"], 0)
            self.assertEqual(status["failure_count"], 1)
            self.assertEqual(status["last_access"], "rebuilt")
            self.assertTrue(status["memory_ready"])

    def test_unavailable_api_status_keeps_the_telemetry_contract(self):
        status = _unavailable_cache_status()

        self.assertEqual(status["access_count"], 0)
        self.assertEqual(status["memory_hit_count"], 0)
        self.assertEqual(status["disk_hit_count"], 0)
        self.assertEqual(status["rebuild_count"], 0)
        self.assertEqual(status["rebuild_failure_count"], 0)
        self.assertIsNone(status["hit_rate"])
        self.assertEqual(status["failure_count"], 0)
        self.assertIsNone(status["last_read_seconds"])
        self.assertIsNone(status["last_rebuild_seconds"])

    def test_status_tracks_rebuild_memory_hit_disk_hit_and_timings(self):
        histories = {
            "AAA": _price_history(),
            "BBB": _price_history(offset=10.0),
        }
        with tempfile.TemporaryDirectory() as temporary:
            store = ForecastArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            first_service = ForecastService(artifact_store=store)

            first_service.prewarm(histories)
            rebuilt = first_service.cache_status()

            self.assertEqual(rebuilt["access_count"], 1)
            self.assertEqual(rebuilt["memory_hit_count"], 0)
            self.assertEqual(rebuilt["disk_hit_count"], 0)
            self.assertEqual(rebuilt["rebuild_count"], 1)
            self.assertEqual(rebuilt["rebuild_failure_count"], 0)
            self.assertEqual(rebuilt["hit_rate"], 0.0)
            self.assertEqual(rebuilt["failure_count"], 0)
            self.assertGreaterEqual(rebuilt["last_rebuild_seconds"], 0.0)
            self.assertGreater(rebuilt["size_bytes"], 0)

            first_service.prewarm(histories)
            memory_hit = first_service.cache_status()

            self.assertEqual(memory_hit["access_count"], 2)
            self.assertEqual(memory_hit["memory_hit_count"], 1)
            self.assertEqual(memory_hit["disk_hit_count"], 0)
            self.assertEqual(memory_hit["rebuild_count"], 1)
            self.assertEqual(memory_hit["hit_rate"], 0.5)

            second_service = ForecastService(artifact_store=store)
            second_service.prewarm(histories)
            disk_hit = second_service.cache_status()

            self.assertEqual(disk_hit["access_count"], 1)
            self.assertEqual(disk_hit["memory_hit_count"], 0)
            self.assertEqual(disk_hit["disk_hit_count"], 1)
            self.assertEqual(disk_hit["rebuild_count"], 0)
            self.assertEqual(disk_hit["hit_rate"], 1.0)
            self.assertEqual(disk_hit["failure_count"], 0)
            self.assertGreaterEqual(disk_hit["last_read_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
