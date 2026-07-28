import unittest

import numpy as np
import pandas as pd

from research.vcp import detect_vcp, pattern_evidence
from tests.helpers import make_ohlcv


def _segments(points, bars_per_segment=7):
    values = []
    for start, end in zip(points[:-1], points[1:]):
        values.extend(np.linspace(start, end, bars_per_segment, endpoint=False))
    values.append(points[-1])
    return values


def textbook_vcp_fixture():
    prefix = np.linspace(55, 105, 150)
    base = _segments([105, 90, 104, 94, 103, 97, 102, 100, 101.5], 8)
    close = np.concatenate([prefix, base])
    volume = np.concatenate([
        np.full(len(prefix), 1_500_000),
        np.linspace(1_300_000, 650_000, len(base)),
    ])
    return make_ohlcv(close, volumes=volume)


def increasing_swings_fixture():
    prefix = np.linspace(55, 105, 150)
    base = _segments([105, 99, 104, 95, 103, 90, 102], 8)
    return make_ohlcv(np.concatenate([prefix, base]))


def monotonic_rally_fixture():
    return make_ohlcv(np.linspace(50, 140, 230))


def unconfirmed_tail_fixture():
    frame = textbook_vcp_fixture().copy()
    tail_close = np.linspace(frame.Close.iloc[-1], 101.0, 8)
    tail = make_ohlcv(tail_close, start=str((frame.index[-1] + pd.offsets.BDay()).date()))
    return pd.concat([frame, tail])


class VCPDetectorTest(unittest.TestCase):
    def test_pattern_evidence_exposes_dated_factor_compatible_values(self):
        evidence = pattern_evidence(detect_vcp(textbook_vcp_fixture()))

        self.assertTrue(evidence["accepted"])
        self.assertEqual(evidence["vcp_pivot"], evidence["pivot"])
        self.assertIsNotNone(evidence["pivot_date"])
        self.assertIsNotNone(evidence["base_start"])
        self.assertGreaterEqual(evidence["n_contractions"], 2)
        self.assertEqual(
            len(evidence["contraction_legs"]),
            evidence["n_contractions"],
        )
        self.assertLess(
            evidence["contraction_legs"][0]["peak_date"],
            evidence["contraction_legs"][0]["trough_date"],
        )
        self.assertIsNone(evidence["reject_reason"])

    def test_pattern_evidence_preserves_typed_rejection_reason(self):
        evidence = pattern_evidence(detect_vcp(monotonic_rally_fixture()))

        self.assertFalse(evidence["accepted"])
        self.assertIn(
            evidence["reject_reason"],
            {"monotonic_rally", "insufficient_swings"},
        )
        self.assertEqual(evidence["contractions"], [])
        self.assertEqual(evidence["n_contractions"], 0)

    def test_decreasing_swings_return_dated_confirmed_legs(self):
        pattern = detect_vcp(textbook_vcp_fixture())

        self.assertTrue(pattern.accepted, pattern.reject_reason)
        self.assertGreaterEqual(len(pattern.legs), 2)
        self.assertGreater(pattern.legs[0].depth_pct, pattern.legs[-1].depth_pct)
        self.assertLess(pattern.legs[0].peak_date, pattern.legs[0].trough_date)
        self.assertIsNotNone(pattern.pivot_date)

    def test_increasing_swings_are_rejected(self):
        pattern = detect_vcp(increasing_swings_fixture())

        self.assertFalse(pattern.accepted)
        self.assertEqual(pattern.reject_reason, "contractions_not_decreasing")

    def test_monotonic_rally_is_not_a_base(self):
        pattern = detect_vcp(monotonic_rally_fixture())

        self.assertFalse(pattern.accepted)
        self.assertIn(pattern.reject_reason, {"monotonic_rally", "insufficient_swings"})

    def test_pending_final_leg_is_not_counted_as_confirmed(self):
        pattern = detect_vcp(unconfirmed_tail_fixture())

        self.assertIsNotNone(pattern.pending_leg)
        self.assertFalse(pattern.pending_leg.confirmed)
        self.assertNotIn(pattern.pending_leg, pattern.legs)

    def test_latest_bar_more_than_five_percent_above_pivot_is_not_a_setup(self):
        history = textbook_vcp_fixture()
        extended = make_ohlcv(
            [110.0],
            start=str((history.index[-1] + pd.offsets.BDay()).date()),
        )

        pattern = detect_vcp(pd.concat([history, extended]))

        self.assertFalse(pattern.accepted)
        self.assertEqual(pattern.stage, "extended")
        self.assertEqual(pattern.reject_reason, "extended_above_buy_zone")
        self.assertGreater(pattern.distance_to_pivot_pct, 5.0)
        self.assertIsNotNone(pattern.pivot)

    def test_latest_bar_above_pivot_moves_from_setup_to_breakout_stage(self):
        history = textbook_vcp_fixture()
        breakout = make_ohlcv(
            [107.0],
            start=str((history.index[-1] + pd.offsets.BDay()).date()),
        )

        pattern = detect_vcp(pd.concat([history, breakout]))

        self.assertFalse(pattern.accepted)
        self.assertEqual(pattern.stage, "breakout")
        self.assertEqual(pattern.reject_reason, "already_above_pivot")
        self.assertGreater(pattern.distance_to_pivot_pct, 0.0)
        self.assertLessEqual(pattern.distance_to_pivot_pct, 5.0)


if __name__ == "__main__":
    unittest.main()
