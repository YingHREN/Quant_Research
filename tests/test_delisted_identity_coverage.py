import unittest

from research.delisted_identity_coverage import select_coverage_sample


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


if __name__ == "__main__":
    unittest.main()
