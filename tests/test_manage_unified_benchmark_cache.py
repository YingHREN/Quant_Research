import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import manage_unified_benchmark_cache as command
from research.unified_benchmark_cache import (
    BenchmarkCacheArtifact,
    BenchmarkCacheIdentity,
    UnifiedBenchmarkCacheStore,
)


def identity(marker):
    return BenchmarkCacheIdentity(
        study_version="unified-downside-walkforward-v2",
        stage="statistical_predictions",
        database_fingerprint=marker * 64,
        assignment_fingerprint="b" * 64,
        config_fingerprint="c" * 64,
        code_fingerprint="d" * 64,
        dependency_artifact_key=None,
        schema_version="unified-benchmark-cache-v1",
    )


def frame(score):
    return pd.DataFrame(
        {
            "ticker": ["AAA"],
            "observation_date": pd.to_datetime(["2026-01-02"]),
            "horizon": pd.Series([5], dtype="int64"),
            "fold": pd.Series([1], dtype="int64"),
            "predicted_event": pd.Series([True], dtype="boolean"),
            "predicted_score": [score],
            "model_version": ["ridge_direction_v1"],
        }
    )


def run_cli(*arguments):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exit_code = command.main(list(arguments))
    return exit_code, json.loads(output.getvalue())


class ManageUnifiedBenchmarkCacheTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "cache.db"
        self.store = UnifiedBenchmarkCacheStore(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def add_artifacts(self, count):
        artifacts = [
            BenchmarkCacheArtifact.from_frames(
                identity(marker),
                {"ridge_down": frame(score)},
            )
            for marker, score in zip(
                ("a", "e", "f"),
                (0.1, 0.2, 0.3),
            )
        ][:count]
        self.store.commit(artifacts)

    def test_status_and_verify_emit_stable_json_without_secrets(self):
        self.add_artifacts(1)

        status_code, status = run_cli(
            "status", "--database", str(self.database)
        )
        verify_code, verify = run_cli(
            "verify", "--database", str(self.database)
        )

        self.assertEqual(status_code, 0)
        self.assertEqual(verify_code, 0)
        self.assertTrue(status["ok"])
        self.assertEqual(status["artifact_count"], 1)
        self.assertEqual(status["stage_counts"], {"statistical_predictions": 1})
        self.assertEqual(verify["invalid_count"], 0)
        rendered = json.dumps([status, verify])
        self.assertNotIn(str(self.database), rendered)
        self.assertNotIn("API_KEY", rendered)

    def test_prune_requires_explicit_apply(self):
        self.add_artifacts(3)

        preview_code, preview = run_cli(
            "prune",
            "--database",
            str(self.database),
            "--keep-per-stage",
            "1",
        )
        self.assertEqual(preview_code, 0)
        self.assertEqual(preview["deleted_count"], 0)
        self.assertEqual(preview["would_delete_count"], 2)
        self.assertEqual(len(self.store.status()), 3)

        applied_code, applied = run_cli(
            "prune",
            "--database",
            str(self.database),
            "--keep-per-stage",
            "1",
            "--apply",
        )
        self.assertEqual(applied_code, 0)
        self.assertEqual(applied["deleted_count"], 2)
        self.assertEqual(len(self.store.status()), 1)

    def test_known_failure_returns_redacted_stable_error(self):
        exit_code, payload = run_cli(
            "prune",
            "--database",
            str(self.database),
            "--keep-per-stage",
            "0",
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(
            payload,
            {
                "ok": False,
                "error_code": "benchmark_cache_command_failed",
            },
        )


if __name__ == "__main__":
    unittest.main()
