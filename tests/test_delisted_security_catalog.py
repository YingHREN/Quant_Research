import unittest

from data.delisted_security_catalog import classify_catalog_row, valid_isin


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


if __name__ == "__main__":
    unittest.main()
