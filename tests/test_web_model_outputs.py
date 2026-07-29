import copy
import unittest

from web.forecasts.model_outputs import build_model_outputs
from web.forecasts.output_registry import (
    ModelOutputContext,
    ModelOutputRegistration,
    ModelOutputRegistry,
)


def forecast_payload():
    return {
        "model_key": "ridge_direction_v1",
        "model_version": "v4",
        "horizon_sessions": 5,
        "predicted_return": 0.0363,
        "raw_direction": "up",
        "direction": "down",
        "training_sample_count": 1000,
        "training_cutoff": "2026-06-30",
        "confidence_status": "uncalibrated",
        "confidence_reason": "insufficient_calibration_samples",
        "bearish_turn_score": 100.0,
        "bearish_turn_conditions": [
            "distribution_volume",
            "ema20_breakdown",
        ],
        "decision": {
            "final_direction": "down",
            "risk_state": "confirmed",
            "action": "override_to_down",
            "reasons": ["immediate_bearish_confirmation"],
            "policy_key": "forecast_decision_policy",
            "policy_version": "v2",
            "persistent_risk_score": 82.0,
            "persistent_risk_raw_score": 70.0,
            "persistent_risk_state": "fading",
            "persistent_risk_age_sessions": 2,
            "persistent_risk_sources": ["individual", "group"],
            "individual_risk_score": 72.0,
            "group_risk_score": 82.0,
            "slow_decline_risk_score": 45.0,
            "high_level_distribution_score": 72.0,
            "high_level_distribution_raw_score": 68.0,
            "high_level_distribution_state": "confirmed",
            "high_level_distribution_raw_state": "confirmed",
            "high_level_distribution_age_sessions": 0,
            "high_level_context_score": 75.0,
            "distribution_pressure_score": 70.0,
            "structure_damage_score": 55.0,
            "high_level_distribution_conditions": [
                "prior_60_session_advance",
                "distribution_day",
                "failed_breakout",
                "below_ema20",
            ],
            "distribution_count_5": 1,
            "distribution_count_10": 3,
            "distribution_count_20": 5,
            "churning_count_10": 2,
            "churning_cluster": True,
            "climax_run_score": 80.0,
            "climax_run_candidate": True,
            "climax_run_conditions": [
                "climax_acceleration",
                "climax_range_expansion",
                "climax_ema_extension",
                "climax_abnormal_volume",
            ],
            "top_risk_recovery": True,
            "top_risk_recovery_conditions": ["strong_reclaim"],
            "immediate_risk_score": 100.0,
        },
    }


def chart_row():
    return {
        "time": "2026-07-01",
        "market_regime_gate": {
            "state": "fail",
            "market_state": "uptrend_under_pressure",
            "state_start": "2026-06-26",
            "follow_through_date": "2026-06-10",
            "rally_day_count": 0,
            "distribution_days": 4,
            "breadth_above_ema20": 0.42,
            "breadth_above_sma50": 0.51,
            "reason_codes": ["distribution_days_ge_4"],
            "version": "market_regime_gate_v1",
        },
        "reversal_signal_count": 2,
        "reversal_candidate": True,
        "prior_high_breakout": True,
        "trendline_breakout": True,
        "higher_low_confirmed": False,
        "early_reversal_score": 75,
        "early_reversal_watch": True,
        "early_reversal_conditions": [
            "prior_session_selloff",
            "current_price_acceptance",
            "current_volume_support",
        ],
        "bottom_model_key": "bottoming_reversal_state_v1",
        "bottom_model_version": "v1",
        "bottom_state": "early_bullish_reversal_watch",
        "bottom_raw_state": "early_bullish_reversal_watch",
        "bottom_score": 68.0,
        "bottom_coverage": 0.8,
        "bottom_state_age_sessions": 2,
        "bottom_location_score": 18.0,
        "bottom_exhaustion_score": 18.0,
        "bottom_demand_score": 17.0,
        "bottom_structure_score": 10.0,
        "bottom_environment_score": 5.0,
        "bottom_conditions": [
            "near_support_zone",
            "buyer_absorption",
            "early_bullish_reversal",
        ],
        "bottom_counter_conditions": ["strong_supply_pressure"],
        "bottom_invalidation_level": 96.5,
        "bottom_unavailable_reason": None,
        "historical_demand_support_model_key": "historical_demand_support_v1",
        "historical_demand_support_model_version": "v1",
        "historical_demand_support_state": "accepted",
        "historical_demand_support_score": 76.0,
        "historical_demand_support_coverage": 0.8,
        "historical_demand_support_lower": 97.0,
        "historical_demand_support_upper": 99.0,
        "historical_demand_support_first_date": "2026-06-12",
        "historical_demand_support_last_confirmed_date": "2026-06-23",
        "historical_demand_support_age_sessions": 6,
        "historical_demand_support_event_types": ["buyer_absorption"],
        "historical_demand_support_event_count": 2,
        "historical_demand_support_retest_count": 1,
        "historical_demand_support_invalidation_level": 95.5,
        "historical_demand_support_conditions": ["retest_accepted"],
        "historical_demand_support_counter_conditions": [],
        "historical_demand_support_unavailable_reason": None,
        "strict_vcp_active": False,
        "strict_vcp_reject_reason": "contractions_not_decreasing",
        "strict_vcp_evidence": {
            "accepted": False,
            "n_contractions": 2,
            "vcp_pivot": 103.5,
            "distance_to_pivot_pct": -1.2,
            "reject_reason": "contractions_not_decreasing",
        },
        "tight_platform_active": True,
        "tight_platform_reject_reason": None,
        "tight_platform_evidence": {
            "available": True,
            "active": True,
            "platform_pivot": 102.5,
            "range_pct": 3.4,
            "vol_dryup_pct": 28.0,
            "reject_reason": None,
        },
        "vcp_breakout_confirmed": True,
        "vcp_breakout_price_confirmed": True,
        "vcp_breakout_volume_confirmed": True,
        "vcp_breakout_buy_zone_confirmed": True,
        "vcp_breakout_pivot": 103.5,
        "vcp_breakout_volume_ratio": 1.62,
        "vcp_breakout_pct_over_pivot": 2.1,
        "vcp_breakout_reject_reason": None,
        "pocket_pivot": False,
        "pocket_pivot_current_volume": 1_250_000,
        "pocket_pivot_prior_down_volume": 1_400_000,
        "pocket_pivot_down_day_count": 4,
        "pocket_pivot_reject_reason": "volume_not_above_prior_down_days",
        "pocket_pivot_evidence": {
            "available": True,
            "active": False,
            "lookback": 10,
            "current_volume": 1_250_000,
            "prior_down_volume": 1_400_000,
            "down_day_count": 4,
            "reject_reason": "volume_not_above_prior_down_days",
        },
        "supply_pressure_model_key": "supply_pressure_v1",
        "supply_pressure_score": 62.0,
        "supply_pressure_coverage": 0.92,
        "supply_close_volume_score": 30.0,
        "supply_rejection_score": 17.0,
        "supply_structure_context_score": 15.0,
        "supply_pressure_conditions": [
            "distribution_day",
            "failed_breakout",
        ],
        "demand_confirmation_model_key": "demand_confirmation_v1",
        "demand_confirmation_score": 71.0,
        "demand_confirmation_coverage": 0.92,
        "demand_participation_score": 27.0,
        "demand_absorption_score": 29.0,
        "demand_breakout_context_score": 15.0,
        "demand_confirmation_conditions": [
            "buyer_absorption",
            "breakout_acceptance",
        ],
        "supply_demand_state": "two_way_contest",
        "unavailable_reasons": [],
        "macro_risk_model_key": "macro_risk_v1",
        "macro_risk_model_version": "v1",
        "macro_risk_score": 68.0,
        "macro_risk_coverage": 0.9,
        "macro_risk_state": "high",
        "macro_risk_conditions": [
            "two_year_yield_high",
            "high_yield_spread_stressed",
        ],
        "macro_risk_components": {
            "rates": {"score": 80.0, "coverage": 1.0},
            "inflation_energy": {"score": 40.0, "coverage": 0.8},
            "credit_liquidity": {"score": 100.0, "coverage": 1.0},
            "risk_aversion": {"score": 50.0, "coverage": 0.75},
        },
        "macro_risk_unavailable_reason": None,
        "canslim_technical_gate": {
            "state": "pass",
            "passed_conditions": 4,
            "condition_count": 4,
            "version": "canslim_technical_gate_v1",
            "preferred_within_15pct": True,
            "reason_codes": [],
            "conditions": {
                "close_above_sma50": {"state": "pass"},
                "ema10_above_ema20": {"state": "pass"},
                "moving_average_slopes_positive": {"state": "pass"},
                "within_20pct_of_52_week_high": {"state": "pass"},
            },
            "values": {
                "distance_from_high_252": -0.08,
                "ema10_slope_5": 0.03,
                "ema20_slope_5": 0.02,
            },
        },
    }


class ModelOutputContractTest(unittest.TestCase):
    def test_historical_demand_support_is_research_advisory_output(self):
        outputs = build_model_outputs(forecast_payload(), chart_row(), {})
        demand_support = next(
            item
            for item in outputs["bullish_structure"]
            if item["key"] == "historical_demand_support_v1"
        )

        self.assertEqual(demand_support["lifecycle"], "research")
        self.assertEqual(demand_support["decision_permission"], "advisory")
        self.assertEqual(demand_support["status"], "active")
        self.assertEqual(demand_support["score"], 76.0)
        self.assertEqual(demand_support["state"], "accepted")
        self.assertEqual(demand_support["invalidation_level"], 95.5)
        self.assertEqual(
            demand_support["conditions"],
            ["buyer_absorption", "retest_accepted"],
        )

    def test_registry_orders_families_and_isolates_builder_failures(self):
        def broken(_context):
            raise RuntimeError("private implementation detail")

        registry = ModelOutputRegistry(
            (
                ModelOutputRegistration(
                    "later", "downside", 20, lambda _context: {
                        "key": "later", "status": "active"
                    }
                ),
                ModelOutputRegistration(
                    "broken", "downside", 10, broken
                ),
                ModelOutputRegistration(
                    "primary", "primary", 5, lambda context: {
                        "key": "primary",
                        "status": "available",
                        "value": context.forecast.get("predicted_return"),
                    }
                ),
            )
        )

        result = registry.build(
            ModelOutputContext(
                forecast={"predicted_return": 0.1},
                chart_row={},
                evaluation={},
                decision={},
            )
        )

        self.assertEqual([row["key"] for row in result["primary"]], ["primary"])
        self.assertEqual(
            [row["key"] for row in result["downside"]],
            ["broken", "later"],
        )
        self.assertEqual(result["downside"][0]["status"], "unavailable")
        self.assertEqual(
            result["downside"][0]["unavailable_reason"],
            "builder_failed",
        )
        self.assertNotIn("error", result["downside"][0])
        self.assertEqual(result["bullish_structure"], [])

    def test_registry_rejects_duplicate_keys_and_invalid_families(self):
        valid = ModelOutputRegistration(
            "same", "primary", 1, lambda _context: {}
        )
        with self.assertRaises(ValueError):
            ModelOutputRegistry((valid, valid))
        with self.assertRaises(ValueError):
            ModelOutputRegistration(
                "invalid", "decision", 1, lambda _context: {}
            )

    def test_register_returns_an_extended_registry_without_mutating_original(self):
        original = ModelOutputRegistry()
        extended = original.register(
            ModelOutputRegistration(
                "macro", "downside", 1, lambda _context: {
                    "key": "macro",
                    "status": "unavailable",
                }
            )
        )

        self.assertEqual(original.registrations, ())
        self.assertEqual(
            [registration.key for registration in extended.registrations],
            ["macro"],
        )
        with self.assertRaises(ValueError):
            extended.register(extended.registrations[0])

    def test_groups_models_by_semantics_without_rule_probabilities(self):
        forecast = forecast_payload()
        row = chart_row()
        evaluation = {
            "evidence_status": "unproven",
            "always_up_direction_accuracy": 0.61,
        }
        original = copy.deepcopy((forecast, row, evaluation))

        outputs = build_model_outputs(forecast, row, evaluation)

        self.assertEqual(
            set(outputs),
            {
                "registry",
                "primary",
                "downside",
                "bullish_structure",
                "macro_context",
                "decision",
            },
        )
        self.assertEqual(outputs["primary"][0]["kind"], "statistical_forecast")
        self.assertEqual(
            outputs["primary"][0]["always_up_direction_accuracy"],
            evaluation["always_up_direction_accuracy"],
        )
        self.assertEqual(outputs["primary"][0]["timing"], "next_session_open")
        self.assertEqual(outputs["primary"][0]["evidence_status"], "unproven")
        self.assertEqual(
            {item["key"] for item in outputs["downside"]},
            {
                "bearish_turn_immediate_v1",
                "bearish_turn_risk_rules_v2",
                "group_regime_risk_v1",
                "slow_decline_risk_v1",
                "high_level_distribution_risk_v1",
                "supply_pressure_v1",
                "intraday_order_flow",
            },
        )
        self.assertEqual(
            [item["key"] for item in outputs["macro_context"]],
            ["market_regime_gate_v1", "macro_risk_v1"],
        )
        self.assertEqual(
            outputs["macro_context"][0]["market_state"],
            "uptrend_under_pressure",
        )
        self.assertEqual(outputs["macro_context"][1]["score"], 68.0)
        self.assertEqual(outputs["macro_context"][1]["status"], "active")
        self.assertEqual(
            outputs["macro_context"][1]["decision_permission"],
            "advisory",
        )
        immediate = outputs["downside"][0]
        self.assertEqual(immediate["kind"], "rule_score")
        self.assertEqual(immediate["score"], 100.0)
        self.assertNotIn("probability", immediate)
        top_risk = next(
            item
            for item in outputs["downside"]
            if item["key"] == "high_level_distribution_risk_v1"
        )
        self.assertEqual(top_risk["score"], 72.0)
        self.assertEqual(top_risk["state"], "confirmed")
        self.assertEqual(top_risk["high_level_context_score"], 75.0)
        self.assertEqual(top_risk["distribution_pressure_score"], 70.0)
        self.assertEqual(top_risk["structure_damage_score"], 55.0)
        self.assertIn("failed_breakout", top_risk["conditions"])
        self.assertEqual(top_risk["distribution_count_20"], 5)
        self.assertEqual(top_risk["churning_count_10"], 2)
        self.assertTrue(top_risk["churning_cluster"])
        self.assertEqual(top_risk["climax_run_score"], 80.0)
        self.assertTrue(top_risk["climax_run_candidate"])
        self.assertIn("climax_acceleration", top_risk["conditions"])
        self.assertTrue(top_risk["risk_recovery"])
        self.assertIn("strong_reclaim", top_risk["conditions"])
        structural = outputs["bullish_structure"][0]
        self.assertEqual(structural["key"], "bullish_structure_reversal_v1")
        self.assertEqual(structural["score"], 2)
        self.assertEqual(structural["status"], "active")
        bullish = {
            item["key"]: item for item in outputs["bullish_structure"]
        }
        bottom = bullish["bottoming_reversal_state_v1"]
        self.assertEqual(bottom["lifecycle"], "research")
        self.assertEqual(bottom["kind"], "remembered_state")
        self.assertEqual(bottom["status"], "active")
        self.assertEqual(
            bottom["state"],
            "early_bullish_reversal_watch",
        )
        self.assertEqual(bottom["score"], 68.0)
        self.assertEqual(bottom["coverage"], 0.8)
        self.assertEqual(bottom["memory_age_sessions"], 2)
        self.assertEqual(bottom["invalidation_level"], 96.5)
        self.assertEqual(
            bottom["counter_conditions"],
            ["strong_supply_pressure"],
        )
        self.assertEqual(
            {metric["label_key"] for metric in bottom["metrics"]},
            {
                "modelOutput.metric.bottom.location",
                "modelOutput.metric.bottom.exhaustion",
                "modelOutput.metric.bottom.demand",
                "modelOutput.metric.bottom.structure",
                "modelOutput.metric.bottom.environment",
                "modelOutput.metric.bottom.invalidation",
            },
        )
        self.assertEqual(
            bullish["canslim_technical_gate_v1"]["state"],
            "pass",
        )
        self.assertEqual(
            bullish["canslim_technical_gate_v1"]["score"],
            4,
        )
        self.assertNotIn(
            "probability",
            bullish["canslim_technical_gate_v1"],
        )
        self.assertEqual(bullish["strict_vcp"]["status"], "inactive")
        self.assertEqual(bullish["tight_platform"]["status"], "active")
        self.assertEqual(
            bullish["strict_vcp"]["unavailable_reason"],
            "contractions_not_decreasing",
        )
        self.assertEqual(
            bullish["vcp_breakout_confirmed_v1"]["status"],
            "active",
        )
        self.assertEqual(
            bullish["pocket_pivot_v1"]["status"],
            "inactive",
        )
        self.assertEqual(
            bullish["pocket_pivot_v1"]["unavailable_reason"],
            "volume_not_above_prior_down_days",
        )
        self.assertEqual(
            bullish["vcp_breakout_confirmed_v1"]["metrics"],
            [
                {
                    "label_key": "modelOutput.metric.pivot",
                    "value": 103.5,
                    "format": "number",
                },
                {
                    "label_key": "modelOutput.metric.volumeRatio",
                    "value": 1.62,
                    "format": "ratio",
                },
                {
                    "label_key": "modelOutput.metric.requiredVolumeRatio",
                    "value": 1.4,
                    "format": "ratio",
                },
                {
                    "label_key": "modelOutput.metric.pctOverPivot",
                    "value": 2.1,
                    "format": "percent",
                },
                {
                    "label_key": "modelOutput.metric.buyZoneLimit",
                    "value": 5.0,
                    "format": "percent",
                },
            ],
        )
        self.assertEqual(
            bullish["pocket_pivot_v1"]["metrics"][0],
            {
                "label_key": "modelOutput.metric.currentVolume",
                "value": 1_250_000,
                "format": "volume",
            },
        )
        self.assertEqual(outputs["decision"]["kind"], "decision_policy")
        self.assertEqual(outputs["decision"]["final_direction"], "down")
        self.assertEqual((forecast, row, evaluation), original)

    def test_default_registry_describes_every_emitted_model(self):
        outputs = build_model_outputs(
            forecast_payload(),
            chart_row(),
            {},
        )

        registered = {
            item["key"] for item in outputs["registry"]["models"]
        }
        emitted = {
            item["key"]
            for group in (
                "primary",
                "downside",
                "bullish_structure",
                "macro_context",
            )
            for item in outputs[group]
        }
        emitted.add(outputs["decision"]["key"])

        self.assertEqual(registered, emitted)
        self.assertEqual(
            outputs["registry"]["version"],
            "model_output_registry_v5",
        )
        self.assertEqual(
            outputs["decision"]["decision_permission"],
            "final_policy",
        )
        for group in (
            "primary",
            "downside",
            "bullish_structure",
            "macro_context",
        ):
            for item in outputs[group]:
                self.assertEqual(item["group"], group)
                self.assertIsInstance(item["order"], int)
                self.assertIn(
                    item["decision_permission"],
                    {
                        "informational",
                        "advisory",
                        "downgrade_to_neutral",
                        "veto_to_down",
                    },
                )

    def test_planned_models_never_fabricate_scores(self):
        outputs = build_model_outputs(forecast_payload(), chart_row(), {})
        planned = [
            item
            for group in (
                "downside",
                "bullish_structure",
                "macro_context",
            )
            for item in outputs[group]
            if item["lifecycle"] == "planned"
        ]

        self.assertEqual(
            {item["key"] for item in planned},
            {"intraday_order_flow"},
        )
        for item in planned:
            self.assertEqual(item["status"], "unavailable")
            self.assertEqual(item["unavailable_reason"], "not_implemented")
            self.assertNotIn("score", item)

    def test_supply_and_demand_models_expose_independent_scores_and_context(self):
        outputs = build_model_outputs(forecast_payload(), chart_row(), {})
        supply = next(
            item
            for item in outputs["downside"]
            if item["key"] == "supply_pressure_v1"
        )
        demand = next(
            item
            for item in outputs["bullish_structure"]
            if item["key"] == "demand_confirmation_v1"
        )

        self.assertEqual(supply["lifecycle"], "production")
        self.assertEqual(supply["status"], "active")
        self.assertEqual(supply["score"], 62.0)
        self.assertEqual(supply["threshold"], 50.0)
        self.assertEqual(supply["coverage"], 0.92)
        self.assertEqual(supply["supply_demand_state"], "two_way_contest")
        self.assertEqual(
            supply["conditions"],
            ["distribution_day", "failed_breakout"],
        )
        self.assertEqual(
            {metric["label_key"] for metric in supply["metrics"]},
            {
                "modelOutput.metric.closeVolumeSupply",
                "modelOutput.metric.rejectionSupply",
                "modelOutput.metric.structureContextSupply",
            },
        )

        self.assertEqual(demand["lifecycle"], "production")
        self.assertEqual(demand["status"], "active")
        self.assertEqual(demand["score"], 71.0)
        self.assertEqual(demand["threshold"], 50.0)
        self.assertEqual(demand["coverage"], 0.92)
        self.assertEqual(demand["supply_demand_state"], "two_way_contest")
        self.assertEqual(
            demand["conditions"],
            ["buyer_absorption", "breakout_acceptance"],
        )
        self.assertEqual(
            {metric["label_key"] for metric in demand["metrics"]},
            {
                "modelOutput.metric.demandParticipation",
                "modelOutput.metric.demandAbsorption",
                "modelOutput.metric.breakoutContextDemand",
            },
        )

    def test_missing_chart_and_risk_values_are_typed_unavailable(self):
        forecast = forecast_payload()
        forecast["decision"] = None

        outputs = build_model_outputs(forecast, {}, {})

        self.assertEqual(outputs["decision"]["status"], "unavailable")
        self.assertEqual(
            outputs["decision"]["unavailable_reason"],
            "decision_context_unavailable",
        )
        for item in outputs["bullish_structure"][:6]:
            self.assertEqual(item["status"], "unavailable")

    def test_insufficient_shape_history_has_a_typed_reason(self):
        row = chart_row()
        row.update(
            {
                "strict_vcp_active": False,
                "strict_vcp_reject_reason": "insufficient_history",
                "strict_vcp_evidence": {
                    "accepted": False,
                    "reject_reason": "insufficient_history",
                },
                "tight_platform_active": False,
                "tight_platform_reject_reason": "insufficient_history",
                "tight_platform_evidence": {
                    "available": False,
                    "active": False,
                    "reject_reason": "insufficient_history",
                },
                "pocket_pivot": False,
                "pocket_pivot_reject_reason": "insufficient_history",
                "pocket_pivot_evidence": {
                    "available": False,
                    "active": False,
                    "reject_reason": "insufficient_history",
                },
            }
        )

        outputs = build_model_outputs(forecast_payload(), row, {})
        bullish = {
            item["key"]: item for item in outputs["bullish_structure"]
        }

        for key in ("strict_vcp", "tight_platform", "pocket_pivot_v1"):
            self.assertEqual(bullish[key]["status"], "unavailable")
            self.assertEqual(
                bullish[key]["unavailable_reason"],
                "insufficient_history",
            )


if __name__ == "__main__":
    unittest.main()
