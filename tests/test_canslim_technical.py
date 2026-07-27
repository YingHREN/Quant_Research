import unittest

import numpy as np
import pandas as pd

from research.canslim_technical import evaluate_technical_gate


def rising_history(periods=260, end="2026-07-24"):
    index = pd.bdate_range(end=end, periods=periods)
    close = np.linspace(100.0, 160.0, periods)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.linspace(1_000_000, 1_200_000, periods),
        },
        index=index,
    )


class TechnicalGateTests(unittest.TestCase):
    def test_rising_260_session_history_passes_all_conditions(self):
        result = evaluate_technical_gate(
            rising_history(),
            "2026-07-24",
        )

        self.assertEqual(result["state"], "pass")
        self.assertEqual(result["passed_conditions"], 4)
        self.assertEqual(result["condition_count"], 4)
        self.assertGreater(
            result["values"]["ema10"],
            result["values"]["ema20"],
        )
        self.assertGreater(result["values"]["sma50"], 0)
        self.assertGreaterEqual(
            result["values"]["distance_from_high_252"],
            -0.20,
        )
        self.assertTrue(result["preferred_within_15pct"])
        self.assertEqual(
            result["version"],
            "canslim_technical_gate_v1",
        )

    def test_251_sessions_leave_52_week_condition_missing(self):
        result = evaluate_technical_gate(
            rising_history(periods=251),
            "2026-07-24",
        )

        self.assertEqual(result["state"], "missing")
        self.assertEqual(
            result["conditions"]["within_20pct_of_52_week_high"]["state"],
            "missing",
        )
        self.assertEqual(
            result["conditions"]["within_20pct_of_52_week_high"]["reason"],
            "insufficient_252_session_history",
        )
        self.assertIsNone(result["values"]["high_close_252"])

    def test_252_sessions_make_52_week_condition_available(self):
        result = evaluate_technical_gate(
            rising_history(periods=252),
            "2026-07-24",
        )

        self.assertEqual(
            result["conditions"]["within_20pct_of_52_week_high"]["state"],
            "pass",
        )
        self.assertIsNotNone(result["values"]["high_close_252"])

    def test_stale_observation_never_passes(self):
        result = evaluate_technical_gate(
            rising_history(),
            "2026-07-24",
            stale=True,
        )

        self.assertEqual(result["state"], "missing")
        self.assertEqual(result["reason_codes"], ["stale_observation"])
        self.assertTrue(
            all(
                row["state"] == "missing"
                for row in result["conditions"].values()
            )
        )

    def test_future_rows_do_not_change_an_old_observation(self):
        history = rising_history(periods=300, end="2026-09-18")
        asof = history.index[259]

        first = evaluate_technical_gate(history.iloc[:260], asof)
        second = evaluate_technical_gate(history, asof)

        self.assertEqual(first, second)

    def test_falling_history_fails_trend_conditions(self):
        history = rising_history()
        descending = np.linspace(160.0, 70.0, len(history))
        history.loc[:, "Open"] = descending + 0.5
        history.loc[:, "High"] = descending + 1.0
        history.loc[:, "Low"] = descending - 1.0
        history.loc[:, "Close"] = descending

        result = evaluate_technical_gate(history, "2026-07-24")

        self.assertEqual(result["state"], "fail")
        self.assertEqual(
            result["conditions"]["close_above_sma50"]["state"],
            "fail",
        )
        self.assertEqual(
            result["conditions"]["ema10_above_ema20"]["state"],
            "fail",
        )
        self.assertEqual(
            result["conditions"]["moving_average_slopes_positive"]["state"],
            "fail",
        )

    def test_invalid_history_returns_stable_missing_payload(self):
        duplicate = pd.concat(
            [rising_history(), rising_history().iloc[[-1]]]
        )
        result = evaluate_technical_gate(
            duplicate,
            "2026-07-24",
        )

        self.assertEqual(result["state"], "missing")
        self.assertEqual(result["reason_codes"], ["duplicate_dates"])

    def test_non_finite_and_non_positive_prices_are_missing(self):
        with_nan = rising_history()
        with_nan.iloc[-1, with_nan.columns.get_loc("Volume")] = np.nan
        with_zero = rising_history()
        with_zero.iloc[-1, with_zero.columns.get_loc("Close")] = 0.0

        nan_result = evaluate_technical_gate(with_nan, "2026-07-24")
        zero_result = evaluate_technical_gate(with_zero, "2026-07-24")

        self.assertEqual(nan_result["reason_codes"], ["non_finite_ohlcv"])
        self.assertEqual(zero_result["reason_codes"], ["non_positive_price"])

    def test_non_monotonic_visible_dates_are_missing(self):
        history = rising_history()
        history = history.iloc[[*range(258), 259, 258]]

        result = evaluate_technical_gate(history, "2026-07-24")

        self.assertEqual(result["reason_codes"], ["non_monotonic_dates"])

    def test_latest_cross_never_uses_a_future_date(self):
        history = rising_history()
        midpoint = len(history) // 2
        close = np.concatenate(
            (
                np.linspace(160.0, 90.0, midpoint),
                np.linspace(90.0, 150.0, len(history) - midpoint),
            )
        )
        history.loc[:, "Open"] = close - 0.5
        history.loc[:, "High"] = close + 1.0
        history.loc[:, "Low"] = close - 1.0
        history.loc[:, "Close"] = close
        asof = history.index[-20]

        result = evaluate_technical_gate(history, asof)

        cross_date = result["values"]["last_ema_cross_date"]
        self.assertIsNotNone(cross_date)
        self.assertLessEqual(pd.Timestamp(cross_date), asof)


if __name__ == "__main__":
    unittest.main()
