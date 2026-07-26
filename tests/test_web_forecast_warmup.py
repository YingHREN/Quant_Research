from types import SimpleNamespace
import unittest
from unittest import mock

import pandas as pd

from web.services.forecast_warmup import ForecastCacheWarmer


class ForecastCacheWarmerTest(unittest.TestCase):
    def test_selects_two_newest_active_cohorts_and_warms_oldest_first(self):
        repository = mock.Mock()
        repository.list_summaries.return_value = [
            SimpleNamespace(latest_date="2026-07-24", inactive=False),
            SimpleNamespace(latest_date="2026-07-23", inactive=False),
            SimpleNamespace(latest_date="2026-07-22", inactive=False),
            SimpleNamespace(latest_date="2025-01-01", inactive=True),
        ]
        repository.load_universe_histories.side_effect = (
            lambda asof: {asof.date().isoformat(): pd.DataFrame()}
        )
        service = mock.Mock()
        service.prewarm.side_effect = [
            {"row_count": 90, "risk_row_count": 20},
            {"row_count": 100, "risk_row_count": 22},
        ]

        result = ForecastCacheWarmer(repository, service)()

        self.assertEqual(
            [call.args[0] for call in repository.load_universe_histories.call_args_list],
            [pd.Timestamp("2026-07-23"), pd.Timestamp("2026-07-24")],
        )
        self.assertEqual(
            result["cohorts"],
            [
                {"asof": "2026-07-23", "row_count": 90, "risk_row_count": 20},
                {"asof": "2026-07-24", "row_count": 100, "risk_row_count": 22},
            ],
        )
        self.assertEqual(result["state"], "ready")
        self.assertGreaterEqual(result["elapsed_seconds"], 0)

    def test_constructor_rejects_incompatible_dependencies(self):
        with self.assertRaises(TypeError):
            ForecastCacheWarmer(object(), mock.Mock())
        repository = mock.Mock()
        repository.list_summaries = None
        with self.assertRaises(TypeError):
            ForecastCacheWarmer(repository, mock.Mock())


if __name__ == "__main__":
    unittest.main()
