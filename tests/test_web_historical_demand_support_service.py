from __future__ import annotations

import unittest

import numpy as np

from tests.helpers import make_ohlcv
from web.services.historical_demand_support import (
    attach_historical_demand_support_rows,
)


class HistoricalDemandSupportChartServiceTest(unittest.TestCase):
    def test_attaches_json_safe_zone_and_merges_nearby_support(self):
        closes = np.linspace(90.0, 110.0, 45)
        history = make_ohlcv(
            closes,
            opens=closes - 0.4,
            highs=closes + 1.0,
            lows=closes - 1.0,
            volumes=np.full(45, 1_000_000.0),
        )
        event_position = 25
        history.iloc[event_position, history.columns.get_loc("Volume")] = (
            2_000_000.0
        )
        chart = []
        for position, (date, close) in enumerate(history["Close"].items()):
            chart.append(
                {
                    "time": date.date().isoformat(),
                    "close": float(close),
                    "demand_confirmation_conditions": (
                        ["buyer_absorption"]
                        if position == event_position
                        else []
                    ),
                    "supply_pressure_conditions": [],
                    "pocket_pivot": False,
                    "near_support_lower": float(close - 2.5),
                    "near_support_upper": float(close - 1.0),
                    "near_support_mid": float(close - 1.75),
                    "near_support_distance_pct": 0.92,
                    "near_support_score": 45,
                    "near_support_sources": ["sma50"],
                    "near_support_state": "above",
                }
            )

        attach_historical_demand_support_rows(
            chart,
            "AAA",
            {"AAA": history, "QQQ": history},
        )

        row = chart[event_position]
        self.assertEqual(
            row["historical_demand_support_model_key"],
            "historical_demand_support_v1",
        )
        self.assertEqual(
            row["historical_demand_support_event_types"],
            ["buyer_absorption"],
        )
        self.assertIsInstance(row["historical_demand_support_score"], float)
        self.assertIn("historical_demand_zone", row["near_support_sources"])

    def test_missing_history_preserves_chart_and_emits_typed_unavailable(self):
        chart = [{"time": "2026-07-01", "close": 100.0, "ridge": 0.02}]

        attach_historical_demand_support_rows(chart, "AAA", {})

        self.assertEqual(chart[0]["ridge"], 0.02)
        self.assertEqual(
            chart[0]["historical_demand_support_state"],
            "unavailable",
        )
        self.assertEqual(
            chart[0]["historical_demand_support_unavailable_reason"],
            "model_data_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
