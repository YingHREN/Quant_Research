import unittest

import numpy as np
import pandas as pd

from research.risk_memory import build_risk_memory_state


class RiskMemoryStateTest(unittest.TestCase):
    def test_mu_like_distribution_risk_does_not_disappear_two_sessions_later(self):
        raw = pd.Series(
            [5.0, 34.0, 15.0, 5.0],
            index=pd.bdate_range("2026-06-25", periods=4),
        )

        result = build_risk_memory_state(raw)

        self.assertEqual(result.iloc[1]["state"], "new")
        self.assertEqual(result.iloc[2]["state"], "fading")
        self.assertEqual(result.iloc[3]["state"], "fading")
        self.assertEqual(result.iloc[3]["raw_score"], 5.0)
        self.assertGreater(result.iloc[3]["state_score"], 25.0)
        self.assertEqual(result.iloc[3]["memory_age_sessions"], 2)

    def test_memory_uses_five_session_half_life(self):
        raw = pd.Series(
            [40.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            index=pd.bdate_range("2026-06-01", periods=6),
        )

        result = build_risk_memory_state(raw)

        self.assertAlmostEqual(result.iloc[-1]["state_score"], 20.0, places=6)

    def test_new_current_evidence_renews_an_active_state(self):
        raw = pd.Series(
            [34.0, 5.0, 38.0],
            index=pd.bdate_range("2026-06-26", periods=3),
        )

        result = build_risk_memory_state(raw)

        self.assertEqual(result.iloc[-1]["state_score"], 38.0)
        self.assertEqual(result.iloc[-1]["state"], "persistent")
        self.assertEqual(result.iloc[-1]["memory_age_sessions"], 0)

    def test_remembered_peak_expires_after_ten_sessions(self):
        raw = pd.Series(
            [80.0] + [0.0] * 10,
            index=pd.bdate_range("2026-06-01", periods=11),
        )

        result = build_risk_memory_state(raw)

        self.assertEqual(result.iloc[9]["memory_age_sessions"], 9)
        self.assertGreater(result.iloc[9]["state_score"], 0.0)
        self.assertEqual(result.iloc[10]["state_score"], 0.0)
        self.assertEqual(result.iloc[10]["state"], "inactive")
        self.assertEqual(result.iloc[10]["memory_age_sessions"], 0)

    def test_missing_raw_score_is_unavailable_not_fabricated(self):
        raw = pd.Series(
            [34.0, np.nan],
            index=pd.bdate_range("2026-06-26", periods=2),
        )

        result = build_risk_memory_state(raw)

        self.assertTrue(np.isnan(result.iloc[-1]["state_score"]))
        self.assertEqual(result.iloc[-1]["state"], "unavailable")
        self.assertTrue(np.isnan(result.iloc[-1]["memory_age_sessions"]))

    def test_appending_future_scores_does_not_change_past_states(self):
        index = pd.bdate_range("2026-06-23", periods=6)
        original = pd.Series([5.0, 5.0, 34.0, 15.0], index=index[:4])
        extended = pd.Series(
            [5.0, 5.0, 34.0, 15.0, 90.0, 0.0],
            index=index,
        )

        before = build_risk_memory_state(original)
        after = build_risk_memory_state(extended).loc[original.index]

        pd.testing.assert_frame_equal(after, before)


if __name__ == "__main__":
    unittest.main()
