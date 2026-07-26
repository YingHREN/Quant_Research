from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import pandas as pd

import build_forecast_cache


class BuildForecastCacheTest(unittest.TestCase):
    def test_main_loads_latest_snapshot_prewarms_and_prints_json(self):
        history = pd.DataFrame(
            {"Close": [1.0]},
            index=pd.DatetimeIndex(["2026-07-24"]),
        )

        class Repository:
            instances = []

            def __init__(self, path):
                self.path = Path(path)
                self.calls = []
                self.__class__.instances.append(self)

            def freshness(self):
                self.calls.append(("freshness",))
                return {"latest_date": "2026-07-24", "by_date": []}

            def load_universe_histories(self, asof):
                self.calls.append(("load_universe_histories", asof))
                return {f"AAA-{asof.date().isoformat()}": history}

            def list_summaries(self):
                self.calls.append(("list_summaries",))
                return [
                    SimpleNamespace(
                        ticker="AAA",
                        latest_date="2026-07-24",
                        inactive=False,
                    ),
                    SimpleNamespace(
                        ticker="BBB",
                        latest_date="2026-07-23",
                        inactive=False,
                    ),
                ]

        service = mock.Mock()
        service.prewarm.return_value = {
            "database_revision": 0,
            "row_count": 123,
            "risk_row_count": 45,
            "evaluation_horizons": ["5", "20", "60"],
        }
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            build_forecast_cache,
            "MarketDataRepository",
            Repository,
        ), mock.patch.object(
            build_forecast_cache,
            "ForecastService",
            return_value=service,
        ), redirect_stdout(output):
            database = Path(temporary) / "prices.db"
            cache = Path(temporary) / "analysis_cache.db"
            result = build_forecast_cache.main(
                ["--database", str(database), "--cache", str(cache)]
            )

        self.assertEqual(result, 0)
        repository = Repository.instances[-1]
        self.assertEqual(
            repository.calls,
            [
                ("freshness",),
                ("list_summaries",),
                (
                    "load_universe_histories",
                    pd.Timestamp("2026-07-24"),
                ),
                (
                    "load_universe_histories",
                    pd.Timestamp("2026-07-23"),
                ),
            ],
        )
        self.assertEqual(service.prewarm.call_count, 2)
        self.assertEqual(
            service.prewarm.call_args_list,
            [
                mock.call({"AAA-2026-07-24": history}),
                mock.call({"AAA-2026-07-23": history}),
            ],
        )
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["asof"], "2026-07-24")
        self.assertEqual(payload["row_count"], 123)
        self.assertEqual(payload["cache_path"], str(cache))
        self.assertEqual(payload["cohorts"][0]["asof"], "2026-07-24")
        self.assertEqual(payload["cohorts"][1]["asof"], "2026-07-23")
        self.assertGreaterEqual(payload["elapsed_seconds"], 0)

    def test_main_returns_nonzero_with_safe_error(self):
        output = io.StringIO()
        with mock.patch.object(
            build_forecast_cache,
            "MarketDataRepository",
            side_effect=RuntimeError("/private/secret.db"),
        ), redirect_stdout(output):
            result = build_forecast_cache.main(
                ["--database", "prices.db", "--cache", "analysis_cache.db"]
            )

        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"error": "forecast_cache_build_failed"},
        )


if __name__ == "__main__":
    unittest.main()
