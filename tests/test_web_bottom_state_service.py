from __future__ import annotations

import unittest

import numpy as np

from tests.helpers import make_ohlcv
from web.services.bottom_state import attach_bottom_state_rows


class BottomStateWebServiceTest(unittest.TestCase):
    def test_attaches_aligned_bottom_state_without_changing_chart_length(self):
        closes = np.linspace(140.0, 100.0, 90)
        history = make_ohlcv(
            closes,
            opens=closes + 0.4,
            highs=closes + 1.2,
            lows=closes - 1.2,
            volumes=np.full(90, 1_000_000.0),
        )
        chart = []
        for date, close in history["Close"].items():
            chart.append(
                {
                    "time": date.date().isoformat(),
                    "close": float(close),
                    "near_support_lower": float(close * 0.985),
                    "near_support_upper": float(close * 0.995),
                    "near_support_score": 70.0,
                    "near_support_state": "testing",
                    "historical_demand_support_state": "testing",
                    "historical_demand_support_score": 82.0,
                    "historical_demand_support_invalidation_level": float(
                        close * 0.98
                    ),
                    "demand_confirmation_score": 55.0,
                    "demand_confirmation_coverage": 1.0,
                    "demand_confirmation_conditions": [],
                    "supply_pressure_score": 10.0,
                    "supply_pressure_conditions": [],
                    "early_reversal_score": 0.0,
                    "early_reversal_watch": False,
                    "prior_high_breakout": False,
                    "trendline_breakout": False,
                    "higher_low_confirmed": False,
                    "market_regime_gate": {
                        "market_state": "market_in_correction",
                    },
                }
            )

        attach_bottom_state_rows(chart, history)

        self.assertEqual(len(chart), len(history))
        self.assertEqual(chart[-1]["bottom_state"], "potential_support")
        self.assertEqual(
            chart[-1]["bottom_model_key"],
            "bottoming_reversal_state_v1",
        )
        self.assertIsInstance(chart[-1]["bottom_conditions"], list)
        self.assertIsInstance(
            chart[-1]["bottom_counter_conditions"],
            list,
        )
        self.assertIn(
            "historical_demand_support",
            chart[-1]["bottom_conditions"],
        )
        self.assertEqual(chart[-1]["bottom_demand_score"], 13.75)

    def test_missing_history_produces_typed_unavailable_rows(self):
        chart = [{"time": "2026-07-01", "close": 100.0}]

        attach_bottom_state_rows(chart, None)

        self.assertEqual(chart[0]["bottom_state"], "unavailable")
        self.assertEqual(
            chart[0]["bottom_unavailable_reason"],
            "model_data_unavailable",
        )
        self.assertEqual(chart[0]["bottom_coverage"], 0.0)


if __name__ == "__main__":
    unittest.main()
