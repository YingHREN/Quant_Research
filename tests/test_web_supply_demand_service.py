import copy
import unittest

import numpy as np
import pandas as pd

from tests.helpers import make_ohlcv
from web.services.supply_demand import attach_supply_demand_rows


class SupplyDemandChartServiceTest(unittest.TestCase):
    def test_unavailable_defaults_are_attached_without_adding_chart_rows(self):
        chart = [{"time": "2026-07-01", "close": 100.0}]
        dates = [row["time"] for row in chart]

        attach_supply_demand_rows(chart, "AAA", {})

        self.assertEqual([row["time"] for row in chart], dates)
        self.assertEqual(len(chart), 1)
        self.assertEqual(
            chart[0]["supply_pressure_model_key"],
            "supply_pressure_v1",
        )
        self.assertEqual(
            chart[0]["demand_confirmation_model_key"],
            "demand_confirmation_v1",
        )
        self.assertIsNone(chart[0]["supply_pressure_score"])
        self.assertIsNone(chart[0]["demand_confirmation_score"])
        self.assertEqual(chart[0]["supply_demand_state"], "unavailable")

    def test_matching_dates_receive_json_safe_model_fields(self):
        history = _history(60)
        chart = [
            {"time": history.index[-2].date().isoformat(), "close": 100.0},
            {"time": history.index[-1].date().isoformat(), "close": 100.0},
        ]
        original_history = history.copy(deep=True)

        attach_supply_demand_rows(
            chart,
            "AAA",
            {"AAA": history, "QQQ": _history(60)},
        )

        self.assertEqual(len(chart), 2)
        self.assertIsInstance(chart[-1]["supply_pressure_score"], float)
        self.assertIsInstance(chart[-1]["demand_confirmation_score"], float)
        self.assertGreater(chart[-1]["supply_pressure_coverage"], 0.75)
        self.assertGreater(chart[-1]["demand_confirmation_coverage"], 0.75)
        self.assertIsInstance(chart[-1]["supply_pressure_conditions"], list)
        self.assertIsInstance(
            chart[-1]["demand_confirmation_conditions"],
            list,
        )
        self.assertIsInstance(chart[-1]["unavailable_reasons"], list)
        pd.testing.assert_frame_equal(history, original_history)

    def test_known_theme_uses_available_normalized_benchmarks(self):
        stock = _history(60)
        qqq = _history(60)
        benchmark = _history(60)
        benchmark.loc[:, "Close"] = np.linspace(100.0, 125.0, len(benchmark))
        benchmark.loc[:, "Open"] = benchmark["Close"]
        benchmark.loc[:, "High"] = benchmark["Close"] * 1.01
        benchmark.loc[:, "Low"] = benchmark["Close"] * 0.99
        chart = [
            {"time": stock.index[-1].date().isoformat(), "close": 100.0}
        ]
        histories = {
            "MU": stock,
            "QQQ": qqq,
            "SOXX": benchmark,
        }
        original = copy.deepcopy(histories)

        attach_supply_demand_rows(chart, "MU", histories)

        self.assertIn(
            "relative_strength_breakdown_sector",
            chart[0]["supply_pressure_conditions"],
        )
        self.assertNotIn(
            "missing_sector_context",
            chart[0]["unavailable_reasons"],
        )
        for ticker in histories:
            pd.testing.assert_frame_equal(histories[ticker], original[ticker])

    def test_unknown_theme_keeps_stock_and_qqq_evidence(self):
        stock = _history(60)
        chart = [
            {"time": stock.index[-1].date().isoformat(), "close": 100.0}
        ]

        attach_supply_demand_rows(
            chart,
            "AAA",
            {"AAA": stock, "QQQ": _history(60)},
        )

        self.assertIsNotNone(chart[0]["supply_pressure_score"])
        self.assertIsNotNone(chart[0]["demand_confirmation_score"])
        self.assertIn(
            "missing_sector_context",
            chart[0]["unavailable_reasons"],
        )

    def test_invalid_history_degrades_only_supply_demand_fields(self):
        chart = [
            {
                "time": "2026-07-01",
                "close": 100.0,
                "ridge_value": 0.03,
            }
        ]
        invalid = _history(30).drop(columns=["Volume"])

        attach_supply_demand_rows(chart, "AAA", {"AAA": invalid})

        self.assertEqual(chart[0]["ridge_value"], 0.03)
        self.assertEqual(chart[0]["supply_demand_state"], "unavailable")
        self.assertIn("model_input_invalid", chart[0]["unavailable_reasons"])


def _history(length):
    return make_ohlcv(
        np.full(length, 100.0),
        highs=np.full(length, 101.0),
        lows=np.full(length, 99.0),
        volumes=np.full(length, 1_000_000.0),
        start="2026-01-02",
    )


if __name__ == "__main__":
    unittest.main()
