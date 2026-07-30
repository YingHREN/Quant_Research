from pathlib import Path
import json
import tempfile
import unittest

import pandas as pd

from research.run_asymmetric_tail_counterexample_audit import (
    build_audit_manifest,
    publish_audit_reports,
    render_audit_report,
    validate_source_identity,
)


def _source_manifest():
    return {
        "study_version": "asymmetric_tail_risk_v1",
        "database": "research_prices.db",
        "database_content_fingerprint": "f" * 64,
        "source_commit": "abc123",
        "start_date": "2018-01-01",
        "ticker_count": 240,
        "configuration": {
            "cohort_seed": 20260726,
            "maximum_tickers": 240,
        },
        "model": {
            "lifecycle": "research",
            "online_authority": "none",
        },
        "counterexamples": [
            {
                "ticker": "AAA",
                "observation_date": "2026-01-02T00:00:00",
                "calibrated_down_probability": 0.5,
                "actual_terminal_return": 0.2,
            }
        ],
    }


def _audit_rows():
    return pd.DataFrame(
        {
            "ticker": ["AAA"],
            "observation_date": [pd.Timestamp("2026-01-02")],
            "group": ["software"],
            "regime": ["uptrend"],
            "calibrated_down_probability": [0.5],
            "calibrated_rebound_probability": [0.2],
            "actual_terminal_return": [0.2],
            "actual_path_mae": [-0.03],
            "opening_gap": [0.01],
            "dollar_volume": [50_000_000.0],
            "price": [20.0],
            "atr20_pct": [0.04],
            "realized_volatility": [0.7],
            "atr20_percentile": [0.8],
            "realized_volatility_percentile": [0.9],
            "opening_gap_band": ["within_3pct"],
            "realized_volatility_band": ["high_25pct"],
            "atr20_band": ["high_25pct"],
            "price_band": ["20_to_100"],
            "dollar_volume_band": ["10m_to_100m"],
            "earnings_proximity_status": ["unavailable"],
        }
    )


def _summary():
    return pd.DataFrame(
        {
            "dimension": ["overall", "group"],
            "stratum": ["all", "software"],
            "row_count": [1, 1],
            "share": [1.0, 1.0],
            "mean_terminal_return": [0.2, 0.2],
            "median_terminal_return": [0.2, 0.2],
            "median_path_mae": [-0.03, -0.03],
            "median_down_probability": [0.5, 0.5],
            "median_rebound_probability": [0.2, 0.2],
        }
    )


class AsymmetricTailCounterexampleAuditRunnerTest(unittest.TestCase):
    def test_source_identity_requires_exact_published_sample(self):
        source = _source_manifest()
        rows = _audit_rows().loc[
            :,
            [
                "ticker",
                "observation_date",
                "calibrated_down_probability",
                "actual_terminal_return",
            ],
        ]
        validate_source_identity(source, rows)

        changed = rows.copy()
        changed.loc[0, "ticker"] = "BBB"
        with self.assertRaisesRegex(ValueError, "identity"):
            validate_source_identity(source, changed)

    def test_source_identity_rejects_tampered_audit_context(self):
        source = _source_manifest()
        source["counterexamples"][0].update(
            {
                "group": "software",
                "opening_gap": 0.01,
                "dollar_volume": 50_000_000.0,
            }
        )
        rows = pd.DataFrame(source["counterexamples"])

        for column, value in (
            ("group", "semiconductor"),
            ("opening_gap", 0.02),
            ("dollar_volume", 60_000_000.0),
        ):
            with self.subTest(column=column):
                changed = rows.copy()
                changed.loc[0, column] = value
                with self.assertRaisesRegex(ValueError, "identity"):
                    validate_source_identity(source, changed)

    def test_source_identity_rejects_unexpected_csv_columns(self):
        source = _source_manifest()
        rows = pd.DataFrame(source["counterexamples"])
        rows["secret_token"] = "/Users/example/private.key"

        with self.assertRaisesRegex(ValueError, "schema"):
            validate_source_identity(source, rows)

    def test_manifest_preserves_sample_and_keeps_research_only_authority(self):
        manifest = build_audit_manifest(
            _source_manifest(),
            _audit_rows(),
            _summary(),
            source_counterexamples_file="asymmetric-tail-risk-counterexamples.csv",
        )

        self.assertEqual(manifest["source_sample_count"], 1)
        self.assertEqual(manifest["audit_row_count"], 1)
        self.assertEqual(
            manifest["data_availability"]["earnings_proximity_available"],
            0,
        )
        self.assertEqual(manifest["model"]["lifecycle"], "research")
        self.assertEqual(manifest["model"]["online_authority"], "none")
        self.assertTrue(
            all(
                hypothesis["online_authority"] == "none"
                for hypothesis in manifest["preregistered_feature_hypotheses"]
            )
        )
        json.dumps(manifest, allow_nan=False)

    def test_manifest_validates_preserved_published_group_not_audit_group(self):
        source = _source_manifest()
        source["counterexamples"][0]["group"] = "software"
        rows = _audit_rows()
        rows["published_group"] = "software"
        rows["group"] = "unavailable"

        manifest = build_audit_manifest(
            source,
            rows,
            _summary(),
            source_counterexamples_file="asymmetric-tail-risk-counterexamples.csv",
        )

        self.assertEqual(manifest["audit_row_count"], 1)

    def test_publication_is_atomic_and_report_states_limitations(self):
        rows = _audit_rows()
        summary = _summary()
        manifest = build_audit_manifest(
            _source_manifest(),
            rows,
            summary,
            source_counterexamples_file="asymmetric-tail-risk-counterexamples.csv",
        )
        report = render_audit_report(summary, manifest)

        with tempfile.TemporaryDirectory() as directory:
            paths = publish_audit_reports(
                Path(directory) / "audit",
                rows,
                manifest,
                report,
            )

            self.assertTrue(paths["json"].exists())
            self.assertTrue(paths["csv"].exists())
            self.assertTrue(paths["md"].exists())
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(
                payload["model"]["online_authority"],
                "none",
            )
            self.assertIn("财报邻近度不可用", report)
            self.assertIn("不修改模型", report)


if __name__ == "__main__":
    unittest.main()
