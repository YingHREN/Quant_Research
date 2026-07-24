import unittest
from types import MappingProxyType

import numpy as np
import pandas as pd

from web.forecasts.base import UnavailableReason
from web.forecasts.dataset import FEATURE_COLUMNS
from web.forecasts.registry import (
    DuplicateForecastProviderKey,
    ForecastRegistry,
)
from web.forecasts.ridge import (
    MODEL_KEY,
    MODEL_VERSION,
    NEUTRAL_BANDS,
    RidgeForecastProvider,
    direction_for_return,
)


def synthetic_frame(periods=18, tickers=("AAA", "BBB")):
    dates = pd.bdate_range("2025-01-02", periods=periods)
    rows = []
    index = []
    for ticker_number, ticker in enumerate(tickers):
        for position, date in enumerate(dates):
            signal = float(position + 2 * ticker_number)
            row = {"close": 100.0 + signal}
            row.update({name: 0.0 for name in FEATURE_COLUMNS})
            row[FEATURE_COLUMNS[0]] = signal
            row[FEATURE_COLUMNS[1]] = signal  # Deliberately singular.
            for horizon in (5, 20, 60):
                label_position = position + horizon
                if label_position < periods:
                    row[f"target_return_{horizon}"] = 0.002 + 0.003 * signal
                    row[f"label_end_date_{horizon}"] = dates[label_position]
                else:
                    row[f"target_return_{horizon}"] = np.nan
                    row[f"label_end_date_{horizon}"] = pd.NaT
            rows.append(row)
            index.append((ticker, date))
    frame = pd.DataFrame(
        rows,
        index=pd.MultiIndex.from_tuples(
            index, names=("ticker", "observation_date")
        ),
    )
    for horizon in (5, 20, 60):
        frame[f"label_end_date_{horizon}"] = pd.to_datetime(
            frame[f"label_end_date_{horizon}"]
        )
    return frame, dates


class RidgeForecastProviderTest(unittest.TestCase):
    def test_atomic_market_feature_schema_advances_model_version(self):
        self.assertEqual(MODEL_KEY, "ridge_direction_v1")
        self.assertEqual(MODEL_VERSION, "v3")

    def test_prediction_is_deterministic_and_singular_features_are_stable(self):
        frame, dates = synthetic_frame()
        provider = RidgeForecastProvider(frame, alpha=0.5, minimum_samples=8)

        first = provider.forecast_series("AAA", (dates[10],), (5,))[0]
        second = provider.forecast_series("AAA", (dates[10],), (5,))[0]

        self.assertAlmostEqual(first.predicted_return, second.predicted_return, places=15)
        self.assertTrue(np.isfinite(first.predicted_return))
        self.assertEqual(first.direction, "up")
        self.assertEqual(first.model_key, MODEL_KEY)
        self.assertEqual(first.training_sample_count, 10)
        self.assertEqual(first.training_cutoff, dates[9])
        self.assertIsNone(first.up_probability)
        self.assertEqual(first.confidence_status, "uncalibrated")

        unregularized = RidgeForecastProvider(
            frame, alpha=0.0, minimum_samples=8
        ).forecast_series("AAA", (dates[10],), (5,))[0]
        self.assertAlmostEqual(unregularized.predicted_return, 0.032, places=12)

    def test_large_finite_universe_does_not_fail_inside_blas(self):
        tickers = tuple(f"T{number:03d}" for number in range(181))
        frame, dates = synthetic_frame(periods=501, tickers=tickers)

        result = RidgeForecastProvider(frame).forecast_series(
            "T000", (dates[-1],), (5,)
        )[0]

        self.assertNotEqual(result.direction, "unavailable")
        self.assertIsNone(result.unavailable_reason)
        self.assertTrue(np.isfinite(result.predicted_return))

    def test_intercept_is_not_penalized(self):
        frame, dates = synthetic_frame()
        training_target_mean = 0.011
        frame.loc[("AAA", dates[10]), list(FEATURE_COLUMNS)] = 0.0
        frame.loc[("AAA", dates[10]), FEATURE_COLUMNS[0]] = 3.0
        frame.loc[("AAA", dates[10]), FEATURE_COLUMNS[1]] = 3.0

        result = RidgeForecastProvider(
            frame, alpha=1e9, minimum_samples=8
        ).forecast_series("AAA", (dates[10],), (5,))[0]

        self.assertAlmostEqual(result.predicted_return, training_target_mean, places=12)

    def test_training_window_expands_and_never_uses_forecast_or_unobservable_rows(self):
        frame, dates = synthetic_frame(periods=20)
        provider = RidgeForecastProvider(frame, alpha=0.25, minimum_samples=4)

        early = provider.forecast_series("AAA", (dates[8],), (5,))[0]
        later = provider.forecast_series("AAA", (dates[11],), (5,))[0]

        self.assertLess(early.training_sample_count, later.training_sample_count)
        self.assertLess(early.training_cutoff, later.training_cutoff)

        contaminated = frame.copy(deep=True)
        ineligible = contaminated.index.get_level_values("observation_date") >= dates[6]
        contaminated.loc[ineligible, FEATURE_COLUMNS[0]] = 1e12
        contaminated.loc[("AAA", dates[8]), FEATURE_COLUMNS[0]] = frame.loc[
            ("AAA", dates[8]), FEATURE_COLUMNS[0]
        ]
        contaminated.loc[("AAA", dates[8]), "target_return_5"] = 1e12
        trapped = RidgeForecastProvider(
            contaminated, alpha=0.25, minimum_samples=4
        ).forecast_series("AAA", (dates[8],), (5,))[0]

        self.assertAlmostEqual(early.predicted_return, trapped.predicted_return, places=15)
        self.assertEqual(early.training_sample_count, trapped.training_sample_count)
        self.assertEqual(early.training_cutoff, trapped.training_cutoff)

    def test_medians_and_scaling_are_fit_only_on_eligible_training_rows(self):
        frame, dates = synthetic_frame(periods=20)
        frame.loc[("AAA", dates[0]), FEATURE_COLUMNS[0]] = np.nan
        frame.loc[("AAA", dates[8]), FEATURE_COLUMNS[0]] = np.nan
        baseline = RidgeForecastProvider(
            frame, alpha=0.25, minimum_samples=4
        ).forecast_series("AAA", (dates[8],), (5,))[0]

        contaminated = frame.copy(deep=True)
        future = contaminated.index.get_level_values("observation_date") > dates[8]
        contaminated.loc[future, FEATURE_COLUMNS[0]] = -1e12
        trapped = RidgeForecastProvider(
            contaminated, alpha=0.25, minimum_samples=4
        ).forecast_series("AAA", (dates[8],), (5,))[0]

        self.assertAlmostEqual(baseline.predicted_return, trapped.predicted_return, places=15)

    def test_minimum_samples_and_missing_forecast_rows_fail_closed(self):
        frame, dates = synthetic_frame()
        provider = RidgeForecastProvider(frame, minimum_samples=20)

        too_early = provider.forecast_series("AAA", (dates[10],), (5,))[0]
        missing = provider.forecast_series("MISSING", (dates[10],), (5,))[0]

        self.assertEqual(too_early.direction, "unavailable")
        self.assertEqual(
            too_early.unavailable_reason,
            UnavailableReason.INSUFFICIENT_TRAINING_SAMPLES,
        )
        self.assertEqual(too_early.training_sample_count, 10)
        self.assertEqual(too_early.training_cutoff, dates[9])
        self.assertEqual(missing.ticker, "MISSING")
        self.assertEqual(missing.asof_date, dates[10])
        self.assertEqual(missing.unavailable_reason, UnavailableReason.INSUFFICIENT_HISTORY)
        self.assertEqual(missing.training_sample_count, 0)
        self.assertIsNone(missing.training_cutoff)

    def test_degenerate_targets_are_unavailable(self):
        frame, dates = synthetic_frame()
        frame.loc[:, "target_return_5"] = frame["target_return_5"].where(
            frame["target_return_5"].isna(), 0.02
        )
        provider = RidgeForecastProvider(frame, minimum_samples=8)

        result = provider.forecast_series("AAA", (dates[10],), (5,))[0]

        self.assertEqual(result.direction, "unavailable")
        self.assertEqual(result.unavailable_reason, UnavailableReason.DEGENERATE_TARGET)

    def test_neutral_bands_are_versioned_and_boundaries_are_neutral(self):
        self.assertEqual(NEUTRAL_BANDS, {5: 0.01, 20: 0.02, 60: 0.04})
        self.assertIsInstance(NEUTRAL_BANDS, MappingProxyType)
        with self.assertRaises(TypeError):
            NEUTRAL_BANDS[5] = 0.99
        for horizon, band in NEUTRAL_BANDS.items():
            with self.subTest(horizon=horizon, side="up"):
                self.assertEqual(direction_for_return(band, horizon), "neutral")
                self.assertEqual(direction_for_return(np.nextafter(band, np.inf), horizon), "up")
            with self.subTest(horizon=horizon, side="down"):
                self.assertEqual(direction_for_return(-band, horizon), "neutral")
                self.assertEqual(
                    direction_for_return(np.nextafter(-band, -np.inf), horizon), "down"
                )

    def test_forecast_requests_validate_dates_tickers_and_horizons(self):
        frame, dates = synthetic_frame()
        provider = RidgeForecastProvider(frame, minimum_samples=4)

        for ticker in ("", "   ", 42):
            with self.subTest(ticker=ticker), self.assertRaises((TypeError, ValueError)):
                provider.forecast_series(ticker, (dates[8],), (5,))
        with self.assertRaisesRegex(ValueError, "valid timestamp"):
            provider.forecast_series("AAA", ("not-a-date",), (5,))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            provider.forecast_series("AAA", (dates[8],), (10,))

    def test_provider_rejects_malformed_forecast_row_keys(self):
        frame, _ = synthetic_frame()
        wrong_names = frame.copy()
        wrong_names.index = wrong_names.index.set_names(("symbol", "date"))
        duplicate = pd.concat((frame, frame.iloc[[0]]))

        with self.assertRaisesRegex(ValueError, "ticker and observation_date"):
            RidgeForecastProvider(wrong_names)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            RidgeForecastProvider(duplicate)


class ForecastRegistryTest(unittest.TestCase):
    def test_registration_is_ordered_and_duplicate_safe(self):
        frame, _ = synthetic_frame()
        first = RidgeForecastProvider(frame, minimum_samples=4)
        duplicate = RidgeForecastProvider(frame, minimum_samples=5)
        registry = ForecastRegistry()

        self.assertIs(registry.register(first), first)
        self.assertEqual(registry.providers, (first,))
        self.assertIs(registry.get(MODEL_KEY), first)
        with self.assertRaises(DuplicateForecastProviderKey):
            registry.register(duplicate)
        self.assertEqual(registry.providers, (first,))
        self.assertIs(registry.get(MODEL_KEY), first)


if __name__ == "__main__":
    unittest.main()
