from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from data.group_assignments import (
    audit_assignments,
    historical_group_assignment_intervals,
    load_group_overrides,
    resolve_group_assignment,
)


class GroupAssignmentTest(unittest.TestCase):
    def test_sndk_override_maps_to_semiconductor_theme(self):
        assignment = resolve_group_assignment(
            "SNDK",
            {
                "sec": {
                    "sector_key": "technology",
                    "industry_code": "3572",
                    "confidence": 0.8,
                },
                "market_behavior": {
                    "sector_key": "technology",
                    "benchmark_ticker": "XLK",
                    "confidence": 0.82,
                },
            },
            "2026-07-24",
        )

        self.assertEqual(assignment.sector_key, "technology")
        self.assertEqual(assignment.sector_benchmark, "XLK")
        self.assertEqual(assignment.theme_keys, ("semiconductor",))
        self.assertEqual(assignment.theme_benchmarks, {"semiconductor": ("SOXX", "SMH")})
        self.assertEqual(assignment.primary_model_group, "semiconductor")
        self.assertEqual(assignment.source, "override")

    def test_unknown_security_is_explicitly_queued_for_review(self):
        assignment = resolve_group_assignment("ZZZZ", {}, "2026-07-24")

        self.assertEqual(assignment.sector_key, "unclassified_review")
        self.assertIsNone(assignment.sector_benchmark)
        self.assertEqual(assignment.classification_state, "needs_review")
        self.assertEqual(assignment.primary_model_group, "unclassified_review")

    def test_sec_exact_theme_precedes_conflicting_market_behavior(self):
        assignment = resolve_group_assignment(
            "CHIP",
            {
                "sec": {
                    "sector_key": "technology",
                    "theme_keys": ("semiconductor",),
                    "confidence": 1.0,
                    "rule_version": "sec_sic_v1",
                },
                "market_behavior": {
                    "sector_key": "financials",
                    "benchmark_ticker": "XLF",
                    "confidence": 0.95,
                },
            },
            "2026-07-24",
        )

        self.assertEqual(assignment.sector_key, "technology")
        self.assertEqual(assignment.theme_keys, ("semiconductor",))
        self.assertEqual(assignment.primary_model_group, "semiconductor")
        self.assertEqual(assignment.source, "sec_exact")

    def test_market_behavior_is_used_only_when_sec_is_absent(self):
        assignment = resolve_group_assignment(
            "BANK",
            {
                "sec": {"sector_key": "unclassified", "confidence": 0.0},
                "market_behavior": {
                    "sector_key": "financials",
                    "benchmark_ticker": "XLF",
                    "confidence": 0.82,
                },
            },
            "2026-07-24",
        )

        self.assertEqual(assignment.sector_key, "financials")
        self.assertEqual(assignment.sector_benchmark, "XLF")
        self.assertEqual(assignment.source, "market_behavior")
        self.assertEqual(assignment.classification_state, "classified")

    def test_inactive_override_does_not_override_sec_assignment(self):
        override = {
            "ticker": "CHIP",
            "effective_from": "2026-07-25",
            "effective_to": "2026-12-31",
            "sector_key": "technology",
            "theme_keys": ["software"],
            "primary_model_group": "software",
            "reason": "future reclassification",
            "rule_version": "security_group_overrides_v1",
        }

        assignment = resolve_group_assignment(
            "CHIP",
            {"sec": {"sector_key": "technology", "confidence": 0.8}},
            "2026-07-24",
            overrides=[override],
        )

        self.assertEqual(assignment.theme_keys, ())
        self.assertEqual(assignment.primary_model_group, "technology")
        self.assertEqual(assignment.source, "sec_broad")

    def test_override_effective_dates_are_half_open(self):
        override = {
            "ticker": "CHIP",
            "effective_from": "2026-07-24",
            "effective_to": "2026-07-26",
            "sector_key": "technology",
            "theme_keys": ["software"],
            "primary_model_group": "software",
            "reason": "temporary software classification",
            "rule_version": "security_group_overrides_v1",
        }
        classifications = {
            "sec": {"sector_key": "technology", "confidence": 0.8}
        }

        starts_on = resolve_group_assignment(
            "CHIP", classifications, "2026-07-24", overrides=[override]
        )
        day_before_end = resolve_group_assignment(
            "CHIP", classifications, "2026-07-25", overrides=[override]
        )
        ends_on = resolve_group_assignment(
            "CHIP", classifications, "2026-07-26", overrides=[override]
        )

        self.assertEqual(starts_on.source, "override")
        self.assertEqual(day_before_end.source, "override")
        self.assertEqual(ends_on.source, "sec_broad")

    def test_zero_length_override_interval_is_rejected(self):
        override = {
            "ticker": "CHIP",
            "effective_from": "2026-07-24",
            "effective_to": "2026-07-24",
            "sector_key": "technology",
            "theme_keys": [],
            "primary_model_group": "technology",
            "reason": "empty interval",
            "rule_version": "security_group_overrides_v1",
        }

        with self.assertRaisesRegex(ValueError, "invalid_group_override"):
            resolve_group_assignment("CHIP", {}, "2026-07-24", overrides=[override])

    def test_overlapping_override_ranges_for_one_ticker_are_rejected(self):
        overrides = [
            {
                "ticker": "CHIP",
                "effective_from": "2026-01-01",
                "effective_to": "2026-12-31",
                "sector_key": "technology",
                "theme_keys": ["semiconductor"],
                "primary_model_group": "semiconductor",
                "reason": "first",
                "rule_version": "security_group_overrides_v1",
            },
            {
                "ticker": "CHIP",
                "effective_from": "2026-07-01",
                "effective_to": "2027-01-01",
                "sector_key": "technology",
                "theme_keys": ["software"],
                "primary_model_group": "software",
                "reason": "second",
                "rule_version": "security_group_overrides_v1",
            },
        ]

        with self.assertRaisesRegex(ValueError, "conflicting_override_effective_ranges"):
            resolve_group_assignment("CHIP", {}, "2026-07-24", overrides=overrides)

    def test_loader_returns_versioned_override_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overrides.json"
            path.write_text(
                json.dumps({"rule_version": "test_v1", "overrides": []}),
                encoding="utf-8",
            )

            overrides = load_group_overrides(path)

        self.assertEqual(overrides, ())

    def test_resolver_accepts_records_returned_by_override_loader(self):
        assignment = resolve_group_assignment(
            "SNDK",
            {},
            "2026-07-24",
            overrides=load_group_overrides(),
        )

        self.assertEqual(assignment.primary_model_group, "semiconductor")
        self.assertEqual(assignment.source, "override")

    def test_historical_backfill_is_evidence_bounded_and_preserves_override(self):
        assignments = historical_group_assignment_intervals(
            "SNDK",
            {
                "sec": {
                    "sector_key": "technology",
                    "confidence": 0.8,
                    "rule_version": "sec_sic_v1",
                }
            },
            observed_at="2026-07-24",
            evidence_start="2025-02-13",
        )

        self.assertEqual(
            [
                (
                    assignment.effective_from,
                    assignment.effective_to,
                    assignment.source,
                    assignment.rule_version,
                )
                for assignment in assignments
            ],
            [
                (
                    "2025-02-13",
                    "2025-02-24",
                    "historical_backfill_assumption/sec_broad",
                    "historical_backfill_v1/sec_sic_v1",
                ),
                (
                    "2025-02-24",
                    "9999-12-31",
                    "override",
                    "security_group_overrides_v1",
                ),
            ],
        )
        self.assertTrue(
            all(assignment.asof == "2026-07-24" for assignment in assignments)
        )

    def test_audit_reports_invalid_benchmarks_duplicate_themes_and_conflicts(self):
        valid = resolve_group_assignment(
            "CHIP",
            {"sec": {"sector_key": "technology", "confidence": 0.8}},
            "2026-07-24",
        )
        invalid = replace(
            valid,
            ticker="BAD",
            sector_benchmark="BAD",
            theme_keys=("semiconductor", "semiconductor"),
            theme_benchmarks={"semiconductor": ("BAD",)},
        )
        conflict = replace(valid, sector_key="financials", sector_benchmark="XLF")

        audit = audit_assignments((valid, invalid, conflict))

        self.assertEqual(audit["coverage"], 1.0)
        self.assertEqual(audit["invalid_benchmarks"], ["BAD"])
        self.assertEqual(audit["duplicate_themes"], ["BAD"])
        self.assertEqual(audit["conflicting_assignments"], ["CHIP"])
        self.assertEqual(audit["needs_review_count"], 0)

    def test_audit_rejects_missing_mapping_on_expired_historical_interval(self):
        current = resolve_group_assignment(
            "ADBE",
            {
                "sec": {
                    "sector_key": "technology",
                    "theme_keys": ("software",),
                    "confidence": 1.0,
                }
            },
            "2026-07-24",
        )
        current = replace(
            current,
            effective_from="2026-07-01",
        )
        historical = replace(
            current,
            rule_version="historical_v1",
            effective_from="2026-01-01",
            effective_to="2026-07-01",
            theme_benchmarks={},
        )

        audit = audit_assignments((historical, current))

        self.assertEqual(
            audit["invalid_benchmark_mappings"],
            [
                {
                    "actual": None,
                    "expected": ("IGV", "XSW"),
                    "group": "software",
                    "kind": "theme",
                    "ticker": "ADBE",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
