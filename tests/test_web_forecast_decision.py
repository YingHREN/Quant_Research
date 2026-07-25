import unittest

from web.forecasts.base import ForecastResult
from web.forecasts.decision import ForecastDecision


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
    }
    values.update(changes)
    return ForecastDecision(**values)


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


if __name__ == "__main__":
    unittest.main()
