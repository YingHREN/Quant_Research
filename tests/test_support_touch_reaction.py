from __future__ import annotations

import unittest

import pandas as pd

from research.support_touch_reaction import (
    build_support_touch_reaction_rows,
)
from tests.helpers import make_ohlcv


def _history(total: int = 30) -> pd.DataFrame:
    closes = [100.0 + position * 0.1 for position in range(total)]
    return make_ohlcv(closes, start="2026-01-02")


def _signals(
    history: pd.DataFrame,
    *,
    active_position: int | None = None,
) -> pd.DataFrame:
    eligible = [False] * len(history)
    if active_position is not None:
        eligible[active_position] = True
    return pd.DataFrame(
        {
            "variant": ["baseline"] * len(history),
            "eligible": eligible,
            "zone_lower": [95.0] * len(history),
            "zone_upper": [97.0] * len(history),
        },
        index=history.index,
    )


def _history_with_touch(
    *,
    total: int,
    touch_position: int,
) -> pd.DataFrame:
    history = _history(total)
    history.iloc[touch_position, history.columns.get_loc("Low")] = 96.0
    return history


def _reaction_fixture() -> tuple[pd.DataFrame, pd.DataFrame, int]:
    history = make_ohlcv(
        [100.0] * 30,
        highs=[101.0] * 30,
        lows=[99.0] * 30,
        volumes=[100.0] * 30,
        start="2026-01-02",
    )
    observation_position = 20
    signals = _signals(history, active_position=observation_position)
    return history, signals, observation_position


def _build_fixture(
    history: pd.DataFrame,
    signals: pd.DataFrame,
) -> pd.Series:
    rows = build_support_touch_reaction_rows(
        "AAA",
        history,
        signals,
        waiting_horizon=5,
    )
    if len(rows) != 1:
        raise AssertionError(f"expected one event, got {len(rows)}")
    return rows.iloc[0]


class SupportTouchReactionContractTest(unittest.TestCase):
    def test_observation_close_at_or_below_zone_upper_is_not_an_event(self):
        history, signals, observation = _reaction_fixture()
        history.iloc[observation, history.columns.get_loc("Close")] = 97.0

        result = build_support_touch_reaction_rows(
            "AAA",
            history,
            signals,
            waiting_horizon=5,
        )

        self.assertTrue(result.empty)

    def test_immature_tail_is_excluded_even_when_touch_would_be_early(self):
        history = _history_with_touch(total=9, touch_position=6)
        signals = _signals(history, active_position=5)

        result = build_support_touch_reaction_rows(
            "AAA",
            history,
            signals,
            waiting_horizon=3,
        )

        self.assertTrue(result.empty)

    def test_rejects_misaligned_signal_dates(self):
        history = _history(30)

        with self.assertRaisesRegex(ValueError, "align"):
            build_support_touch_reaction_rows(
                "AAA",
                history,
                _signals(history).iloc[:-1],
                waiting_horizon=5,
            )


class SupportTouchReactionLabelTest(unittest.TestCase):
    def test_intersection_reclaimed_on_second_session_is_accepted(self):
        history, signals, observation = _reaction_fixture()
        history.iloc[observation + 2] = [97.0, 98.0, 96.0, 96.5, 200.0]
        history.iloc[observation + 3] = [97.0, 99.0, 96.5, 98.0, 100.0]
        history.iloc[observation + 4] = [98.0, 100.0, 97.5, 98.5, 100.0]

        row = _build_fixture(history, signals)

        self.assertEqual(row["touch_type"], "intersection")
        self.assertEqual(row["touch_date"], history.index[observation + 2])
        self.assertEqual(row["touch_delay_sessions"], 2)
        self.assertEqual(row["reaction_label"], "accepted")
        self.assertTrue(row["accepted"])
        self.assertFalse(row["failed"])
        self.assertFalse(row["ambiguous"])
        self.assertEqual(row["reclaim_delay_sessions"], 1)
        self.assertAlmostEqual(row["maximum_rebound_atr"], 1.5)
        self.assertAlmostEqual(row["maximum_penetration_atr"], 0.0)
        self.assertAlmostEqual(
            row["close_change_from_touch"],
            98.5 / 96.5 - 1.0,
        )
        self.assertAlmostEqual(row["touch_volume_ratio"], 2.0)

    def test_gap_below_zone_without_reclaim_is_failed(self):
        history, signals, observation = _reaction_fixture()
        history.iloc[observation + 2] = [93.0, 94.0, 92.0, 93.0, 100.0]
        history.iloc[observation + 3] = [93.5, 94.5, 92.5, 94.0, 100.0]
        history.iloc[observation + 4] = [94.0, 94.8, 93.0, 94.5, 100.0]

        row = _build_fixture(history, signals)

        self.assertEqual(row["touch_type"], "gap_through")
        self.assertEqual(row["reaction_label"], "failed")
        self.assertTrue(row["failed"])
        self.assertFalse(row["accepted"])
        self.assertFalse(row["ambiguous"])

    def test_two_consecutive_closes_below_lower_are_failed(self):
        history, signals, observation = _reaction_fixture()
        history.iloc[observation + 2] = [96.0, 96.5, 94.5, 94.8, 100.0]
        history.iloc[observation + 3] = [95.0, 96.0, 94.4, 94.9, 100.0]
        history.iloc[observation + 4] = [95.0, 96.5, 94.5, 96.0, 100.0]

        row = _build_fixture(history, signals)

        self.assertEqual(row["touch_type"], "intersection")
        self.assertEqual(row["reaction_label"], "failed")

    def test_half_atr_deep_close_is_failed(self):
        history, signals, observation = _reaction_fixture()
        history.iloc[observation + 2] = [96.0, 96.0, 93.0, 93.5, 100.0]
        history.iloc[observation + 3] = [94.0, 96.0, 93.5, 95.0, 100.0]
        history.iloc[observation + 4] = [95.0, 97.0, 94.5, 96.0, 100.0]

        row = _build_fixture(history, signals)

        self.assertEqual(row["reaction_label"], "failed")

    def test_failure_overrides_an_earlier_reclaim(self):
        history, signals, observation = _reaction_fixture()
        history.iloc[observation + 2] = [97.0, 99.0, 96.0, 98.0, 100.0]
        history.iloc[observation + 3] = [96.0, 96.5, 93.0, 93.5, 100.0]
        history.iloc[observation + 4] = [94.0, 96.0, 93.5, 95.5, 100.0]

        row = _build_fixture(history, signals)

        self.assertEqual(row["reclaim_delay_sessions"], 0)
        self.assertEqual(row["reaction_label"], "failed")
        self.assertFalse(row["accepted"])

    def test_touch_without_reclaim_or_failure_is_ambiguous(self):
        history, signals, observation = _reaction_fixture()
        history.iloc[observation + 2] = [96.0, 96.8, 95.5, 96.0, 100.0]
        history.iloc[observation + 3] = [96.0, 96.7, 95.4, 96.2, 100.0]
        history.iloc[observation + 4] = [96.2, 96.9, 95.7, 96.4, 100.0]

        row = _build_fixture(history, signals)

        self.assertEqual(row["reaction_label"], "ambiguous")
        self.assertTrue(row["ambiguous"])
        self.assertFalse(row["accepted"])
        self.assertFalse(row["failed"])

    def test_no_touch_is_retained_outside_touch_rate_denominator(self):
        history, signals, _ = _reaction_fixture()

        row = _build_fixture(history, signals)

        self.assertEqual(row["touch_status"], "not_touched")
        self.assertEqual(row["reaction_label"], "not_touched")
        self.assertFalse(row["accepted"])
        self.assertFalse(row["failed"])
        self.assertFalse(row["ambiguous"])

    def test_no_touch_episode_ends_when_waiting_horizon_expires(self):
        history, signals, observation = _reaction_fixture()

        row = _build_fixture(history, signals)

        self.assertEqual(
            row["event_end_date"],
            history.index[observation + 5],
        )


class SupportTouchReactionEpisodeTest(unittest.TestCase):
    def test_same_unresolved_zone_creates_one_episode(self):
        history = _history(40)
        signals = _signals(history)
        signals.loc[history.index[20:26], "eligible"] = True

        rows = build_support_touch_reaction_rows(
            "AAA",
            history,
            signals,
            waiting_horizon=5,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]["observation_date"], history.index[20])

    def test_quarter_atr_zone_move_can_create_a_new_episode(self):
        history = make_ohlcv(
            [100.0] * 40,
            highs=[101.0] * 40,
            lows=[99.0] * 40,
            start="2026-01-02",
        )
        signals = _signals(history)
        signals.loc[history.index[[20, 28]], "eligible"] = True
        signals.loc[history.index[28], "zone_lower"] = 95.5
        signals.loc[history.index[28], "zone_upper"] = 97.5

        rows = build_support_touch_reaction_rows(
            "AAA",
            history,
            signals,
            waiting_horizon=5,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.iloc[1]["observation_date"], history.index[28])

    def test_ten_session_spacing_can_create_a_new_episode(self):
        history = make_ohlcv(
            [100.0] * 45,
            highs=[101.0] * 45,
            lows=[99.0] * 45,
            start="2026-01-02",
        )
        signals = _signals(history)
        signals.loc[history.index[[20, 30]], "eligible"] = True

        rows = build_support_touch_reaction_rows(
            "AAA",
            history,
            signals,
            waiting_horizon=5,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows.iloc[1]["observation_date"], history.index[30])

    def test_appended_future_rows_do_not_change_mature_episodes(self):
        long_history = make_ohlcv(
            [100.0] * 50,
            highs=[101.0] * 50,
            lows=[99.0] * 50,
            start="2026-01-02",
        )
        long_signals = _signals(long_history)
        long_signals.loc[long_history.index[[20, 30]], "eligible"] = True
        short_history = long_history.iloc[:45].copy()
        short_signals = long_signals.iloc[:45].copy()

        short_rows = build_support_touch_reaction_rows(
            "AAA",
            short_history,
            short_signals,
            waiting_horizon=5,
        )
        long_rows = build_support_touch_reaction_rows(
            "AAA",
            long_history,
            long_signals,
            waiting_horizon=5,
        )

        pd.testing.assert_frame_equal(short_rows, long_rows)


if __name__ == "__main__":
    unittest.main()
