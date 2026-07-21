import unittest
from unittest.mock import patch

import pandas as pd

from research.events import scan_ticker_events
from research.vcp import VCPPattern
from tests.helpers import make_ohlcv


def _pattern(date, stage, accepted=True, pivot=10.0, reason=None):
    timestamp = pd.Timestamp(date)
    return VCPPattern(
        asof_date=timestamp,
        accepted=accepted,
        stage=stage,
        base_start=pd.Timestamp("2020-01-01") if accepted else None,
        base_end=timestamp,
        legs=(),
        pending_leg=None,
        pivot=pivot if accepted else None,
        pivot_date=pd.Timestamp("2020-01-02") if accepted else None,
        distance_to_pivot_pct=-2.0 if accepted else None,
        reject_reason=reason,
        metrics={"base_depth_pct": 15.0},
    )


class EventScannerTest(unittest.TestCase):
    def setUp(self):
        self.history = make_ohlcv(
            [9.0, 9.3, 9.6, 9.8, 10.4, 10.6],
            volumes=[100, 100, 90, 80, 160, 120],
        )

    def test_repeated_near_pivot_days_are_one_event(self):
        stages = ["none", "forming", "near_pivot", "near_pivot", "breakout", "none"]

        def fake_detect(frame):
            index = len(frame) - 1
            stage = stages[index]
            return _pattern(frame.index[-1], stage, accepted=stage != "none")

        with patch("research.events.detect_vcp", side_effect=fake_detect):
            events = scan_ticker_events("TEST", self.history, min_history=1)

        self.assertEqual(len(events), 1)
        self.assertEqual(sum(t.stage == "near_pivot" for t in events[0].transitions), 1)

    def test_breakout_uses_pivot_known_before_breakout(self):
        def fake_detect(frame):
            if len(frame) == 2:
                return _pattern(frame.index[-1], "forming", pivot=10.0)
            if len(frame) == 3:
                return _pattern(frame.index[-1], "near_pivot", pivot=10.0)
            return _pattern(frame.index[-1], "none", accepted=False)

        with patch("research.events.detect_vcp", side_effect=fake_detect):
            event = scan_ticker_events("TEST", self.history, min_history=1)[0]

        self.assertEqual(event.breakout_pivot, 10.0)
        self.assertEqual(event.breakout_date, self.history.index[4])
        prior = [transition for transition in event.transitions if transition.date < event.breakout_date]
        self.assertTrue(any(transition.pivot == event.breakout_pivot for transition in prior))

    def test_failed_structure_becomes_invalidated_not_successful(self):
        falling = make_ohlcv([9.0, 9.4, 9.7, 9.1, 8.7, 8.5])

        def fake_detect(frame):
            if len(frame) == 2:
                return _pattern(frame.index[-1], "forming", pivot=10.0)
            if len(frame) == 3:
                return _pattern(frame.index[-1], "near_pivot", pivot=10.0)
            return _pattern(frame.index[-1], "none", accepted=False, reason="below_ma50")

        with patch("research.events.detect_vcp", side_effect=fake_detect):
            event = scan_ticker_events("TEST", falling, min_history=1)[0]

        self.assertIsNotNone(event.invalidated_date)
        self.assertIsNone(event.breakout_date)


if __name__ == "__main__":
    unittest.main()
