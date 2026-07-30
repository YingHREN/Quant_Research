from pathlib import Path
import json
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.run_point_in_time_sector_recovery import (
    apply_fold_availability_gate,
    attach_recovered_features_to_pairs,
    evaluate_sector_recovery,
    publish_recovery_reports,
)


FEATURES = (
    "pit_sector_relative_strength_20",
    "pit_stock_sector_relative_strength_20",
    "pit_sector_assignment_age_days",
    "pit_sector_residual_correlation",
)


def _pairs(count=20):
    rows = []
    for index in range(count):
        fold = index % 5 + 1
        date = pd.Timestamp("2026-01-02") + pd.Timedelta(days=index)
        rows.append(
            {
                "pair_id": index + 1,
                "case_key": f"C{index}|{date.date().isoformat()}",
                "control_key": f"D{index}|{date.date().isoformat()}",
                "case_ticker": f"C{index}",
                "case_observation_date": date,
                "control_ticker": f"D{index}",
                "control_observation_date": date,
                "fold": fold,
                "group": "technology" if index % 2 else "other",
                "regime": "uptrend",
            }
        )
    return pd.DataFrame(rows)


def _features_for(pairs):
    rows = []
    index = []
    for row in pairs.itertuples(index=False):
        for side, value in (("case", 1.0), ("control", 0.0)):
            ticker = getattr(row, f"{side}_ticker")
            date = getattr(row, f"{side}_observation_date")
            index.append((ticker, date))
            rows.append(
                {
                    "pit_sector_relative_strength_20": value,
                    "pit_stock_sector_relative_strength_20": value,
                    "pit_sector_assignment_age_days": 20.0,
                    "pit_sector_residual_correlation": 0.7,
                    "pit_sector_assignment_available": True,
                    "pit_sector_key": "technology",
                    "pit_sector_benchmark": "XLK",
                    "pit_sector_unavailable_reason": "",
                }
            )
    return pd.DataFrame(
        rows,
        index=pd.MultiIndex.from_tuples(
            index,
            names=["ticker", "observation_date"],
        ),
    )


class PointInTimeSectorRecoveryTest(unittest.TestCase):
    def test_recovery_reuses_frozen_pairs_without_rematching(self):
        pairs = _pairs()

        enriched = attach_recovered_features_to_pairs(
            pairs,
            _features_for(pairs),
        )

        self.assertEqual(
            enriched[["case_key", "control_key"]]
            .to_records(index=False)
            .tolist(),
            pairs[["case_key", "control_key"]]
            .to_records(index=False)
            .tolist(),
        )
        self.assertIn(
            "case_pit_sector_relative_strength_20",
            enriched,
        )

    def test_duplicate_or_missing_frozen_keys_fail_closed(self):
        pairs = _pairs()
        duplicate = pd.concat((pairs, pairs.iloc[[0]]), ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            attach_recovered_features_to_pairs(
                duplicate,
                _features_for(pairs),
            )

        missing = _features_for(pairs).iloc[1:]
        with self.assertRaisesRegex(ValueError, "missing"):
            attach_recovered_features_to_pairs(pairs, missing)

    def test_fold_availability_gate_rejects_below_85_percent(self):
        evidence = pd.DataFrame(
            {
                "feature": [
                    "pit_sector_relative_strength_20",
                    "pit_stock_sector_relative_strength_20",
                ],
                "gate_passed": [True, True],
                "gate_reasons": [(), ()],
            }
        )
        coverage = pd.DataFrame(
            [
                {
                    "feature": feature,
                    "fold": fold,
                    "pair_availability": (
                        0.8499
                        if feature == "pit_sector_relative_strength_20"
                        and fold == 3
                        else 0.85
                    ),
                }
                for feature in evidence["feature"]
                for fold in range(1, 6)
            ]
        )

        checked = apply_fold_availability_gate(evidence, coverage)

        rejected = checked.set_index("feature").loc[
            "pit_sector_relative_strength_20"
        ]
        accepted = checked.set_index("feature").loc[
            "pit_stock_sector_relative_strength_20"
        ]
        self.assertFalse(rejected["final_gate_passed"])
        self.assertIn(
            "fold_pair_availability_below_gate",
            rejected["final_gate_reasons"],
        )
        self.assertTrue(accepted["final_gate_passed"])

    def test_evaluation_is_research_only_and_age_is_diagnostic_only(self):
        pairs = _pairs(count=100)
        features = _features_for(pairs)

        evidence, coverage, manifest = evaluate_sector_recovery(
            pairs,
            features,
            bootstrap_samples=100,
            bootstrap_block_days=2,
            seed=7,
        )

        self.assertFalse(evidence.empty)
        self.assertEqual(set(coverage["fold"]), {1, 2, 3, 4, 5})
        self.assertEqual(manifest["decision"]["online_authority"], "none")
        self.assertEqual(
            {
                row["reason"]
                for row in manifest["unavailable_reasons"]
            },
            {"available"},
        )
        self.assertNotIn(
            "pit_sector_assignment_age_days",
            manifest["decision"]["admitted_features"],
        )
        self.assertNotIn(
            "pit_sector_residual_correlation",
            manifest["decision"]["admitted_features"],
        )

    def test_publication_is_atomic_and_strict(self):
        pairs = _pairs(count=100)
        features = _features_for(pairs)
        evidence, coverage, manifest = evaluate_sector_recovery(
            pairs,
            features,
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
                "pair_cohort_fingerprint": "c" * 64,
            }
        )
        assignments = pd.DataFrame(
            {
                "ticker": ["AAA"],
                "classification_date": ["2026-01-30"],
                "effective_from": ["2026-02-02"],
                "expires_after": ["2026-03-16"],
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            paths = publish_recovery_reports(
                Path(directory) / "recovery",
                assignments,
                coverage,
                evidence,
                manifest,
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
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])
            self.assertTrue(paths["assignments_csv"].exists())
            self.assertTrue(paths["coverage_csv"].exists())
            self.assertTrue(paths["features_csv"].exists())
            self.assertTrue(paths["md"].exists())


if __name__ == "__main__":
    unittest.main()
