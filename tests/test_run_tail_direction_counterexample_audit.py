from pathlib import Path
import json
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.run_tail_direction_counterexample_audit import (
    publish_audit_reports,
    render_audit_report,
    run_audit_from_dataset,
    validate_audit_report_payload,
)
from research.tail_direction_counterexample_audit import AUDIT_FEATURE_TYPES
from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS


def _dataset():
    dates = pd.to_datetime(
        ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
    )
    predictions = pd.DataFrame(
        {
            "ticker": ["UP1", "DN1", "UP2", "DN2"],
            "observation_date": dates,
            "fold": [1, 1, 2, 2],
            "group": ["software", "software", "other", "other"],
            "regime": ["uptrend"] * 4,
            "calibrated_down_probability": [0.50] * 4,
            "actual_terminal_return": [0.12, -0.07, 0.15, -0.08],
            "actual_path_mae": [-0.02, -0.09, -0.03, -0.10],
        }
    )
    index = pd.MultiIndex.from_arrays(
        (predictions["ticker"], dates),
        names=("ticker", "observation_date"),
    )
    features = pd.DataFrame(
        {
            feature: (
                [1.0, 0.0, 2.0, 0.0]
                if AUDIT_FEATURE_TYPES[feature] == "numeric"
                else [1, 0, 1, 0]
            )
            for feature in RIDGE_V4_FEATURE_COLUMNS
        },
        index=index,
    )
    histories = {}
    for ticker, date in zip(predictions["ticker"], dates):
        history_dates = pd.date_range(
            date - pd.Timedelta(days=120),
            date,
            freq="B",
        )
        close = pd.Series(
            np.linspace(90.0, 110.0, len(history_dates)),
            index=history_dates,
        )
        histories[ticker] = pd.DataFrame(
            {
                "Open": close.shift(1).fillna(close.iloc[0]),
                "High": close * 1.01,
                "Low": close * 0.99,
                "Close": close,
                "Volume": 1_000_000.0,
            },
            index=history_dates,
        )
    return {
        "predictions": predictions,
        "feature_frame": features,
        "histories": histories,
        "groups": {
            "UP1": "software",
            "DN1": "software",
            "UP2": "other",
            "DN2": "other",
        },
        "analysis_tickers": ("DN1", "DN2", "UP1", "UP2"),
        "target_frame": features,
        "nested_fold_evidence": (),
        "metadata": {"latest_date": "2026-01-07"},
    }


class TailDirectionAuditRunnerTest(unittest.TestCase):
    def test_runner_is_research_only_and_preserves_unavailable_states(self):
        pairs, coverage, evidence, manifest = run_audit_from_dataset(
            _dataset(),
            bootstrap_samples=100,
            bootstrap_block_days=2,
            seed=7,
        )

        self.assertEqual(len(pairs), 2)
        self.assertFalse(coverage.empty)
        self.assertFalse(evidence.empty)
        self.assertEqual(
            manifest["decision"]["online_authority"],
            "none",
        )
        self.assertEqual(
            manifest["decision"]["authorized_consumers"],
            ["offline_research_only"],
        )
        self.assertEqual(
            manifest["data_availability"]["earnings_proximity"],
            "unavailable",
        )
        self.assertEqual(
            manifest["data_availability"]["market_cap"],
            "unavailable",
        )
        self.assertEqual(
            manifest["decision"]["status"],
            "no_features_admitted",
        )

    def test_publication_is_atomic_strict_and_secret_free(self):
        pairs, coverage, evidence, manifest = run_audit_from_dataset(
            _dataset(),
            bootstrap_samples=100,
            bootstrap_block_days=2,
            seed=7,
        )
        manifest.update(
            {
                "source_commit": "a" * 40,
                "dirty_worktree": False,
                "database": "research_prices.db",
                "database_content_fingerprint": "b" * 64,
            }
        )
        report = render_audit_report(evidence, coverage, manifest)

        with tempfile.TemporaryDirectory() as directory:
            paths = publish_audit_reports(
                Path(directory) / "tail-audit",
                pairs,
                coverage,
                evidence,
                manifest,
                report,
            )

            payload = json.loads(
                paths["json"].read_text(encoding="utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(value)
                ),
            )
            self.assertEqual(
                payload["decision"]["online_authority"],
                "none",
            )
            self.assertTrue(paths["pairs_csv"].exists())
            self.assertTrue(paths["coverage_csv"].exists())
            self.assertTrue(paths["features_csv"].exists())
            self.assertTrue(paths["md"].exists())
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_payload_rejects_nonfinite_secret_and_absolute_path(self):
        for payload in (
            {"value": np.nan},
            {"api_key": "not-allowed"},
            {"value": "/Users/private/research.db"},
            {"value": "secret=abcdefghijklmnop"},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    validate_audit_report_payload(payload)

    def test_report_explains_no_features_and_missing_data(self):
        pairs, coverage, evidence, manifest = run_audit_from_dataset(
            _dataset(),
            bootstrap_samples=100,
            bootstrap_block_days=2,
            seed=7,
        )

        report = render_audit_report(evidence, coverage, manifest)

        self.assertIn("没有特征通过", report)
        self.assertIn("财报临近", report)
        self.assertIn("真实市值", report)
        self.assertIn("online_authority=none", report)


if __name__ == "__main__":
    unittest.main()
