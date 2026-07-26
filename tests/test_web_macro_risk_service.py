import tempfile
import unittest
from pathlib import Path

from web.services.macro_risk import MacroRiskService
from web.services.macro_store import MacroObservationStore


class MacroRiskServiceTest(unittest.TestCase):
    def test_store_returns_only_vintages_available_by_requested_time(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MacroObservationStore(Path(directory) / "macro.db")
            store.initialize()
            store.upsert(
                [
                    {
                        "series_id": "DGS2",
                        "observation_date": "2026-07-01",
                        "available_at": "2026-07-01T18:00:00+00:00",
                        "value": 4.7,
                        "realtime_start": "2026-07-01",
                        "realtime_end": "2026-07-24",
                        "source": "FRED",
                    },
                    {
                        "series_id": "DGS2",
                        "observation_date": "2026-07-01",
                        "available_at": "2026-07-25T18:00:00+00:00",
                        "value": 3.1,
                        "realtime_start": "2026-07-25",
                        "realtime_end": "9999-12-31",
                        "source": "ALFRED",
                    },
                ]
            )

            rows = store.load_available(
                "2026-07-20T23:59:59+00:00",
                series_ids=("DGS2",),
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(float(rows.iloc[0]["value"]), 4.7)

    def test_missing_store_degrades_without_creating_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            service = MacroRiskService(path)

            result = service.build("2026-07-20")

            self.assertEqual(result["state"], "unavailable")
            self.assertEqual(
                result["unavailable_reason"],
                "macro_data_unavailable",
            )
            self.assertFalse(path.exists())

    def test_attach_rows_uses_each_chart_date_point_in_time(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MacroObservationStore(Path(directory) / "macro.db")
            store.initialize()
            store.upsert(
                [
                    {
                        "series_id": "VIXCLS",
                        "observation_date": "2026-07-01",
                        "available_at": "2026-07-01T18:00:00+00:00",
                        "value": 18.0,
                        "realtime_start": "2026-07-01",
                        "realtime_end": "9999-12-31",
                        "source": "FRED",
                    }
                ]
            )
            service = MacroRiskService(store.path)
            chart = [{"time": "2026-06-30"}, {"time": "2026-07-02"}]

            service.attach_chart_rows(chart)

            self.assertEqual(
                chart[0]["macro_risk_unavailable_reason"],
                "insufficient_macro_coverage",
            )
            self.assertIn(
                chart[1]["macro_risk_unavailable_reason"],
                {"insufficient_macro_coverage", None},
            )

    def test_attach_rows_can_limit_work_to_forecast_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            service = MacroRiskService(path)
            chart = [{"time": "2026-07-01"}, {"time": "2026-07-02"}]

            service.attach_chart_rows(chart, dates=("2026-07-02",))

            self.assertNotIn("macro_risk_score", chart[0])
            self.assertEqual(
                chart[1]["macro_risk_unavailable_reason"],
                "macro_data_unavailable",
            )


if __name__ == "__main__":
    unittest.main()
