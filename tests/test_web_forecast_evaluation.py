import math
import unittest

import numpy as np
import pandas as pd

from web.forecasts.base import ForecastResult, UnavailableReason
from web.forecasts.dataset import FEATURE_COLUMNS
from web.forecasts.evaluation import (
    CALIBRATION_INSUFFICIENT_CLASSES,
    CALIBRATION_INSUFFICIENT_SAMPLES,
    CROSS_SECTION_MINIMUM,
    calibrate_up_probability,
    walk_forward_evaluate,
)
from web.forecasts.ridge import MODEL_VERSION, RidgeForecastProvider


EVALUATION_DATE = pd.Timestamp("2025-01-10")


def evaluation_frame(tickers=("A", "B", "C", "D", "E", "F")):
    actuals = {
        "A": -0.030,
        "B": 0.005,
        "C": 0.000,
        "D": 0.040,
        "E": 0.050,
        "F": 0.010,
    }
    dates = pd.bdate_range(end=EVALUATION_DATE, periods=11).append(
        pd.bdate_range(EVALUATION_DATE + pd.offsets.BDay(1), periods=5)
    )
    rows = []
    index = []
    for ticker in tickers:
        for position, observation_date in enumerate(dates):
            row = {"close": 100.0, **{name: 0.0 for name in FEATURE_COLUMNS}}
            row["target_return_5"] = (
                0.02
                if position == 0
                else 99.0
                if position == 5
                else actuals[ticker]
                if position == 10
                else np.nan
            )
            row["label_end_date_5"] = (
                dates[position + 5] if position + 5 < len(dates) else pd.NaT
            )
            rows.append(row)
            index.append((ticker, observation_date))
    return pd.DataFrame(
        rows,
        index=pd.MultiIndex.from_tuples(
            index, names=("ticker", "observation_date")
        ),
    ).sort_index()


class FixedProvider:
    model_key = "fixed"
    model_version = "test-v1"

    def __init__(self, predictions, unavailable_reasons=None):
        self.predictions = dict(predictions)
        self.unavailable_reasons = dict(unavailable_reasons or {})
        self.calls = []

    def forecast_series(self, ticker, dates, horizons):
        self.calls.append((ticker, tuple(dates), tuple(horizons)))
        asof = pd.Timestamp(tuple(dates)[0]).normalize()
        horizon = tuple(horizons)[0]
        prediction = self.predictions.get(ticker) if asof == EVALUATION_DATE else None
        if prediction is None:
            sample_count = 6 if asof == EVALUATION_DATE else 0
            return [
                ForecastResult(
                    ticker=ticker,
                    asof_date=asof,
                    horizon_sessions=horizon,
                    direction="unavailable",
                    predicted_return=None,
                    up_probability=None,
                    confidence_status="unavailable",
                    confidence_reason=None,
                    training_sample_count=sample_count,
                    training_cutoff="2025-01-08" if sample_count else None,
                    model_key=self.model_key,
                    model_version=self.model_version,
                    unavailable_reason=self.unavailable_reasons.get(
                        ticker, UnavailableReason.INSUFFICIENT_TRAINING_SAMPLES
                    ),
                )
            ]
        if prediction > 0.01:
            direction = "up"
        elif prediction < -0.01:
            direction = "down"
        else:
            direction = "neutral"
        return [
            ForecastResult(
                ticker=ticker,
                asof_date=asof,
                horizon_sessions=horizon,
                direction=direction,
                predicted_return=prediction,
                up_probability=None,
                confidence_status="uncalibrated",
                confidence_reason=CALIBRATION_INSUFFICIENT_SAMPLES,
                training_sample_count=6,
                training_cutoff="2025-01-08",
                model_key=self.model_key,
                model_version=self.model_version,
            )
        ]


class WalkForwardEvaluationTest(unittest.TestCase):
    def test_metrics_use_available_predictions_and_point_in_time_baselines(self):
        predictions = {
            "A": -0.040,
            "B": -0.005,
            "C": 0.000,
            "D": 0.020,
            "E": 0.060,
            "F": None,
        }
        provider = FixedProvider(predictions)

        result = walk_forward_evaluate(evaluation_frame(), 5, provider)

        errors = np.array((-0.010, -0.010, 0.000, -0.020, 0.010))
        expected_ic = pd.Series(
            [predictions[ticker] for ticker in "ABCDE"]
        ).rank().corr(
            pd.Series([-0.030, 0.005, 0.000, 0.040, 0.050]).rank()
        )
        self.assertEqual(result.sample_count, 5)
        self.assertAlmostEqual(result.coverage, 5 / 18)
        self.assertAlmostEqual(result.mae, np.mean(np.abs(errors)))
        self.assertAlmostEqual(result.rmse, math.sqrt(np.mean(errors**2)))
        self.assertEqual(result.direction_accuracy, 1.0)
        self.assertAlmostEqual(result.always_up_direction_accuracy, 2 / 5)
        self.assertEqual(result.balanced_accuracy, 1.0)
        self.assertEqual(result.macro_f1, 1.0)
        self.assertEqual(result.non_overlapping_sample_count, 5)
        self.assertEqual(result.non_overlapping_direction_accuracy, 1.0)
        self.assertEqual(result.evidence_status, "insufficient")
        self.assertAlmostEqual(result.zero_return_mae, 0.025)
        # Every baseline is 2%; the 99.0 trap ending on the as-of date is excluded.
        self.assertAlmostEqual(result.historical_mean_mae, 0.027)
        self.assertAlmostEqual(result.rank_ic, expected_ic)
        self.assertEqual(
            dict(result.signal_bucket_returns),
            {"down": -0.030, "neutral": 0.0025, "up": 0.045},
        )
        self.assertEqual(result.evaluation_start, EVALUATION_DATE)
        self.assertEqual(result.evaluation_end, EVALUATION_DATE)
        self.assertEqual(result.model_key, "fixed")
        self.assertEqual(result.model_version, "test-v1")
        self.assertIsNone(result.unavailable_reason)
        self.assertEqual(len(provider.calls), 18)
        self.assertTrue(all(call[2] == (5,) for call in provider.calls))

    def test_rank_ic_and_signal_buckets_are_explicitly_unavailable_below_threshold(self):
        self.assertEqual(CROSS_SECTION_MINIMUM, 5)
        tickers = ("A", "B", "C", "D")
        provider = FixedProvider(
            {"A": -0.040, "B": -0.005, "C": 0.000, "D": 0.020}
        )

        result = walk_forward_evaluate(evaluation_frame(tickers), 5, provider)

        self.assertEqual(result.sample_count, 4)
        self.assertEqual(result.coverage, 4 / 12)
        self.assertIsNone(result.rank_ic)
        self.assertEqual(
            dict(result.signal_bucket_returns),
            {"down": None, "neutral": None, "up": None},
        )

    def test_no_available_predictions_returns_typed_unavailable_evaluation(self):
        frame = evaluation_frame(("A",))
        result = walk_forward_evaluate(frame, 5, FixedProvider({"A": None}))

        self.assertEqual(result.sample_count, 0)
        self.assertEqual(result.coverage, 0.0)
        self.assertIsNone(result.mae)
        self.assertIsNone(result.rmse)
        self.assertIsNone(result.direction_accuracy)
        self.assertIsNone(result.always_up_direction_accuracy)
        self.assertIsNone(result.balanced_accuracy)
        self.assertIsNone(result.macro_f1)
        self.assertEqual(result.non_overlapping_sample_count, 0)
        self.assertIsNone(result.non_overlapping_direction_accuracy)
        self.assertEqual(result.evidence_status, "insufficient")
        self.assertIsNone(result.zero_return_mae)
        self.assertIsNone(result.historical_mean_mae)
        self.assertIsNone(result.rank_ic)
        self.assertEqual(dict(result.signal_bucket_returns), {})
        self.assertEqual(
            result.evaluation_start,
            frame.index.get_level_values("observation_date").min(),
        )
        self.assertEqual(result.evaluation_end, EVALUATION_DATE)
        self.assertEqual(result.model_key, "fixed")
        self.assertEqual(result.model_version, "test-v1")
        self.assertEqual(
            result.unavailable_reason,
            UnavailableReason.INSUFFICIENT_TRAINING_SAMPLES,
        )

    def test_empty_valid_frame_is_explicitly_unavailable(self):
        provider = FixedProvider({})

        result = walk_forward_evaluate(evaluation_frame().iloc[0:0], 5, provider)

        self.assertEqual(result.sample_count, 0)
        self.assertEqual(result.unavailable_reason, UnavailableReason.INSUFFICIENT_HISTORY)
        self.assertEqual(provider.calls, [])

    def test_no_forecasts_preserves_unanimous_or_mixed_provider_reasons(self):
        cases = (
            (
                ("A",),
                {"A": UnavailableReason.DEGENERATE_TARGET},
                "degenerate_target",
            ),
            (
                ("A",),
                {"A": UnavailableReason.MODEL_ERROR},
                "model_error",
            ),
            (
                ("A", "B"),
                {
                    "A": UnavailableReason.DEGENERATE_TARGET,
                    "B": UnavailableReason.MODEL_ERROR,
                },
                "no_available_forecasts",
            ),
        )
        for tickers, reasons, expected in cases:
            with self.subTest(expected=expected):
                result = walk_forward_evaluate(
                    evaluation_frame(tickers),
                    5,
                    FixedProvider({}, unavailable_reasons=reasons),
                )

                self.assertEqual(result.unavailable_reason.value, expected)
                self.assertEqual(result.coverage, 0.0)


class CalibrationTest(unittest.TestCase):
    def test_calibration_minimum_cannot_weaken_the_100_row_gate(self):
        with self.assertRaisesRegex(ValueError, "at least 100"):
            calibrate_up_probability([0.1], [], horizon=5, minimum_samples=99)

        stricter = calibrate_up_probability(
            [*np.linspace(-1.0, 1.0, 100), 0.5],
            np.resize((-0.01, 0.02), 100),
            horizon=5,
            minimum_samples=101,
        )

        self.assertIsNone(stricter.up_probability)
        self.assertEqual(stricter.sample_count, 100)
        self.assertEqual(stricter.reason, CALIBRATION_INSUFFICIENT_SAMPLES)

    def test_monotonic_empirical_calibration_requires_earlier_oos_rows(self):
        history_predictions = np.linspace(-1.0, 1.0, 100)
        history_actuals = np.where(history_predictions > 0.0, 0.02, -0.01)

        low = calibrate_up_probability(
            [*history_predictions, -0.75], history_actuals, horizon=5
        )
        high = calibrate_up_probability(
            [*history_predictions, 0.75], history_actuals, horizon=5
        )

        self.assertEqual(low.sample_count, 100)
        self.assertIsNone(low.reason)
        self.assertIsNone(high.reason)
        self.assertLessEqual(low.up_probability, high.up_probability)
        self.assertEqual(low.up_probability, 0.0)
        self.assertEqual(high.up_probability, 1.0)

        # A same-row outcome is never allowed to influence its own probability.
        with_current_down = calibrate_up_probability(
            [*history_predictions, 0.75], [*history_actuals, -1.0], horizon=5
        )
        with_current_up = calibrate_up_probability(
            [*history_predictions, 0.75], [*history_actuals, 1.0], horizon=5
        )
        self.assertEqual(with_current_down, with_current_up)
        self.assertEqual(with_current_up.up_probability, high.up_probability)

    def test_calibration_gate_preserves_none_and_reason(self):
        insufficient = calibrate_up_probability(
            [*np.linspace(-1.0, 1.0, 99), 0.5],
            np.resize((-0.01, 0.02), 99),
            horizon=5,
        )
        one_class = calibrate_up_probability(
            [*np.linspace(-1.0, 1.0, 100), 0.5],
            np.full(100, 0.02),
            horizon=5,
        )

        self.assertIsNone(insufficient.up_probability)
        self.assertEqual(insufficient.sample_count, 99)
        self.assertEqual(insufficient.reason, CALIBRATION_INSUFFICIENT_SAMPLES)
        self.assertIsNone(one_class.up_probability)
        self.assertEqual(one_class.sample_count, 100)
        self.assertEqual(one_class.reason, CALIBRATION_INSUFFICIENT_CLASSES)

    def test_positive_but_neutral_actual_is_not_an_up_outcome(self):
        history_predictions = np.linspace(-1.0, 1.0, 100)
        actuals = np.where(history_predictions > 0.0, 0.02, 0.005)

        low = calibrate_up_probability(
            [*history_predictions, -0.75], actuals, horizon=5
        )
        high = calibrate_up_probability(
            [*history_predictions, 0.75], actuals, horizon=5
        )

        self.assertIsNone(low.reason)
        self.assertIsNone(high.reason)
        self.assertEqual(low.up_probability, 0.0)
        self.assertEqual(high.up_probability, 1.0)

    def test_ridge_uses_only_matured_oos_calibration_and_never_fabricates_probability(self):
        from tests.test_web_forecasts import synthetic_frame

        frame, dates = synthetic_frame(periods=20)
        history_predictions = np.linspace(-1.0, 1.0, 100)
        history = pd.DataFrame(
            {
                "ticker": "AAA",
                "asof_date": pd.bdate_range("2024-01-02", periods=100),
                "label_end_date": pd.bdate_range("2024-01-09", periods=100),
                "training_cutoff": pd.bdate_range("2023-01-02", periods=100),
                "horizon_sessions": 5,
                "predicted_return": history_predictions,
                "actual_return": np.where(history_predictions > 0.0, 0.02, -0.01),
                "model_key": "ridge_direction_v1",
                "model_version": MODEL_VERSION,
            }
        )
        poisoned = pd.DataFrame(
            {
                "ticker": ["AAA"],
                "asof_date": [dates[8]],
                "label_end_date": [dates[10]],
                "training_cutoff": [dates[7]],
                "horizon_sessions": [5],
                "predicted_return": [1.0],
                "actual_return": [-1.0],
                "model_key": ["ridge_direction_v1"],
                "model_version": [MODEL_VERSION],
            }
        )

        calibrated = RidgeForecastProvider(
            frame,
            minimum_samples=4,
            calibration_history=pd.concat((history, poisoned), ignore_index=True),
        ).forecast_series("AAA", (dates[10],), (5,))[0]
        uncalibrated = RidgeForecastProvider(
            frame,
            minimum_samples=4,
            calibration_history=history.iloc[:-1],
        ).forecast_series("AAA", (dates[10],), (5,))[0]
        one_class_history = history.assign(actual_return=0.02)
        one_class = RidgeForecastProvider(
            frame,
            minimum_samples=4,
            calibration_history=one_class_history,
        ).forecast_series("AAA", (dates[10],), (5,))[0]
        other_ticker = RidgeForecastProvider(
            frame,
            minimum_samples=4,
            calibration_history=history.assign(ticker="BBB"),
        ).forecast_series("AAA", (dates[10],), (5,))[0]

        self.assertEqual(calibrated.confidence_status, "calibrated")
        self.assertIsNotNone(calibrated.up_probability)
        self.assertEqual(calibrated.up_probability, 1.0)
        self.assertIsNone(calibrated.confidence_reason)
        self.assertEqual(uncalibrated.confidence_status, "uncalibrated")
        self.assertIsNone(uncalibrated.up_probability)
        self.assertEqual(
            uncalibrated.confidence_reason, CALIBRATION_INSUFFICIENT_SAMPLES
        )
        self.assertEqual(one_class.confidence_status, "uncalibrated")
        self.assertIsNone(one_class.up_probability)
        self.assertEqual(
            one_class.confidence_reason, CALIBRATION_INSUFFICIENT_CLASSES
        )
        self.assertEqual(other_ticker.confidence_status, "uncalibrated")
        self.assertIsNone(other_ticker.up_probability)
        self.assertEqual(
            other_ticker.confidence_reason, CALIBRATION_INSUFFICIENT_SAMPLES
        )

        for missing_provenance in (
            "ticker",
            "training_cutoff",
            "model_key",
            "model_version",
        ):
            with self.subTest(missing_provenance=missing_provenance):
                with self.assertRaisesRegex(ValueError, "missing columns"):
                    RidgeForecastProvider(
                        frame,
                        minimum_samples=4,
                        calibration_history=history.drop(columns=missing_provenance),
                    )

    def test_calibration_history_requires_unique_oos_observation_identity(self):
        from tests.test_web_forecasts import synthetic_frame

        frame, _ = synthetic_frame(periods=20)
        row = pd.DataFrame(
            {
                "ticker": ["AAA"],
                "asof_date": ["2024-01-02"],
                "label_end_date": ["2024-01-09"],
                "training_cutoff": ["2023-12-29"],
                "horizon_sessions": [5],
                "predicted_return": [0.1],
                "actual_return": [0.2],
                "model_key": ["ridge_direction_v1"],
                "model_version": ["v1"],
            }
        )

        with self.assertRaisesRegex(ValueError, "duplicate calibration observation"):
            RidgeForecastProvider(
                frame,
                calibration_history=pd.concat([row] * 100, ignore_index=True),
            )

        for cutoff in ("2024-01-02", "2024-01-03"):
            with self.subTest(cutoff=cutoff), self.assertRaisesRegex(
                ValueError, "cutoffs must precede"
            ):
                RidgeForecastProvider(
                    frame,
                    calibration_history=row.assign(training_cutoff=cutoff),
                )


if __name__ == "__main__":
    unittest.main()
