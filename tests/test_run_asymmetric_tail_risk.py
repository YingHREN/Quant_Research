from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

import research.run_asymmetric_tail_risk as tail_runner
from research.run_asymmetric_tail_risk import (
    publish_tail_reports,
    render_tail_report,
    validate_tail_report_payload,
)


class AsymmetricTailRunnerTest(unittest.TestCase):
    def test_run_study_delegates_reusable_dataset_construction(self):
        class DatasetSentinel(RuntimeError):
            pass

        with mock.patch.object(
            tail_runner,
            "build_tail_study_dataset",
            side_effect=DatasetSentinel("dataset-built"),
        ) as builder:
            with self.assertRaisesRegex(DatasetSentinel, "dataset-built"):
                tail_runner.run_study(
                    database="research.db",
                    start_date="2020-01-01",
                    max_tickers=12,
                    seed=7,
                    minimum_samples=20,
                )

        builder.assert_called_once_with(
            database="research.db",
            start_date="2020-01-01",
            max_tickers=12,
            seed=7,
            minimum_samples=20,
        )

    @staticmethod
    def _metrics():
        return pd.DataFrame(
            [
                {
                    "sample_mode": "non_overlapping",
                    "scope_type": "overall",
                    "scope_name": "all",
                    "fold": None,
                    "row_count": 100,
                    "risk_count": 10,
                    "coverage": 0.10,
                    "down_precision": 0.60,
                    "down_recall": 0.30,
                    "baseline_down_precision": 0.50,
                    "down_precision_gain": 0.10,
                    "mean_terminal_return": -0.02,
                    "risk_rebound_rate": 0.02,
                    "all_rebound_rate": 0.05,
                }
            ]
        )

    @staticmethod
    def _manifest():
        return {
            "study_version": "asymmetric_tail_risk_v1",
            "source_commit": "abc123",
            "dirty_worktree": False,
            "database": "research_prices.db",
            "database_content_fingerprint": "f" * 64,
            "model": {
                "lifecycle": "research",
                "online_authority": "none",
            },
            "decision": {
                "promoted": False,
                "status": "rejected",
                "reasons": ["economic_return_gate_failed"],
                "online_authority": "none",
            },
            "causal_audit": {
                "passed": True,
                "outer_training_labels_end_before_test_start": True,
            },
            "nested_fold_evidence": [
                {
                    "fold": 1,
                    "boundary_status": "unavailable",
                    "boundary_candidates": [
                        {
                            "down_threshold": 0.4,
                            "rebound_cap": 0.2,
                            "risk_count": 12,
                            "coverage": 0.02,
                            "down_precision": 0.6,
                            "mean_terminal_return": 0.01,
                            "reasons": [
                                "insufficient_risk_coverage",
                                "non_negative_risk_return",
                            ],
                        }
                    ],
                }
            ],
            "metrics": [],
            "counterexamples": [],
        }

    def test_publication_is_atomic_strict_and_research_only(self):
        metrics = self._metrics()
        manifest = self._manifest()
        report = render_tail_report(metrics, manifest)

        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "tail-report"
            paths = publish_tail_reports(
                prefix,
                metrics,
                pd.DataFrame(),
                manifest,
                report,
            )

            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(payload["model"]["lifecycle"], "research")
            self.assertEqual(payload["model"]["online_authority"], "none")
            self.assertFalse(payload["decision"]["promoted"])
            self.assertTrue(paths["csv"].exists())
            self.assertTrue(paths["counterexamples_csv"].exists())
            self.assertTrue(paths["md"].exists())
            self.assertEqual(
                list(Path(directory).glob("*.tmp")),
                [],
            )

    def test_payload_rejects_secrets_nonfinite_and_absolute_paths(self):
        unsafe_values = (
            {"value": np.nan},
            {"api_key": "not-allowed"},
            {"value": "/private/tmp/hidden.db"},
            {"value": "secret=abcdefghijklmnop"},
        )

        for payload in unsafe_values:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_tail_report_payload(payload)

    def test_report_states_raw_return_and_no_online_authority(self):
        report = render_tail_report(self._metrics(), self._manifest())

        self.assertIn("未截尾", report)
        self.assertIn("online_authority=none", report)
        self.assertIn("不修改 Ridge", report)
        self.assertIn("训练内候选边界", report)
        self.assertIn("insufficient_risk_coverage", report)


if __name__ == "__main__":
    unittest.main()
