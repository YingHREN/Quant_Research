from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.resistance import build_near_resistance_rows
from research.reversal import build_reversal_rows


NBIS_CLOSES = [
    256.63,
    240.30,
    261.15,
    276.17,
    229.18,
    215.62,
    213.02,
    195.19,
    216.48,
    216.20,
    219.65,
    210.51,
    194.09,
    199.51,
    171.77,
    177.71,
    182.62,
    216.92,
    218.16,
    220.97,
]
NBIS_HIGHS = [
    275.52,
    248.80,
    263.80,
    290.60,
    246.49,
    237.26,
    224.97,
    207.52,
    218.49,
    229.499,
    224.4799,
    219.00,
    222.75,
    203.25,
    193.10,
    186.7699,
    194.34,
    217.62,
    228.665,
    230.30,
]


def frame_from_closes(closes, *, highs=None, start="2026-04-01"):
    index = pd.bdate_range(start, periods=len(closes))
    close = pd.Series(closes, index=index, dtype=float)
    high = (
        pd.Series(highs, index=index, dtype=float)
        if highs is not None
        else close + 1.0
    )
    low = pd.concat((close - 5.0, high - 8.0), axis=1).min(axis=1)
    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close) - 0.5,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": np.linspace(1_000_000.0, 1_500_000.0, len(close)),
        },
        index=index,
    )


def nbis_shaped_history():
    prefix = [233.513] * 40
    closes = prefix + NBIS_CLOSES
    highs = [price + 4.0 for price in prefix] + NBIS_HIGHS
    return frame_from_closes(closes, highs=highs)


class NearResistanceTest(unittest.TestCase):
    def test_nearest_candidate_cluster_beats_far_twenty_day_pivot(self):
        history = nbis_shaped_history()

        row = build_near_resistance_rows(
            history,
            build_reversal_rows(history),
        )[-1]

        self.assertAlmostEqual(row["near_resistance_lower"], 226.7448, places=4)
        self.assertAlmostEqual(row["near_resistance_upper"], 230.30, places=2)
        self.assertAlmostEqual(row["far_resistance"], 276.17, places=2)
        self.assertIn("sma50", row["near_resistance_sources"])
        self.assertIn("recent_high_10", row["near_resistance_sources"])
        self.assertGreater(row["near_resistance_score"], 0)
        self.assertLessEqual(row["near_resistance_score"], 100)

    def test_single_candidate_expands_to_a_small_atr_zone(self):
        history = frame_from_closes(list(range(61, 101)))

        row = build_near_resistance_rows(
            history,
            build_reversal_rows(history),
        )[-1]

        self.assertEqual(row["near_resistance_sources"], ["recent_high_10"])
        self.assertLess(row["near_resistance_lower"], 101.0)
        self.assertGreater(row["near_resistance_upper"], 101.0)
        self.assertGreaterEqual(row["near_resistance_lower"], 100.0)

    def test_no_level_above_close_returns_explicit_missing_values(self):
        history = frame_from_closes(list(range(80, 121)), highs=list(range(80, 121)))

        row = build_near_resistance_rows(
            history,
            build_reversal_rows(history),
        )[-1]

        self.assertEqual(
            row,
            {
                "near_resistance_lower": None,
                "near_resistance_upper": None,
                "near_resistance_mid": None,
                "near_resistance_distance_pct": None,
                "near_resistance_score": None,
                "near_resistance_sources": [],
                "far_resistance": None,
            },
        )

    def test_appending_future_rows_does_not_change_historical_zones(self):
        prefix = nbis_shaped_history()
        future = frame_from_closes(
            [225.0, 205.0, 240.0],
            highs=[231.0, 212.0, 245.0],
            start=str((prefix.index[-1] + pd.offsets.BDay()).date()),
        )
        extended = pd.concat((prefix, future))

        short_rows = build_near_resistance_rows(
            prefix,
            build_reversal_rows(prefix),
        )
        long_rows = build_near_resistance_rows(
            extended,
            build_reversal_rows(extended),
        )

        self.assertEqual(long_rows[: len(short_rows)], short_rows)


if __name__ == "__main__":
    unittest.main()
