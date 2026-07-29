import unittest

import numpy as np
import pandas as pd

from research.historical_demand_support import (
    build_historical_demand_support_rows,
)


def _history(length=40, *, start="2026-01-02"):
    index = pd.bdate_range(start, periods=length)
    close = np.linspace(90.0, 110.0, length)
    return pd.DataFrame(
        {
            "Open": close - 0.4,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(length, 1_000_000.0),
        },
        index=index,
    )


def _flat_history(length=50, *, start="2026-01-02"):
    index = pd.bdate_range(start, periods=length)
    return pd.DataFrame(
        {
            "Open": np.full(length, 99.6),
            "High": np.full(length, 101.0),
            "Low": np.full(length, 99.0),
            "Close": np.full(length, 100.0),
            "Volume": np.full(length, 1_000_000.0),
        },
        index=index,
    )


def _demand_rows(history):
    return pd.DataFrame(
        {
            "demand_confirmation_conditions": [
                [] for _ in range(len(history))
            ],
            "supply_pressure_conditions": [
                [] for _ in range(len(history))
            ],
        },
        index=history.index,
    )


def _build(
    history,
    *,
    demand_conditions=None,
    pocket_pivots=None,
    qqq_close=None,
    sector_close=None,
):
    demand = _demand_rows(history)
    for position, conditions in (demand_conditions or {}).items():
        demand.iat[
            position,
            demand.columns.get_loc("demand_confirmation_conditions"),
        ] = list(conditions)
    entries = [{} for _ in range(len(history))]
    for position, active in (pocket_pivots or {}).items():
        entries[position] = {"pocket_pivot": bool(active)}
    return build_historical_demand_support_rows(
        history,
        demand_rows=demand,
        entry_signal_rows=entries,
        qqq_close=qqq_close,
        sector_close=sector_close,
    )


class HistoricalDemandSupportContractTest(unittest.TestCase):
    def test_rejects_misaligned_evidence(self):
        history = _history()
        with self.assertRaisesRegex(ValueError, "align"):
            build_historical_demand_support_rows(
                history,
                demand_rows=_demand_rows(history).iloc[:-1],
                entry_signal_rows=[{} for _ in range(len(history))],
            )

    def test_short_history_is_explicitly_unavailable(self):
        history = _history(19)
        result = build_historical_demand_support_rows(
            history,
            demand_rows=_demand_rows(history),
            entry_signal_rows=[{} for _ in range(len(history))],
        )

        self.assertEqual(result.index.tolist(), history.index.tolist())
        self.assertEqual(
            result.iloc[-1]["historical_demand_support_state"],
            "unavailable",
        )
        self.assertEqual(
            result.iloc[-1][
                "historical_demand_support_unavailable_reason"
            ],
            "insufficient_atr_history",
        )
        self.assertEqual(
            result.iloc[-1]["historical_demand_support_coverage"],
            0.0,
        )
        self.assertEqual(
            result.iloc[-1]["historical_demand_support_event_types"],
            [],
        )

    def test_rejects_duplicate_or_non_monotonic_dates(self):
        history = _history()
        duplicated = pd.concat((history.iloc[:2], history.iloc[[1]]))
        with self.assertRaisesRegex(ValueError, "unique"):
            build_historical_demand_support_rows(
                duplicated,
                demand_rows=_demand_rows(duplicated),
                entry_signal_rows=[{} for _ in range(len(duplicated))],
            )

        descending = history.iloc[::-1]
        with self.assertRaisesRegex(ValueError, "increasing"):
            build_historical_demand_support_rows(
                descending,
                demand_rows=_demand_rows(descending),
                entry_signal_rows=[{} for _ in range(len(descending))],
            )

    def test_rejects_non_finite_ohlcv(self):
        history = _history()
        history.iloc[-1, history.columns.get_loc("Volume")] = np.inf
        with self.assertRaisesRegex(ValueError, "finite"):
            build_historical_demand_support_rows(
                history,
                demand_rows=_demand_rows(history),
                entry_signal_rows=[{} for _ in range(len(history))],
            )


class HistoricalDemandSupportEventTest(unittest.TestCase):
    def test_each_supported_condition_creates_a_typed_event(self):
        cases = (
            ("up_volume_confirmation", "up_volume_confirmation"),
            ("buyer_absorption", "buyer_absorption"),
            ("breakout_acceptance", "breakout_acceptance"),
            ("breakout_follow_through", "breakout_follow_through"),
        )
        for condition, expected_type in cases:
            with self.subTest(condition=condition):
                history = _history(45)
                history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
                result = _build(
                    history,
                    demand_conditions={25: [condition]},
                )
                event_row = result.iloc[25]
                self.assertEqual(
                    event_row["historical_demand_support_event_types"],
                    [expected_type],
                )
                self.assertEqual(
                    event_row["historical_demand_support_event_count"],
                    1,
                )
                self.assertEqual(
                    event_row["historical_demand_support_first_date"],
                    history.index[25].date().isoformat(),
                )
                self.assertGreater(
                    event_row["historical_demand_support_volume_ratio"],
                    1.2,
                )
                self.assertLess(
                    event_row["historical_demand_support_lower"],
                    event_row["historical_demand_support_upper"],
                )

    def test_pocket_pivot_creates_an_event(self):
        history = _history(45)
        history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
        result = _build(history, pocket_pivots={25: True})

        self.assertEqual(
            result.iloc[25]["historical_demand_support_event_types"],
            ["pocket_pivot"],
        )

    def test_same_bar_conditions_are_deduplicated_by_priority(self):
        history = _history(45)
        history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
        result = _build(
            history,
            demand_conditions={
                25: ["up_volume_confirmation", "buyer_absorption"],
            },
            pocket_pivots={25: True},
        )

        row = result.iloc[25]
        self.assertEqual(row["historical_demand_support_event_count"], 1)
        self.assertEqual(
            row["historical_demand_support_event_types"],
            ["buyer_absorption"],
        )

    def test_environment_confirmation_is_point_in_time(self):
        history = _history(45)
        history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
        benchmark = pd.Series(
            np.linspace(100.0, 104.0, len(history)),
            index=history.index,
        )
        result = _build(
            history,
            demand_conditions={25: ["up_volume_confirmation"]},
            qqq_close=benchmark,
            sector_close=benchmark,
        )

        self.assertIn(
            "environment_confirmed",
            result.iloc[25]["historical_demand_support_conditions"],
        )


class HistoricalDemandSupportMemoryTest(unittest.TestCase):
    def test_nearby_events_cluster_and_keep_first_and_latest_dates(self):
        history = _flat_history(40)
        history.iloc[[25, 30], history.columns.get_loc("Volume")] = 2_000_000
        history.iloc[26:30, history.columns.get_loc("Open")] = 102.5
        history.iloc[26:30, history.columns.get_loc("High")] = 103.5
        history.iloc[26:30, history.columns.get_loc("Low")] = 102.0
        history.iloc[26:30, history.columns.get_loc("Close")] = 103.0
        result = _build(
            history,
            demand_conditions={
                25: ["up_volume_confirmation"],
                30: ["buyer_absorption"],
            },
        )

        row = result.iloc[30]
        self.assertEqual(row["historical_demand_support_event_count"], 2)
        self.assertEqual(
            row["historical_demand_support_first_date"],
            history.index[25].date().isoformat(),
        )
        self.assertEqual(
            row["historical_demand_support_last_confirmed_date"],
            history.index[30].date().isoformat(),
        )
        self.assertEqual(
            set(row["historical_demand_support_event_types"]),
            {"up_volume_confirmation", "buyer_absorption"},
        )

    def test_score_has_a_forty_session_half_life(self):
        history = _flat_history(70)
        history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
        result = _build(
            history,
            demand_conditions={25: ["up_volume_confirmation"]},
        )

        event_score = result.iloc[25][
            "historical_demand_support_score"
        ]
        aged_score = result.iloc[65][
            "historical_demand_support_score"
        ]
        self.assertAlmostEqual(aged_score, event_score * 0.5, places=6)

    def test_event_expires_after_one_hundred_twenty_sessions(self):
        history = _flat_history(147)
        history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
        result = _build(
            history,
            demand_conditions={25: ["up_volume_confirmation"]},
        )

        self.assertNotEqual(
            result.iloc[145]["historical_demand_support_state"],
            "unavailable",
        )
        self.assertEqual(
            result.iloc[146]["historical_demand_support_state"],
            "unavailable",
        )

    def test_high_volume_half_atr_break_invalidates_immediately(self):
        history = _flat_history(30)
        history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
        history.iloc[26] = [98.8, 99.0, 97.8, 98.3, 2_000_000]
        result = _build(
            history,
            demand_conditions={25: ["up_volume_confirmation"]},
        )

        row = result.iloc[26]
        self.assertEqual(
            row["historical_demand_support_state"],
            "invalidated",
        )
        self.assertIn(
            "high_volume_support_break",
            row["historical_demand_support_counter_conditions"],
        )

    def test_two_closes_below_zone_invalidate_without_high_volume(self):
        history = _flat_history(31)
        history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
        history.iloc[26] = [99.7, 100.0, 99.1, 99.3, 1_000_000]
        history.iloc[27] = [99.6, 99.9, 99.0, 99.2, 1_000_000]
        result = _build(
            history,
            demand_conditions={25: ["up_volume_confirmation"]},
        )

        row = result.iloc[27]
        self.assertEqual(
            row["historical_demand_support_state"],
            "invalidated",
        )
        self.assertIn(
            "consecutive_closes_below_support",
            row["historical_demand_support_counter_conditions"],
        )

    def test_retest_that_closes_back_above_zone_is_accepted(self):
        history = _flat_history(31)
        history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
        history.iloc[26] = [102.5, 103.5, 102.0, 103.0, 1_000_000]
        history.iloc[27] = [100.1, 100.6, 99.3, 100.4, 1_200_000]
        result = _build(
            history,
            demand_conditions={25: ["up_volume_confirmation"]},
        )

        row = result.iloc[27]
        self.assertEqual(
            row["historical_demand_support_state"],
            "accepted",
        )
        self.assertEqual(
            row["historical_demand_support_retest_count"],
            1,
        )
        self.assertIn(
            "support_retest_accepted",
            row["historical_demand_support_conditions"],
        )

    def test_future_append_does_not_change_historical_rows(self):
        history = _flat_history(80)
        history.iloc[25, history.columns.get_loc("Volume")] = 2_000_000
        demand_conditions = {25: ["up_volume_confirmation"]}
        prefix = _build(
            history.iloc[:60],
            demand_conditions=demand_conditions,
        )
        extended = _build(
            history,
            demand_conditions=demand_conditions,
        )

        pd.testing.assert_frame_equal(
            prefix,
            extended.loc[prefix.index],
        )


if __name__ == "__main__":
    unittest.main()
