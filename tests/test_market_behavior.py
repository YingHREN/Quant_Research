from __future__ import annotations

from datetime import date, timedelta
import unittest

from data.market_behavior import classify_market_behavior


def prices_from_returns(returns, start=date(2025, 1, 1)):
    price = 100.0
    rows = [(start.isoformat(), price)]
    for offset, value in enumerate(returns, 1):
        price *= 1.0 + value
        rows.append(((start + timedelta(days=offset)).isoformat(), price))
    return rows


class MarketBehaviorTest(unittest.TestCase):
    def histories(self, periods=170):
        market = [0.001 if index % 2 == 0 else -0.0007 for index in range(periods)]
        tech_residual = [
            (0.008 if index % 5 in (0, 1) else -0.004) for index in range(periods)
        ]
        energy_residual = [
            (0.007 if index % 7 in (0, 3) else -0.002) for index in range(periods)
        ]
        stock = [
            market[index] * 1.1 + tech_residual[index] * 1.25
            for index in range(periods)
        ]
        tech = [
            market[index] * 0.9 + tech_residual[index]
            for index in range(periods)
        ]
        energy = [
            market[index] * 0.8 + energy_residual[index]
            for index in range(periods)
        ]
        return {
            "AAA": prices_from_returns(stock),
            "SPY": prices_from_returns(market),
            "XLK": prices_from_returns(tech),
            "XLE": prices_from_returns(energy),
        }

    def test_residual_behavior_classification_identifies_sector_and_conflict(self):
        result = classify_market_behavior(
            self.histories(),
            "AAA",
            {"technology": "XLK", "energy": "XLE"},
            sec_sector="industrials",
            asof="2025-06-19",
            min_observations=126,
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.sector_key, "technology")
        self.assertEqual(result.benchmark_ticker, "XLK")
        self.assertGreater(result.residual_correlation, 0.95)
        self.assertGreater(result.residual_beta, 1.0)
        self.assertFalse(result.agrees_with_sec)
        self.assertIn("industrials", result.conflict_reason)
        self.assertEqual(result.rule_version, "market_behavior_v1")

    def test_future_prices_do_not_change_point_in_time_result(self):
        histories = self.histories()
        asof = histories["AAA"][150][0]
        before = classify_market_behavior(
            histories,
            "AAA",
            {"technology": "XLK", "energy": "XLE"},
            sec_sector="technology",
            asof=asof,
            min_observations=126,
        )
        for ticker in histories:
            last_date = date.fromisoformat(histories[ticker][-1][0])
            last_price = histories[ticker][-1][1]
            histories[ticker].append(
                ((last_date + timedelta(days=1)).isoformat(), last_price * 5)
            )
        after = classify_market_behavior(
            histories,
            "AAA",
            {"technology": "XLK", "energy": "XLE"},
            sec_sector="technology",
            asof=asof,
            min_observations=126,
        )

        self.assertEqual(before, after)
        self.assertTrue(after.agrees_with_sec)
        self.assertEqual(after.conflict_reason, "与 SEC 基本面板块一致")

    def test_insufficient_common_history_returns_none(self):
        result = classify_market_behavior(
            self.histories(periods=30),
            "AAA",
            {"technology": "XLK", "energy": "XLE"},
            sec_sector="technology",
            asof="2026-01-01",
            min_observations=126,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
