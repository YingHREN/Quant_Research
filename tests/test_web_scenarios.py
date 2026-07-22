from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from web.services.scenarios import HistoricalScenarioProvider


def deterministic_history(periods: int) -> pd.DataFrame:
    """Create a repeatable, positive adjusted-close history."""
    index = pd.bdate_range("2024-01-02", periods=periods)
    daily_log_return = 0.001 + 0.008 * np.sin(np.arange(periods) / 7.0)
    adjusted_close = 100.0 * np.exp(np.cumsum(daily_log_return))
    return pd.DataFrame(
        {
            "Close": adjusted_close * 1.01,
            "Adj Close": adjusted_close,
        },
        index=index,
    )


class HistoricalScenarioProviderTest(unittest.TestCase):
    def test_uses_non_overlapping_samples_and_no_future_bars(self):
        history = deterministic_history(320)
        asof = history.index[-61]
        future_changed = history.copy()
        future_changed.loc[future_changed.index > asof, "Adj Close"] *= 100

        result = HistoricalScenarioProvider().build(future_changed, asof)
        expected = HistoricalScenarioProvider().build(history.loc[:asof], None)

        self.assertEqual(result, expected)
        self.assertEqual(result["provider"], "historical_distribution")
        self.assertEqual(result["observation_date"], asof.date().isoformat())
        self.assertTrue(result["horizons"]["20"]["non_overlapping"])
        self.assertEqual(result["horizons"]["20"]["sample_count"], 12)

    def test_quantiles_are_ordered_and_start_at_observation_close(self):
        history = deterministic_history(500)
        band = HistoricalScenarioProvider().build(history, None)["horizons"]["20"]

        self.assertEqual(band["paths"]["pessimistic"][0]["return"], 0.0)
        self.assertEqual(band["paths"]["median"][0]["return"], 0.0)
        self.assertEqual(band["paths"]["optimistic"][0]["return"], 0.0)
        self.assertEqual(
            band["paths"]["median"][0]["price"], history["Adj Close"].iloc[-1]
        )
        for position in range(len(band["paths"]["median"])):
            self.assertLessEqual(
                band["paths"]["pessimistic"][position]["price"],
                band["paths"]["median"][position]["price"],
            )
            self.assertLessEqual(
                band["paths"]["median"][position]["price"],
                band["paths"]["optimistic"][position]["price"],
            )

    def test_caps_quantile_returns_and_interpolates_log_paths(self):
        history = deterministic_history(500)
        history.loc[history.index[-21], "Adj Close"] = 1.0
        history.loc[history.index[-1], "Adj Close"] = 10_000.0

        band = HistoricalScenarioProvider().build(history, None)["horizons"]["20"]

        self.assertLessEqual(
            abs(band["quantiles"]["optimistic"]), band["return_cap"]
        )
        endpoint = band["paths"]["optimistic"][-1]
        self.assertAlmostEqual(endpoint["return"], band["quantiles"]["optimistic"])
        midpoint = band["paths"]["median"][10]
        expected_midpoint = history["Adj Close"].iloc[-1] * np.exp(
            np.log1p(band["quantiles"]["median"]) / 2
        )
        self.assertAlmostEqual(midpoint["price"], expected_midpoint)

    def test_missing_horizon_returns_reason(self):
        result = HistoricalScenarioProvider().build(deterministic_history(80), None)

        self.assertEqual(
            result["horizons"]["60"]["missing_reason"], "insufficient_samples"
        )
        self.assertEqual(result["horizons"]["60"]["paths"], {})


if __name__ == "__main__":
    unittest.main()
