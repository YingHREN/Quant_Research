from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.hierarchical_direction import (
    HALF_LIFE_BY_HORIZON,
    freeze_behavior_groups,
    recency_class_weights,
)


class RecencyWeightTest(unittest.TestCase):
    def test_half_life_ratio_is_exact_and_weights_decay_monotonically(self):
        dates = pd.bdate_range("2025-01-02", periods=127)
        labels = np.repeat("up", len(dates))

        weights, diagnostics = recency_class_weights(
            dates,
            labels,
            5,
            minimum_effective_samples=1,
            minimum_class_effective_samples=1,
        )

        self.assertEqual(HALF_LIFE_BY_HORIZON[5], 126)
        self.assertEqual(diagnostics["status"], "available")
        self.assertAlmostEqual(weights[0] / weights[-1], 0.5, places=12)
        self.assertTrue(np.all(np.diff(weights) > 0.0))
        self.assertAlmostEqual(float(weights.mean()), 1.0, places=12)

    def test_time_weighted_class_balancing_equalizes_class_weight_sums(self):
        dates = pd.bdate_range("2024-01-02", periods=400)
        labels = np.asarray(
            ["down"] * 40 + ["neutral"] * 120 + ["up"] * 240,
            dtype=object,
        )

        weights, diagnostics = recency_class_weights(
            dates,
            labels,
            20,
            minimum_effective_samples=1,
            minimum_class_effective_samples=1,
        )

        sums = {
            label: float(weights[labels == label].sum())
            for label in ("down", "neutral", "up")
        }
        self.assertAlmostEqual(sums["down"], sums["neutral"], places=10)
        self.assertAlmostEqual(sums["neutral"], sums["up"], places=10)
        self.assertEqual(
            set(diagnostics["class_effective_sample_size"]),
            {"down", "neutral", "up"},
        )
        self.assertGreater(diagnostics["effective_sample_size"], 0.0)

    def test_inputs_are_immutable_and_invalid_labels_fail_closed(self):
        dates = pd.bdate_range("2025-01-02", periods=12)
        labels = np.asarray(["up", "neutral", "down"] * 4, dtype=object)
        labels_before = labels.copy()
        dates_before = dates.copy()

        recency_class_weights(
            dates,
            labels,
            60,
            minimum_effective_samples=1,
            minimum_class_effective_samples=1,
        )

        np.testing.assert_array_equal(labels, labels_before)
        self.assertTrue(dates.equals(dates_before))
        with self.assertRaisesRegex(ValueError, "direction labels"):
            recency_class_weights(
                dates,
                np.asarray(["unknown"] * len(dates), dtype=object),
                60,
                minimum_effective_samples=1,
                minimum_class_effective_samples=1,
            )
        with self.assertRaisesRegex(ValueError, "supported"):
            recency_class_weights(
                dates,
                labels,
                7,
                minimum_effective_samples=1,
                minimum_class_effective_samples=1,
            )

    def test_effective_sample_gates_return_typed_unavailable_diagnostics(self):
        dates = pd.bdate_range("2025-01-02", periods=120)
        labels = np.asarray(["up", "neutral", "down"] * 40, dtype=object)

        weights, diagnostics = recency_class_weights(dates, labels, 5)

        self.assertIsNone(weights)
        self.assertEqual(diagnostics["status"], "unavailable")
        self.assertEqual(
            diagnostics["reason"],
            "insufficient_effective_samples",
        )

        imbalanced = np.asarray(["down"] + ["up"] * 119, dtype=object)
        weights, diagnostics = recency_class_weights(
            dates,
            imbalanced,
            5,
            minimum_effective_samples=1,
        )
        self.assertIsNone(weights)
        self.assertEqual(
            diagnostics["reason"],
            "insufficient_class_effective_samples",
        )


def _price_frame(returns, start="2025-01-02"):
    prices = [100.0]
    for value in returns:
        prices.append(prices[-1] * (1.0 + value))
    dates = pd.bdate_range(start, periods=len(prices))
    return pd.DataFrame(
        {
            "Open": prices,
            "High": np.asarray(prices) + 1.0,
            "Low": np.asarray(prices) - 1.0,
            "Close": prices,
            "Adj Close": prices,
            "Volume": np.full(len(prices), 1_000_000.0),
        },
        index=dates,
    )


def _behavior_histories(periods=180):
    market = np.asarray(
        [0.001 if index % 2 == 0 else -0.0007 for index in range(periods)]
    )
    technology_residual = np.asarray(
        [0.008 if index % 5 in (0, 1) else -0.004 for index in range(periods)]
    )
    energy_residual = np.asarray(
        [0.007 if index % 7 in (0, 3) else -0.002 for index in range(periods)]
    )
    return {
        "ZZZ": _price_frame(market * 1.1 + technology_residual * 1.25),
        "SPY": _price_frame(market),
        "XLK": _price_frame(market * 0.9 + technology_residual),
        "XLE": _price_frame(market * 0.8 + energy_residual),
    }


class FoldFrozenGroupTest(unittest.TestCase):
    def test_behavior_groups_are_deterministic_and_cutoff_causal(self):
        histories = _behavior_histories()
        cutoff = histories["ZZZ"].index[160]
        before = {
            ticker: frame.copy(deep=True)
            for ticker, frame in histories.items()
        }

        groups, diagnostics = freeze_behavior_groups(
            histories,
            ("ZZZ",),
            cutoff,
            sector_etfs={"technology": "XLK", "energy": "XLE"},
        )

        self.assertEqual(groups, {"ZZZ": "technology"})
        self.assertEqual(diagnostics["classified_count"], 1)
        self.assertEqual(diagnostics["unavailable_count"], 0)
        self.assertEqual(diagnostics["rule_version"], "market_behavior_v1")
        for ticker in histories:
            pd.testing.assert_frame_equal(histories[ticker], before[ticker])

        for ticker, frame in histories.items():
            future_date = frame.index[-1] + pd.offsets.BDay()
            changed = frame.iloc[-1].copy()
            changed.loc[["Close", "Adj Close"]] *= 20.0
            histories[ticker].loc[future_date] = changed
        after_groups, after_diagnostics = freeze_behavior_groups(
            histories,
            ("ZZZ",),
            cutoff,
            sector_etfs={"technology": "XLK", "energy": "XLE"},
        )
        self.assertEqual(after_groups, groups)
        self.assertEqual(
            after_diagnostics["sector_counts"],
            diagnostics["sector_counts"],
        )

    def test_missing_or_short_reference_history_is_explicitly_unavailable(self):
        histories = _behavior_histories(periods=40)

        groups, diagnostics = freeze_behavior_groups(
            histories,
            ("ZZZ", "AAA"),
            histories["ZZZ"].index[-1],
            sector_etfs={"technology": "XLK"},
        )

        self.assertEqual(list(groups), ["AAA", "ZZZ"])
        self.assertEqual(groups, {"AAA": None, "ZZZ": None})
        self.assertEqual(diagnostics["classified_count"], 0)
        self.assertEqual(diagnostics["unavailable_count"], 2)

        without_spy = dict(histories)
        del without_spy["SPY"]
        groups, diagnostics = freeze_behavior_groups(
            without_spy,
            ("ZZZ",),
            histories["ZZZ"].index[-1],
            sector_etfs={"technology": "XLK"},
        )
        self.assertEqual(groups["ZZZ"], None)
        self.assertEqual(diagnostics["unavailable_count"], 1)


if __name__ == "__main__":
    unittest.main()
