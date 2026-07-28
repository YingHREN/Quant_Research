import unittest

from web.services.market_cap import market_cap_fields


class MarketCapFieldsTest(unittest.TestCase):
    def test_uses_exact_tier_boundaries(self):
        self.assertEqual(
            market_cap_fields(200_000_000_000, "2026-07-24"),
            {
                "market_cap": 200_000_000_000.0,
                "market_cap_asof": "2026-07-24",
                "market_cap_tier": "mega",
            },
        )
        self.assertEqual(
            market_cap_fields(10_000_000_000, "2026-07-24")["market_cap_tier"],
            "large",
        )
        self.assertEqual(
            market_cap_fields(2_000_000_000, "2026-07-24")["market_cap_tier"],
            "mid",
        )
        self.assertEqual(
            market_cap_fields(300_000_000, "2026-07-24")["market_cap_tier"],
            "small",
        )
        self.assertEqual(
            market_cap_fields(299_999_999, "2026-07-24")["market_cap_tier"],
            "micro",
        )

    def test_fails_closed_for_missing_or_invalid_values(self):
        unavailable = {
            "market_cap": None,
            "market_cap_asof": None,
            "market_cap_tier": "unavailable",
        }
        for value in (None, 0, -1, float("nan"), float("inf"), "bad"):
            with self.subTest(value=value):
                self.assertEqual(
                    market_cap_fields(value, "2026-07-24"),
                    unavailable,
                )

    def test_normalizes_valid_asof_date(self):
        self.assertEqual(
            market_cap_fields(1_000_000, "2026-07-24T15:30:00Z")[
                "market_cap_asof"
            ],
            "2026-07-24",
        )


if __name__ == "__main__":
    unittest.main()
