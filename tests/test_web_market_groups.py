import unittest

from web.market_groups import (
    MARKET_GROUPS,
    REFERENCE_TICKERS,
    market_group,
    market_group_for_ticker,
    modeled_market_groups,
)


class MarketGroupTest(unittest.TestCase):
    def test_ticker_group_lookup_is_explicit_and_optional(self):
        self.assertEqual(market_group_for_ticker("mu").key, "semiconductor")
        self.assertEqual(market_group_for_ticker("NBIS").key, "semiconductor")
        self.assertIsNone(market_group_for_ticker("AAPL"))

    def test_reference_universe_is_stable_and_complete(self):
        self.assertEqual(
            REFERENCE_TICKERS,
            (
                "SPY",
                "QQQ",
                "XLK",
                "XLC",
                "XLY",
                "XLP",
                "XLE",
                "XLF",
                "XLV",
                "XLI",
                "XLB",
                "XLRE",
                "XLU",
                "SOXX",
                "SMH",
            ),
        )

    def test_semiconductor_and_ai_infrastructure_are_not_conflated(self):
        group = market_group("semiconductor")
        self.assertEqual(group.benchmark_tickers, ("SOXX", "SMH"))
        self.assertIn("AMD", group.constituent_tickers)
        self.assertNotIn("NBIS", group.constituent_tickers)
        self.assertIn("NBIS", group.related_tickers)
        self.assertIs(MARKET_GROUPS["semiconductor"], group)

    def test_each_sector_etf_is_a_selectable_proxy_only_group(self):
        technology = market_group("technology")
        self.assertEqual(technology.benchmark_tickers, ("XLK",))
        self.assertEqual(technology.constituent_tickers, ())
        self.assertEqual(technology.related_tickers, ())

    def test_modeled_groups_require_explicit_constituents(self):
        self.assertEqual(
            tuple(group.key for group in modeled_market_groups()),
            ("semiconductor",),
        )
