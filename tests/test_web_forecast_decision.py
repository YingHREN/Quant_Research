import unittest
from unittest import mock

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from web.forecasts.base import ForecastResult
from web.forecasts.decision import (
    ForecastDecision,
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

    def test_duplicate_group_membership_is_rejected(self):
        semiconductor = market_group("semiconductor")

        with mock.patch(
            "web.forecasts.decision.modeled_market_groups",
            return_value=(semiconductor, semiconductor),
        ), self.assertRaisesRegex(ValueError, "duplicate"):
            build_forecast_risk_context(self.histories())

    def test_modeled_groups_exclude_proxy_only_sectors(self):
        groups = modeled_market_groups()

        self.assertEqual(tuple(group.key for group in groups), ("semiconductor",))


if __name__ == "__main__":
    unittest.main()
