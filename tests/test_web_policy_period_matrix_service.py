import tempfile
import unittest
from pathlib import Path

import pandas as pd

from web.services.policy_event_store import PolicyEventStore
from web.services.policy_period_matrix import PolicyPeriodMatrixService


OFFICIAL_URL = (
    "https://www.federalreserve.gov/"
    "newsevents/pressreleases/monetary20200315a.htm"
)


def event(event_id="event-a", available_at="2020-01-01T20:00:00+00:00"):
    return {
        "event_id": event_id,
        "catalog_version": "fed-policy-v1",
        "event_type": "policy_rate",
        "effective_date": "2020-01-02",
        "available_at": available_at,
        "source_url": OFFICIAL_URL,
        "source_title": "Official event",
        "source_published_at": available_at,
        "payload_json": "{}",
    }


def period():
    return {
        "period_id": "complete",
        "catalog_version": "fed-policy-v1",
        "label_zh": "完整时期",
        "label_en": "Complete period",
        "start_date": "2020-01-02",
        "end_date": "2020-03-31",
        "available_at": "2020-03-31T20:00:00+00:00",
        "interpretation_zh": "历史描述。",
        "interpretation_en": "Historical description.",
        "source_event_ids_json": '["event-a"]',
    }


def histories():
    dates = pd.to_datetime(
        ("2020-01-02", "2020-02-03", "2020-03-31")
    )
    return {
        "SPY": pd.DataFrame(
            {"Close": [100.0, 102.0, 105.0]},
            index=dates,
        ),
        "XLK": pd.DataFrame(
            {"Close": [100.0, 105.0, 110.0]},
            index=dates,
        ),
    }


class PolicyPeriodMatrixServiceTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "policy.db"
        self.store = PolicyEventStore(self.database)
        self.store.initialize()
        self.store.upsert_catalog([event()], [period()])

    def tearDown(self):
        self.directory.cleanup()

    def test_build_reads_visible_catalog_and_returns_fresh_copy(self):
        service = PolicyPeriodMatrixService(
            self.database,
            max_cache_size=4,
        )

        first = service.build("2026-07-29", histories())
        first["rows"][0]["status"] = "tampered"
        second = service.build("2026-07-29", histories())

        self.assertEqual(
            second["artifact_key"],
            "policy_period_matrix_v1",
        )
        self.assertNotEqual(second["rows"][0]["status"], "tampered")
        self.assertEqual(second["periods"][0]["events"][0]["event_id"], "event-a")

    def test_cache_token_changes_when_policy_database_changes(self):
        service = PolicyPeriodMatrixService(self.database)
        before = service.cache_token()

        self.store.upsert_events(
            [event("event-b", "2020-01-02T20:00:00+00:00")]
        )

        self.assertNotEqual(service.cache_token(), before)

    def test_missing_database_is_not_created(self):
        missing = Path(self.directory.name) / "missing.db"

        payload = PolicyPeriodMatrixService(missing).build(
            "2026-07-29",
            histories(),
        )

        self.assertEqual(
            payload["unavailable_reason"],
            "policy_catalog_unavailable",
        )
        self.assertEqual(payload["rows"], [])
        self.assertFalse(missing.exists())

    def test_cache_size_must_be_positive_integer(self):
        for value in (0, -1, 1.5, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    PolicyPeriodMatrixService(
                        self.database,
                        max_cache_size=value,
                    )


if __name__ == "__main__":
    unittest.main()
