from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.hierarchical_direction import (
    HALF_LIFE_BY_HORIZON,
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


if __name__ == "__main__":
    unittest.main()
