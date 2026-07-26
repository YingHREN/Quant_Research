from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import pandas as pd

import manage_analysis_cache
from web.services.entry_signals import EntrySignalArtifactStore
from web.services.forecast_artifacts import (
    ForecastArtifact,
    ForecastArtifactIdentity,
    ForecastArtifactStore,
)


def _forecast_identity(version):
    return ForecastArtifactIdentity(
        model_key="ridge_direction_v1",
        model_version="v4",
        feature_version=version,
        risk_context_version="risk-v1",
    )


def _forecast_artifact(value):
    index = pd.MultiIndex.from_tuples(
        [("AAA", pd.Timestamp("2026-07-24"))],
        names=["ticker", "observation_date"],
    )
    frame = pd.DataFrame({"feature": [value]}, index=index)
    return ForecastArtifact(
        frame=frame,
        risk_context=frame.copy(),
        evaluations={"5": {"sample_count": 1}},
        coverage={"AAA": (1, pd.Timestamp("2026-07-24"))},
        fingerprints={"AAA": b"fingerprint"},
    )


class ManageAnalysisCacheTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "analysis_cache.db"
        forecast = ForecastArtifactStore(self.path, max_entries=10)
        forecast.save(
            _forecast_identity("features-v1"),
            "market-a",
            _forecast_artifact(1.0),
        )
        forecast.save(
            _forecast_identity("features-v2"),
            "market-a",
            _forecast_artifact(2.0),
        )
        entries = EntrySignalArtifactStore(self.path, max_entries=10)
        entries.save("AAA", "fingerprint-a", [{"time": "2026-07-24"}])
        entries.save("BBB", "fingerprint-b", [{"time": "2026-07-24"}])

    def _run(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = manage_analysis_cache.main(
                ["--cache", str(self.path), *args]
            )
        return result, json.loads(output.getvalue())

    def test_status_reports_both_tables_and_disk_usage(self):
        result, payload = self._run("status")

        self.assertEqual(result, 0)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(
            payload["tables"]["forecast_artifacts"]["entry_count"],
            2,
        )
        self.assertEqual(
            payload["tables"]["entry_signal_artifacts"]["entry_count"],
            2,
        )
        self.assertGreater(payload["disk_usage_bytes"], 0)
        self.assertGreater(payload["payload_bytes"], 0)

    def test_verify_reports_checksum_corruption_without_deleting(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                UPDATE entry_signal_artifacts
                SET payload_checksum = 'broken'
                WHERE ticker = 'AAA'
                """
            )

        result, payload = self._run("verify")

        self.assertEqual(result, 2)
        self.assertEqual(payload["status"], "corrupt")
        self.assertEqual(payload["checked_entries"], 4)
        self.assertEqual(payload["invalid_count"], 1)
        self.assertEqual(
            payload["invalid_entries"][0]["table"],
            "entry_signal_artifacts",
        )
        with sqlite3.connect(self.path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM entry_signal_artifacts"
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_prune_is_preview_only_until_apply_is_explicit(self):
        result, preview = self._run(
            "prune",
            "--forecast-keep",
            "1",
            "--entry-keep",
            "1",
        )

        self.assertEqual(result, 0)
        self.assertEqual(preview["status"], "dry_run")
        self.assertEqual(preview["would_remove"]["forecast_artifacts"], 1)
        self.assertEqual(preview["would_remove"]["entry_signal_artifacts"], 1)

        result, applied = self._run(
            "prune",
            "--forecast-keep",
            "1",
            "--entry-keep",
            "1",
            "--apply",
        )

        self.assertEqual(result, 0)
        self.assertEqual(applied["status"], "pruned")
        self.assertEqual(applied["removed"], preview["would_remove"])
        self.assertEqual(applied["remaining"]["forecast_artifacts"], 1)
        self.assertEqual(applied["remaining"]["entry_signal_artifacts"], 1)

    def test_prewarm_reuses_existing_forecast_builder(self):
        with mock.patch.object(
            manage_analysis_cache.build_forecast_cache,
            "main",
            return_value=0,
        ) as build:
            result = manage_analysis_cache.main(
                [
                    "--cache",
                    str(self.path),
                    "prewarm",
                    "--database",
                    "prices.db",
                ]
            )

        self.assertEqual(result, 0)
        build.assert_called_once_with(
            ["--database", "prices.db", "--cache", str(self.path)]
        )


if __name__ == "__main__":
    unittest.main()
