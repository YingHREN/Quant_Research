from __future__ import annotations

import unittest

from factors.compute import pocket_pivot, pocket_pivot_evidence
from tests.helpers import make_ohlcv


class PocketPivotEvidenceTest(unittest.TestCase):
    def test_requires_complete_comparison_window(self):
        history = make_ohlcv(range(1, 12))

        result = pocket_pivot_evidence(history)

        self.assertFalse(result["available"])
        self.assertFalse(result["active"])
        self.assertEqual(result["reject_reason"], "insufficient_history")

    def test_up_day_above_every_prior_down_day_volume_is_active(self):
        history = make_ohlcv(
            [10, 9, 10, 9.5, 10, 9.8, 10.2, 9.9, 10.1, 9.7, 9.8, 10.2],
            volumes=[100, 120, 90, 140, 80, 130, 85, 150, 90, 145, 95, 200],
        )

        result = pocket_pivot_evidence(history)

        self.assertTrue(result["available"])
        self.assertTrue(result["active"])
        self.assertEqual(result["current_volume"], 200.0)
        self.assertEqual(result["prior_down_volume"], 150.0)
        self.assertEqual(result["down_day_count"], 5)
        self.assertIsNone(result["reject_reason"])
        self.assertTrue(pocket_pivot(history))

    def test_volume_not_above_prior_down_day_is_inactive(self):
        history = make_ohlcv(
            [10, 9, 10, 9.5, 10, 9.8, 10.2, 9.9, 10.1, 9.7, 9.8, 10.2],
            volumes=[100, 120, 90, 140, 80, 130, 85, 150, 90, 145, 95, 150],
        )

        result = pocket_pivot_evidence(history)

        self.assertFalse(result["active"])
        self.assertEqual(result["reject_reason"], "volume_not_above_prior_down_days")
        self.assertFalse(pocket_pivot(history))

    def test_no_down_days_never_auto_qualifies(self):
        history = make_ohlcv(
            range(1, 13),
            volumes=[100] * 11 + [1_000],
        )

        result = pocket_pivot_evidence(history)

        self.assertTrue(result["available"])
        self.assertFalse(result["active"])
        self.assertEqual(result["down_day_count"], 0)
        self.assertIsNone(result["prior_down_volume"])
        self.assertEqual(result["reject_reason"], "no_down_days_in_window")
        self.assertFalse(pocket_pivot(history))


if __name__ == "__main__":
    unittest.main()
