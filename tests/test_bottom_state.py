from __future__ import annotations

import copy
import unittest

import numpy as np
import pandas as pd

from research.bottom_state import build_bottom_state_rows
from tests.helpers import make_ohlcv


def downtrend_history(length: int = 90) -> pd.DataFrame:
    closes = np.linspace(140.0, 100.0, length)
    return make_ohlcv(
        closes,
        opens=closes + 0.4,
        highs=closes + 1.2,
        lows=closes - 1.2,
        volumes=np.full(length, 1_000_000.0),
    )


def blank_evidence(index: pd.Index) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "near_support_lower": np.nan,
            "near_support_upper": np.nan,
            "near_support_score": np.nan,
            "near_support_state": "unavailable",
            "demand_confirmation_score": np.nan,
            "demand_confirmation_coverage": 0.0,
            "demand_confirmation_conditions": [[] for _ in index],
            "supply_pressure_score": np.nan,
            "supply_pressure_conditions": [[] for _ in index],
            "early_reversal_score": 0.0,
            "early_reversal_watch": False,
            "prior_high_breakout": False,
            "trendline_breakout": False,
            "higher_low_confirmed": False,
            "market_regime_state": "market_in_correction",
        },
        index=index,
    )


def support_evidence(frame: pd.DataFrame) -> pd.DataFrame:
    evidence = blank_evidence(frame.index)
    evidence.loc[:, "near_support_lower"] = frame["Close"] * 0.985
    evidence.loc[:, "near_support_upper"] = frame["Close"] * 0.995
    evidence.loc[:, "near_support_score"] = 70.0
    evidence.loc[:, "near_support_state"] = "testing"
    return evidence


class BottomStateModelTest(unittest.TestCase):
    def test_plain_uptrend_is_not_mislabeled_as_a_bottom(self):
        closes = np.linspace(100.0, 140.0, 90)
        frame = make_ohlcv(closes)

        row = build_bottom_state_rows(
            frame,
            blank_evidence(frame.index),
        ).iloc[-1]

        self.assertEqual(row["bottom_state"], "unavailable")
        self.assertEqual(
            row["bottom_unavailable_reason"],
            "no_downtrend_context",
        )

    def test_downtrend_near_support_enters_potential_support(self):
        frame = downtrend_history()

        row = build_bottom_state_rows(
            frame,
            support_evidence(frame),
        ).iloc[-1]

        self.assertEqual(row["bottom_state"], "potential_support")
        self.assertGreaterEqual(row["bottom_location_score"], 10.0)
        self.assertGreaterEqual(row["bottom_coverage"], 0.6)
        self.assertAlmostEqual(
            row["bottom_invalidation_level"],
            frame["Close"].iloc[-1] * 0.985,
        )

    def test_seller_exhaustion_then_early_reversal_upgrades_state(self):
        frame = downtrend_history()
        evidence = support_evidence(frame)
        exhaustion_date = frame.index[-3]
        early_date = frame.index[-1]
        evidence.at[exhaustion_date, "demand_confirmation_score"] = 58.0
        evidence.at[exhaustion_date, "demand_confirmation_coverage"] = 1.0
        evidence.at[
            exhaustion_date,
            "demand_confirmation_conditions",
        ] = ["seller_exhaustion", "buyer_absorption"]
        evidence.at[early_date, "demand_confirmation_score"] = 72.0
        evidence.at[early_date, "demand_confirmation_coverage"] = 1.0
        evidence.at[
            early_date,
            "demand_confirmation_conditions",
        ] = ["buyer_absorption", "positive_signed_volume"]
        evidence.at[early_date, "early_reversal_score"] = 75.0
        evidence.at[early_date, "early_reversal_watch"] = True

        rows = build_bottom_state_rows(frame, evidence)

        self.assertEqual(
            rows.loc[exhaustion_date, "bottom_state"],
            "seller_exhaustion_watch",
        )
        self.assertEqual(
            rows.loc[early_date, "bottom_state"],
            "early_bullish_reversal_watch",
        )
        self.assertIn(
            "seller_exhaustion",
            rows.loc[exhaustion_date, "bottom_conditions"],
        )

    def test_higher_low_and_breakout_confirm_structure(self):
        frame = downtrend_history()
        evidence = support_evidence(frame)
        evidence.loc[frame.index[-4] :, "early_reversal_watch"] = True
        evidence.loc[frame.index[-4] :, "early_reversal_score"] = 75.0
        evidence.loc[
            frame.index[-4] :,
            "demand_confirmation_score",
        ] = 70.0
        evidence.loc[
            frame.index[-4] :,
            "demand_confirmation_coverage",
        ] = 1.0
        evidence.at[
            frame.index[-1],
            "demand_confirmation_conditions",
        ] = ["breakout_acceptance"]
        evidence.at[frame.index[-1], "higher_low_confirmed"] = True
        evidence.at[frame.index[-1], "prior_high_breakout"] = True

        row = build_bottom_state_rows(frame, evidence).iloc[-1]

        self.assertEqual(
            row["bottom_state"],
            "bullish_structure_confirmed",
        )
        self.assertTrue(row["bottom_state_transition"])
        self.assertGreaterEqual(row["bottom_structure_score"], 15.0)

    def test_positive_state_has_memory_but_failure_has_immediate_veto(self):
        frame = downtrend_history(96)
        evidence = support_evidence(frame)
        signal_position = len(frame) - 7
        signal_date = frame.index[signal_position]
        evidence.at[signal_date, "early_reversal_watch"] = True
        evidence.at[signal_date, "early_reversal_score"] = 75.0
        evidence.at[signal_date, "demand_confirmation_score"] = 70.0
        evidence.at[signal_date, "demand_confirmation_coverage"] = 1.0
        evidence.at[
            signal_date,
            "demand_confirmation_conditions",
        ] = ["buyer_absorption"]

        remembered = build_bottom_state_rows(frame, evidence)

        self.assertEqual(
            remembered.iloc[-2]["bottom_state"],
            "early_bullish_reversal_watch",
        )
        self.assertEqual(
            remembered.iloc[-2]["bottom_state_age_sessions"],
            5,
        )

        failed = frame.copy()
        failed.iloc[-1, failed.columns.get_loc("Low")] = (
            float(failed["Low"].iloc[-11:-1].min()) - 5.0
        )
        failed.iloc[-1, failed.columns.get_loc("Close")] = (
            float(failed["Low"].iloc[-1]) + 0.2
        )
        failed.iloc[-1, failed.columns.get_loc("Volume")] = 2_000_000.0
        failed_rows = build_bottom_state_rows(failed, evidence)

        self.assertEqual(failed_rows.iloc[-1]["bottom_state"], "bottom_failed")
        self.assertIn(
            "volume_expanded_new_low",
            failed_rows.iloc[-1]["bottom_counter_conditions"],
        )

    def test_appending_future_rows_cannot_rewrite_prior_output(self):
        prefix = downtrend_history()
        prefix_evidence = support_evidence(prefix)
        future_index = pd.bdate_range(
            prefix.index[-1] + pd.Timedelta(days=1),
            periods=5,
        )
        future = make_ohlcv(
            np.linspace(101.0, 110.0, 5),
        )
        future.index = future_index
        extended = pd.concat([prefix, future])
        extended_evidence = pd.concat(
            [
                prefix_evidence,
                support_evidence(future),
            ]
        )

        expected = build_bottom_state_rows(prefix, prefix_evidence)
        actual = build_bottom_state_rows(extended, extended_evidence).iloc[
            : len(prefix)
        ]

        pd.testing.assert_frame_equal(
            actual.reset_index(drop=True),
            expected.reset_index(drop=True),
        )

    def test_inputs_are_not_mutated(self):
        frame = downtrend_history()
        evidence = support_evidence(frame)
        expected_frame = frame.copy(deep=True)
        expected_evidence = copy.deepcopy(evidence)

        build_bottom_state_rows(frame, evidence)

        pd.testing.assert_frame_equal(frame, expected_frame)
        pd.testing.assert_frame_equal(evidence, expected_evidence)


if __name__ == "__main__":
    unittest.main()
