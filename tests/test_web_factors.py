import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from factors.compute import _atr, pivot_breakout
from scoring.engine import evaluate
from web.factors.base import FactorResult
from web.factors.builtin import (
    _legacy_inputs,
    build_chart_rows,
    build_default_registry,
)
from web.factors.registry import DuplicateFactorKey, FactorRegistry
from web.services.analysis import AnalysisContext


class ConstantFactor:
    key, label, group, direction = "constant", "Constant", "test", "higher"
    description, version = "fixture", "v1"
    methodology, overview = "Fixture values supplied by the test context.", True

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
        self.assertEqual(rows["EEE"][0].peer_count, 5)
        self.assertIsNone(rows["OTHER"][0].percentile)
        self.assertEqual(rows["OTHER"][0].peer_count, 1)

    def test_percentile_is_missing_with_fewer_than_five_same_date_peers(self):
        rows = self.registry.evaluate_universe(
            [context("AAA", 1), context("BBB", 2), context("CCC", 3), context("DDD", 4)]
        )

        self.assertTrue(all(row[0].percentile is None for row in rows.values()))
        self.assertTrue(all(row[0].peer_count == 4 for row in rows.values()))
        self.assertEqual(
            rows["AAA"][0].to_dict()["peer_count"],
            4,
        )

    def test_neutral_numeric_factor_never_evaluates_peers(self):
        calls = []

        class NeutralCountingFactor(ConstantFactor):
            key = "neutral_counting"
            direction = "neutral"

            def compute(self, factor_context):
                calls.append((self.key, factor_context.ticker))
                return factor_context.metadata["value"]

        class DirectionalCountingFactor(ConstantFactor):
            key = "directional_counting"

            def compute(self, factor_context):
                calls.append((self.key, factor_context.ticker))
                return factor_context.metadata["value"]

        registry = FactorRegistry(
            [NeutralCountingFactor(), DirectionalCountingFactor()]
        )
        registry.evaluate_selected_with_peers(
            context("AAA", 1),
            [context("BBB", 2), context("CCC", 3)],
        )

        self.assertEqual(
            [ticker for key, ticker in calls if key == "neutral_counting"],
            ["AAA"],
        )
        self.assertEqual(
            [ticker for key, ticker in calls if key == "directional_counting"],
            ["AAA", "BBB", "CCC"],
        )

    def test_peer_factor_cache_is_revision_scoped_and_bounded(self):
        calls = []

        class CountingFactor(ConstantFactor):
            def compute(self, factor_context):
                calls.append(factor_context.ticker)
                return factor_context.metadata["value"]

        registry = FactorRegistry(
            [CountingFactor()],
            max_peer_cache_size=2,
        )
        selected = context("AAA", 1)
        peers = [context("BBB", 2), context("CCC", 3)]

        first = registry.evaluate_selected_with_peers(
            selected,
            peers,
            cache_namespace=7,
        )
        second = registry.evaluate_selected_with_peers(
            selected,
            peers,
            cache_namespace=7,
        )

        self.assertEqual(calls.count("AAA"), 2)
        self.assertEqual(calls.count("BBB"), 1)
        self.assertEqual(calls.count("CCC"), 1)
        self.assertIsNot(first[0], second[0])
        self.assertLessEqual(registry.peer_cache_size, 2)

        registry.evaluate_selected_with_peers(
            selected,
            peers,
            cache_namespace=8,
        )

        self.assertEqual(calls.count("BBB"), 2)
        self.assertEqual(calls.count("CCC"), 2)
        self.assertLessEqual(registry.peer_cache_size, 2)

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
                "peer_count": None,
                "display_score": None,
                "observation_date": "2026-07-21",
                "missing": False,
                "missing_reason": None,
                "description": "fixture",
                "methodology": "Fixture values supplied by the test context.",
                "overview": True,
                "version": "v1",
            },
        )

    def test_result_preserves_legacy_optional_positional_arguments(self):
        result = FactorResult(
            "constant",
            "Constant",
            "test",
            "higher",
            2.5,
            "2.5",
            "2026-07-21",
            False,
            None,
            "fixture",
            "Fixture methodology.",
            True,
            "v1",
            0.75,
            12,
            75.0,
        )

        self.assertEqual(result.percentile, 0.75)
        self.assertEqual(result.peer_count, 12)
        self.assertEqual(result.display_score, 75.0)
        self.assertIsNone(result.window)
        self.assertFalse(result.i18n)


class BuiltinFactorTest(unittest.TestCase):
    def test_builtin_result_preserves_english_fields_and_adds_chinese_explanation(self):
        registry = build_default_registry()
        factor = next(
            factor for factor in registry.factors if factor.key == "close_vs_ema20_pct"
        )

        result = registry.evaluate_one(factor, context_from_history(price_history()))
        payload = result.to_dict()

        self.assertEqual(payload["label"], "Close vs EMA20")
        self.assertEqual(
            payload["description"],
            "Close relative to the point-in-time 20-session EMA.",
        )
        self.assertEqual(payload["direction"], "higher")
        self.assertEqual(
            set(payload["i18n"]["zh-CN"]),
            {"label", "description", "methodology", "window", "direction"},
        )
        self.assertEqual(payload["i18n"]["zh-CN"]["label"], "收盘价相对 EMA20")
        with self.assertRaises(TypeError):
            factor.i18n["zh-CN"]["label"] = "不可变"

    def test_every_builtin_factor_and_group_has_complete_chinese_metadata(self):
        registry = build_default_registry()
        required = {"label", "description", "methodology", "window", "direction"}

        for entity in (*registry.factors, *registry.groups):
            with self.subTest(entity=entity.key):
                self.assertEqual(set(entity.i18n["zh-CN"]), required)
                self.assertTrue(all(entity.i18n["zh-CN"].values()))
                if hasattr(entity, "to_dict"):
                    self.assertEqual(set(entity.to_dict()["i18n"]["zh-CN"]), required)

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
                "volume_ratio_change",
                "pivot_distance_change_pct",
                "crossed_ema20",
                "crossed_sma50",
                "ema20_cross",
                "sma50_cross",
                "prior_high_resistance",
                "prior_high_breakout_pct",
                "prior_high_breakout",
                "descending_trendline",
                "trendline_breakout",
                "trendline_high_1_date",
                "trendline_high_2_date",
                "latest_confirmed_high_date",
                "latest_confirmed_high_confirmed_date",
                "latest_confirmed_low_date",
                "latest_confirmed_low_price",
                "latest_confirmed_low_confirmed_date",
                "higher_low_confirmed",
                "higher_low_previous_date",
                "higher_low_previous_price",
                "higher_low_latest_date",
                "higher_low_latest_price",
                "higher_low_confirmation_date",
                "reversal_signal_count",
                "reversal_candidate",
                "early_reversal_score",
                "early_reversal_watch",
                "early_reversal_conditions",
                "early_prior_session_selloff",
                "early_current_price_acceptance",
                "early_descending_trendline_proximity",
                "early_current_volume_support",
                "near_resistance_lower",
                "near_resistance_upper",
                "near_resistance_mid",
                "near_resistance_distance_pct",
                "near_resistance_score",
                "near_resistance_sources",
                "far_resistance",
                "near_support_lower",
                "near_support_upper",
                "near_support_mid",
                "near_support_distance_pct",
                "near_support_score",
                "near_support_sources",
                "near_support_state",
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
        self.assertAlmostEqual(
            last["volume_ratio_change"],
            last["volume_ratio"] - rows[-2]["volume_ratio"],
        )
        self.assertAlmostEqual(
            last["pivot_distance_change_pct"],
            last["pivot_distance_pct"] - rows[-2]["pivot_distance_pct"],
        )
        self.assertIsInstance(last["crossed_ema20"], bool)
        self.assertIsInstance(last["crossed_sma50"], bool)
        self.assertIn(last["ema20_cross"], (None, "above", "below"))
        self.assertIn(last["sma50_cross"], (None, "above", "below"))
        self.assertIsInstance(last["prior_high_breakout"], bool)
        self.assertIsInstance(last["trendline_breakout"], bool)
        self.assertIsInstance(last["higher_low_confirmed"], bool)
        self.assertIsInstance(last["reversal_candidate"], bool)
        self.assertIn(last["reversal_signal_count"], (0, 1, 2, 3))
        self.assertIsInstance(last["early_reversal_score"], int)
        self.assertIn(last["early_reversal_score"], (0, 25, 50, 75, 100))
        self.assertIsInstance(last["early_reversal_watch"], bool)
        self.assertIsInstance(last["early_reversal_conditions"], list)
        self.assertIsInstance(last["near_resistance_sources"], list)
        self.assertIsInstance(last["near_support_sources"], list)
        self.assertIn(
            last["near_support_state"],
            {"above", "testing", "inside", "unavailable"},
        )
        for key in (
            "near_resistance_lower",
            "near_resistance_upper",
            "near_resistance_mid",
            "near_resistance_distance_pct",
            "near_resistance_score",
            "far_resistance",
        ):
            self.assertTrue(
                last[key] is None or isinstance(last[key], (int, float)),
                key,
            )
        for key in (
            "near_support_lower",
            "near_support_upper",
            "near_support_mid",
            "near_support_distance_pct",
            "near_support_score",
        ):
            self.assertTrue(
                last[key] is None or isinstance(last[key], (int, float)),
                key,
            )

    def test_directional_peer_factors_do_not_build_chart_rows(self):
        registry = build_default_registry()
        factors = {factor.key: factor for factor in registry.factors}
        history = price_history(260)
        ctx = context_from_history(history)
        asof = history.loc[history.index <= ctx.observation_date]
        close = asof["Close"].astype(float)
        volume = asof["Volume"].astype(float)
        expected = {
            "close_vs_ema20_pct": (
                close.iloc[-1]
                / close.ewm(span=20, adjust=False).mean().iloc[-1]
                - 1
            )
            * 100,
            "close_vs_sma50_pct": (
                close.iloc[-1] / close.rolling(50).mean().iloc[-1] - 1
            )
            * 100,
            "close_vs_sma200_pct": (
                close.iloc[-1] / close.rolling(200).mean().iloc[-1] - 1
            )
            * 100,
            "volume_ratio": (
                volume.iloc[-1] / volume.rolling(20).mean().iloc[-1]
            ),
        }

        with patch(
            "web.factors.builtin.build_chart_rows",
            side_effect=AssertionError("directional peer factor rebuilt the chart"),
        ):
            actual = {
                key: registry.evaluate_one(factors[key], ctx)
                for key in expected
            }

        for key, expected_value in expected.items():
            with self.subTest(factor=key):
                self.assertFalse(actual[key].missing)
                self.assertAlmostEqual(actual[key].raw_value, expected_value)

    def test_default_registry_groups_builtins_and_exposes_structure_rejections(self):
        registry = build_default_registry()
        self.assertEqual(
            {factor.group for factor in registry.factors},
            {"trend", "momentum", "structure", "volume", "risk", "legacy"},
        )
        by_key = {factor.key: factor for factor in registry.factors}
        for key in (
            "prior_high_breakout",
            "trendline_breakout",
            "higher_low_confirmed",
            "reversal_signal_count",
            "early_reversal_score",
        ):
            self.assertIn(key, by_key)
        ctx = context_from_history(price_history(40))

        strict_vcp = registry.evaluate_one(by_key["strict_vcp"], ctx)
        platform = registry.evaluate_one(by_key["tight_platform"], ctx)

        self.assertEqual(
            strict_vcp.raw_value["reject_reason"],
            "insufficient_history",
        )
        self.assertEqual(
            strict_vcp.raw_value["rejection_reason_code"],
            "insufficient_history",
        )
        self.assertEqual(platform.raw_value["reason"], "历史不足")
        early = registry.evaluate_one(by_key["early_reversal_score"], ctx)
        self.assertEqual(early.raw_value, build_chart_rows(ctx)[-1]["early_reversal_score"])
        self.assertEqual(early.formatted, f"{early.raw_value}/100")
        self.assertFalse(by_key["early_reversal_score"].percentile_eligible)

    def test_legacy_score_is_explicitly_not_predictive(self):
        registry = build_default_registry()
        factor = next(factor for factor in registry.factors if factor.key == "legacy_score")

        self.assertEqual(factor.label, "Traditional rules score")
        self.assertIn("Not validated for prediction", factor.description)

    def test_every_builtin_exposes_methodology_and_overview_metadata(self):
        registry = build_default_registry()

        for factor in registry.factors:
            with self.subTest(factor=factor.key):
                self.assertTrue(getattr(factor, "methodology", None))
                self.assertIsInstance(getattr(factor, "overview", None), bool)

        result = registry.evaluate_one(registry.factors[0], context_from_history(price_history()))
        self.assertTrue(result.to_dict().get("methodology"))
        self.assertIsInstance(result.to_dict().get("overview"), bool)

    def test_legacy_score_preserves_canonical_non_price_only_evaluation(self):
        registry = build_default_registry()
        factor = next(factor for factor in registry.factors if factor.key == "legacy_score")
        ctx = context_from_history(price_history())
        inputs = _legacy_inputs(ctx)
        expected = evaluate(inputs, market_ok=True, price_only=False).total

        with patch("web.factors.builtin.market_uptrend", return_value=True):
            actual = registry.evaluate_one(factor, ctx)

        self.assertEqual(actual.raw_value, expected)

    def test_chart_atr_matches_canonical_minimum_history_boundary(self):
        history = price_history(25)
        rows = build_chart_rows(context_from_history(history))

        self.assertIsNone(rows[19]["atr20"])
        for position in range(20, len(rows)):
            self.assertEqual(
                rows[position]["atr20"], _atr(history.iloc[: position + 1], 20)
            )

    def test_chart_pivot_matches_canonical_base_lookback_boundary(self):
        history = price_history(25)
        rows = build_chart_rows(context_from_history(history))

        self.assertIsNone(rows[20]["pivot"])
        for position in range(21, len(rows)):
            self.assertEqual(
                rows[position]["pivot"],
                pivot_breakout(history.iloc[: position + 1])["pivot"],
            )

    def test_shared_adapter_results_are_cached_on_context(self):
        registry = build_default_registry()
        factors = {factor.key: factor for factor in registry.factors}
        ctx = context_from_history(price_history())

        original = __import__(
            "research.vcp",
            fromlist=["detect_vcp"],
        ).detect_vcp
        with patch("web.factors.builtin.detect_vcp", wraps=original) as calculate:
            registry.evaluate_one(factors["strict_vcp"], ctx)
            registry.evaluate_one(factors["legacy_score"], ctx)

        self.assertEqual(calculate.call_count, 1)
