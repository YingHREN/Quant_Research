import unittest

import numpy as np
import pandas as pd

from web.services.top_risk_timeline import (
    build_top_risk_timeline,
    unavailable_top_risk_timeline,
)


class TopRiskTimelineTest(unittest.TestCase):
    def risk_frame(
        self,
        states,
        *,
        raw_states=None,
        scores=None,
        recoveries=None,
        ticker="NBIS",
        start="2026-06-01",
    ):
        dates = pd.date_range(start, periods=len(states), freq="B")
        raw_states = states if raw_states is None else raw_states
        scores = (
            [float(index * 10) for index in range(len(states))]
            if scores is None
            else scores
        )
        recoveries = (
            [False] * len(states) if recoveries is None else recoveries
        )
        index = pd.MultiIndex.from_product(
            [[ticker], dates],
            names=("ticker", "observation_date"),
        )
        return pd.DataFrame(
            {
                "high_level_distribution_score": scores,
                "high_level_distribution_raw_score": scores,
                "high_level_distribution_state": states,
                "high_level_distribution_raw_state": raw_states,
                "high_level_distribution_age_sessions": list(
                    range(len(states))
                ),
                "top_risk_recovery": recoveries,
            },
            index=index,
        )

    def dates(self, frame):
        return frame.index.get_level_values("observation_date")

    def test_emits_each_top_risk_transition_once(self):
        frame = self.risk_frame(
            ["inactive", "watch", "watch", "high", "fading", "confirmed"],
            raw_states=[
                "inactive", "watch", "watch", "high", "inactive", "confirmed"
            ],
            scores=[0.0, 45.0, 47.0, 65.0, 44.0, 82.0],
        )

        result = build_top_risk_timeline(
            frame,
            "nbis",
            self.dates(frame),
        )

        self.assertEqual(
            [event["type"] for event in result["events"]],
            [
                "top_risk_watch",
                "top_risk_high",
                "top_risk_confirmed",
            ],
        )
        self.assertEqual(
            [event["time"] for event in result["events"]],
            ["2026-06-02", "2026-06-04", "2026-06-08"],
        )
        self.assertEqual(result["latest"]["state"], "confirmed")
        self.assertEqual(result["latest"]["score"], 82.0)
        self.assertEqual(result["status"], "available")

    def test_recovery_is_emitted_once_and_fading_is_not_an_event(self):
        frame = self.risk_frame(
            ["high", "fading", "inactive", "inactive"],
            raw_states=["high", "inactive", "inactive", "inactive"],
            scores=[70.0, 52.0, 0.0, 0.0],
            recoveries=[False, False, True, False],
        )

        result = build_top_risk_timeline(frame, "NBIS", self.dates(frame))

        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["top_risk_high", "top_risk_recovery"],
        )
        self.assertEqual(result["events"][1]["time"], "2026-06-03")

    def test_risk_downgrades_do_not_repeat_markers_before_clearance(self):
        frame = self.risk_frame(
            [
                "inactive",
                "watch",
                "high",
                "confirmed",
                "high",
                "watch",
                "fading",
                "inactive",
                "watch",
            ],
            raw_states=[
                "inactive",
                "watch",
                "high",
                "confirmed",
                "high",
                "watch",
                "inactive",
                "inactive",
                "watch",
            ],
            scores=[0.0, 45.0, 65.0, 85.0, 68.0, 47.0, 35.0, 0.0, 44.0],
        )

        result = build_top_risk_timeline(frame, "NBIS", self.dates(frame))

        self.assertEqual(
            [event["type"] for event in result["events"]],
            [
                "top_risk_watch",
                "top_risk_high",
                "top_risk_confirmed",
                "top_risk_recovery",
                "top_risk_watch",
            ],
        )

    def test_appending_future_rows_does_not_change_existing_events(self):
        frame = self.risk_frame(
            ["inactive", "watch", "watch", "high", "confirmed", "fading"],
            raw_states=[
                "inactive", "watch", "watch", "high", "confirmed", "inactive"
            ],
            scores=[0.0, 42.0, 45.0, 63.0, 85.0, 58.0],
        )
        before = build_top_risk_timeline(
            frame.iloc[:-2],
            "NBIS",
            self.dates(frame)[:-2],
        )
        after = build_top_risk_timeline(frame, "NBIS", self.dates(frame))
        cutoff = self.dates(frame)[-3].date().isoformat()

        self.assertEqual(
            before["events"],
            [
                event
                for event in after["events"]
                if event["time"] <= cutoff
            ],
        )

    def test_filters_rows_outside_chart_dates(self):
        frame = self.risk_frame(
            ["watch", "high", "confirmed"],
            scores=[45.0, 65.0, 85.0],
        )

        result = build_top_risk_timeline(
            frame,
            "NBIS",
            self.dates(frame)[1:],
        )

        self.assertEqual(
            [event["type"] for event in result["events"]],
            ["top_risk_high", "top_risk_confirmed"],
        )
        self.assertEqual(result["latest"]["time"], "2026-06-03")

    def test_non_finite_optional_values_are_serialized_as_none(self):
        frame = self.risk_frame(
            ["confirmed"],
            scores=[np.nan],
        )
        frame["high_level_distribution_raw_score"] = np.inf
        frame["high_level_distribution_age_sessions"] = np.nan

        result = build_top_risk_timeline(frame, "NBIS", self.dates(frame))

        self.assertIsNone(result["latest"]["score"])
        self.assertIsNone(result["latest"]["raw_score"])
        self.assertIsNone(result["latest"]["memory_age_sessions"])

    def test_missing_ticker_or_columns_returns_typed_unavailable(self):
        frame = self.risk_frame(["watch"])

        missing_ticker = build_top_risk_timeline(
            frame, "AMD", self.dates(frame)
        )
        missing_columns = build_top_risk_timeline(
            frame.drop(columns=["high_level_distribution_state"]),
            "NBIS",
            self.dates(frame),
        )

        self.assertEqual(missing_ticker["status"], "unavailable")
        self.assertEqual(missing_ticker["unavailable_reason"], "not_available")
        self.assertEqual(missing_columns["status"], "unavailable")
        self.assertEqual(missing_columns["events"], [])

    def test_duplicate_ticker_date_keys_are_rejected(self):
        frame = self.risk_frame(["watch"])
        duplicate = pd.concat([frame, frame])

        with self.assertRaisesRegex(
            ValueError, "duplicate ticker-date rows"
        ):
            build_top_risk_timeline(
                duplicate,
                "NBIS",
                self.dates(frame),
            )

    def test_unavailable_contract_is_stable(self):
        self.assertEqual(
            unavailable_top_risk_timeline("model_error"),
            {
                "model_key": "high_level_distribution_risk_v1",
                "model_version": "v1",
                "status": "unavailable",
                "unavailable_reason": "model_error",
                "latest": None,
                "events": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
