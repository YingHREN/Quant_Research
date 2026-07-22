import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from web.factors.builtin import build_default_registry, build_chart_rows
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


def price_history(periods=260):
    dates = pd.bdate_range(end="2026-07-22", periods=periods)
    close = pd.Series(np.linspace(80.0, 120.0, periods), index=dates)
    close.iloc[-2:] = [110.0, 150.0]
    volume = pd.Series(np.linspace(1_000_000, 1_500_000, periods), index=dates)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": volume,
        },
        index=dates,
    )


def context_from_history(history):
    return AnalysisContext(
        ticker="AAA",
        observation_date=pd.Timestamp("2026-07-21"),
        history=history,
        benchmark_history=history.copy(),
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


class BuiltinFactorTest(unittest.TestCase):
    def test_chart_rows_include_ohlcv_indicators_and_prior_changes(self):
        rows = build_chart_rows(context_from_history(price_history(260)))

        last = rows[-1]
        self.assertEqual(
            set(last),
            {
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "daily_return",
                "true_range_pct",
                "volume_change",
                "volume_ma20",
                "volume_ratio",
                "ema20",
                "sma50",
                "sma200",
                "atr20",
                "pivot",
                "pivot_distance_pct",
                "crossed_ema20",
                "crossed_sma50",
            },
        )
        self.assertEqual(last["time"], "2026-07-21")
        self.assertNotIn("2026-07-22", [row["time"] for row in rows])
        self.assertAlmostEqual(last["daily_return"], 110.0 / rows[-2]["close"] - 1)
        self.assertAlmostEqual(
            last["volume_change"], 1_498_069.498069498 / rows[-2]["volume"] - 1
        )
        self.assertAlmostEqual(last["volume_ratio"], last["volume"] / last["volume_ma20"])
        self.assertAlmostEqual(last["pivot_distance_pct"], (110.0 / last["pivot"] - 1) * 100)
        self.assertIsInstance(last["crossed_ema20"], bool)
        self.assertIsInstance(last["crossed_sma50"], bool)

    def test_default_registry_groups_builtins_and_exposes_structure_rejections(self):
        registry = build_default_registry()
        self.assertEqual(
            {factor.group for factor in registry.factors},
            {"trend", "momentum", "structure", "volume", "risk", "legacy"},
        )
        by_key = {factor.key: factor for factor in registry.factors}
        ctx = context_from_history(price_history(40))

        strict_vcp = registry.evaluate_one(by_key["strict_vcp"], ctx)
        platform = registry.evaluate_one(by_key["tight_platform"], ctx)

        self.assertEqual(strict_vcp.raw_value["reject_reason"], "历史不足")
        self.assertEqual(platform.raw_value["reason"], "历史不足")

    def test_legacy_score_is_explicitly_not_predictive(self):
        registry = build_default_registry()
        factor = next(factor for factor in registry.factors if factor.key == "legacy_score")

        self.assertEqual(factor.label, "Traditional rules score")
        self.assertIn("Not validated for prediction", factor.description)

    def test_shared_adapter_results_are_cached_on_context(self):
        registry = build_default_registry()
        factors = {factor.key: factor for factor in registry.factors}
        ctx = context_from_history(price_history())

        original = __import__("factors.compute", fromlist=["vcp_analysis"]).vcp_analysis
        with patch("web.factors.builtin.vcp_analysis", wraps=original) as calculate:
            registry.evaluate_one(factors["strict_vcp"], ctx)
            registry.evaluate_one(factors["legacy_score"], ctx)

        self.assertEqual(calculate.call_count, 1)
