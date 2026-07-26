from __future__ import annotations

import unittest

from data.sector_classification import classify_sic


class SectorClassificationTest(unittest.TestCase):
    def test_exact_industries_override_broad_manufacturing_ranges(self):
        cases = {
            "3674": ("technology", ("semiconductor",)),
            "7372": ("technology", ("software",)),
            "2834": ("health_care", ()),
            "3841": ("health_care", ()),
            "3711": ("consumer_discretionary", ()),
        }

        for sic, expected in cases.items():
            with self.subTest(sic=sic):
                result = classify_sic(sic, "SEC industry")

                self.assertEqual(
                    (result.sector_key, result.theme_keys),
                    expected,
                )
                self.assertEqual(result.confidence, 1.0)
                self.assertEqual(result.source, "sec")
                self.assertEqual(result.rule_version, "sec_sic_v1")

    def test_finance_real_estate_energy_and_utilities_remain_distinct(self):
        cases = {
            "6022": "financials",
            "6798": "real_estate",
            "1311": "energy",
            "4911": "utilities",
        }

        for sic, expected in cases.items():
            with self.subTest(sic=sic):
                result = classify_sic(sic)

                self.assertEqual(result.sector_key, expected)
                self.assertGreaterEqual(result.confidence, 0.8)

    def test_unknown_or_invalid_sic_is_explicitly_unclassified(self):
        for sic in (None, "", "0", "9999", "secret"):
            with self.subTest(sic=sic):
                result = classify_sic(sic, "Unknown")

                self.assertEqual(result.sector_key, "unclassified")
                self.assertEqual(result.confidence, 0.0)
                self.assertEqual(result.sic, None if sic in (None, "", "0", "secret") else "9999")
                self.assertEqual(result.industry_description, "Unknown")

    def test_serialized_result_preserves_provenance(self):
        result = classify_sic("7372", "Prepackaged Software")

        self.assertEqual(
            result.to_dict(),
            {
                "sector_key": "technology",
                "theme_keys": ["software"],
                "sic": "7372",
                "industry_description": "Prepackaged Software",
                "source": "sec",
                "rule_version": "sec_sic_v1",
                "confidence": 1.0,
            },
        )


if __name__ == "__main__":
    unittest.main()
