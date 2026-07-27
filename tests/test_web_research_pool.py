from pathlib import Path
import tempfile
import unittest

from web.services.research_pool import (
    ResearchPoolMembershipStore,
    apply_research_pool_membership,
)


class ResearchPoolMembershipStoreTest(unittest.TestCase):
    def test_membership_overrides_are_persistent_and_default_to_catalog_state(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research-pool.db"
            store = ResearchPoolMembershipStore(database)

            self.assertTrue(store.resolve("AAA", default=True))
            self.assertFalse(store.resolve("BBB", default=False))

            store.set_membership("AAA", False)
            store.set_membership("BBB", True)

            reopened = ResearchPoolMembershipStore(database)
            self.assertFalse(reopened.resolve("AAA", default=True))
            self.assertTrue(reopened.resolve("BBB", default=False))

    def test_payload_keeps_catalog_rows_browseable_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchPoolMembershipStore(
                Path(directory) / "research-pool.db"
            )
            store.set_membership("RESEARCH", False)
            store.set_membership("ACTIVE", True)
            payload = {
                "tickers": [
                    {
                        "ticker": "RESEARCH",
                        "pool_membership": {
                            "active": False,
                            "research": True,
                        },
                    },
                    {
                        "ticker": "ACTIVE",
                        "pool_membership": {
                            "active": True,
                            "research": False,
                        },
                    },
                ],
                "pool_summary": {
                    "active_count": 1,
                    "research_count": 1,
                    "overlap_count": 0,
                },
            }

            result = apply_research_pool_membership(payload, store)

        by_ticker = {row["ticker"]: row for row in result["tickers"]}
        self.assertEqual(
            by_ticker["RESEARCH"]["pool_membership"],
            {
                "active": False,
                "research": False,
                "research_catalog": True,
            },
        )
        self.assertEqual(
            by_ticker["ACTIVE"]["pool_membership"],
            {
                "active": True,
                "research": True,
                "research_catalog": False,
            },
        )
        self.assertEqual(
            result["pool_summary"],
            {
                "active_count": 1,
                "research_count": 1,
                "overlap_count": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
