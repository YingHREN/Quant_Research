from __future__ import annotations

import math
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


def cap_triggering_history() -> pd.DataFrame:
    """Build seven extreme old 20-session gains outside current volatility data."""
    history = deterministic_history(500)
    adjusted_close = history.columns.get_loc("Adj Close")
    for position, price in zip(range(199, 340, 20), 100 * 2 ** np.arange(8)):
        history.iloc[position, adjusted_close] = price
    history.iloc[359, adjusted_close] = 100.0
    return history


class HistoricalScenarioProviderTest(unittest.TestCase):
    def test_defaults_are_the_documented_horizons_and_quantiles(self):
        provider = HistoricalScenarioProvider()

        self.assertEqual(provider.horizons, (20, 40, 60))
        self.assertEqual(provider.quantiles, (0.25, 0.5, 0.75))

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
        history = cap_triggering_history()
        close = history["Adj Close"]
        raw_returns = [
            close.iloc[end] / close.iloc[end - 20] - 1
            for end in range(len(close) - 1, 19, -20)
        ]
        realized_volatility = float(
            close.pct_change().dropna().iloc[-63:].std(ddof=1) * math.sqrt(252)
        )
        expected_cap = float(3 * realized_volatility * math.sqrt(20 / 252))
        raw_optimistic = float(np.quantile(raw_returns, 0.75))

        band = HistoricalScenarioProvider().build(history, None)["horizons"]["20"]

        self.assertGreater(raw_optimistic, expected_cap)
        self.assertEqual(band["return_cap"], expected_cap)
        self.assertEqual(band["quantiles"]["optimistic"], expected_cap)
        endpoint = band["paths"]["optimistic"][-1]
        self.assertAlmostEqual(endpoint["return"], expected_cap)
        self.assertAlmostEqual(endpoint["price"], close.iloc[-1] * (1 + expected_cap))
        midpoint = band["paths"]["median"][10]
        expected_midpoint = close.iloc[-1] * np.exp(
            np.log1p(band["quantiles"]["median"]) / 2
        )
        self.assertAlmostEqual(midpoint["price"], expected_midpoint)

    def test_exactly_eight_samples_is_an_available_inclusive_boundary(self):
        provider = HistoricalScenarioProvider()
        enough = provider.build(deterministic_history(161), None)["horizons"]["20"]
        too_short = provider.build(deterministic_history(160), None)["horizons"]["20"]

        self.assertTrue(enough["available"])
        self.assertIsNone(enough["missing_reason"])
        self.assertEqual(enough["sample_count"], 8)
        self.assertFalse(too_short["available"])
        self.assertEqual(too_short["sample_count"], 7)
        self.assertEqual(too_short["missing_reason"], "insufficient_samples")

    def test_missing_horizon_returns_reason(self):
        result = HistoricalScenarioProvider().build(deterministic_history(80), None)

        self.assertEqual(
            result["horizons"]["60"]["missing_reason"], "insufficient_samples"
        )
        self.assertEqual(result["horizons"]["60"]["paths"], {})


if __name__ == "__main__":
    unittest.main()
