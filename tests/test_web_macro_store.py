import sqlite3
import tempfile
import unittest
from pathlib import Path

from web.services.macro_store import MacroObservationStore


def observation(**overrides):
    row = {
        "series_id": "CPIAUCSL",
        "observation_date": "2026-06-01",
        "available_at": "2026-07-14T12:30:00+00:00",
        "value": 321.5,
        "realtime_start": "2026-07-14",
        "realtime_end": "9999-12-31",
        "source": "ALFRED_initial_release",
        "revision_policy": "initial_release_only",
    }
    row.update(overrides)
    return row


class MacroObservationRevisionPolicyTest(unittest.TestCase):
    def test_initialize_migrates_legacy_table_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "macro.db"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE macro_observations (
                        series_id TEXT NOT NULL,
                        observation_date TEXT NOT NULL,
                        available_at TEXT NOT NULL,
                        value REAL NOT NULL,
                        realtime_start TEXT NOT NULL,
                        realtime_end TEXT NOT NULL,
                        source TEXT NOT NULL,
                        PRIMARY KEY (
                            series_id,
                            observation_date,
                            realtime_start
                        )
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO macro_observations VALUES (
                        'CPIAUCSL',
                        '2026-06-01',
                        '2026-07-14T12:30:00+00:00',
                        321.5,
                        '2026-07-14',
                        '9999-12-31',
                        'legacy_import'
                    )
                    """
                )

            store = MacroObservationStore(path)
            store.initialize()
            rows = store.load_available("2026-07-15T00:00:00+00:00")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows.iloc[0]["source"], "legacy_import")
            self.assertEqual(
                rows.iloc[0]["revision_policy"],
                "legacy_unspecified",
            )

    def test_upsert_rejects_missing_revision_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MacroObservationStore(Path(directory) / "macro.db")
            store.initialize()
            row = observation()
            row.pop("revision_policy")

            with self.assertRaisesRegex(
                ValueError,
                "revision_policy",
            ):
                store.upsert([row])

    def test_load_available_preserves_revision_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MacroObservationStore(Path(directory) / "macro.db")
            store.initialize()

            store.upsert([observation()])
            rows = store.load_available("2026-07-15T00:00:00+00:00")

            self.assertEqual(
                rows.iloc[0]["revision_policy"],
                "initial_release_only",
            )


if __name__ == "__main__":
    unittest.main()
