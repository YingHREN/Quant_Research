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

    def test_warmup_passes_effective_dated_assignments_to_forecast_service(self):
        repository = mock.Mock()
        repository.list_summaries.return_value = [
            SimpleNamespace(latest_date="2026-07-24", inactive=False),
        ]
        history = pd.DataFrame(
            {"Close": [1.0, 2.0]},
            index=pd.DatetimeIndex(["2026-07-01", "2026-07-24"]),
        )
        repository.load_universe_histories.return_value = {"AAA": history}
        assignment_repository = mock.Mock()
        assignment_payload = {
            "status": "available",
            "revision": 17,
            "by_ticker": {
                "AAA": [
                    {
                        "state": "assigned",
                        "ticker": "AAA",
                        "effective_from": "2026-01-01",
                        "effective_to": None,
                    }
                ]
            },
        }
        assignment_repository.build_history.return_value = assignment_payload
        service = mock.Mock()
        service.prewarm.return_value = {
            "row_count": 2,
            "risk_row_count": 2,
        }

        ForecastCacheWarmer(
            repository,
            service,
            group_assignment_repository=assignment_repository,
        )()

        assignment_repository.build_history.assert_called_once_with(
            ("AAA",),
            start_asof="2026-07-01",
            end_asof="2026-07-24",
        )
        service.prewarm.assert_called_once_with(
            {"AAA": history},
            assignments=assignment_payload,
        )


if __name__ == "__main__":
    unittest.main()
