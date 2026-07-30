from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.bottom_state_replay import build_bottom_state_replay
from tests.helpers import make_ohlcv
from web.services.bottom_state import bottom_evidence_frame


def _histories(length=90):
    closes = np.linspace(140.0, 100.0, length)
    stock = make_ohlcv(
        closes,
        opens=closes + 0.3,
        highs=closes + 1.0,
        lows=closes - 1.0,
        volumes=np.linspace(900_000.0, 1_100_000.0, length),
    )
    benchmark = make_ohlcv(
        np.linspace(400.0, 420.0, length),
        volumes=np.full(length, 5_000_000.0),
    )
    return {"AAA": stock, "QQQ": benchmark, "SPY": benchmark.copy()}


class BottomStateReplayContractTest(unittest.TestCase):
    def test_bottom_evidence_frame_preserves_model_input_contract(self):
        chart = [
            {
                "time": "2026-01-02",
                "near_support_lower": 98.0,
                "near_support_upper": 100.0,
                "near_support_score": 70.0,
                "near_support_state": "testing",
                "historical_demand_support_state": "accepted",
                "historical_demand_support_score": 82.0,
                "historical_demand_support_invalidation_level": 96.0,
                "demand_confirmation_score": 65.0,
                "demand_confirmation_coverage": 1.0,
                "demand_confirmation_conditions": ["buyer_absorption"],
                "supply_pressure_score": 20.0,
                "supply_pressure_conditions": [],
                "early_reversal_score": 75.0,
                "early_reversal_watch": True,
                "prior_high_breakout": False,
                "trendline_breakout": True,
                "higher_low_confirmed": True,
                "market_regime_gate": {
                    "market_state": "confirmed_uptrend",
                },
            }
        ]

        evidence = bottom_evidence_frame(chart)

        self.assertEqual(list(evidence.index), [pd.Timestamp("2026-01-02")])
        self.assertEqual(evidence.iloc[0]["near_support_state"], "testing")
        self.assertEqual(
            evidence.iloc[0]["market_regime_state"],
            "confirmed_uptrend",
        )
        self.assertEqual(
            evidence.iloc[0]["demand_confirmation_conditions"],
            ["buyer_absorption"],
        )

    def test_replay_returns_aligned_evidence_and_state_rows(self):
        histories = _histories()

        evidence, states = build_bottom_state_replay("aaa", histories)

        self.assertTrue(evidence.index.equals(histories["AAA"].index))
        self.assertTrue(states.index.equals(histories["AAA"].index))
        self.assertEqual(len(states), len(histories["AAA"]))
        self.assertEqual(
            set(states["bottom_model_key"]),
            {"bottoming_reversal_state_v1"},
        )

    def test_appending_future_prices_does_not_rewrite_replayed_prefix(self):
        prefix = _histories()
        extended = {}
        for ticker, history in prefix.items():
            future_index = pd.bdate_range(
                history.index[-1] + pd.Timedelta(days=1),
                periods=3,
            )
            future = make_ohlcv(
                np.linspace(
                    float(history["Close"].iloc[-1]) + 1.0,
                    float(history["Close"].iloc[-1]) + 3.0,
                    3,
                )
            )
            future.index = future_index
            extended[ticker] = pd.concat((history, future))

        expected = build_bottom_state_replay("AAA", prefix)[1]
        actual = build_bottom_state_replay("AAA", extended)[1].iloc[
            : len(expected)
        ]

        pd.testing.assert_frame_equal(actual, expected)


if __name__ == "__main__":
    unittest.main()
