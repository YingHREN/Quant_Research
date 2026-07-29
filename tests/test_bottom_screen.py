from __future__ import annotations

import unittest

import numpy as np

from tests.helpers import make_ohlcv
from web.services.universe import build_bottom_screen_summary


class BottomScreenSummaryTest(unittest.TestCase):
    def test_stabilizing_downtrend_enters_a_filterable_bottom_stage(self):
        declining = np.linspace(140.0, 100.0, 80)
        stabilizing = np.array(
            [99.5, 99.2, 99.4, 99.1, 99.6, 99.8, 100.1, 100.4, 100.8, 101.0]
        )
        closes = np.concatenate([declining, stabilizing])
        volumes = np.full(len(closes), 1_000_000.0)
        volumes[-3:] = [1_100_000.0, 1_300_000.0, 1_400_000.0]
        history = make_ohlcv(
            closes,
            opens=closes - 0.3,
            highs=closes + 0.8,
            lows=closes - 0.8,
            volumes=volumes,
        )

        result = build_bottom_screen_summary(history)

        self.assertTrue(result["bottoming_candidate"])
        self.assertIn(
            result["bottom_state"],
            {
                "potential_support",
                "seller_exhaustion_watch",
                "early_bullish_reversal_watch",
                "bullish_structure_confirmed",
                "breakout_retest_confirmed",
            },
        )
        self.assertEqual(
            result["bottom_model_key"],
            "bottoming_reversal_state_v1",
        )
        self.assertEqual(result["bottom_screen_source"], "lightweight_90d_v1")

    def test_healthy_uptrend_and_short_history_are_not_candidates(self):
        uptrend = make_ohlcv(np.linspace(100.0, 140.0, 90))
        short = make_ohlcv(np.linspace(100.0, 90.0, 40))

        up_result = build_bottom_screen_summary(uptrend)
        short_result = build_bottom_screen_summary(short)

        self.assertFalse(up_result["bottoming_candidate"])
        self.assertEqual(up_result["bottom_state"], "unavailable")
        self.assertFalse(short_result["bottoming_candidate"])
        self.assertEqual(short_result["bottom_state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
