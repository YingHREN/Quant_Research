import hashlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import pandas as pd
from pandas.testing import assert_frame_equal

from research.unified_benchmark_cache import (
    BenchmarkCacheArtifact,
    BenchmarkCacheIdentity,
    UnifiedBenchmarkCacheStore,
)


def identity(stage="statistical_predictions", marker="a"):
    return BenchmarkCacheIdentity(
        study_version="unified-downside-walkforward-v2",
        stage=stage,
        database_fingerprint=marker * 64,
        assignment_fingerprint="b" * 64,
        config_fingerprint="c" * 64,
        code_fingerprint="d" * 64,
        dependency_artifact_key=(
            "e" * 64 if stage == "rule_predictions" else None
        ),
        schema_version="unified-benchmark-cache-v1",
    )


def frame(score=0.7):
    return pd.DataFrame(
        {
            "ticker": ["AAA"],
            "observation_date": pd.to_datetime(["2026-01-02"]),
            "horizon": pd.Series([5], dtype="int64"),
            "predicted_score": [score],
        }
    )


class UnifiedBenchmarkCacheStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "cache.db"
        self.store = UnifiedBenchmarkCacheStore(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_identity_is_canonical_and_strictly_validated(self):
        first = identity()
        second = identity()

        self.assertEqual(first.artifact_key, second.artifact_key)
        self.assertEqual(len(first.artifact_key), 64)
        invalid_cases = [
            ({"stage": "labels"}, "stage"),
            ({"database_fingerprint": "short"}, "fingerprint"),
            ({"dependency_artifact_key": "f" * 64}, "forbids"),
            (
                {
                    "stage": "rule_predictions",
                    "dependency_artifact_key": None,
                },
                "requires",
            ),
            ({"study_version": " "}, "study_version"),
        ]
        for changes, message in invalid_cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, message):
                    replace(first, **changes)

    def test_commit_is_atomic_idempotent_and_rejects_conflicting_payload(self):
        artifact = BenchmarkCacheArtifact.from_frames(
            identity(), {"ridge_down": frame()}
        )
        self.assertEqual(self.store.commit([artifact]), 1)
        self.assertEqual(self.store.commit([artifact]), 0)

        conflicting = BenchmarkCacheArtifact.from_frames(
            identity(), {"ridge_down": frame(score=0.1)}
        )
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.store.commit([conflicting])

        pending = BenchmarkCacheArtifact.from_frames(
            identity(marker="f"), {"ridge_down": frame()}
        )
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.store.commit([pending, conflicting])
        self.assertEqual(self.store.read(pending.identity).status, "miss")

    def test_read_returns_hit_miss_or_corrupt_without_mutating_database(self):
        artifact = BenchmarkCacheArtifact.from_frames(
            identity(), {"ridge_down": frame()}
        )
        self.store.commit([artifact])

        hit = self.store.read(artifact.identity)
        self.assertEqual(hit.status, "hit")
        self.assertIsNone(hit.reason)
        assert_frame_equal(hit.frames["ridge_down"], frame())
        self.assertEqual(self.store.read(identity(marker="f")).status, "miss")

        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                UPDATE benchmark_cache_artifacts
                SET payload = ?
                WHERE artifact_key = ?
                """,
                (
                    bytes([artifact.payload[0] ^ 1]) + artifact.payload[1:],
                    artifact.identity.artifact_key,
                ),
            )
        corrupt = self.store.read(artifact.identity)
        self.assertEqual(corrupt.status, "miss_corrupt")
        self.assertIsNone(corrupt.frames)
        self.assertIn("checksum", corrupt.reason)
        self.assertEqual(len(self.store.status()), 1)

    def test_verify_reports_corruption_and_explicit_repair_replaces_only_corrupt(self):
        artifact = BenchmarkCacheArtifact.from_frames(
            identity(), {"ridge_down": frame()}
        )
        self.store.commit([artifact])
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                UPDATE benchmark_cache_artifacts
                SET payload_checksum = ?
                WHERE artifact_key = ?
                """,
                ("0" * 64, artifact.identity.artifact_key),
            )

        verified = self.store.verify()
        self.assertEqual(verified.loc[0, "status"], "corrupt")
        self.assertIn("checksum", verified.loc[0, "reason"])
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.store.commit([artifact])
        self.assertEqual(
            self.store.commit([artifact], repair_corrupt=True),
            1,
        )
        self.assertEqual(self.store.read(artifact.identity).status, "hit")

        conflicting = BenchmarkCacheArtifact.from_frames(
            identity(), {"ridge_down": frame(score=0.2)}
        )
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.store.commit([conflicting], repair_corrupt=True)

    def test_prune_is_preview_only_until_apply(self):
        artifacts = [
            BenchmarkCacheArtifact.from_frames(
                identity(marker=marker), {"ridge_down": frame(score=score)}
            )
            for marker, score in [("a", 0.1), ("f", 0.2), ("9", 0.3)]
        ]
        self.assertEqual(self.store.commit(artifacts), 3)

        preview = self.store.prune(keep_per_stage=1)
        self.assertEqual(int(preview["would_delete"].sum()), 2)
        self.assertEqual(int(preview["deleted"].sum()), 0)
        self.assertEqual(len(self.store.status()), 3)

        applied = self.store.prune(keep_per_stage=1, apply=True)
        self.assertEqual(int(applied["deleted"].sum()), 2)
        self.assertEqual(len(self.store.status()), 1)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            self.store.prune(keep_per_stage=0)

    def test_artifact_validation_rejects_tampered_metadata(self):
        artifact = BenchmarkCacheArtifact.from_frames(
            identity(), {"ridge_down": frame()}
        )
        invalid = [
            replace(artifact, payload_checksum="0" * 64),
            replace(artifact, payload_size_bytes=artifact.payload_size_bytes + 1),
            replace(artifact, row_count=artifact.row_count + 1),
            replace(artifact, payload_codec="pickle"),
        ]
        for candidate in invalid:
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(ValueError, "artifact"):
                    self.store.commit([candidate])


if __name__ == "__main__":
    unittest.main()
