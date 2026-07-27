import unittest

from data.delisted_security_catalog import (
    build_delisted_catalog,
    classify_catalog_row,
    summarize_delisted_catalog,
    valid_isin,
)


def row(
    code,
    name,
    *,
    exchange="NASDAQ",
    currency="USD",
    type_="Common Stock",
    isin=None,
):
    return {
        "Code": code,
        "Name": name,
        "Exchange": exchange,
        "Currency": currency,
        "Type": type_,
        "Isin": isin,
    }


class DelistedSecurityCatalogTest(unittest.TestCase):
    def test_accepts_common_stock_and_spac_common_without_suffix_guessing(self):
        common = classify_catalog_row(
            row(
                "AAPL",
                "Apple Inc",
                isin="US0378331005",
            )
        )
        spac = classify_catalog_row(
            row("ACME", "Acme Acquisition Corp")
        )
        ending_letter = classify_catalog_row(
            row("MWW", "Monster Worldwide Inc")
        )

        self.assertEqual(common["classification"], "accepted_common")
        self.assertTrue(common["backfill_eligible"])
        self.assertEqual(common["identity_status"], "strong_isin")
        self.assertEqual(
            common["identity_key"],
            "isin:US0378331005",
        )
        self.assertEqual(spac["classification"], "accepted_common")
        self.assertEqual(ending_letter["classification"], "accepted_common")

    def test_company_name_words_and_plain_ads_are_not_security_type_signals(self):
        cases = (
            ("APTS", "Preferred Apartment Communities Inc"),
            ("UNT", "Unit Corporation"),
            ("ABHH", "American Bank Note Holographics Inc"),
            (
                "AMAM",
                "Ambrx Biopharma Inc. American Depositary Shares",
            ),
        )

        for code, name in cases:
            with self.subTest(code=code):
                result = classify_catalog_row(row(code, name))
                self.assertEqual(
                    result["classification"],
                    "accepted_common",
                )
                self.assertEqual(result["reason_codes"], [])
                self.assertTrue(result["backfill_eligible"])

    def test_rejects_explicit_non_common_security_signals(self):
        cases = (
            ("AAA-WS", "AAA Inc", "warrant_signal"),
            ("AAAW", "AAA Inc Warrants", "warrant_signal"),
            ("AAA-U", "AAA Inc", "unit_signal"),
            ("AAAU", "AAA Acquisition Corp Unit", "unit_signal"),
            ("AAA-RT", "AAA Inc", "right_signal"),
            ("AAAR", "AAA Inc Rights", "right_signal"),
            ("AAAP", "AAA 8% Preferred Stock", "preferred_signal"),
            ("AAAD", "AAA Senior Notes due 2030", "debt_signal"),
            ("AAAF", "AAA Closed-End Fund", "fund_signal"),
        )

        for code, name, reason in cases:
            with self.subTest(code=code):
                result = classify_catalog_row(row(code, name))
                self.assertEqual(
                    result["classification"],
                    "rejected_non_common",
                )
                self.assertEqual(result["reason_codes"], [reason])
                self.assertFalse(result["backfill_eligible"])
                self.assertTrue(result["evidence"])

    def test_scope_gate_and_ambiguous_identity_require_no_backfill(self):
        out_of_scope = (
            row("AAA", "AAA Inc", exchange="PINK"),
            row("AAA", "AAA Inc", currency="EUR"),
            row("AAA", "AAA Inc", type_="ETF"),
            row("BAD_CODE", "Bad Code Inc"),
        )
        for source in out_of_scope:
            with self.subTest(source=source):
                result = classify_catalog_row(source)
                self.assertEqual(result["classification"], "out_of_scope")
                self.assertFalse(result["backfill_eligible"])
                self.assertTrue(result["reason_codes"])

        ambiguous = classify_catalog_row(row("AAA", "AAA"))
        invalid_isin = classify_catalog_row(
            row("AAPL", "Apple Inc", isin="US0378331006")
        )
        missing_isin = classify_catalog_row(row("AAA", "AAA Inc"))

        self.assertEqual(ambiguous["classification"], "needs_review")
        self.assertEqual(ambiguous["reason_codes"], ["ambiguous_name"])
        self.assertEqual(invalid_isin["classification"], "needs_review")
        self.assertEqual(invalid_isin["identity_status"], "invalid_isin")
        self.assertEqual(
            invalid_isin["reason_codes"],
            ["invalid_isin"],
        )
        self.assertIsNone(invalid_isin["identity_key"])
        self.assertEqual(missing_isin["identity_status"], "ticker_only")
        self.assertTrue(missing_isin["backfill_eligible"])

    def test_isin_validation_uses_format_and_check_digit(self):
        self.assertTrue(valid_isin("US0378331005"))
        self.assertTrue(valid_isin("GB0002634946"))
        self.assertFalse(valid_isin("US0378331006"))
        self.assertFalse(valid_isin("TOO-SHORT"))
        self.assertFalse(valid_isin(None))

    def test_non_mapping_row_is_rejected(self):
        with self.assertRaises(TypeError):
            classify_catalog_row(["not", "a", "mapping"])

    def test_catalog_is_order_independent_and_rejects_in_scope_duplicates(self):
        rows = [
            row("ZZZ", "Last Inc"),
            row("AAA", "First Inc"),
            row("OTC", "OTC Inc", exchange="PINK"),
        ]

        forward = build_delisted_catalog(rows)
        backward = build_delisted_catalog(list(reversed(rows)))

        self.assertEqual(forward, backward)
        self.assertEqual(
            [item["ticker"] for item in forward["securities"]],
            ["AAA", "ZZZ", "OTC"],
        )
        with self.assertRaisesRegex(ValueError, "duplicate in-scope security"):
            build_delisted_catalog(
                [
                    row("AAA", "First Inc"),
                    row("AAA", "Duplicate Inc"),
                ]
            )

    def test_same_isin_conflict_blocks_identity_merge_not_type_backfill(self):
        catalog = build_delisted_catalog(
            [
                row(
                    "OLD",
                    "Old Company Inc",
                    isin="US0378331005",
                ),
                row(
                    "NEW",
                    "Different Company Inc",
                    exchange="NYSE",
                    isin="US0378331005",
                ),
            ]
        )

        self.assertEqual(len(catalog["securities"]), 2)
        for item in catalog["securities"]:
            self.assertEqual(item["classification"], "accepted_common")
            self.assertEqual(
                item["reason_codes"],
                ["identity_conflict"],
            )
            self.assertTrue(item["backfill_eligible"])
            self.assertEqual(item["identity_status"], "conflicting_isin")
            self.assertIsNone(item["identity_key"])

    def test_identity_conflict_does_not_change_out_of_scope_classification(self):
        catalog = build_delisted_catalog(
            [
                row("AAA", "AAA Inc", isin="US0378331005"),
                row(
                    "OTC",
                    "Different OTC Inc",
                    exchange="PINK",
                    isin="US0378331005",
                ),
            ]
        )
        by_ticker = {
            item["ticker"]: item for item in catalog["securities"]
        }

        self.assertEqual(
            by_ticker["OTC"]["classification"],
            "out_of_scope",
        )
        self.assertFalse(by_ticker["OTC"]["backfill_eligible"])
        self.assertEqual(
            by_ticker["OTC"]["identity_status"],
            "conflicting_isin",
        )

    def test_summary_counts_scope_classification_reason_and_identity(self):
        catalog = build_delisted_catalog(
            [
                row("AAA", "AAA Inc", isin="US0378331005"),
                row("BBB-WS", "BBB Warrants", exchange="NYSE"),
                row("CCC", "CCC", exchange="NYSE MKT"),
                row("DDD", "DDD Inc", exchange="PINK"),
            ]
        )

        summary = summarize_delisted_catalog(catalog)

        self.assertEqual(summary["input_rows"], 4)
        self.assertEqual(summary["in_scope_rows"], 3)
        self.assertEqual(
            summary["classification_counts"],
            {
                "accepted_common": 1,
                "needs_review": 1,
                "out_of_scope": 1,
                "rejected_non_common": 1,
            },
        )
        self.assertEqual(
            summary["reason_counts"],
            {
                "ambiguous_name": 1,
                "unsupported_exchange": 1,
                "warrant_signal": 1,
            },
        )
        self.assertEqual(
            summary["identity_status_counts"],
            {"strong_isin": 1, "ticker_only": 3},
        )
        self.assertEqual(
            summary["reason_samples"]["warrant_signal"],
            ["BBB-WS"],
        )
        self.assertEqual(
            summary["by_exchange"]["NYSE"]["rejected_non_common"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
