import unittest

import numpy as np
import pandas as pd

from research.supply_demand import build_supply_demand_rows
from tests.helpers import make_ohlcv


class SupplyDemandModelTest(unittest.TestCase):
    def test_correlated_close_volume_evidence_is_capped_and_not_inverse_demand(self):
        closes = np.full(40, 100.0)
        closes[-1] = 99.6
        opens = closes.copy()
        opens[-1] = 100.0
        highs = np.full(40, 101.0)
        lows = np.full(40, 99.0)
        lows[-1] = 99.5
        volumes = np.full(40, 1_000_000.0)
        volumes[-1] = 2_000_000.0
        frame = make_ohlcv(
            closes,
            opens=opens,
            highs=highs,
            lows=lows,
            volumes=volumes,
        )

        row = build_supply_demand_rows(frame).iloc[-1]

        self.assertIn("distribution_day", row["supply_pressure_conditions"])
        self.assertIn(
            "high_volume_non_progress",
            row["supply_pressure_conditions"],
        )
        self.assertIn(
            "negative_signed_volume",
            row["supply_pressure_conditions"],
        )
        self.assertLessEqual(row["supply_close_volume_score"], 40.0)
        self.assertGreater(row["supply_pressure_score"], 0.0)
        self.assertNotEqual(
            row["demand_confirmation_score"],
            100.0 - row["supply_pressure_score"],
        )

    def test_seller_exhaustion_requires_volume_without_further_downside(self):
        frame = _flat_history(35)
        frame.iloc[-1] = {
            "Open": 100.0,
            "High": 102.0,
            "Low": 99.6,
            "Close": 101.5,
            "Volume": 2_000_000.0,
        }

        row = build_supply_demand_rows(frame).iloc[-1]

        self.assertIn(
            "seller_exhaustion",
            row["demand_confirmation_conditions"],
        )

        weaker = frame.copy()
        weaker.iloc[-1, weaker.columns.get_loc("Low")] = 95.0
        weak_row = build_supply_demand_rows(weaker).iloc[-1]
        self.assertNotIn(
            "seller_exhaustion",
            weak_row["demand_confirmation_conditions"],
        )

    def test_buyer_absorption_requires_reclaim_after_undercutting_prior_low(self):
        frame = _flat_history(35)
        frame.iloc[-1] = {
            "Open": 99.0,
            "High": 101.0,
            "Low": 98.0,
            "Close": 100.5,
            "Volume": 1_500_000.0,
        }

        row = build_supply_demand_rows(frame).iloc[-1]

        self.assertIn(
            "buyer_absorption",
            row["demand_confirmation_conditions"],
        )

        failed_reclaim = frame.copy()
        failed_reclaim.iloc[
            -1, failed_reclaim.columns.get_loc("Close")
        ] = 98.5
        failed_row = build_supply_demand_rows(failed_reclaim).iloc[-1]
        self.assertNotIn(
            "buyer_absorption",
            failed_row["demand_confirmation_conditions"],
        )

    def test_low_volume_higher_low_is_confirmed_on_following_session(self):
        frame = _flat_history(36)
        frame.loc[:, "Low"] = 97.0
        frame.iloc[-2] = {
            "Open": 100.0,
            "High": 100.5,
            "Low": 98.5,
            "Close": 99.0,
            "Volume": 700_000.0,
        }
        frame.iloc[-1] = {
            "Open": 99.0,
            "High": 101.0,
            "Low": 98.8,
            "Close": 100.5,
            "Volume": 1_000_000.0,
        }

        row = build_supply_demand_rows(frame).iloc[-1]

        self.assertIn(
            "low_volume_higher_low",
            row["demand_confirmation_conditions"],
        )

    def test_breakout_acceptance_and_failed_breakout_are_mutually_exclusive(self):
        accepted = _flat_history(35)
        accepted.iloc[-1] = {
            "Open": 100.0,
            "High": 103.0,
            "Low": 99.8,
            "Close": 102.0,
            "Volume": 1_500_000.0,
        }

        accepted_row = build_supply_demand_rows(accepted).iloc[-1]

        self.assertIn(
            "breakout_acceptance",
            accepted_row["demand_confirmation_conditions"],
        )
        self.assertNotIn(
            "failed_breakout",
            accepted_row["supply_pressure_conditions"],
        )

        failed = _flat_history(35)
        failed.iloc[-1] = {
            "Open": 100.0,
            "High": 103.0,
            "Low": 98.5,
            "Close": 99.5,
            "Volume": 1_500_000.0,
        }
        failed_row = build_supply_demand_rows(failed).iloc[-1]
        self.assertIn(
            "failed_breakout",
            failed_row["supply_pressure_conditions"],
        )
        self.assertNotIn(
            "breakout_acceptance",
            failed_row["demand_confirmation_conditions"],
        )

    def test_breakout_follow_through_uses_prior_frozen_pivot(self):
        frame = _flat_history(37)
        frame.iloc[-2] = {
            "Open": 100.0,
            "High": 103.0,
            "Low": 99.8,
            "Close": 102.0,
            "Volume": 1_500_000.0,
        }
        frame.iloc[-1] = {
            "Open": 102.0,
            "High": 103.0,
            "Low": 101.5,
            "Close": 102.5,
            "Volume": 1_000_000.0,
        }

        row = build_supply_demand_rows(frame).iloc[-1]

        self.assertIn(
            "breakout_follow_through",
            row["demand_confirmation_conditions"],
        )

    def test_pressure_test_efficiency_compares_with_recent_test_only(self):
        frame = _flat_history(45)
        frame.iloc[-12] = {
            "Open": 100.0,
            "High": 100.8,
            "Low": 99.5,
            "Close": 100.6,
            "Volume": 1_200_000.0,
        }
        frame.iloc[-1] = {
            "Open": 100.0,
            "High": 100.8,
            "Low": 99.5,
            "Close": 100.1,
            "Volume": 1_500_000.0,
        }

        row = build_supply_demand_rows(frame).iloc[-1]

        self.assertIn(
            "pressure_test_efficiency_decay",
            row["supply_pressure_conditions"],
        )

    def test_missing_market_context_lowers_coverage_without_faking_zero_evidence(self):
        frame = _flat_history(50)
        qqq = pd.Series(100.0, index=frame.index)
        sector = pd.Series(100.0, index=frame.index)

        complete = build_supply_demand_rows(
            frame,
            qqq_close=qqq,
            sector_close=sector,
        ).iloc[-1]
        missing = build_supply_demand_rows(frame).iloc[-1]

        self.assertLess(
            missing["supply_pressure_coverage"],
            complete["supply_pressure_coverage"],
        )
        self.assertLess(
            missing["demand_confirmation_coverage"],
            complete["demand_confirmation_coverage"],
        )
        self.assertIn("missing_qqq_context", missing["unavailable_reasons"])
        self.assertIn("missing_sector_context", missing["unavailable_reasons"])

    def test_high_supply_and_high_demand_form_two_way_contest(self):
        frame = _flat_history(60)
        for offset in (-8, -6):
            frame.iloc[offset] = {
                "Open": 100.0,
                "High": 102.0,
                "Low": 98.8,
                "Close": 99.2,
                "Volume": 2_000_000.0,
            }
        frame.iloc[-1] = {
            "Open": 100.0,
            "High": 100.3,
            "Low": 99.0,
            "Close": 100.2,
            "Volume": 2_000_000.0,
        }
        stronger_market = pd.Series(
            np.linspace(100.0, 120.0, len(frame)),
            index=frame.index,
        )

        row = build_supply_demand_rows(
            frame,
            qqq_close=stronger_market,
            sector_close=stronger_market,
        ).iloc[-1]

        self.assertGreaterEqual(row["supply_pressure_score"], 50.0)
        self.assertGreaterEqual(row["demand_confirmation_score"], 50.0)
        self.assertEqual(row["supply_demand_state"], "two_way_contest")

    def test_short_history_is_unavailable_instead_of_neutral(self):
        row = build_supply_demand_rows(_flat_history(10)).iloc[-1]

        self.assertTrue(np.isnan(row["supply_pressure_score"]))
        self.assertTrue(np.isnan(row["demand_confirmation_score"]))
        self.assertEqual(row["supply_demand_state"], "unavailable")
        self.assertIn("insufficient_history", row["unavailable_reasons"])

    def test_future_rows_do_not_change_historical_scores_or_states(self):
        base = _flat_history(60)
        future_index = pd.bdate_range(
            base.index[-1] + pd.Timedelta(days=1),
            periods=3,
        )
        future = make_ohlcv(
            [300.0, 20.0, 500.0],
            volumes=[9_000_000.0] * 3,
        ).set_axis(future_index)

        expected = build_supply_demand_rows(base)
        actual = build_supply_demand_rows(
            pd.concat([base, future])
        ).loc[base.index]

        pd.testing.assert_frame_equal(actual, expected)


def _flat_history(length):
    return make_ohlcv(
        np.full(length, 100.0),
        highs=np.full(length, 101.0),
        lows=np.full(length, 99.0),
        volumes=np.full(length, 1_000_000.0),
    )


if __name__ == "__main__":
    unittest.main()
