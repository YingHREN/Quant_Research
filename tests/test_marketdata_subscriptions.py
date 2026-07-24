import unittest

from marketdata.subscriptions import build_pool, plan_change


class DynamicSubscriptionPoolTest(unittest.TestCase):
    def test_priority_deduplication_and_limit(self):
        peers = [f"P{number}" for number in range(1, 25)]
        candidates = ["AMD", "P1"] + [f"C{number}" for number in range(1, 20)]
        pool = build_pool("amd", peers, candidates, limit=30)
        self.assertEqual(pool[:4], ("SPY", "QQQ", "SOXX", "AMD"))
        self.assertEqual(len(pool), 30)
        self.assertEqual(len(set(pool)), 30)
        self.assertEqual(pool[4:20], tuple(f"P{number}" for number in range(1, 17)))

    def test_change_subscribes_new_symbols_before_unsubscribing_old(self):
        change = plan_change(("SPY", "QQQ", "SOXX", "AMD"),
                             ("SPY", "QQQ", "SOXX", "NVDA"))
        self.assertEqual(change.subscribe, ("NVDA",))
        self.assertEqual(change.unsubscribe, ("AMD",))


if __name__ == "__main__":
    unittest.main()
