import unittest

import numpy as np
import pandas as pd

from research.high_level_distribution import (
    build_high_level_distribution_state,
)
from tests.helpers import make_ohlcv


def elevated_distribution_history():
    base = np.linspace(50.0, 70.0, 220)
    advance = np.linspace(70.0, 120.0, 59)
    closes = np.concatenate([base, advance, [103.0]])
    opens = closes.copy()
    highs = np.maximum(opens, closes) * 1.01
    lows = np.minimum(opens, closes) * 0.99
    volumes = np.full(len(closes), 1_000_000.0)
    opens[-1] = 119.0
    highs[-1] = 123.0
    lows[-1] = 101.0
    volumes[-1] = 2_200_000.0
    return make_ohlcv(
        closes,
        highs=highs,
        lows=lows,
        opens=opens,
        volumes=volumes,
    )


class HighLevelDistributionTest(unittest.TestCase):
    def test_group_supply_breadth_is_capped_and_named(self):
        frame = elevated_distribution_history()
        group_supply = pd.DataFrame(
            {
                "distribution_breadth": 0.40,
                "down_volume_breadth": 0.70,
                "mean_volume_ratio": 1.40,
            },
            index=frame.index,
        )

        row = build_high_level_distribution_state(
            frame,
            group_supply=group_supply,
        ).iloc[-1]

        self.assertEqual(row["relative_supply_score"], 30.0)
        self.assertIn(
            "group_distribution_breadth",
            row["distribution_pressure_conditions"],
        )
        self.assertIn(
            "group_down_volume_breadth",
            row["distribution_pressure_conditions"],
        )

    def test_strong_reclaim_clears_remembered_top_risk(self):
        frame = elevated_distribution_history()
        next_date = pd.bdate_range(
            frame.index[-1] + pd.Timedelta(days=1),
            periods=1,
        )
        recovery = make_ohlcv(
            [125.0],
            opens=[110.0],
            highs=[126.0],
            lows=[109.0],
            volumes=[2_500_000.0],
        ).set_axis(next_date)

        row = build_high_level_distribution_state(
            pd.concat([frame, recovery])
        ).iloc[-1]

        self.assertTrue(row["risk_recovery"])
        self.assertEqual(row["high_level_distribution_state"], "inactive")
        self.assertEqual(row["high_level_distribution_state_score"], 0.0)
        self.assertIn("strong_reclaim", row["risk_recovery_conditions"])

    def test_churning_requires_multiple_events_in_rolling_window(self):
        frame = elevated_distribution_history()
        for offset in (-8, -4):
            previous = frame.iloc[offset - 1]["Close"]
            frame.iloc[offset, frame.columns.get_loc("Open")] = previous
            frame.iloc[offset, frame.columns.get_loc("Close")] = previous * 1.002
            frame.iloc[offset, frame.columns.get_loc("High")] = previous * 1.02
            frame.iloc[offset, frame.columns.get_loc("Low")] = previous * 0.98
            frame.iloc[offset, frame.columns.get_loc("Volume")] = 2_000_000.0

        row = build_high_level_distribution_state(frame).iloc[-2]

        self.assertEqual(row["churning_count_10"], 2)
        self.assertTrue(row["churning_cluster"])
        self.assertIn(
            "multi_session_churning",
            row["distribution_pressure_conditions"],
        )

    def test_single_high_volume_non_progress_day_is_not_churning_cluster(self):
        frame = elevated_distribution_history()
        previous = frame.iloc[-4]["Close"]
        frame.iloc[-3, frame.columns.get_loc("Open")] = previous
        frame.iloc[-3, frame.columns.get_loc("Close")] = previous * 1.002
        frame.iloc[-3, frame.columns.get_loc("High")] = previous * 1.02
        frame.iloc[-3, frame.columns.get_loc("Low")] = previous * 0.98
        frame.iloc[-3, frame.columns.get_loc("Volume")] = 2_000_000.0

        row = build_high_level_distribution_state(frame).iloc[-2]

        self.assertEqual(row["churning_count_10"], 1)
        self.assertFalse(row["churning_cluster"])

    def test_climax_run_requires_multiple_independent_evidence_groups(self):
        early = np.linspace(50.0, 100.0, 260)
        acceleration = 100.0 * np.power(1.02, np.arange(1, 21))
        closes = np.concatenate([early, acceleration])
        frame = make_ohlcv(closes)
        frame.iloc[-10:, frame.columns.get_loc("High")] = closes[-10:] * 1.04
        frame.iloc[-10:, frame.columns.get_loc("Low")] = closes[-10:] * 0.97
        frame.iloc[-10:, frame.columns.get_loc("Volume")] = 2_200_000.0

        row = build_high_level_distribution_state(frame).iloc[-1]

        self.assertGreaterEqual(row["climax_run_score"], 60.0)
        self.assertTrue(row["climax_run_candidate"])
        self.assertGreaterEqual(len(row["climax_run_conditions"]), 3)
        self.assertNotEqual(
            row["high_level_distribution_raw_state"],
            "confirmed",
        )

    def test_distribution_counts_expire_causally(self):
        frame = elevated_distribution_history()
        event_position = len(frame) - 12
        frame.iloc[event_position, frame.columns.get_loc("Open")] *= 1.05
        frame.iloc[event_position, frame.columns.get_loc("Close")] *= 0.95
        frame.iloc[event_position, frame.columns.get_loc("High")] = (
            frame.iloc[event_position]["Open"] * 1.01
        )
        frame.iloc[event_position, frame.columns.get_loc("Low")] = (
            frame.iloc[event_position]["Close"] * 0.99
        )
        frame.iloc[event_position, frame.columns.get_loc("Volume")] = 2_000_000

        rows = build_high_level_distribution_state(frame)

        self.assertGreaterEqual(rows.iloc[event_position]["distribution_count_5"], 1)
        self.assertEqual(rows.iloc[-1]["distribution_count_10"], 1)
        self.assertGreaterEqual(rows.iloc[-1]["distribution_count_20"], 2)

    def test_prior_advance_supply_and_structure_damage_confirm_top_risk(self):
        row = build_high_level_distribution_state(
            elevated_distribution_history()
        ).iloc[-1]

        self.assertGreaterEqual(row["high_level_context_score"], 60.0)
        self.assertGreaterEqual(row["distribution_pressure_score"], 60.0)
        self.assertGreaterEqual(row["structure_damage_score"], 40.0)
        self.assertEqual(row["high_level_distribution_raw_state"], "confirmed")
        self.assertEqual(row["high_level_distribution_state"], "confirmed")
        self.assertIn(
            "distribution_day",
            row["distribution_pressure_conditions"],
        )
        self.assertIn(
            "failed_breakout",
            row["distribution_pressure_conditions"],
        )

    def test_similar_selloff_without_prior_advance_is_not_high_level_risk(self):
        frame = elevated_distribution_history()
        frame.loc[:, "Close"] = 50.0
        frame.loc[:, "Open"] = 50.0
        frame.loc[:, "High"] = 50.5
        frame.loc[:, "Low"] = 49.5
        frame.loc[:, "Volume"] = 1_000_000.0
        frame.iloc[-1, frame.columns.get_loc("Open")] = 55.0
        frame.iloc[-1, frame.columns.get_loc("High")] = 56.0
        frame.iloc[-1, frame.columns.get_loc("Low")] = 45.0
        frame.iloc[-1, frame.columns.get_loc("Close")] = 46.0
        frame.iloc[-1, frame.columns.get_loc("Volume")] = 2_200_000.0

        row = build_high_level_distribution_state(frame).iloc[-1]

        self.assertLess(row["high_level_context_score"], 60.0)
        self.assertEqual(row["high_level_distribution_raw_state"], "low")
        self.assertNotEqual(row["high_level_distribution_state"], "confirmed")

    def test_correlated_supply_evidence_is_capped_by_group(self):
        row = build_high_level_distribution_state(
            elevated_distribution_history()
        ).iloc[-1]

        self.assertLessEqual(row["close_volume_supply_score"], 40.0)
        self.assertLessEqual(row["rejection_supply_score"], 30.0)
        self.assertLessEqual(row["relative_supply_score"], 30.0)
        self.assertAlmostEqual(
            row["distribution_pressure_score"],
            row["close_volume_supply_score"]
            + row["rejection_supply_score"]
            + row["relative_supply_score"],
        )

    def test_recent_high_context_survives_first_distribution_breakdown(self):
        frame = elevated_distribution_history()
        frame.iloc[-1, frame.columns.get_loc("High")] = 119.0
        frame.iloc[-1, frame.columns.get_loc("Open")] = 118.0
        frame.iloc[-1, frame.columns.get_loc("Close")] = 90.0
        frame.iloc[-1, frame.columns.get_loc("Low")] = 88.0

        row = build_high_level_distribution_state(frame).iloc[-1]

        self.assertLess(row["high_level_context_raw_score"], 60.0)
        self.assertGreaterEqual(row["high_level_context_score"], 60.0)
        self.assertEqual(row["distribution_pressure_score"], 40.0)
        self.assertGreaterEqual(row["structure_damage_score"], 40.0)
        self.assertEqual(row["high_level_distribution_raw_state"], "confirmed")

    def test_confirmed_event_decays_without_remaining_confirmed(self):
        frame = elevated_distribution_history()
        next_index = pd.bdate_range(
            frame.index[-1] + pd.Timedelta(days=1),
            periods=2,
        )
        recovery = make_ohlcv(
            [121.0, 122.0],
            volumes=[800_000.0, 800_000.0],
        ).set_axis(next_index)

        rows = build_high_level_distribution_state(
            pd.concat([frame, recovery])
        )
        row = rows.iloc[-1]

        self.assertNotEqual(
            row["high_level_distribution_raw_state"],
            "confirmed",
        )
        self.assertEqual(row["high_level_distribution_state"], "fading")
        self.assertEqual(
            row["high_level_distribution_memory_age_sessions"],
            2,
        )

    def test_appending_future_rows_does_not_change_historical_output(self):
        base = elevated_distribution_history()
        future_index = pd.bdate_range(
            base.index[-1] + pd.Timedelta(days=1),
            periods=3,
        )
        future = make_ohlcv([300.0, 20.0, 500.0]).set_axis(future_index)

        expected = build_high_level_distribution_state(base)
        actual = build_high_level_distribution_state(
            pd.concat([base, future])
        ).loc[base.index]

        pd.testing.assert_frame_equal(actual, expected)


if __name__ == "__main__":
    unittest.main()
