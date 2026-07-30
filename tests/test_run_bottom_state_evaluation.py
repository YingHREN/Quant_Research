from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.run_bottom_state_evaluation import (
    BOTTOM_ABLATIONS,
    render_bottom_evaluation_report,
    run_bottom_state_evaluation,
    write_bottom_evaluation_outputs,
)
from tests.helpers import make_ohlcv


def _history(length=120):
    closes = np.linspace(180.0, 90.0, length)
    return make_ohlcv(
        closes,
        opens=closes + 0.5,
        highs=closes + 1.5,
        lows=closes - 1.5,
        volumes=np.full(length, 1_000_000.0),
        start="2025-01-02",
    )


def _synthetic_states(index, *, positive=True):
    result = pd.DataFrame(
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
    if positive:
        for position, state in (
            (65, "potential_support"),
            (75, "seller_exhaustion_watch"),
            (85, "early_bullish_reversal_watch"),
            (95, "bullish_structure_confirmed"),
        ):
            result.iloc[position, 0] = state
            result.iloc[position, 1] = state
            result.iloc[position, 2] = True
    return result


def _synthetic_replay(ticker, histories):
    history = histories[ticker]
    evidence = pd.DataFrame(
        {
            "market_regime_state": "market_in_correction",
        },
        index=history.index,
    )
    return evidence, _synthetic_states(history.index)


def _synthetic_state_builder(
    history,
    evidence,
    *,
    disabled_components=frozenset(),
):
    del evidence
    return _synthetic_states(
        history.index,
        positive="location" not in disabled_components,
    )


class BottomStateEvaluationRunnerTest(unittest.TestCase):
    def setUp(self):
        self.tickers = ("AAA", "CCC")
        self.histories = {ticker: _history() for ticker in self.tickers}
        self.histories["SPY"] = _history()
        self.histories["QQQ"] = _history()
        self.groups = {
            "AAA": "semiconductor",
            "CCC": "software",
        }
        self.intervals = pd.DataFrame(
            [
                {
                    "ticker": ticker,
                    "effective_from": "2020-01-01",
                    "effective_to": None,
                    "group": group,
                    "source": "historical_backfill_assumption",
                    "observed_at": "2026-07-24",
                }
                for ticker, group in self.groups.items()
            ]
        )
        self.cohorts = {
            "development": ("AAA",),
            "confirmation": ("CCC",),
        }

    def _run(self):
        return run_bottom_state_evaluation(
            self.histories,
            cohorts=self.cohorts,
            fallback_groups=self.groups,
            group_intervals=self.intervals,
            asof="2026-07-24",
            start="2025-01-01",
            n_folds=5,
            minimum_sessions=80,
            replay_builder=_synthetic_replay,
            state_builder=_synthetic_state_builder,
        )

    def test_synthetic_runner_emits_frozen_variants_and_typed_coverage(self):
        metrics, events, manifest = self._run()

        self.assertEqual(set(events["cohort"]), {
            "development",
            "confirmation",
        })
        self.assertEqual(set(events["variant"]), set(BOTTOM_ABLATIONS))
        self.assertEqual(set(events["horizon"]), {5, 10, 20})
        self.assertEqual(
            set(events["scope"]),
            {"all_transitions", "non_overlapping"},
        )
        self.assertEqual(set(events["fold"]), {1, 2, 3, 4, 5})
        coverage = metrics.loc[metrics["row_type"].eq("coverage")]
        self.assertEqual(
            len(coverage),
            len(self.tickers) * len(BOTTOM_ABLATIONS) * 3 * 2,
        )
        unavailable = coverage.loc[
            coverage["variant"].eq("no_location")
        ]
        self.assertTrue(unavailable["positive_event_count"].eq(0).all())
        self.assertTrue(
            unavailable["status"].eq("unavailable_no_positive_events").all()
        )
        performance = metrics.loc[metrics["row_type"].eq("performance")]
        self.assertIn("group", set(performance["slice_dimension"]))
        self.assertIn(
            "market_regime",
            set(performance["slice_dimension"]),
        )
        self.assertIn(
            "drawdown_bin",
            set(performance["slice_dimension"]),
        )
        self.assertFalse(manifest["decision"]["eligible"])
        self.assertEqual(
            manifest["decision"]["authority"],
            "advisory_only",
        )
        self.assertIn(
            "future_holdout_required",
            manifest["decision"]["reasons"],
        )
        self.assertNotIn("online_authority", manifest)
        self.assertEqual(
            set(manifest["cohorts"]),
            {"development", "confirmation"},
        )

    def test_runner_order_is_deterministic(self):
        first_metrics, first_events, first_manifest = self._run()
        second_metrics, second_events, second_manifest = self._run()

        pd.testing.assert_frame_equal(first_metrics, second_metrics)
        pd.testing.assert_frame_equal(first_events, second_events)
        self.assertEqual(first_manifest, second_manifest)

    def test_report_and_outputs_are_strict_and_chinese(self):
        metrics, _, manifest = self._run()
        report = render_bottom_evaluation_report(metrics, manifest)

        self.assertIn("# 底部状态因果评估", report)
        self.assertIn("确认队列", report)
        self.assertIn("消融", report)
        self.assertIn("仅供研究", report)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_bottom_evaluation_outputs(
                metrics,
                manifest,
                report_path=root / "study.md",
                metrics_path=root / "study.csv",
                manifest_path=root / "study.json",
            )

            self.assertEqual(
                json.loads((root / "study.json").read_text("utf-8")),
                manifest,
            )
            loaded = pd.read_csv(root / "study.csv")
            self.assertTrue(loaded["row_type"].notna().all())

    def test_overlapping_cohorts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "disjoint"):
            run_bottom_state_evaluation(
                self.histories,
                cohorts={
                    "development": ("AAA",),
                    "confirmation": ("AAA",),
                },
                fallback_groups=self.groups,
                group_intervals=self.intervals,
                asof="2026-07-24",
                replay_builder=_synthetic_replay,
                state_builder=_synthetic_state_builder,
            )


if __name__ == "__main__":
    unittest.main()
