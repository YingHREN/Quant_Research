from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.bottom_state_evaluation import (
    bottom_evaluation_decision,
    build_bottom_transition_events,
    evaluate_bottom_events,
    match_downtrend_baselines,
)
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


def _matching_event(
    event_id,
    *,
    ticker="AAA",
    date="2024-01-10",
    state="seller_exhaustion_watch",
    role="event",
    drawdown_bin="-15_-25",
    group="semiconductor",
    regime="market_in_correction",
    fold=1,
    cohort="confirmation",
    variant="full",
    horizon=10,
    scope="non_overlapping",
    forward_return=0.05,
):
    return {
        "event_id": event_id,
        "ticker": ticker,
        "observation_date": pd.Timestamp(date),
        "observation_state": state,
        "event_role": role,
        "drawdown_bin": drawdown_bin,
        "group": group,
        "market_regime": regime,
        "fold": fold,
        "cohort": cohort,
        "variant": variant,
        "horizon": horizon,
        "scope": scope,
        "forward_return": forward_return,
        "positive_return": forward_return > 0,
        "maximum_favorable_excursion": max(forward_return, 0.08),
        "maximum_adverse_excursion": min(forward_return, -0.03),
        "confirmed_within_horizon": state == "bullish_structure_confirmed",
        "failed_within_horizon": False,
        "sessions_to_confirmation": 3.0,
        "sessions_to_failure": np.nan,
        "state_maintained": True,
    }


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


class BottomBaselineMatchingTest(unittest.TestCase):
    def test_matching_prefers_same_ticker_then_same_group(self):
        events = pd.DataFrame(
            [
                _matching_event("event"),
                _matching_event(
                    "same-group",
                    ticker="BBB",
                    date="2024-01-09",
                    state="downtrend_continuation",
                    role="baseline",
                ),
                _matching_event(
                    "same-ticker",
                    date="2024-01-08",
                    state="downtrend_continuation",
                    role="baseline",
                ),
            ]
        )

        matched = match_downtrend_baselines(events)

        self.assertEqual(matched.iloc[0]["baseline_ticker"], "AAA")
        self.assertEqual(
            matched.iloc[0]["match_tier"],
            "same_ticker_exact_bin",
        )

    def test_matching_never_crosses_market_regime(self):
        events = pd.DataFrame(
            [
                _matching_event("event"),
                _matching_event(
                    "baseline",
                    state="downtrend_continuation",
                    role="baseline",
                    regime="confirmed_uptrend",
                ),
            ]
        )

        self.assertTrue(match_downtrend_baselines(events).empty)

    def test_matching_can_use_only_an_adjacent_drawdown_bin(self):
        events = pd.DataFrame(
            [
                _matching_event("event", drawdown_bin="-15_-25"),
                _matching_event(
                    "distant",
                    date="2024-01-09",
                    state="downtrend_continuation",
                    role="baseline",
                    drawdown_bin="below_-40",
                ),
                _matching_event(
                    "adjacent",
                    date="2024-01-08",
                    state="downtrend_continuation",
                    role="baseline",
                    drawdown_bin="-25_-40",
                ),
            ]
        )

        matched = match_downtrend_baselines(events)

        self.assertEqual(
            matched.iloc[0]["baseline_drawdown_bin"],
            "-25_-40",
        )
        self.assertEqual(
            matched.iloc[0]["match_tier"],
            "same_ticker_adjacent_bin",
        )

    def test_one_baseline_row_cannot_be_reused(self):
        events = pd.DataFrame(
            [
                _matching_event("event-1", date="2024-01-10"),
                _matching_event("event-2", date="2024-01-11"),
                _matching_event(
                    "baseline",
                    date="2024-01-09",
                    state="downtrend_continuation",
                    role="baseline",
                ),
            ]
        )

        matched = match_downtrend_baselines(events)

        self.assertEqual(len(matched), 1)
        self.assertEqual(
            matched["baseline_event_id"].nunique(),
            len(matched),
        )

    def test_matching_is_independent_of_future_outcome_columns(self):
        source = pd.DataFrame(
            [
                _matching_event("event"),
                _matching_event(
                    "baseline-near",
                    date="2024-01-09",
                    state="downtrend_continuation",
                    role="baseline",
                ),
                _matching_event(
                    "baseline-far",
                    date="2024-01-01",
                    state="downtrend_continuation",
                    role="baseline",
                ),
            ]
        )
        changed = source.copy()
        for column in (
            "forward_return",
            "maximum_favorable_excursion",
            "maximum_adverse_excursion",
        ):
            changed.loc[:, column] = source[column] * -100.0

        first = match_downtrend_baselines(source)
        second = match_downtrend_baselines(changed)

        self.assertEqual(
            first[["event_id", "baseline_event_id"]].to_dict("records"),
            second[["event_id", "baseline_event_id"]].to_dict("records"),
        )


class BottomEvaluationAggregationTest(unittest.TestCase):
    def test_matched_metrics_report_exact_event_baseline_deltas(self):
        event = _matching_event(
            "event",
            forward_return=0.10,
        )
        event.update(
            {
                "maximum_favorable_excursion": 0.15,
                "maximum_adverse_excursion": -0.04,
                "confirmed_within_horizon": True,
                "failed_within_horizon": False,
                "sessions_to_confirmation": 2.0,
                "sessions_to_failure": np.nan,
                "state_maintained": True,
            }
        )
        baseline = _matching_event(
            "baseline",
            date="2024-01-09",
            state="downtrend_continuation",
            role="baseline",
            forward_return=-0.02,
        )
        baseline.update(
            {
                "maximum_favorable_excursion": 0.03,
                "maximum_adverse_excursion": -0.08,
                "confirmed_within_horizon": False,
                "failed_within_horizon": True,
                "sessions_to_confirmation": np.nan,
                "sessions_to_failure": 4.0,
                "state_maintained": False,
            }
        )

        metrics = evaluate_bottom_events(pd.DataFrame([event, baseline]))
        row = metrics.loc[
            metrics["metric_scope"].eq("matched")
            & metrics["state_slice"].eq("early_states")
            & metrics["slice_dimension"].eq("all")
        ].iloc[0]

        self.assertEqual(row["event_count"], 1)
        self.assertEqual(row["matched_count"], 1)
        self.assertEqual(row["match_coverage"], 1.0)
        self.assertAlmostEqual(row["mean_return"], 0.10)
        self.assertAlmostEqual(row["median_return"], 0.10)
        self.assertAlmostEqual(row["positive_rate"], 1.0)
        self.assertAlmostEqual(row["mean_mfe"], 0.15)
        self.assertAlmostEqual(row["mean_mae"], -0.04)
        self.assertAlmostEqual(row["confirmation_rate"], 1.0)
        self.assertAlmostEqual(row["failure_rate"], 0.0)
        self.assertAlmostEqual(row["maintenance_rate"], 1.0)
        self.assertAlmostEqual(row["baseline_mean_return"], -0.02)
        self.assertAlmostEqual(row["return_gain"], 0.12)
        self.assertAlmostEqual(row["positive_rate_gain"], 1.0)
        self.assertAlmostEqual(row["mae_delta"], 0.04)

    def test_unmatched_metrics_remain_visible_when_no_baseline_exists(self):
        metrics = evaluate_bottom_events(
            pd.DataFrame([_matching_event("event")])
        )

        unmatched = metrics.loc[
            metrics["metric_scope"].eq("all_events")
            & metrics["state_slice"].eq("early_states")
            & metrics["slice_dimension"].eq("all")
        ].iloc[0]

        self.assertEqual(unmatched["event_count"], 1)
        self.assertEqual(unmatched["matched_count"], 0)
        self.assertEqual(unmatched["match_coverage"], 0.0)


def _gate_metrics(
    *,
    group_count=100,
    fold_wins=3,
    include_group=True,
    include_drawdown=True,
):
    common = {
        "cohort": "confirmation",
        "variant": "full",
        "horizon": 10,
        "scope": "non_overlapping",
        "state_slice": "early_states",
        "metric_scope": "matched",
        "event_count": 300,
        "matched_count": 300,
        "positive_rate_gain": 0.06,
        "return_gain": 0.03,
        "mae_delta": 0.01,
        "confirmation_rate": 0.40,
    }
    rows = [
        {
            **common,
            "slice_dimension": "all",
            "slice_value": "all",
        }
    ]
    for fold in range(5):
        rows.append(
            {
                **common,
                "slice_dimension": "fold",
                "slice_value": str(fold),
                "return_gain": 0.01 if fold < fold_wins else -0.01,
                "positive_rate_gain": (
                    0.01 if fold < fold_wins else -0.01
                ),
            }
        )
    if include_group:
        for group in ("semiconductor", "software", "other"):
            rows.append(
                {
                    **common,
                    "slice_dimension": "group",
                    "slice_value": group,
                    "matched_count": group_count,
                }
            )
    if include_drawdown:
        rows.append(
            {
                **common,
                "slice_dimension": "drawdown_bin",
                "slice_value": "-15_-25",
            }
        )
    for variant in (
        "no_location",
        "no_exhaustion",
        "no_demand",
        "no_structure",
        "no_environment",
    ):
        rows.append(
            {
                **common,
                "variant": variant,
                "slice_dimension": "all",
                "slice_value": "all",
                "return_gain": 0.01,
            }
        )
    for state, rate in (
        ("seller_exhaustion_watch", 0.30),
        ("early_bullish_reversal_watch", 0.40),
        ("structure_confirmed", 0.50),
    ):
        rows.append(
            {
                **common,
                "state_slice": state,
                "slice_dimension": "all",
                "slice_value": "all",
                "confirmation_rate": rate,
            }
        )
    return pd.DataFrame(rows)


class BottomEvaluationGateTest(unittest.TestCase):
    def test_gate_passes_only_with_all_performance_and_audit_conditions(self):
        decision = bottom_evaluation_decision(
            _gate_metrics(),
            evidence_contract_passed=True,
            group_causal_audit_passed=True,
            future_holdout_passed=True,
        )

        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["authority"], "advisory_only")
        self.assertEqual(decision["reasons"], [])

    def test_gate_fails_closed_without_future_holdout(self):
        decision = bottom_evaluation_decision(
            _gate_metrics(),
            evidence_contract_passed=True,
            group_causal_audit_passed=True,
            future_holdout_passed=False,
        )

        self.assertFalse(decision["eligible"])
        self.assertIn("future_holdout_required", decision["reasons"])

    def test_gate_requires_three_fold_wins_and_two_large_group_wins(self):
        decision = bottom_evaluation_decision(
            _gate_metrics(group_count=99, fold_wins=2),
            evidence_contract_passed=True,
            group_causal_audit_passed=True,
            future_holdout_passed=True,
        )

        self.assertFalse(decision["eligible"])
        self.assertIn("insufficient_fold_wins", decision["reasons"])
        self.assertIn("insufficient_group_evidence", decision["reasons"])

    def test_gate_fails_when_group_or_drawdown_slices_are_missing(self):
        decision = bottom_evaluation_decision(
            _gate_metrics(include_group=False, include_drawdown=False),
            evidence_contract_passed=True,
            group_causal_audit_passed=True,
            future_holdout_passed=True,
        )

        self.assertFalse(decision["eligible"])
        self.assertIn("group_slices_missing", decision["reasons"])
        self.assertIn("drawdown_slices_missing", decision["reasons"])


if __name__ == "__main__":
    unittest.main()
