import unittest
from unittest import mock
from time import perf_counter

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from web.forecasts import decision as forecast_decision_module
from web.forecasts.base import ForecastResult
from web.forecasts.decision import (
    ForecastDecision,
    ForecastDecisionPolicy,
    build_forecast_risk_context,
)
from web.market_groups import market_group, modeled_market_groups


def available_forecast(**changes):
    values = {
        "ticker": "AAA",
        "asof_date": "2026-07-01",
        "horizon_sessions": 5,
        "direction": "up",
        "raw_direction": "up",
        "predicted_return": 0.08,
        "up_probability": None,
        "confidence_status": "uncalibrated",
        "confidence_reason": "insufficient_calibration_samples",
        "training_sample_count": 500,
        "training_cutoff": "2026-06-30",
        "model_key": "ridge_direction_v1",
        "model_version": "v4",
    }
    values.update(changes)
    return ForecastResult(**values)


def high_risk_decision(**changes):
    values = {
        "final_direction": "neutral",
        "risk_state": "high",
        "action": "downgrade_to_neutral",
        "reasons": ("persistent_bearish_risk",),
        "policy_key": "forecast_decision_policy",
        "policy_version": "v1",
        "persistent_risk_score": 34.0,
        "persistent_risk_raw_score": 15.0,
        "persistent_risk_state": "fading",
        "persistent_risk_age_sessions": 1,
        "immediate_risk_score": 57.0,
        "persistent_risk_sources": ("individual", "group"),
        "individual_risk_score": 34.0,
        "group_risk_score": 41.0,
        "slow_decline_risk_score": 12.0,
        "high_level_distribution_score": 0.0,
        "high_level_distribution_raw_score": 0.0,
        "high_level_distribution_state": "inactive",
        "high_level_distribution_raw_state": "inactive",
        "high_level_distribution_age_sessions": 0,
        "high_level_context_score": 0.0,
        "distribution_pressure_score": 0.0,
        "structure_damage_score": 0.0,
        "high_level_distribution_conditions": (),
        "distribution_count_5": 1,
        "distribution_count_10": 2,
        "distribution_count_20": 3,
        "churning_count_10": 2,
        "churning_cluster": True,
        "climax_run_score": 80.0,
        "climax_run_candidate": True,
        "climax_run_conditions": ("climax_acceleration",),
        "top_risk_recovery": True,
        "top_risk_recovery_conditions": ("strong_reclaim",),
    }
    values.update(changes)
    return ForecastDecision(**values)


def rising(periods=80, slope=0.2, end="2026-07-23"):
    index = pd.bdate_range(end=end, periods=periods)
    close = 100.0 + np.arange(periods) * slope
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(periods, 1_000_000.0),
        },
        index=index,
    )


class ForecastDecisionContractTest(unittest.TestCase):
    def test_attaches_decision_without_mutating_raw_forecast(self):
        forecast = available_forecast()
        decision = high_risk_decision()

        adjusted = forecast.with_decision(decision)

        self.assertEqual(forecast.direction, "up")
        self.assertEqual(adjusted.direction, "neutral")
        self.assertEqual(adjusted.raw_direction, "up")
        self.assertEqual(adjusted.predicted_return, forecast.predicted_return)
        self.assertEqual(adjusted.decision, decision)
        self.assertEqual(
            adjusted.to_dict()["decision"],
            {
                "final_direction": "neutral",
                "risk_state": "high",
                "action": "downgrade_to_neutral",
                "reasons": ["persistent_bearish_risk"],
                "policy_key": "forecast_decision_policy",
                "policy_version": "v1",
                "persistent_risk_score": 34.0,
                "persistent_risk_raw_score": 15.0,
                "persistent_risk_state": "fading",
                "persistent_risk_age_sessions": 1,
                "immediate_risk_score": 57.0,
                "persistent_risk_sources": ["individual", "group"],
                "individual_risk_score": 34.0,
                "group_risk_score": 41.0,
                "slow_decline_risk_score": 12.0,
                "high_level_distribution_score": 0.0,
                "high_level_distribution_raw_score": 0.0,
                "high_level_distribution_state": "inactive",
                "high_level_distribution_raw_state": "inactive",
                "high_level_distribution_age_sessions": 0,
                "high_level_context_score": 0.0,
                "distribution_pressure_score": 0.0,
                "structure_damage_score": 0.0,
                "high_level_distribution_conditions": [],
                "distribution_count_5": 1,
                "distribution_count_10": 2,
                "distribution_count_20": 3,
                "churning_count_10": 2,
                "churning_cluster": True,
                "climax_run_score": 80.0,
                "climax_run_candidate": True,
                "climax_run_conditions": ["climax_acceleration"],
                "top_risk_recovery": True,
                "top_risk_recovery_conditions": ["strong_reclaim"],
            },
        )

    def test_rejects_invalid_decision_fields(self):
        invalid = (
            {"final_direction": "sideways"},
            {"risk_state": "danger"},
            {"action": "silently_replace"},
            {"reasons": ("",)},
            {"policy_key": ""},
            {"policy_version": ""},
            {"persistent_risk_score": 101.0},
            {"persistent_risk_raw_score": -1.0},
            {"persistent_risk_state": "unknown"},
            {"persistent_risk_age_sessions": -1},
            {"immediate_risk_score": 101.0},
            {"persistent_risk_sources": ("unknown",)},
            {"group_risk_score": 101.0},
        )

        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(
                (TypeError, ValueError)
            ):
                high_risk_decision(**changes)

    def test_rejects_decision_for_unavailable_forecast(self):
        forecast = available_forecast(
            direction="unavailable",
            raw_direction="unavailable",
            predicted_return=None,
            confidence_status="unavailable",
            confidence_reason=None,
            training_sample_count=0,
            training_cutoff=None,
            unavailable_reason="insufficient_history",
        )

        with self.assertRaises(ValueError):
            forecast.with_decision(high_risk_decision())

    def test_rejects_mismatched_stored_decision(self):
        with self.assertRaises(ValueError):
            available_forecast(direction="up", decision=high_risk_decision())


class ForecastRiskContextTest(unittest.TestCase):
    def histories(self):
        return {
            "QQQ": rising(),
            "SOXX": rising(slope=0.3),
            "SMH": rising(slope=0.3),
            "MU": rising(slope=0.4),
            "AAPL": rising(slope=0.1),
        }

    def test_builds_only_explicit_group_risk_rows(self):
        context = build_forecast_risk_context(self.histories())

        self.assertEqual(
            context.index.names,
            ["ticker", "observation_date"],
        )
        self.assertEqual(
            list(context.columns),
            [
                "persistent_risk_raw_score",
                "persistent_risk_score",
                "persistent_risk_state",
                "persistent_risk_age_sessions",
                "persistent_risk_sources",
                "individual_risk_score",
                "sector_group_key",
                "sector_risk_score",
                "theme_group_key",
                "theme_risk_score",
                "group_risk_score",
                "slow_decline_risk_score",
                "high_level_distribution_score",
                "high_level_distribution_raw_score",
                "high_level_distribution_state",
                "high_level_distribution_raw_state",
                "high_level_distribution_age_sessions",
                "high_level_context_score",
                "distribution_pressure_score",
                "structure_damage_score",
                "high_level_distribution_conditions",
                "distribution_count_5",
                "distribution_count_10",
                "distribution_count_20",
                "churning_count_10",
                "churning_cluster",
                "climax_run_score",
                "climax_run_candidate",
                "climax_run_conditions",
                "top_risk_recovery",
                "top_risk_recovery_conditions",
            ],
        )
        self.assertIn("MU", context.index.get_level_values("ticker"))
        self.assertNotIn("AAPL", context.index.get_level_values("ticker"))
        available = context["persistent_risk_score"].notna()
        self.assertTrue(
            (
                context.loc[available, "persistent_risk_score"]
                >= context.loc[available, "persistent_risk_raw_score"]
            ).all()
        )

    def test_future_append_does_not_change_earlier_context(self):
        histories = self.histories()
        before = build_forecast_risk_context(histories)
        extended = {}
        for ticker, history in histories.items():
            future = rising(periods=2, slope=-30.0, end="2026-07-27")
            extended[ticker] = pd.concat(
                [history, future.loc[future.index > history.index[-1]]]
            )

        after = build_forecast_risk_context(extended)

        assert_frame_equal(after.loc[before.index], before)

    def test_broad_semiconductor_stress_is_a_named_risk_source(self):
        periods = 100
        dates = pd.bdate_range(end="2026-07-23", periods=periods)
        histories = {
            "QQQ": rising(periods=periods, slope=0.2),
            "SOXX": rising(periods=periods, slope=0.25),
            "SMH": rising(periods=periods, slope=0.25),
        }
        for ticker in ("MU", "AMD", "NVDA"):
            close = 100.0 + np.arange(periods) * 0.2
            close[-5:] = close[-6] * np.array([0.97, 0.94, 0.90, 0.87, 0.84])
            volume = np.full(periods, 1_000_000.0)
            volume[-5:] = 2_500_000.0
            histories[ticker] = pd.DataFrame(
                {
                    "Open": close * 1.01,
                    "High": close * 1.02,
                    "Low": close * 0.98,
                    "Close": close,
                    "Volume": volume,
                },
                index=dates,
            )

        context = build_forecast_risk_context(histories)
        latest = context.loc[("MU", dates[-1])]

        self.assertGreaterEqual(latest["group_risk_score"], 60.0)
        self.assertIn("group", latest["persistent_risk_sources"])

    def test_software_erosion_is_a_named_slow_decline_source(self):
        periods = 140
        dates = pd.bdate_range(end="2026-07-23", periods=periods)

        def trend(start, end):
            close = np.linspace(start, end, periods)
            return pd.DataFrame(
                {
                    "Open": close * 1.002,
                    "High": close * 1.008,
                    "Low": close * 0.992,
                    "Close": close,
                    "Volume": np.full(periods, 1_000_000.0),
                },
                index=dates,
            )

        context = build_forecast_risk_context(
            {
                "QQQ": trend(100.0, 135.0),
                "IGV": trend(100.0, 96.0),
                "XSW": trend(100.0, 94.0),
                "ADBE": trend(150.0, 95.0),
            }
        )
        latest = context.loc[("ADBE", dates[-1])]

        self.assertGreaterEqual(latest["slow_decline_risk_score"], 70.0)
        self.assertIn("slow_decline", latest["persistent_risk_sources"])

    def test_duplicate_group_membership_is_rejected(self):
        semiconductor = market_group("semiconductor")

        with mock.patch(
            "web.forecasts.decision.modeled_market_groups",
            return_value=(semiconductor, semiconductor),
        ), self.assertRaisesRegex(ValueError, "duplicate"):
            build_forecast_risk_context(self.histories())

    def test_modeled_groups_exclude_proxy_only_sectors(self):
        groups = modeled_market_groups()

        self.assertEqual(
            tuple(group.key for group in groups),
            ("semiconductor", "software"),
        )

    def test_assignment_driven_context_keeps_sector_and_theme_evidence(self):
        assignments = {
            "SNDK": {
                "state": "assigned",
                "ticker": "SNDK",
                "sector_key": "technology",
                "sector_benchmark": "XLK",
                "theme_keys": ["semiconductor"],
                "theme_benchmarks": {
                    "semiconductor": ["SOXX", "SMH"],
                },
                "primary_model_group": "semiconductor",
            },
            "AMD": {
                "state": "assigned",
                "ticker": "AMD",
                "sector_key": "technology",
                "sector_benchmark": "XLK",
                "theme_keys": ["semiconductor"],
                "theme_benchmarks": {
                    "semiconductor": ["SOXX", "SMH"],
                },
                "primary_model_group": "semiconductor",
            },
            "NVDA": {
                "state": "assigned",
                "ticker": "NVDA",
                "sector_key": "technology",
                "sector_benchmark": "XLK",
                "theme_keys": ["semiconductor"],
                "theme_benchmarks": {
                    "semiconductor": ["SOXX", "SMH"],
                },
                "primary_model_group": "semiconductor",
            },
        }
        histories = {
            "QQQ": rising(),
            "XLK": rising(slope=0.2),
            "SOXX": rising(slope=0.3),
            "SMH": rising(slope=0.3),
            "SNDK": rising(slope=0.4),
            "AMD": rising(slope=0.35),
            "NVDA": rising(slope=0.45),
        }

        context = build_forecast_risk_context(histories, assignments)
        sndk = context.xs("SNDK", level="ticker")

        self.assertFalse(sndk.empty)
        self.assertTrue(sndk["high_level_distribution_state"].notna().all())
        self.assertEqual(set(sndk["sector_group_key"]), {"technology"})
        self.assertEqual(set(sndk["theme_group_key"]), {"semiconductor"})
        self.assertTrue(sndk["sector_risk_score"].notna().any())
        self.assertTrue(sndk["theme_risk_score"].notna().any())

    def test_effective_dated_membership_changes_within_one_context_build(self):
        assignments = {
            "SNDK": [
                {
                    "state": "assigned",
                    "ticker": "SNDK",
                    "effective_from": "2026-01-01",
                    "effective_to": "2026-07-01",
                    "sector_key": "technology",
                    "sector_benchmark": "XLK",
                    "theme_keys": ["software"],
                    "theme_benchmarks": {"software": ["IGV", "XSW"]},
                    "primary_model_group": "software",
                },
                {
                    "state": "assigned",
                    "ticker": "SNDK",
                    "effective_from": "2026-07-01",
                    "effective_to": None,
                    "sector_key": "technology",
                    "sector_benchmark": "XLK",
                    "theme_keys": ["semiconductor"],
                    "theme_benchmarks": {
                        "semiconductor": ["SOXX", "SMH"],
                    },
                    "primary_model_group": "semiconductor",
                },
            ]
        }
        histories = {
            "QQQ": rising(end="2026-07-10"),
            "XLK": rising(slope=0.2, end="2026-07-10"),
            "IGV": rising(slope=0.15, end="2026-07-10"),
            "XSW": rising(slope=0.12, end="2026-07-10"),
            "SOXX": rising(slope=0.3, end="2026-07-10"),
            "SMH": rising(slope=0.3, end="2026-07-10"),
            "SNDK": rising(slope=0.4, end="2026-07-10"),
        }

        context = build_forecast_risk_context(histories, assignments)
        sndk = context.xs("SNDK", level="ticker")

        self.assertEqual(
            set(sndk.loc[sndk.index < "2026-07-01", "theme_group_key"]),
            {"software"},
        )
        self.assertEqual(
            set(sndk.loc[sndk.index >= "2026-07-01", "theme_group_key"]),
            {"semiconductor"},
        )

    def test_group_rolling_state_receives_date_masked_membership_histories(self):
        assignments = {
            ticker: [
                {
                    "state": "assigned",
                    "ticker": ticker,
                    "effective_from": "2026-01-01",
                    "effective_to": None,
                    "sector_key": "technology",
                    "sector_benchmark": "XLK",
                    "theme_keys": [],
                    "theme_benchmarks": {},
                    "primary_model_group": "technology",
                }
            ]
            for ticker in ("A", "B")
        }
        assignments["OLD"] = [
            {
                **assignments["A"][0],
                "ticker": "OLD",
                "effective_to": "2026-07-01",
            },
            {
                **assignments["A"][0],
                "ticker": "OLD",
                "effective_from": "2026-07-01",
                "sector_key": "financials",
                "sector_benchmark": "XLF",
                "primary_model_group": "financials",
            },
        ]
        assignments["NEW"] = [
            {
                **assignments["OLD"][1],
                "ticker": "NEW",
                "effective_from": "2026-01-01",
                "effective_to": "2026-07-01",
            },
            {
                **assignments["OLD"][0],
                "ticker": "NEW",
                "effective_from": "2026-07-01",
                "effective_to": None,
            },
        ]
        histories = {
            "QQQ": rising(periods=140, end="2026-07-20"),
            "XLK": rising(periods=140, slope=0.2, end="2026-07-20"),
            "XLF": rising(periods=140, slope=0.1, end="2026-07-20"),
            "A": rising(periods=140, slope=0.2, end="2026-07-20"),
            "B": rising(periods=140, slope=0.2, end="2026-07-20"),
            "OLD": rising(periods=140, slope=-0.3, end="2026-07-20"),
            "NEW": rising(periods=140, slope=0.4, end="2026-07-20"),
        }
        observed = []
        real_builder = forecast_decision_module.build_group_regime_state

        def probe(scoped_histories, group, *, membership_intervals=None):
            if group.key == "technology":
                observed.append(
                    (
                        set(group.constituent_tickers),
                        membership_intervals["OLD"],
                        membership_intervals["NEW"],
                    )
                )
            return real_builder(
                scoped_histories,
                group,
                membership_intervals=membership_intervals,
            )

        with mock.patch.object(
            forecast_decision_module,
            "build_group_regime_state",
            side_effect=probe,
        ):
            context = build_forecast_risk_context(histories, assignments)

        self.assertEqual(
            observed,
            [
                (
                    {"A", "B", "NEW", "OLD"},
                    (
                        (
                            pd.Timestamp("2026-01-01"),
                            pd.Timestamp("2026-07-01"),
                        ),
                    ),
                    ((pd.Timestamp("2026-07-01"), None),),
                )
            ],
        )
        self.assertIn(
            pd.Timestamp("2026-06-30"),
            context.xs("A", level="ticker").index,
        )
        self.assertIn(
            pd.Timestamp("2026-07-01"),
            context.xs("NEW", level="ticker").index,
        )

    def test_same_group_rejoin_resets_pre_departure_risk_memory(self):
        assignments = {
            "T": [
                {
                    "state": "assigned",
                    "ticker": "T",
                    "effective_from": "2025-12-01",
                    "effective_to": "2026-03-02",
                    "sector_key": "technology",
                    "sector_benchmark": "XLK",
                    "theme_keys": [],
                    "theme_benchmarks": {},
                    "primary_model_group": "technology",
                },
                {
                    "state": "assigned",
                    "ticker": "T",
                    "effective_from": "2026-03-02",
                    "effective_to": "2026-05-01",
                    "sector_key": "financials",
                    "sector_benchmark": "XLF",
                    "theme_keys": [],
                    "theme_benchmarks": {},
                    "primary_model_group": "financials",
                },
                {
                    "state": "assigned",
                    "ticker": "T",
                    "effective_from": "2026-05-01",
                    "effective_to": None,
                    "sector_key": "technology",
                    "sector_benchmark": "XLK",
                    "theme_keys": [],
                    "theme_benchmarks": {},
                    "primary_model_group": "technology",
                },
            ]
        }
        base = rising(periods=180, slope=0.2, end="2026-07-10")
        shocked = base.copy(deep=True)
        shock_dates = shocked.loc[
            (shocked.index >= "2026-02-16")
            & (shocked.index < "2026-03-02")
        ].index
        shocked.loc[shock_dates, "Close"] -= np.linspace(
            2.0,
            28.0,
            len(shock_dates),
        )
        shocked.loc[shock_dates, "Open"] = shocked.loc[
            shock_dates, "Close"
        ] + 2.0
        shocked.loc[shock_dates, "High"] = shocked.loc[
            shock_dates, "Open"
        ] + 1.0
        shocked.loc[shock_dates, "Low"] = shocked.loc[
            shock_dates, "Close"
        ] - 1.0
        shocked.loc[shock_dates, "Volume"] = 3_000_000.0
        references = {
            "QQQ": rising(periods=180, end="2026-07-10"),
            "XLK": rising(periods=180, slope=0.2, end="2026-07-10"),
            "XLF": rising(periods=180, slope=0.1, end="2026-07-10"),
        }

        clean_context = build_forecast_risk_context(
            {**references, "T": base},
            assignments,
        ).xs("T", level="ticker")
        shocked_context = build_forecast_risk_context(
            {**references, "T": shocked},
            assignments,
        ).xs("T", level="ticker")
        after_rejoin = clean_context.index >= "2026-05-01"

        assert_frame_equal(
            shocked_context.loc[after_rejoin],
            clean_context.loc[after_rejoin],
        )

    def test_first_evidence_dates_do_not_create_global_full_builds(self):
        first_dates = pd.bdate_range("2025-01-02", periods=193)
        assignments = {
            f"T{offset:03d}": [
                {
                    "state": "assigned",
                    "ticker": f"T{offset:03d}",
                    "effective_from": start.strftime("%Y-%m-%d"),
                    "effective_to": None,
                    "sector_key": "technology",
                    "sector_benchmark": "XLK",
                    "theme_keys": [],
                    "theme_benchmarks": {},
                    "primary_model_group": "technology",
                }
            ]
            for offset, start in enumerate(first_dates)
        }
        assignments["T000"] = [
            {
                **assignments["T000"][0],
                "effective_to": "2026-07-01",
            },
            {
                **assignments["T000"][0],
                "effective_from": "2026-07-01",
                "sector_key": "financials",
                "sector_benchmark": "XLF",
                "primary_model_group": "financials",
            },
        ]
        histories = {
            "QQQ": rising(periods=80, end="2026-07-10"),
            "XLK": rising(periods=80, slope=0.2, end="2026-07-10"),
            "XLF": rising(periods=80, slope=0.1, end="2026-07-10"),
            **{
                ticker: rising(
                    periods=80,
                    slope=0.05 + offset / 10_000,
                    end="2026-07-10",
                )
                for offset, ticker in enumerate(assignments)
            },
        }

        with mock.patch.object(
            forecast_decision_module,
            "build_group_score_frame",
            wraps=forecast_decision_module.build_group_score_frame,
        ) as group_score_builder, mock.patch.object(
            forecast_decision_module,
            "build_high_level_distribution_state",
            wraps=forecast_decision_module.build_high_level_distribution_state,
        ) as high_level_builder:
            started = perf_counter()
            context = build_forecast_risk_context(histories, assignments)
            elapsed = perf_counter() - started

        self.assertEqual(group_score_builder.call_count, 2)
        self.assertEqual(high_level_builder.call_count, 194)
        self.assertLess(elapsed, 35.0)
        self.assertFalse(context.empty)

    def test_related_membership_does_not_duplicate_dynamic_context_rows(self):
        assignments = {
            "NBIS": {
                "state": "assigned",
                "ticker": "NBIS",
                "sector_key": "technology",
                "sector_benchmark": "XLK",
                "theme_keys": [],
                "theme_benchmarks": {},
                "primary_model_group": "technology",
            },
        }
        histories = {
            "QQQ": rising(),
            "XLK": rising(slope=0.2),
            "SOXX": rising(slope=0.3),
            "SMH": rising(slope=0.3),
            "NBIS": rising(slope=0.4),
        }

        context = build_forecast_risk_context(histories, assignments)

        self.assertFalse(context.index.has_duplicates)
        self.assertEqual(
            set(context.index.get_level_values("ticker")),
            {"NBIS"},
        )
        self.assertEqual(
            set(context.xs("NBIS", level="ticker")["sector_group_key"]),
            {"technology"},
        )

    def test_missing_sector_and_theme_benchmarks_leave_group_risk_unavailable(self):
        assignments = {
            ticker: {
                "state": "assigned",
                "ticker": ticker,
                "sector_key": "technology",
                "sector_benchmark": "XLK",
                "theme_keys": ["semiconductor"],
                "theme_benchmarks": {
                    "semiconductor": ["SOXX", "SMH"],
                },
                "primary_model_group": "semiconductor",
            }
            for ticker in ("SNDK", "AMD", "NVDA")
        }
        histories = {
            "QQQ": rising(),
            "SNDK": rising(slope=0.4),
            "AMD": rising(slope=0.35),
            "NVDA": rising(slope=0.45),
        }

        context = build_forecast_risk_context(histories, assignments)
        sndk = context.xs("SNDK", level="ticker")

        self.assertTrue(sndk["sector_risk_score"].isna().all())
        self.assertTrue(sndk["theme_risk_score"].isna().all())
        self.assertTrue(sndk["group_risk_score"].isna().all())
        self.assertTrue(sndk["high_level_distribution_state"].notna().all())
        self.assertTrue(
            sndk["persistent_risk_sources"].map(
                lambda sources: "group" not in sources
            ).all()
        )

    def test_dynamic_context_invokes_full_ticker_builders_once_for_nbis(self):
        assignments = {
            "NBIS": {
                "state": "assigned",
                "ticker": "NBIS",
                "sector_key": "technology",
                "sector_benchmark": "XLK",
                "theme_keys": [],
                "theme_benchmarks": {},
                "primary_model_group": "technology",
            },
        }
        histories = {
            "QQQ": rising(),
            "XLK": rising(slope=0.2),
            "SOXX": rising(slope=0.3),
            "SMH": rising(slope=0.3),
            "NBIS": rising(slope=0.4),
        }

        with mock.patch.object(
            forecast_decision_module,
            "build_group_score_frame",
            wraps=forecast_decision_module.build_group_score_frame,
        ) as group_score_builder, mock.patch.object(
            forecast_decision_module,
            "build_high_level_distribution_state",
            wraps=forecast_decision_module.build_high_level_distribution_state,
        ) as high_level_builder:
            context = build_forecast_risk_context(histories, assignments)

        self.assertEqual(group_score_builder.call_count, 1)
        self.assertEqual(high_level_builder.call_count, 1)
        self.assertEqual(
            set(context.index.get_level_values("ticker")),
            {"NBIS"},
        )

    def test_dynamic_context_scopes_histories_for_each_active_group(self):
        assignments = {
            "SNDK": {
                "state": "assigned",
                "ticker": "SNDK",
                "sector_key": "technology",
                "sector_benchmark": "XLK",
                "theme_keys": ["semiconductor"],
                "theme_benchmarks": {
                    "semiconductor": ["SOXX", "SMH"],
                },
                "primary_model_group": "semiconductor",
            },
            "ADBE": {
                "state": "assigned",
                "ticker": "ADBE",
                "sector_key": "technology",
                "sector_benchmark": "XLK",
                "theme_keys": ["software"],
                "theme_benchmarks": {
                    "software": ["IGV", "XSW"],
                },
                "primary_model_group": "software",
            },
        }
        histories = {
            "QQQ": rising(),
            "XLK": rising(slope=0.2),
            "SOXX": rising(slope=0.3),
            "SMH": rising(slope=0.3),
            "IGV": rising(slope=0.15),
            "XSW": rising(slope=0.12),
            "SNDK": rising(slope=0.4),
            "ADBE": rising(slope=0.1),
            "NBIS": rising(slope=0.25),
            "UNRELATED": rising(slope=0.05),
        }
        observed = {"score": [], "regime": [], "slow": []}

        def probe(builder, bucket):
            def wrapped(scoped_histories, group):
                observed[bucket].append(
                    (group.key, frozenset(scoped_histories))
                )
                return builder(scoped_histories, group)

            return wrapped

        with mock.patch.object(
            forecast_decision_module,
            "build_group_score_frame",
            side_effect=probe(
                forecast_decision_module.build_group_score_frame,
                "score",
            ),
        ), mock.patch.object(
            forecast_decision_module,
            "build_group_regime_state",
            side_effect=probe(
                forecast_decision_module.build_group_regime_state,
                "regime",
            ),
        ), mock.patch.object(
            forecast_decision_module,
            "build_slow_decline_state",
            side_effect=probe(
                forecast_decision_module.build_slow_decline_state,
                "slow",
            ),
        ):
            context = build_forecast_risk_context(histories, assignments)

        score_scopes = dict(observed["score"])
        slow_scopes = dict(observed["slow"])
        regime_scopes = dict(observed["regime"])
        expected_primary_scopes = {
            "semiconductor": frozenset(
                ("SNDK", "SOXX", "SMH", "QQQ")
            ),
            "software": frozenset(
                ("ADBE", "IGV", "XSW", "XLK", "QQQ")
            ),
        }
        self.assertEqual(score_scopes, expected_primary_scopes)
        self.assertEqual(slow_scopes, expected_primary_scopes)
        self.assertEqual(
            regime_scopes["semiconductor"],
            frozenset(("SNDK", "NBIS", "SOXX", "SMH", "QQQ")),
        )
        self.assertEqual(
            regime_scopes["technology"],
            frozenset(("SNDK", "ADBE", "XLK", "QQQ")),
        )
        self.assertNotIn(
            "UNRELATED",
            set().union(*(set(scope) for scope in regime_scopes.values())),
        )
        self.assertTrue(
            context.xs("SNDK", level="ticker")[
                "individual_risk_score"
            ].notna().any()
        )
        self.assertTrue(
            context.xs("ADBE", level="ticker")[
                "individual_risk_score"
            ].notna().any()
        )


class ForecastDecisionPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = ForecastDecisionPolicy()

    @staticmethod
    def context(
        score,
        raw=None,
        state="new",
        age=0,
        individual=None,
        group=0.0,
        slow_decline=0.0,
        top_score=0.0,
        top_raw_score=0.0,
        top_state="inactive",
        top_raw_state="inactive",
        top_age=0,
        sources=None,
    ):
        individual_score = score if individual is None else individual
        return {
            "persistent_risk_score": score,
            "persistent_risk_raw_score": score if raw is None else raw,
            "persistent_risk_state": state,
            "persistent_risk_age_sessions": age,
            "persistent_risk_sources": (
                ("individual",) if sources is None else sources
            ),
            "individual_risk_score": individual_score,
            "group_risk_score": group,
            "slow_decline_risk_score": slow_decline,
            "high_level_distribution_score": top_score,
            "high_level_distribution_raw_score": top_raw_score,
            "high_level_distribution_state": top_state,
            "high_level_distribution_raw_state": top_raw_state,
            "high_level_distribution_age_sessions": top_age,
            "high_level_context_score": 0.0,
            "distribution_pressure_score": 0.0,
            "structure_damage_score": 0.0,
            "high_level_distribution_conditions": (),
            "distribution_count_5": 0,
            "distribution_count_10": 0,
            "distribution_count_20": 0,
            "churning_count_10": 0,
            "churning_cluster": False,
            "climax_run_score": 0.0,
            "climax_run_candidate": False,
            "climax_run_conditions": (),
            "top_risk_recovery": False,
            "top_risk_recovery_conditions": (),
        }

    def test_unavailable_context_retains_raw_forecast(self):
        result = self.policy.decide(available_forecast(), None)

        self.assertEqual(result.direction, "up")
        self.assertEqual(result.decision.risk_state, "unavailable")
        self.assertEqual(result.decision.action, "retain")

    def test_nan_context_is_unavailable_instead_of_raising(self):
        result = self.policy.decide(
            available_forecast(),
            {
                "persistent_risk_score": np.nan,
                "persistent_risk_raw_score": np.nan,
                "persistent_risk_state": np.nan,
                "persistent_risk_age_sessions": np.nan,
            },
        )

        self.assertEqual(result.direction, "up")
        self.assertEqual(result.decision.risk_state, "unavailable")
        self.assertEqual(result.predicted_return, 0.08)

    def test_watch_context_retains_direction(self):
        result = self.policy.decide(
            available_forecast(),
            self.context(25.0, raw=10.0, state="fading", age=2),
        )

        self.assertEqual(result.direction, "up")
        self.assertEqual(result.decision.risk_state, "watch")
        self.assertEqual(result.decision.action, "retain")
        self.assertEqual(result.decision.persistent_risk_score, 25.0)

    def test_high_persistent_risk_downgrades_bullish_to_neutral(self):
        result = self.policy.decide(
            available_forecast(bearish_turn_score=20.0),
            self.context(34.0, raw=15.0, state="fading", age=1),
        )

        self.assertEqual(result.raw_direction, "up")
        self.assertEqual(result.direction, "neutral")
        self.assertEqual(result.decision.risk_state, "high")
        self.assertEqual(result.decision.action, "downgrade_to_neutral")
        self.assertEqual(result.predicted_return, 0.08)
        self.assertIn("individual_bearish_risk", result.decision.reasons)
        self.assertEqual(
            result.decision.persistent_risk_sources,
            ("individual",),
        )

    def test_persistent_and_immediate_confluence_overrides_to_down(self):
        result = self.policy.decide(
            available_forecast(bearish_turn_score=57.0),
            self.context(34.0, raw=34.0, state="new", age=0),
        )

        self.assertEqual(result.direction, "down")
        self.assertEqual(result.decision.risk_state, "confirmed")
        self.assertEqual(result.decision.action, "override_to_down")
        self.assertIn(
            "persistent_immediate_confluence",
            result.decision.reasons,
        )

    def test_high_level_watch_does_not_change_ridge_direction(self):
        result = self.policy.decide(
            available_forecast(),
            self.context(
                0.0,
                individual=0.0,
                top_score=45.0,
                top_raw_score=45.0,
                top_state="watch",
                top_raw_state="watch",
            ),
        )

        self.assertEqual(result.direction, "up")
        self.assertEqual(result.decision.action, "retain")

    def test_high_level_distribution_risk_downgrades_ridge_to_neutral(self):
        result = self.policy.decide(
            available_forecast(),
            self.context(
                0.0,
                individual=0.0,
                top_score=65.0,
                top_raw_score=65.0,
                top_state="high",
                top_raw_state="high",
            ),
        )

        self.assertEqual(result.direction, "neutral")
        self.assertEqual(result.decision.risk_state, "high")
        self.assertIn(
            "high_level_distribution_risk",
            result.decision.reasons,
        )

    def test_current_confirmed_top_risk_overrides_ridge_to_down(self):
        result = self.policy.decide(
            available_forecast(),
            self.context(
                0.0,
                individual=0.0,
                top_score=72.0,
                top_raw_score=72.0,
                top_state="confirmed",
                top_raw_state="confirmed",
            ),
        )

        self.assertEqual(result.direction, "down")
        self.assertEqual(result.decision.risk_state, "confirmed")
        self.assertIn(
            "high_level_structure_damage_confirmation",
            result.decision.reasons,
        )

    def test_fading_top_risk_cannot_independently_override_to_down(self):
        result = self.policy.decide(
            available_forecast(),
            self.context(
                0.0,
                individual=0.0,
                top_score=72.0,
                top_raw_score=0.0,
                top_state="fading",
                top_raw_state="inactive",
                top_age=2,
            ),
        )

        self.assertEqual(result.direction, "neutral")
        self.assertNotEqual(result.direction, "down")

    def test_group_and_slow_decline_use_source_specific_thresholds(self):
        group_watch = self.policy.decide(
            available_forecast(),
            self.context(
                59.0,
                individual=0.0,
                group=59.0,
                sources=("group",),
            ),
        )
        group_high = self.policy.decide(
            available_forecast(),
            self.context(
                60.0,
                individual=0.0,
                group=60.0,
                sources=("group",),
            ),
        )
        slow_watch = self.policy.decide(
            available_forecast(),
            self.context(
                69.0,
                individual=0.0,
                slow_decline=69.0,
                sources=("slow_decline",),
            ),
        )
        slow_high = self.policy.decide(
            available_forecast(),
            self.context(
                70.0,
                individual=0.0,
                slow_decline=70.0,
                sources=("slow_decline",),
            ),
        )

        self.assertEqual(group_watch.direction, "up")
        self.assertEqual(group_watch.decision.risk_state, "watch")
        self.assertEqual(group_high.direction, "neutral")
        self.assertEqual(group_high.decision.risk_state, "high")
        self.assertEqual(slow_watch.direction, "up")
        self.assertEqual(slow_watch.decision.risk_state, "watch")
        self.assertEqual(slow_high.direction, "neutral")
        self.assertEqual(slow_high.decision.risk_state, "high")

    def test_existing_immediate_override_is_normalized(self):
        forecast = available_forecast(
            direction="down",
            raw_direction="up",
            bearish_turn_score=100.0,
            direction_adjustment_reason="bearish_turn_risk",
            bearish_turn_conditions=("distribution_volume",),
        )

        result = self.policy.decide(forecast, self.context(46.5))

        self.assertEqual(result.direction, "down")
        self.assertEqual(result.raw_direction, "up")
        self.assertEqual(result.decision.risk_state, "confirmed")
        self.assertIn(
            "immediate_bearish_confirmation",
            result.decision.reasons,
        )

    def test_raw_down_is_never_upgraded(self):
        forecast = available_forecast(
            direction="down",
            raw_direction="down",
            predicted_return=-0.08,
        )

        result = self.policy.decide(forecast, self.context(5.0, state="inactive"))

        self.assertEqual(result.direction, "down")
        self.assertEqual(result.decision.action, "retain")


if __name__ == "__main__":
    unittest.main()
