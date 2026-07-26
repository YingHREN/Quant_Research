import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import pandas as pd

from web.services.forecast_artifacts import (
    ForecastArtifact,
    ForecastArtifactIdentity,
    ForecastArtifactStore,
)


def _frame(value=1.0):
    index = pd.MultiIndex.from_tuples(
        [("AAA", pd.Timestamp("2026-07-21"))],
        names=["ticker", "observation_date"],
    )
    return pd.DataFrame({"feature": [value]}, index=index)


def _artifact(value=1.0):
    return ForecastArtifact(
        frame=_frame(value),
        risk_context=_frame(value + 1.0),
        evaluations={"5": {"sample_count": 10}},
        coverage={"AAA": (1, pd.Timestamp("2026-07-21"))},
        fingerprints={"AAA": b"fingerprint"},
    )


def _identity(feature_version="features-v1"):
    return ForecastArtifactIdentity(
        model_key="ridge_direction_v1",
        model_version="v4",
        feature_version=feature_version,
        risk_context_version="risk-v1",
    )


class ForecastArtifactStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "analysis_cache.db"

    def test_round_trip_is_versioned_bounded_and_returns_copies(self):
        store = ForecastArtifactStore(self.path, max_entries=2)
        artifact = _artifact()

        self.assertTrue(store.save(_identity(), "market-a", artifact))
        restored = store.load(_identity(), "market-a")

        pd.testing.assert_frame_equal(restored.frame, artifact.frame)
        pd.testing.assert_frame_equal(
            restored.risk_context,
            artifact.risk_context,
        )
        self.assertEqual(restored.evaluations, artifact.evaluations)
        restored.frame.iloc[0, 0] = 999.0
        self.assertEqual(
            store.load(_identity(), "market-a").frame.iloc[0, 0],
            1.0,
        )
        self.assertIsNone(store.load(_identity(), "market-b"))

        store.save(_identity("features-v2"), "market-a", _artifact(2.0))
        store.save(_identity("features-v3"), "market-a", _artifact(3.0))

        self.assertEqual(store.entry_count(), 2)
        with sqlite3.connect(self.path) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(forecast_artifacts)"
                )
            }
        self.assertTrue(
            {
                "cache_key",
                "market_signature",
                "model_key",
                "model_version",
                "feature_version",
                "risk_context_version",
                "format_version",
                "payload_codec",
                "payload_checksum",
                "payload",
            }.issubset(columns)
        )

    def test_corrupt_checksum_payload_and_codec_are_safe_misses(self):
        mutations = (
            ("payload_checksum", "broken"),
            ("payload", sqlite3.Binary(b"truncated")),
            ("payload_codec", "unknown"),
        )
        for column, value in mutations:
            with self.subTest(column=column):
                path = Path(self.temporary.name) / f"{column}.db"
                store = ForecastArtifactStore(path)
                self.assertTrue(store.save(_identity(), "market-a", _artifact()))
                with sqlite3.connect(path) as connection:
                    connection.execute(
                        f"UPDATE forecast_artifacts SET {column} = ?",
                        (value,),
                    )
                self.assertIsNone(store.load(_identity(), "market-a"))

    def test_write_failure_returns_false_and_preserves_existing_entry(self):
        store = ForecastArtifactStore(self.path)
        self.assertTrue(store.save(_identity(), "market-a", _artifact()))

        with mock.patch(
            "web.services.forecast_artifacts.sqlite3.connect",
            side_effect=sqlite3.OperationalError("read only"),
        ):
            self.assertFalse(
                store.save(_identity("features-v2"), "market-a", _artifact(2.0))
            )

        self.assertIsNotNone(store.load(_identity(), "market-a"))

    def test_invalid_capacity_is_rejected(self):
        for value in (True, 0, -1, 1.5):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    ForecastArtifactStore(self.path, max_entries=value)


if __name__ == "__main__":
    unittest.main()
