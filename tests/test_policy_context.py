import unittest

from research.policy_context import POLICY_SERIES_IDS


class PolicySeriesCatalogTest(unittest.TestCase):
    def test_catalog_covers_policy_liquidity_real_rate_and_pce_inputs(self):
        self.assertEqual(
            POLICY_SERIES_IDS,
            (
                "DFEDTARL",
                "DFEDTARU",
                "WALCL",
                "WSHOSHO",
                "WSHOMCB",
                "WRESBAL",
                "WTREGEN",
                "RRPONTSYD",
                "DFII10",
                "PCEPI",
                "PCEPILFE",
            ),
        )


if __name__ == "__main__":
    unittest.main()
