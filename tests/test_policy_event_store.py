import tempfile
import unittest
from pathlib import Path

from web.services.policy_event_store import PolicyEventStore


def policy_event(**overrides):
    row = {
        "event_id": "fomc-2026-07-rate",
        "catalog_version": "fed-policy-v1",
        "event_type": "policy_rate",
        "effective_date": "2026-07-30",
        "available_at": "2026-07-29T18:00:00+00:00",
        "source_url": (
            "https://www.federalreserve.gov/newsevents/"
            "pressreleases/monetary20260729a.htm"
        ),
        "source_title": "Federal Reserve issues FOMC statement",
        "source_published_at": "2026-07-29T18:00:00+00:00",
        "payload_json": '{"target_lower":4.25,"target_upper":4.5}',
    }
    row.update(overrides)
    return row


def policy_period(**overrides):
    row = {
        "period_id": "restrictive-hold-2026",
        "catalog_version": "fed-policy-v1",
        "label_zh": "限制性利率维持",
        "label_en": "Restrictive rate hold",
        "start_date": "2026-07-30",
        "end_date": None,
        "available_at": "2026-07-29T18:00:00+00:00",
        "interpretation_zh": "人工描述，不是预测。",
        "interpretation_en": "Human description, not a forecast.",
        "source_event_ids_json": '["fomc-2026-07-rate"]',
    }
    row.update(overrides)
    return row


class PolicyEventStoreTest(unittest.TestCase):
    def test_events_are_idempotent_and_hidden_before_available_at(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PolicyEventStore(Path(directory) / "macro.db")
            store.initialize()

            self.assertEqual(store.upsert_events([policy_event()]), 1)
            self.assertEqual(store.upsert_events([policy_event()]), 1)
            before = store.load_events("2026-07-29T17:59:59+00:00")
            after = store.load_events("2026-07-29T18:00:00+00:00")

            self.assertTrue(before.empty)
            self.assertEqual(len(after), 1)
            self.assertEqual(after.iloc[0]["event_id"], "fomc-2026-07-rate")

    def test_event_type_filter_is_applied_after_point_in_time_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PolicyEventStore(Path(directory) / "macro.db")
            store.initialize()
            store.upsert_events(
                [
                    policy_event(),
                    policy_event(
                        event_id="fed-2026-08-qt",
                        event_type="qt",
                        effective_date="2026-08-01",
                        payload_json='{"monthly_cap_usd":25000000000}',
                    ),
                ]
            )

            rows = store.load_events(
                "2026-08-02T00:00:00+00:00",
                event_types=("qt",),
            )

            self.assertEqual(rows["event_type"].tolist(), ["qt"])

    def test_event_rejects_non_official_source(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PolicyEventStore(Path(directory) / "macro.db")
            store.initialize()

            with self.assertRaisesRegex(ValueError, "Federal Reserve"):
                store.upsert_events(
                    [
                        policy_event(
                            source_url="https://example.com/fomc-summary"
                        )
                    ]
                )

    def test_event_rejects_availability_without_utc_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PolicyEventStore(Path(directory) / "macro.db")
            store.initialize()

            with self.assertRaisesRegex(ValueError, "UTC offset"):
                store.upsert_events(
                    [
                        policy_event(
                            available_at="2026-07-29T18:00:00",
                        )
                    ]
                )

    def test_event_rejects_invalid_payload_json(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PolicyEventStore(Path(directory) / "macro.db")
            store.initialize()

            with self.assertRaisesRegex(ValueError, "JSON"):
                store.upsert_events(
                    [policy_event(payload_json="{not-json")]
                )

    def test_period_revision_does_not_rewrite_official_event(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PolicyEventStore(Path(directory) / "macro.db")
            store.initialize()
            store.upsert_events([policy_event()])
            store.upsert_periods([policy_period()])
            store.upsert_periods(
                [
                    policy_period(
                        label_zh="限制性政策维持",
                        interpretation_zh="更新后的人工解释，不是预测。",
                    )
                ]
            )

            events = store.load_events("2026-08-01T00:00:00+00:00")
            periods = store.load_periods("2026-08-01T00:00:00+00:00")

            self.assertEqual(events.iloc[0]["source_title"], (
                "Federal Reserve issues FOMC statement"
            ))
            self.assertEqual(periods.iloc[0]["label_zh"], "限制性政策维持")

    def test_incomplete_periods_can_be_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PolicyEventStore(Path(directory) / "macro.db")
            store.initialize()
            store.upsert_periods(
                [
                    policy_period(),
                    policy_period(
                        period_id="completed-easing",
                        start_date="2025-01-01",
                        end_date="2025-06-30",
                    ),
                ]
            )

            all_periods = store.load_periods(
                "2026-08-01T00:00:00+00:00"
            )
            completed = store.load_periods(
                "2026-08-01T00:00:00+00:00",
                include_incomplete=False,
            )

            self.assertEqual(len(all_periods), 2)
            self.assertEqual(
                completed["period_id"].tolist(),
                ["completed-easing"],
            )

    def test_period_source_event_ids_are_stored_as_canonical_json(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PolicyEventStore(Path(directory) / "macro.db")
            store.initialize()
            store.upsert_periods(
                [
                    policy_period(
                        source_event_ids_json=[
                            "fomc-2026-07-rate",
                            "fed-2026-08-qt",
                        ]
                    )
                ]
            )

            rows = store.load_periods("2026-08-01T00:00:00+00:00")

            self.assertEqual(
                rows.iloc[0]["source_event_ids_json"],
                '["fomc-2026-07-rate","fed-2026-08-qt"]',
            )


if __name__ == "__main__":
    unittest.main()
