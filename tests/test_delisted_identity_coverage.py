import json
import unittest

from research.delisted_identity_coverage import (
    adjudicate_identity,
    normalize_provider_evidence,
    select_coverage_sample,
    summarize_coverage,
)


class SelectCoverageSampleTests(unittest.TestCase):
    def test_sample_is_stable_and_covers_identity_panels(self):
        catalog = [
            {
                "ticker": "AAA",
                "exchange": "NASDAQ",
                "name": "Alpha Inc",
                "identity_status": "strong_isin",
                "provider_isin": "US0000000001",
                "backfill_eligible": True,
            },
            {
                "ticker": "BBB",
                "exchange": "NYSE",
                "name": "Beta PLC ADR",
                "identity_status": "ticker_only",
                "provider_isin": None,
                "backfill_eligible": True,
            },
            {
                "ticker": "CCC",
                "exchange": "NASDAQ",
                "name": "Gamma Inc",
                "identity_status": "conflicting_isin",
                "provider_isin": "US0000000002",
                "backfill_eligible": True,
            },
        ]
        history = {
            "AAA": {"valid_rows": 200, "last_date": "2025-01-01"},
            "BBB": {"valid_rows": 0, "last_date": None},
            "CCC": {"valid_rows": 100, "last_date": "2009-12-31"},
        }
        quotas = {
            "strong_isin": 1,
            "ticker_only": 1,
            "conflicting_isin": 1,
        }

        first = select_coverage_sample(catalog, history, quotas)
        second = select_coverage_sample(
            list(reversed(catalog)),
            history,
            quotas,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            {row["identity_panel"] for row in first},
            set(quotas),
        )
        self.assertTrue(all("selection_hash" in row for row in first))

    def test_rejects_panel_smaller_than_requested_quota(self):
        catalog = [
            {
                "ticker": "AAA",
                "exchange": "NASDAQ",
                "name": "Alpha Inc",
                "identity_status": "strong_isin",
                "provider_isin": "US0000000001",
                "backfill_eligible": True,
            }
        ]

        with self.assertRaisesRegex(
            ValueError,
            r"strong_isin has 1 rows; 2 required",
        ):
            select_coverage_sample(
                catalog,
                {"AAA": {"valid_rows": 1}},
                {"strong_isin": 2},
            )

    def test_rejects_duplicate_eligible_ticker(self):
        row = {
            "ticker": "AAA",
            "exchange": "NASDAQ",
            "name": "Alpha Inc",
            "identity_status": "strong_isin",
            "provider_isin": "US0000000001",
            "backfill_eligible": True,
        }

        with self.assertRaisesRegex(
            ValueError,
            r"duplicate eligible ticker: AAA",
        ):
            select_coverage_sample(
                [row, dict(row)],
                {"AAA": {"valid_rows": 1}},
                {"strong_isin": 1},
            )

    def test_rejects_non_positive_quota(self):
        with self.assertRaisesRegex(
            ValueError,
            r"quota counts must be positive integers",
        ):
            select_coverage_sample([], {}, {"strong_isin": 0})

    def test_rejects_boolean_quota(self):
        with self.assertRaisesRegex(
            ValueError,
            r"quota counts must be positive integers",
        ):
            select_coverage_sample([], {}, {"strong_isin": True})

    def test_rejects_fractional_quota(self):
        with self.assertRaisesRegex(
            ValueError,
            r"quota counts must be positive integers",
        ):
            select_coverage_sample([], {}, {"strong_isin": 1.5})

    def test_excluded_security_cannot_satisfy_panel_quota(self):
        catalog = [
            {
                "ticker": "AAA",
                "exchange": "NASDAQ",
                "name": "Alpha Warrant",
                "identity_status": "strong_isin",
                "provider_isin": "US0000000001",
                "backfill_eligible": False,
            }
        ]

        with self.assertRaisesRegex(
            ValueError,
            r"strong_isin has 0 rows; 1 required",
        ):
            select_coverage_sample(
                catalog,
                {"AAA": {"valid_rows": 100}},
                {"strong_isin": 1},
            )


class AdjudicateIdentityTests(unittest.TestCase):
    def test_normalizes_provider_isin_to_cik_evidence(self):
        result = normalize_provider_evidence(
            [
                {
                    "isin": " us123 ",
                    "cik": "123",
                    "code": "old",
                    "name": "Example Devices",
                }
            ],
            "2026-07-27T12:00:00Z",
        )

        self.assertEqual(
            result,
            (
                {
                    "key_type": "isin_cik",
                    "isin": "US123",
                    "cik": "0000000123",
                    "ticker": "OLD",
                    "name": "Example Devices",
                    "available_at": "2026-07-27T12:00:00Z",
                    "source": "eodhd",
                },
            ),
        )

    def test_ticker_only_match_never_confirms_identity(self):
        result = adjudicate_identity(
            {
                "ticker": "OLD",
                "name": "Unrelated Company",
                "provider_isin": None,
                "first_date": "2018-01-01",
                "last_date": "2020-01-01",
            },
            [
                {
                    "cik": "0000000123",
                    "match_reasons": ["current_ticker"],
                    "matched_former_name": None,
                }
            ],
            (),
        )

        self.assertEqual(result["link_status"], "review_required")
        self.assertEqual(result["cik"], None)
        self.assertEqual(result["reason_codes"], ["ticker_only_match"])

    def test_unique_isin_to_cik_with_exact_name_confirms(self):
        result = adjudicate_identity(
            {
                "ticker": "OLD",
                "name": "Example Devices",
                "provider_isin": "US123",
                "first_date": "2018-01-01",
                "last_date": "2020-01-01",
            },
            [
                {
                    "cik": "0000000123",
                    "match_reasons": ["exact_former_name"],
                    "matched_former_name": {
                        "name": "Example Devices Corp.",
                        "from": "2012-01-01",
                        "to": "2020-06-01",
                    },
                }
            ],
            [
                {
                    "key_type": "isin_cik",
                    "isin": "US123",
                    "cik": "0000000123",
                    "available_at": "2026-07-27T00:00:00Z",
                }
            ],
        )

        self.assertEqual(result["link_status"], "confirmed")
        self.assertEqual(result["cik"], "0000000123")
        self.assertEqual(
            result["decision_rule"],
            "isin_cik_plus_exact_name",
        )

    def test_dated_former_name_overlapping_price_history_confirms(self):
        result = adjudicate_identity(
            {
                "ticker": "OLD",
                "name": "Example Devices",
                "provider_isin": None,
                "first_date": "2018-01-01",
                "last_date": "2020-01-01",
            },
            [
                {
                    "cik": "0000000123",
                    "match_reasons": ["exact_former_name"],
                    "matched_former_name": {
                        "name": "Example Devices Corp.",
                        "from": "2012-01-01",
                        "to": "2020-06-01",
                    },
                }
            ],
            (),
        )

        self.assertEqual(result["link_status"], "confirmed")
        self.assertEqual(result["cik"], "0000000123")
        self.assertEqual(
            result["decision_rule"],
            "dated_former_name_overlap",
        )

    def test_no_identity_candidates_remains_unresolved(self):
        result = adjudicate_identity(
            {
                "ticker": "OLD",
                "name": "Unknown Company",
                "provider_isin": None,
            },
            (),
            (),
        )

        self.assertEqual(result["link_status"], "unresolved")
        self.assertIsNone(result["cik"])
        self.assertEqual(result["reason_codes"], ["no_identity_candidates"])

    def test_undated_exact_name_requires_review(self):
        result = adjudicate_identity(
            {
                "ticker": "OLD",
                "name": "Example Holdings",
                "provider_isin": None,
            },
            [
                {
                    "cik": "0000000123",
                    "match_reasons": ["exact_current_name"],
                    "matched_former_name": None,
                }
            ],
            (),
        )

        self.assertEqual(result["link_status"], "review_required")
        self.assertIsNone(result["cik"])
        self.assertEqual(result["reason_codes"], ["undated_exact_name"])

    def test_competing_ciks_require_review(self):
        result = adjudicate_identity(
            {
                "ticker": "OLD",
                "name": "Shared Company",
                "provider_isin": None,
            },
            [
                {
                    "cik": "0000000123",
                    "match_reasons": ["exact_current_name"],
                    "matched_former_name": None,
                },
                {
                    "cik": "0000000456",
                    "match_reasons": ["exact_current_name"],
                    "matched_former_name": None,
                },
            ],
            (),
        )

        self.assertEqual(result["link_status"], "review_required")
        self.assertIsNone(result["cik"])
        self.assertEqual(result["reason_codes"], ["competing_ciks"])
        self.assertEqual(len(result["conflicting_evidence"]), 2)

    def test_conflicting_isin_to_cik_evidence_requires_review(self):
        result = adjudicate_identity(
            {
                "ticker": "OLD",
                "name": "Example",
                "provider_isin": "US123",
            },
            (),
            [
                {
                    "key_type": "isin_cik",
                    "isin": "US123",
                    "cik": "0000000123",
                },
                {
                    "key_type": "isin_cik",
                    "isin": "US123",
                    "cik": "0000000456",
                },
            ],
        )

        self.assertEqual(result["link_status"], "review_required")
        self.assertIsNone(result["cik"])
        self.assertEqual(result["reason_codes"], ["conflicting_isin_cik"])
        self.assertEqual(len(result["conflicting_evidence"]), 2)

    def test_security_type_contradiction_rejects_link(self):
        result = adjudicate_identity(
            {
                "ticker": "OLD",
                "name": "Example",
                "provider_isin": None,
            },
            (),
            [
                {
                    "key_type": "security_type",
                    "value": "Warrant",
                    "source": "eodhd",
                }
            ],
        )

        self.assertEqual(result["link_status"], "rejected")
        self.assertIsNone(result["cik"])
        self.assertEqual(
            result["reason_codes"],
            ["security_type_contradiction"],
        )

    def test_adjudication_is_independent_of_evidence_order(self):
        sample = {
            "ticker": "OLD",
            "name": "Shared Company",
            "provider_isin": None,
        }
        candidates = [
            {
                "cik": "0000000456",
                "match_reasons": ["exact_current_name"],
                "matched_former_name": None,
            },
            {
                "cik": "0000000123",
                "match_reasons": ["exact_current_name"],
                "matched_former_name": None,
            },
        ]

        first = adjudicate_identity(sample, candidates, ())
        second = adjudicate_identity(
            sample,
            list(reversed(candidates)),
            (),
        )

        self.assertEqual(
            json.dumps(first, sort_keys=True),
            json.dumps(second, sort_keys=True),
        )


class SummarizeCoverageTests(unittest.TestCase):
    def test_reports_exact_panel_sources_collisions_sic_and_usage(self):
        sample = [
            {"ticker": "AAA", "identity_panel": "strong_isin"},
            {"ticker": "BBB", "identity_panel": "ticker_only"},
            {"ticker": "CCC", "identity_panel": "ticker_only"},
            {"ticker": "DDD", "identity_panel": "conflicting_isin"},
        ]
        decisions = [
            {
                "ticker": "AAA",
                "link_status": "confirmed",
                "decision_rule": "isin_cik_plus_exact_name",
                "reason_codes": [],
            },
            {
                "ticker": "BBB",
                "link_status": "confirmed",
                "decision_rule": "dated_former_name_overlap",
                "reason_codes": [],
            },
            {
                "ticker": "CCC",
                "link_status": "review_required",
                "decision_rule": "competing_ciks",
                "reason_codes": ["competing_ciks"],
            },
            {
                "ticker": "DDD",
                "link_status": "rejected",
                "decision_rule": "security_type_contradiction",
                "reason_codes": ["security_type_contradiction"],
            },
        ]
        sic_audits = {
            "observations": [
                {
                    "cik": "0000000001",
                    "available_at": "2018-01-02T10:00:00Z",
                },
                {
                    "cik": "0000000001",
                    "available_at": "2020-01-02T10:00:00Z",
                },
            ],
            "statuses": [
                {"status": "success"},
                {"status": "not_found"},
            ],
        }
        usage = {
            "sec_requests": 3,
            "provider_requests": 2,
            "provider_api_units": 20,
            "download_bytes": 1000,
            "runtime_seconds": 4.5,
            "projection_panels": {
                "ticker_only": {
                    "sample_count": 2,
                    "population_count": 20,
                    "projected_storage_bytes": 10000,
                    "projected_runtime_seconds": 45.0,
                }
            },
        }

        summary = summarize_coverage(
            sample,
            decisions,
            sic_audits,
            usage,
        )

        self.assertEqual(
            summary["decision_counts"],
            {
                "confirmed": 2,
                "rejected": 1,
                "review_required": 1,
                "unresolved": 0,
            },
        )
        self.assertEqual(
            summary["identity_panels"]["ticker_only"]["decision_counts"],
            {
                "confirmed": 1,
                "rejected": 0,
                "review_required": 1,
                "unresolved": 0,
            },
        )
        self.assertEqual(
            summary["confirmation_sources"],
            {"provider_assisted": 1, "sec_only": 1},
        )
        self.assertEqual(summary["reason_counts"]["competing_ciks"], 1)
        self.assertEqual(
            summary["sic"],
            {
                "available_ciks": 1,
                "observation_count": 2,
                "status_counts": {"not_found": 1, "success": 1},
                "earliest_available_at": "2018-01-02T10:00:00Z",
                "latest_available_at": "2020-01-02T10:00:00Z",
            },
        )
        self.assertEqual(summary["usage"]["provider_api_units"], 20)
        projection = summary["usage"]["projection_panels"]["ticker_only"]
        self.assertEqual(projection["source_sample_count"], 2)
        self.assertEqual(projection["population_count"], 20)

    def test_empty_sample_rates_are_explicitly_unavailable(self):
        summary = summarize_coverage([], [], {}, {})

        self.assertEqual(
            summary["confirmation_rate"],
            {
                "numerator": 0,
                "denominator": 0,
                "value": None,
                "reason": "no_eligible_rows",
            },
        )


if __name__ == "__main__":
    unittest.main()
