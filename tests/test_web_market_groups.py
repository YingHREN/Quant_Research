import unittest
from copy import deepcopy

from web.market_groups import (
    MARKET_GROUPS,
    REFERENCE_TICKERS,
    SECTOR_ETFS,
    market_group,
    market_group_for_ticker,
    modeled_market_groups,
    resolved_market_groups,
)


def assignment(
    ticker,
    *,
    sector_key="technology",
    sector_benchmark="XLK",
    theme_keys=(),
    theme_benchmarks=None,
    primary_model_group=None,
):
    return {
        "state": "assigned",
        "ticker": ticker,
        "sector_key": sector_key,
        "sector_benchmark": sector_benchmark,
        "theme_keys": list(theme_keys),
        "theme_benchmarks": (
            {} if theme_benchmarks is None else deepcopy(theme_benchmarks)
        ),
        "primary_model_group": primary_model_group or sector_key,
    }


class MarketGroupTest(unittest.TestCase):
    def test_ticker_group_lookup_is_explicit_and_optional(self):
        self.assertEqual(market_group_for_ticker("mu").key, "semiconductor")
        self.assertEqual(market_group_for_ticker("NBIS").key, "semiconductor")
        self.assertEqual(market_group_for_ticker("ADBE").key, "software")
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
                "IGV",
                "XSW",
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

    def test_software_group_has_explicit_benchmarks_and_constituents(self):
        group = market_group("software")

        self.assertEqual(group.benchmark_tickers, ("IGV", "XSW"))
        self.assertEqual(group.fallback_benchmark_tickers, ("XLK",))
        self.assertIn("ADBE", group.constituent_tickers)
        self.assertIn("CRM", group.constituent_tickers)

    def test_modeled_groups_require_explicit_constituents(self):
        self.assertEqual(
            tuple(group.key for group in modeled_market_groups()),
            ("semiconductor", "software"),
        )

    def test_resolved_groups_make_sndk_a_semiconductor_constituent(self):
        assignments = {
            "SNDK": assignment(
                "SNDK",
                theme_keys=("semiconductor",),
                theme_benchmarks={
                    "semiconductor": ["SOXX", "SMH"],
                },
                primary_model_group="semiconductor",
            ),
            "ADBE": assignment(
                "ADBE",
                theme_keys=("software",),
                theme_benchmarks={"software": ["IGV", "XSW"]},
                primary_model_group="software",
            ),
        }

        groups = resolved_market_groups(
            assignments,
            {"SNDK", "ADBE", "SOXX", "SMH", "IGV", "XSW"},
        )

        semiconductor = next(
            group for group in groups if group.key == "semiconductor"
        )
        software = next(group for group in groups if group.key == "software")
        self.assertEqual(semiconductor.constituent_tickers, ("SNDK",))
        self.assertEqual(software.constituent_tickers, ("ADBE",))

    def test_resolved_groups_keep_every_broad_sector_and_stable_benchmark(self):
        groups = {
            group.key: group
            for group in resolved_market_groups({}, set(SECTOR_ETFS.values()))
        }

        self.assertEqual(set(SECTOR_ETFS), set(SECTOR_ETFS).intersection(groups))
        for key, benchmark in SECTOR_ETFS.items():
            with self.subTest(key=key):
                self.assertEqual(groups[key].benchmark_tickers, (benchmark,))
                self.assertEqual(groups[key].constituent_tickers, ())

    def test_primary_model_group_prevents_duplicate_constituent_membership(self):
        assignments = {
            "SNDK": assignment(
                "SNDK",
                theme_keys=("semiconductor",),
                theme_benchmarks={
                    "semiconductor": ["SOXX", "SMH"],
                },
                primary_model_group="semiconductor",
            ),
        }

        groups = resolved_market_groups(assignments, {"SNDK", "SOXX", "SMH"})
        containing = [
            group.key
            for group in groups
            if "SNDK" in group.constituent_tickers
        ]

        self.assertEqual(containing, ["semiconductor"])

    def test_resolved_groups_preserve_ai_infrastructure_as_related_only(self):
        assignments = {
            "NBIS": assignment("NBIS", primary_model_group="technology"),
        }

        groups = resolved_market_groups(assignments, {"NBIS", "XLK", "SOXX"})
        semiconductor = next(
            group for group in groups if group.key == "semiconductor"
        )
        technology = next(group for group in groups if group.key == "technology")

        self.assertNotIn("NBIS", semiconductor.constituent_tickers)
        self.assertIn("NBIS", semiconductor.related_tickers)
        self.assertEqual(technology.constituent_tickers, ("NBIS",))

    def test_resolved_groups_do_not_mutate_assignment_inputs(self):
        assignments = {
            "SNDK": assignment(
                "SNDK",
                theme_keys=("semiconductor",),
                theme_benchmarks={
                    "semiconductor": ["SOXX", "SMH"],
                },
                primary_model_group="semiconductor",
            ),
        }
        before = deepcopy(assignments)

        resolved_market_groups(assignments, {"SNDK", "SOXX", "SMH"})

        self.assertEqual(assignments, before)
