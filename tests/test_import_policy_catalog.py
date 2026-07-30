import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from import_policy_catalog import DEFAULT_CATALOG, import_catalog
from web.services.policy_event_store import PolicyEventStore


def catalog_payload():
    return {
        "catalog_version": "fed-policy-v1",
        "events": [
            {
                "event_id": "fomc-2020-03-15-rate",
                "event_type": "policy_rate",
                "effective_date": "2020-03-16",
                "available_at": "2020-03-15T21:00:00+00:00",
                "source_url": (
                    "https://www.federalreserve.gov/newsevents/"
                    "pressreleases/monetary20200315a.htm"
                ),
                "source_title": "Federal Reserve issues FOMC statement",
                "source_published_at": "2020-03-15T21:00:00+00:00",
                "payload_json": {
                    "target_lower": 0.0,
                    "target_upper": 0.25,
                },
            }
        ],
        "periods": [
            {
                "period_id": "emergency-easing-2020",
                "label_zh": "紧急宽松",
                "label_en": "Emergency easing",
                "start_date": "2020-03-16",
                "end_date": "2022-03-15",
                "available_at": "2022-03-16T18:00:00+00:00",
                "interpretation_zh": "人工历史描述，不是预测。",
                "interpretation_en": (
                    "Human historical description, not a forecast."
                ),
                "source_event_ids_json": ["fomc-2020-03-15-rate"],
            }
        ],
    }


def write_catalog(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


class ImportPolicyCatalogTest(unittest.TestCase):
    def test_bundled_catalog_imports_as_audited_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            result = import_catalog(
                DEFAULT_CATALOG,
                Path(directory) / "macro.db",
            )

            self.assertEqual(result["catalog_version"], "fed-policy-v1")
            self.assertGreaterEqual(result["events"], 6)
            self.assertGreaterEqual(result["periods"], 3)

    def test_unknown_period_source_rejects_without_writing_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            database = root / "macro.db"
            payload = catalog_payload()
            payload["periods"][0]["source_event_ids_json"] = [
                "missing-event"
            ]
            write_catalog(catalog, payload)

            with self.assertRaisesRegex(ValueError, "unknown event"):
                import_catalog(catalog, database)

            store = PolicyEventStore(database)
            store.initialize()
            rows = store.load_events("2026-01-01T00:00:00+00:00")
            self.assertTrue(rows.empty)

    def test_import_is_idempotent_and_summary_omits_local_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            database = root / "macro.db"
            write_catalog(catalog, catalog_payload())

            first = import_catalog(catalog, database)
            second = import_catalog(catalog, database)
            store = PolicyEventStore(database)

            self.assertEqual(first, {
                "catalog_version": "fed-policy-v1",
                "events": 1,
                "periods": 1,
            })
            self.assertEqual(second, first)
            self.assertNotIn(str(root), json.dumps(first))
            self.assertEqual(
                len(store.load_events("2026-01-01T00:00:00+00:00")),
                1,
            )
            self.assertEqual(
                len(store.load_periods("2026-01-01T00:00:00+00:00")),
                1,
            )

    def test_database_failure_rolls_back_events_and_periods_together(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            database = root / "macro.db"
            write_catalog(catalog, catalog_payload())
            store = PolicyEventStore(database)
            store.initialize()
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TRIGGER reject_policy_period
                    BEFORE INSERT ON policy_periods
                    BEGIN
                        SELECT RAISE(ABORT, 'period rejected');
                    END
                    """
                )

            with self.assertRaisesRegex(sqlite3.IntegrityError, (
                "period rejected"
            )):
                import_catalog(catalog, database)

            self.assertTrue(
                store.load_events("2026-01-01T00:00:00+00:00").empty
            )
            self.assertTrue(
                store.load_periods("2026-01-01T00:00:00+00:00").empty
            )


if __name__ == "__main__":
    unittest.main()
