from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.bottom_state_evaluation import build_bottom_transition_events
from tests.helpers import make_ohlcv


def _history(length=90):
    closes = np.full(length, 100.0)
    return make_ohlcv(
        closes,
        opens=closes,
        highs=closes + 1.0,
        lows=closes - 1.0,
        volumes=np.full(length, 1_000_000.0),
    )


def _states(index):
    return pd.DataFrame(
        {
            "bottom_state": "downtrend_continuation",
            "bottom_raw_state": "downtrend_continuation",
            "bottom_state_transition": False,
            "bottom_score": 20.0,
            "bottom_coverage": 1.0,
            "bottom_state_age_sessions": 0,
        },
        index=index,
    )


class BottomTransitionContractTest(unittest.TestCase):
    def test_memory_extension_days_do_not_create_positive_events(self):
        history = _history()
        states = _states(history.index)
        event_position = 70
        states.iloc[event_position : event_position + 4, 0] = (
            "potential_support"
        )
        states.iloc[event_position : event_position + 4, 1] = (
            "potential_support"
        )
        states.iloc[event_position, 2] = True

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(5,),
        )
        positive = rows.loc[
            rows["observation_state"].eq("potential_support")
            & rows["scope"].eq("all_transitions")
        ]

        self.assertEqual(len(positive), 1)
        self.assertEqual(
            positive.iloc[0]["observation_date"],
            history.index[event_position],
        )

    def test_immature_tail_is_excluded(self):
        history = _history()
        states = _states(history.index)
        states.iloc[-4, 0] = "seller_exhaustion_watch"
        states.iloc[-4, 1] = "seller_exhaustion_watch"
        states.iloc[-4, 2] = True

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(5,),
        )

        self.assertNotIn(
            history.index[-4],
            set(rows["observation_date"]),
        )

    def test_empty_result_preserves_output_schema(self):
        history = _history(4)
        states = _states(history.index)

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(5,),
        )

        self.assertTrue(rows.empty)
        self.assertIn("observation_date", rows.columns)
        self.assertIn("forward_return", rows.columns)

    def test_non_integer_horizon_is_rejected(self):
        history = _history()
        states = _states(history.index)

        with self.assertRaisesRegex(
            ValueError,
            "unique positive integers",
        ):
            build_bottom_transition_events(
                "AAA",
                history,
                states,
                horizons=(5.5,),
            )


class BottomTransitionOutcomeTest(unittest.TestCase):
    def test_future_window_returns_and_excursions_are_exact(self):
        history = _history()
        states = _states(history.index)
        position = 70
        states.iloc[position, 0] = "seller_exhaustion_watch"
        states.iloc[position, 1] = "seller_exhaustion_watch"
        states.iloc[position, 2] = True
        future_closes = [104.0, 102.0, 106.0, 101.0, 105.0]
        future_highs = [105.0, 103.0, 108.0, 102.0, 106.0]
        future_lows = [98.0, 100.0, 101.0, 97.0, 104.0]
        for offset in range(1, 6):
            history.iloc[
                position + offset,
                history.columns.get_loc("Close"),
            ] = future_closes[offset - 1]
            history.iloc[
                position + offset,
                history.columns.get_loc("High"),
            ] = future_highs[offset - 1]
            history.iloc[
                position + offset,
                history.columns.get_loc("Low"),
            ] = future_lows[offset - 1]

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(5,),
        )
        row = rows.loc[
            rows["observation_state"].eq("seller_exhaustion_watch")
            & rows["scope"].eq("all_transitions")
        ].iloc[0]

        self.assertAlmostEqual(row["forward_return"], 0.05)
        self.assertAlmostEqual(row["maximum_favorable_excursion"], 0.08)
        self.assertAlmostEqual(row["maximum_adverse_excursion"], -0.03)

    def test_failure_precedes_same_day_confirmation(self):
        history = _history()
        states = _states(history.index)
        position = 70
        states.iloc[position, 0] = "early_bullish_reversal_watch"
        states.iloc[position, 1] = "early_bullish_reversal_watch"
        states.iloc[position, 2] = True
        terminal_position = position + 2
        states.iloc[terminal_position, 0] = "bottom_failed"
        states.iloc[terminal_position, 1] = "bullish_structure_confirmed"
        states.iloc[terminal_position, 2] = True

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(5,),
        )
        row = rows.loc[
            rows["observation_state"].eq("early_bullish_reversal_watch")
            & rows["scope"].eq("all_transitions")
        ].iloc[0]

        self.assertEqual(row["first_terminal_state"], "failed")
        self.assertEqual(row["sessions_to_failure"], 2)

    def test_earlier_confirmation_precedes_later_failure(self):
        history = _history()
        states = _states(history.index)
        position = 70
        states.iloc[position, 0] = "early_bullish_reversal_watch"
        states.iloc[position, 1] = "early_bullish_reversal_watch"
        states.iloc[position, 2] = True
        states.iloc[position + 2, 0] = "bullish_structure_confirmed"
        states.iloc[position + 2, 1] = "bullish_structure_confirmed"
        states.iloc[position + 2, 2] = True
        states.iloc[position + 4, 0] = "bottom_failed"
        states.iloc[position + 4, 1] = "bottom_failed"
        states.iloc[position + 4, 2] = True

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(5,),
        )
        row = rows.loc[
            rows["observation_state"].eq("early_bullish_reversal_watch")
            & rows["scope"].eq("all_transitions")
        ].iloc[0]

        self.assertEqual(row["first_terminal_state"], "confirmed")
        self.assertEqual(row["sessions_to_confirmation"], 2)

    def test_structure_state_at_observation_has_zero_confirmation_delay(self):
        history = _history()
        states = _states(history.index)
        position = 70
        states.iloc[position, 0] = "bullish_structure_confirmed"
        states.iloc[position, 1] = "bullish_structure_confirmed"
        states.iloc[position, 2] = True

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(5,),
        )
        row = rows.loc[
            rows["observation_state"].eq("bullish_structure_confirmed")
            & rows["scope"].eq("all_transitions")
        ].iloc[0]

        self.assertEqual(row["sessions_to_confirmation"], 0)

    def test_non_overlapping_scope_skips_second_active_positive_event(self):
        history = _history(110)
        states = _states(history.index)
        states.iloc[70, 0] = "potential_support"
        states.iloc[70, 1] = "potential_support"
        states.iloc[70, 2] = True
        states.iloc[74, 0] = "seller_exhaustion_watch"
        states.iloc[74, 1] = "seller_exhaustion_watch"
        states.iloc[74, 2] = True

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(20,),
        )
        positive = rows.loc[
            rows["scope"].eq("non_overlapping")
            & rows["observation_state"].isin(
                ("potential_support", "seller_exhaustion_watch")
            )
        ]

        self.assertEqual(len(positive), 1)
        self.assertEqual(
            positive.iloc[0]["observation_date"],
            history.index[70],
        )

    def test_transition_on_prior_twenty_day_event_end_is_not_overlapping(self):
        history = _history(120)
        states = _states(history.index)
        states.iloc[70, 0] = "potential_support"
        states.iloc[70, 1] = "potential_support"
        states.iloc[70, 2] = True
        states.iloc[90, 0] = "seller_exhaustion_watch"
        states.iloc[90, 1] = "seller_exhaustion_watch"
        states.iloc[90, 2] = True

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(20,),
        )
        positive = rows.loc[
            rows["scope"].eq("non_overlapping")
            & rows["observation_state"].isin(
                ("potential_support", "seller_exhaustion_watch")
            )
        ]

        self.assertEqual(
            list(positive["observation_date"]),
            [history.index[70], history.index[90]],
        )

    def test_failure_ends_active_non_overlapping_episode(self):
        history = _history(110)
        states = _states(history.index)
        for position, state in (
            (70, "potential_support"),
            (74, "bottom_failed"),
            (75, "seller_exhaustion_watch"),
        ):
            states.iloc[position, 0] = state
            states.iloc[position, 1] = state
            states.iloc[position, 2] = True

        rows = build_bottom_transition_events(
            "AAA",
            history,
            states,
            horizons=(5,),
        )
        positive = rows.loc[
            rows["scope"].eq("non_overlapping")
            & rows["observation_state"].isin(
                ("potential_support", "seller_exhaustion_watch")
            )
        ]

        self.assertEqual(
            list(positive["observation_date"]),
            [history.index[70], history.index[75]],
        )

    def test_appended_future_rows_do_not_change_already_mature_events(self):
        history = _history(100)
        states = _states(history.index)
        states.iloc[70, 0] = "seller_exhaustion_watch"
        states.iloc[70, 1] = "seller_exhaustion_watch"
        states.iloc[70, 2] = True
        extended_history = _history(110)
        extended_history.iloc[: len(history)] = history.to_numpy()
        extended_states = _states(extended_history.index)
        extended_states.iloc[: len(states)] = states.to_numpy()

        expected = build_bottom_transition_events(
            "AAA",
            history,
            states,
        )
        actual = build_bottom_transition_events(
            "AAA",
            extended_history,
            extended_states,
        )
        mature = actual.loc[
            actual["event_end_date"].le(history.index[-1])
        ].reset_index(drop=True)

        pd.testing.assert_frame_equal(
            mature,
            expected.reset_index(drop=True),
        )


if __name__ == "__main__":
    unittest.main()
