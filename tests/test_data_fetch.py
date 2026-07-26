from __future__ import annotations

import unittest

from data.fetch import _cache_path, _period_years


class DailyFetchWindowTest(unittest.TestCase):
    def test_cache_key_separates_short_and_ten_year_history(self):
        one_year = _cache_path("AMD", "1y")
        ten_year = _cache_path("AMD", "10y")

        self.assertNotEqual(one_year, ten_year)
        self.assertIn("_1y_", one_year)
        self.assertIn("_10y_", ten_year)

    def test_period_year_parser_accepts_positive_year_windows(self):
        self.assertEqual(_period_years("1y"), 1)
        self.assertEqual(_period_years("10y"), 10)

    def test_period_year_parser_rejects_ambiguous_or_unbounded_windows(self):
        for period in ("max", "6mo", "0y", "-2y", ""):
            with self.subTest(period=period):
                with self.assertRaises(ValueError):
                    _period_years(period)


if __name__ == "__main__":
    unittest.main()
