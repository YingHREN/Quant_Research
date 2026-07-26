import copy
import unittest

from web.forecasts.model_outputs import build_model_outputs


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
        "strict_vcp": False,
        "tight_platform": True,
    }


class ModelOutputContractTest(unittest.TestCase):
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
            {"primary", "downside", "bullish_structure", "decision"},
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
                "macro_risk",
                "intraday_order_flow",
            },
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
        self.assertEqual(outputs["decision"]["kind"], "decision_policy")
        self.assertEqual(outputs["decision"]["final_direction"], "down")
        self.assertEqual((forecast, row, evaluation), original)

    def test_planned_models_never_fabricate_scores(self):
        outputs = build_model_outputs(forecast_payload(), chart_row(), {})
        planned = [
            item
            for group in ("downside", "bullish_structure")
            for item in outputs[group]
            if item["lifecycle"] == "planned"
        ]

        self.assertEqual(
            {item["key"] for item in planned},
            {"macro_risk", "intraday_order_flow", "demand_confirmation"},
        )
        for item in planned:
            self.assertEqual(item["status"], "unavailable")
            self.assertEqual(item["unavailable_reason"], "not_implemented")
            self.assertNotIn("score", item)

    def test_missing_chart_and_risk_values_are_typed_unavailable(self):
        forecast = forecast_payload()
        forecast["decision"] = None

        outputs = build_model_outputs(forecast, {}, {})

        self.assertEqual(outputs["decision"]["status"], "unavailable")
        self.assertEqual(
            outputs["decision"]["unavailable_reason"],
            "decision_context_unavailable",
        )
        for item in outputs["bullish_structure"][:4]:
            self.assertEqual(item["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
