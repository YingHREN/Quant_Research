import unittest

import pandas as pd

from web.factors.registry import DuplicateFactorKey, FactorRegistry
from web.services.analysis import AnalysisContext


class ConstantFactor:
    key, label, group, direction = "constant", "Constant", "test", "higher"
    description, version = "fixture", "v1"

    def compute(self, context):
        return context.metadata["value"]

    def format(self, value):
        return f"{value:.1f}"


class BrokenFactor(ConstantFactor):
    key = "broken"

    def compute(self, context):
        raise RuntimeError("secret /tmp/path")


def context(ticker="AAA", value=2.5, observation_date="2026-07-21"):
    history = pd.DataFrame(index=pd.DatetimeIndex([observation_date]))
    return AnalysisContext(
        ticker=ticker,
        observation_date=pd.Timestamp(observation_date),
        history=history,
        benchmark_history=history.copy(),
        metadata={"value": value},
    )


class FactorRegistryTest(unittest.TestCase):
    def setUp(self):
        self.registry = FactorRegistry([ConstantFactor()])

    def test_duplicate_key_is_rejected(self):
        with self.assertRaises(DuplicateFactorKey):
            self.registry.register(ConstantFactor())

    def test_failure_is_isolated_and_message_is_safe(self):
        result = FactorRegistry([BrokenFactor()]).evaluate_one(BrokenFactor(), context())

        self.assertTrue(result.missing)
        self.assertEqual(result.missing_reason, "factor_error")
        self.assertNotIn("/tmp/path", str(result.to_dict()))

    def test_percentile_uses_same_observation_date_only(self):
        rows = self.registry.evaluate_universe(
            [
                context("AAA", 1),
                context("BBB", 2),
                context("CCC", 3),
                context("DDD", 4),
                context("EEE", 5),
                context("OTHER", 100, "2026-07-20"),
            ]
        )

        self.assertEqual(rows["EEE"][0].percentile, 1.0)
        self.assertEqual(rows["EEE"][0].display_score, 100.0)
        self.assertIsNone(rows["OTHER"][0].percentile)

    def test_percentile_is_missing_with_fewer_than_five_same_date_peers(self):
        rows = self.registry.evaluate_universe(
            [context("AAA", 1), context("BBB", 2), context("CCC", 3), context("DDD", 4)]
        )

        self.assertTrue(all(row[0].percentile is None for row in rows.values()))

    def test_result_json_shape_is_safe_and_stable(self):
        result = self.registry.evaluate_one(ConstantFactor(), context(value=2.5))

        self.assertEqual(
            result.to_dict(),
            {
                "key": "constant",
                "label": "Constant",
                "group": "test",
                "direction": "higher",
                "raw_value": 2.5,
                "formatted": "2.5",
                "percentile": None,
                "display_score": None,
                "observation_date": "2026-07-21",
                "missing": False,
                "missing_reason": None,
                "description": "fixture",
                "version": "v1",
            },
        )
