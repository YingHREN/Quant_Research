import unittest

import pandas as pd

from research.policy_period_matrix import build_policy_period_matrix


OFFICIAL_URL = (
    "https://www.federalreserve.gov/"
    "newsevents/pressreleases/monetary20200315a.htm"
)


def history(values, dates=None):
    return pd.DataFrame(
        {"Close": values},
        index=pd.to_datetime(
            dates
            or (
                "2020-01-02",
                "2020-01-31",
                "2020-02-28",
                "2020-03-31",
            )
        ),
    )


def period_fixture():
    return pd.DataFrame(
        [
            {
                "period_id": "complete",
                "catalog_version": "fed-policy-v1",
                "label_zh": "完整时期",
                "label_en": "Complete period",
                "start_date": "2020-01-02",
                "end_date": "2020-03-31",
                "available_at": "2020-03-31T20:00:00+00:00",
                "interpretation_zh": "历史描述，不是预测。",
                "interpretation_en": (
                    "Historical description, not a forecast."
                ),
                "source_event_ids_json": (
                    '["event-a","missing-event"]'
                ),
            },
            {
                "period_id": "open",
                "catalog_version": "fed-policy-v1",
                "label_zh": "进行中时期",
                "label_en": "Open period",
                "start_date": "2020-04-01",
                "end_date": None,
                "available_at": "2020-04-01T20:00:00+00:00",
                "interpretation_zh": "进行中。",
                "interpretation_en": "In progress.",
                "source_event_ids_json": "[]",
            },
            {
                "period_id": "future-period",
                "catalog_version": "fed-policy-v1",
                "label_zh": "未来时期",
                "label_en": "Future period",
                "start_date": "2027-01-01",
                "end_date": "2027-03-31",
                "available_at": "2027-03-31T20:00:00+00:00",
                "interpretation_zh": "未来才可见。",
                "interpretation_en": "Visible in the future.",
                "source_event_ids_json": '["future-event"]',
            },
        ]
    )


def event_fixture():
    return pd.DataFrame(
        [
            {
                "event_id": "event-a",
                "catalog_version": "fed-policy-v1",
                "event_type": "policy_rate",
                "effective_date": "2020-03-16",
                "available_at": "2020-03-15T21:00:00+00:00",
                "source_url": OFFICIAL_URL,
                "source_title": "Federal Reserve issues FOMC statement",
                "source_published_at": (
                    "2020-03-15T21:00:00+00:00"
                ),
                "payload_json": (
                    '{"target_lower":0.0,"target_upper":0.25}'
                ),
            },
            {
                "event_id": "future-event",
                "catalog_version": "fed-policy-v1",
                "event_type": "policy_rate",
                "effective_date": "2027-01-01",
                "available_at": "2027-01-01T20:00:00+00:00",
                "source_url": OFFICIAL_URL,
                "source_title": "Future official event",
                "source_published_at": (
                    "2027-01-01T20:00:00+00:00"
                ),
                "payload_json": "{}",
            },
        ]
    )


def history_fixture():
    return {
        "SPY": history([100.0, 102.0, 105.0, 110.0]),
        "XLK": history([100.0, 103.0, 106.0, 110.0]),
        "XLRE": history(
            [10.0, 11.0],
            dates=("2021-01-04", "2021-01-05"),
        ),
    }


class PolicyPeriodMatrixTest(unittest.TestCase):
    def test_complete_period_exposes_metrics_without_ranking(self):
        payload = build_policy_period_matrix(
            periods=period_fixture(),
            events=event_fixture(),
            histories=history_fixture(),
            asof="2026-07-29T23:59:59+00:00",
        )

        self.assertEqual(
            payload["artifact_key"],
            "policy_period_matrix_v1",
        )
        self.assertEqual(payload["lifecycle"], "research")
        self.assertEqual(payload["decision_permission"], "advisory")
        self.assertEqual(payload["online_authority"], "none")
        self.assertTrue(payload["point_in_time"])
        self.assertTrue(payload["historical_description_only"])
        self.assertNotIn("score", payload)
        self.assertNotIn("recommendation", payload)
        complete = next(
            row
            for row in payload["rows"]
            if row["period_id"] == "complete"
            and row["ticker"] == "XLK"
        )
        self.assertEqual(complete["status"], "complete")
        self.assertAlmostEqual(complete["total_return"], 0.10)
        self.assertEqual(
            payload["metrics"],
            [
                "total_return",
                "annualized_return",
                "relative_spy_return",
                "max_drawdown",
                "positive_month_ratio",
            ],
        )

    def test_non_complete_rows_keep_metrics_null(self):
        payload = build_policy_period_matrix(
            periods=period_fixture(),
            events=event_fixture(),
            histories=history_fixture(),
            asof="2026-07-29T23:59:59+00:00",
        )

        for row in payload["rows"]:
            if row["status"] != "complete":
                for metric in payload["metrics"]:
                    self.assertIsNone(row[metric])

        xlre = next(
            row
            for row in payload["rows"]
            if row["period_id"] == "complete"
            and row["ticker"] == "XLRE"
        )
        self.assertEqual(xlre["status"], "not_listed")

    def test_period_detail_resolves_only_known_source_events(self):
        payload = build_policy_period_matrix(
            periods=period_fixture(),
            events=event_fixture(),
            histories=history_fixture(),
            asof="2026-07-29T23:59:59+00:00",
        )
        period = next(
            item
            for item in payload["periods"]
            if item["period_id"] == "complete"
        )

        self.assertEqual(period["source_event_ids"], ["event-a"])
        self.assertEqual(len(period["events"]), 1)
        self.assertEqual(period["events"][0]["source_url"], OFFICIAL_URL)
        self.assertEqual(
            period["events"][0]["event_type"],
            "policy_rate",
        )

    def test_future_event_and_period_are_invisible_at_asof(self):
        payload = build_policy_period_matrix(
            periods=period_fixture(),
            events=event_fixture(),
            histories=history_fixture(),
            asof="2026-07-29T23:59:59+00:00",
        )

        self.assertNotIn(
            "future-period",
            {row["period_id"] for row in payload["rows"]},
        )
        self.assertNotIn(
            "future-event",
            {
                event["event_id"]
                for period in payload["periods"]
                for event in period["events"]
            },
        )

    def test_empty_periods_return_typed_unavailable_payload(self):
        payload = build_policy_period_matrix(
            periods=pd.DataFrame(),
            events=pd.DataFrame(),
            histories={},
            asof="2026-07-29",
        )

        self.assertEqual(payload["rows"], [])
        self.assertEqual(payload["periods"], [])
        self.assertEqual(payload["coverage"]["complete_rows"], 0)
        self.assertEqual(payload["coverage"]["eligible_rows"], 0)
        self.assertIsNone(payload["coverage"]["ratio"])
        self.assertEqual(
            payload["unavailable_reason"],
            "policy_periods_unavailable",
        )


if __name__ == "__main__":
    unittest.main()
