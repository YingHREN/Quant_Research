from __future__ import annotations

import unittest

import pandas as pd

from research.run_historical_demand_support_study import promotion_decision


def metric_rows(*, adverse_delta: float = 0.0, increment: float = 0.02):
    rows = []
    for fold in range(1, 6):
        for group in ("semiconductor", "software"):
            rows.extend(
                (
                    {
                        "variant": "baseline",
                        "fold": fold,
                        "group": group,
                        "support_hold_rate": 0.50,
                        "max_adverse_excursion": -0.08,
                        "sample_count": 40,
                    },
                    {
                        "variant": "baseline_plus_historical_demand",
                        "fold": fold,
                        "group": group,
                        "support_hold_rate": 0.50 + increment,
                        "max_adverse_excursion": -0.08 + adverse_delta,
                        "sample_count": 40,
                    },
                )
            )
    return pd.DataFrame(rows)


class HistoricalDemandSupportStudyTest(unittest.TestCase):
    def test_promotion_requires_stable_fold_group_and_ablation_gains(self):
        decision = promotion_decision(
            metric_rows(),
            causal_audit_passed=True,
        )

        self.assertTrue(decision["eligible"])
        self.assertEqual(decision["stable_fold_wins"], 5)
        self.assertEqual(decision["improved_group_count"], 2)

    def test_promotion_fails_closed_when_drawdown_is_worse(self):
        decision = promotion_decision(
            metric_rows(adverse_delta=-0.01),
            causal_audit_passed=True,
        )

        self.assertFalse(decision["eligible"])
        self.assertIn("max_adverse_excursion_worse", decision["reasons"])

    def test_promotion_fails_closed_when_audit_is_missing(self):
        decision = promotion_decision(
            metric_rows(),
            causal_audit_passed=False,
        )

        self.assertFalse(decision["eligible"])
        self.assertIn("causal_audit_failed", decision["reasons"])


if __name__ == "__main__":
    unittest.main()
