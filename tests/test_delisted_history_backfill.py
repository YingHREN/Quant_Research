import unittest

from research.delisted_history_backfill import (
    freeze_candidates,
    summarize_backfill,
)


CATALOG_SHA256 = "a" * 64


def security(
    ticker,
    exchange,
    *,
    classification="accepted_common",
    eligible=True,
):
    return {
        "ticker": ticker,
        "name": f"{ticker} Company",
        "exchange": exchange,
        "currency": "USD",
        "provider_type": "Common Stock",
        "provider_isin": None,
        "identity_status": "ticker_only",
        "identity_key": None,
        "classification": classification,
        "backfill_eligible": eligible,
        "rule_version": "delisted_security_purification_v1",
    }


def catalog(*rows):
    return {
        "schema_version": "delisted_security_catalog_v1",
        "rule_version": "delisted_security_purification_v1",
        "input_rows": len(rows),
        "securities": list(rows),
    }


class DelistedHistoryBackfillTest(unittest.TestCase):
    def test_freeze_selects_only_accepted_eligible_and_sorts(self):
        payload = catalog(
            security("ZZZ", "NYSE"),
            security("AAA", "NASDAQ"),
            security(
                "REVIEW",
                "NYSE",
                classification="needs_review",
                eligible=False,
            ),
            security(
                "WARRANT",
                "NASDAQ",
                classification="rejected_non_common",
                eligible=False,
            ),
        )

        result = freeze_candidates(
            payload,
            CATALOG_SHA256,
            "2016-01-01",
            "2026-07-27",
        )

        self.assertEqual(
            result["schema_version"],
            "delisted_history_backfill_candidates_v1",
        )
        self.assertEqual(
            result["backfill_version"],
            "delisted_history_backfill_v1",
        )
        self.assertEqual(result["catalog_sha256"], CATALOG_SHA256)
        self.assertEqual(result["candidate_count"], 2)
        self.assertEqual(
            [
                (row["exchange"], row["ticker"])
                for row in result["securities"]
            ],
            [("NASDAQ", "AAA"), ("NYSE", "ZZZ")],
        )
        self.assertEqual(result["start_date"], "2016-01-01")
        self.assertEqual(result["finish_date"], "2026-07-27")

    def test_freeze_rejects_contract_mismatch_and_duplicate_security(self):
        wrong_schema = catalog(security("AAA", "NASDAQ"))
        wrong_schema["schema_version"] = "other"
        with self.assertRaisesRegex(ValueError, "schema"):
            freeze_candidates(
                wrong_schema,
                CATALOG_SHA256,
                "2016-01-01",
                "2026-07-27",
            )

        wrong_rule = catalog(security("AAA", "NASDAQ"))
        wrong_rule["rule_version"] = "other"
        with self.assertRaisesRegex(ValueError, "rule"):
            freeze_candidates(
                wrong_rule,
                CATALOG_SHA256,
                "2016-01-01",
                "2026-07-27",
            )

        with self.assertRaisesRegex(ValueError, "SHA-256"):
            freeze_candidates(
                catalog(security("AAA", "NASDAQ")),
                "not-a-hash",
                "2016-01-01",
                "2026-07-27",
            )

        with self.assertRaisesRegex(ValueError, "duplicate"):
            freeze_candidates(
                catalog(
                    security("AAA", "NASDAQ"),
                    security("AAA", "NASDAQ"),
                ),
                CATALOG_SHA256,
                "2016-01-01",
                "2026-07-27",
            )

    def test_summary_enforces_one_audit_per_frozen_candidate(self):
        candidates = freeze_candidates(
            catalog(
                security("AAA", "NASDAQ"),
                security("BBB", "NYSE"),
            ),
            CATALOG_SHA256,
            "2016-01-01",
            "2026-07-27",
        )
        with self.assertRaisesRegex(ValueError, "match"):
            summarize_backfill(
                candidates,
                [
                    {
                        "ticker": "AAA",
                        "exchange": "NASDAQ",
                        "request_status": "success",
                    }
                ],
            )

    def test_summary_counts_status_quality_rows_bytes_and_exchange(self):
        candidates = freeze_candidates(
            catalog(
                security("AAA", "NASDAQ"),
                security("BBB", "NASDAQ"),
                security("CCC", "NYSE"),
                security("DDD", "NYSE MKT"),
            ),
            CATALOG_SHA256,
            "2016-01-01",
            "2026-07-27",
        )
        audits = [
            {
                "ticker": "AAA",
                "exchange": "NASDAQ",
                "request_status": "success",
                "quality_status": "clean",
                "valid_rows": 10,
                "raw_rows": 10,
                "invalid_rows": 0,
                "duplicate_dates": 0,
                "raw_bytes": 100,
            },
            {
                "ticker": "BBB",
                "exchange": "NASDAQ",
                "request_status": "empty",
                "quality_status": "no_rows",
                "valid_rows": 0,
                "raw_rows": 0,
                "invalid_rows": 0,
                "duplicate_dates": 0,
                "raw_bytes": 2,
            },
            {
                "ticker": "CCC",
                "exchange": "NYSE",
                "request_status": "success",
                "quality_status": "warning",
                "valid_rows": 20,
                "raw_rows": 23,
                "invalid_rows": 2,
                "duplicate_dates": 1,
                "raw_bytes": 300,
            },
            {
                "ticker": "DDD",
                "exchange": "NYSE MKT",
                "request_status": "fetch_error",
                "quality_status": "unavailable",
                "valid_rows": 0,
                "raw_rows": 0,
                "invalid_rows": 0,
                "duplicate_dates": 0,
                "raw_bytes": 0,
                "retryable": True,
            },
        ]

        result = summarize_backfill(candidates, audits)
        by_exchange = {
            row["exchange"]: row for row in result["by_exchange"]
        }

        self.assertEqual(result["candidate_count"], 4)
        self.assertEqual(result["audited_count"], 4)
        self.assertEqual(result["usable_histories"], 2)
        self.assertEqual(result["empty_responses"], 1)
        self.assertEqual(result["retryable_errors"], 1)
        self.assertEqual(result["permanent_errors"], 0)
        self.assertEqual(result["quality_warnings"], 1)
        self.assertEqual(result["valid_rows"], 30)
        self.assertEqual(result["raw_rows"], 33)
        self.assertEqual(result["invalid_rows"], 2)
        self.assertEqual(result["duplicate_dates"], 1)
        self.assertEqual(result["raw_bytes"], 402)
        self.assertEqual(result["completion_status"], "partial")
        self.assertEqual(by_exchange["NASDAQ"]["candidate_count"], 2)
        self.assertEqual(by_exchange["NASDAQ"]["usable_histories"], 1)
        self.assertEqual(by_exchange["NYSE"]["quality_warnings"], 1)
        self.assertEqual(by_exchange["NYSE MKT"]["retryable_errors"], 1)

    def test_summary_treats_permanent_error_as_complete(self):
        candidates = freeze_candidates(
            catalog(security("AAA", "NASDAQ")),
            CATALOG_SHA256,
            "2016-01-01",
            "2026-07-27",
        )

        result = summarize_backfill(
            candidates,
            [
                {
                    "ticker": "AAA",
                    "exchange": "NASDAQ",
                    "request_status": "http_error",
                    "quality_status": "unavailable",
                    "valid_rows": 0,
                    "raw_rows": 0,
                    "invalid_rows": 0,
                    "duplicate_dates": 0,
                    "raw_bytes": 0,
                    "retryable": False,
                }
            ],
        )

        self.assertEqual(result["permanent_errors"], 1)
        self.assertEqual(result["completion_status"], "complete")


if __name__ == "__main__":
    unittest.main()
