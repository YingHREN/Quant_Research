import unittest
from dataclasses import FrozenInstanceError
import json

import numpy as np
import pandas as pd
from pandas.testing import assert_series_equal

from web.forecasts.base import (
    ForecastEvaluation,
    ForecastResult,
    UnavailableReason,
)
from web.forecasts.dataset import (
    FEATURE_COLUMNS,
    attach_forward_targets,
    build_feature_frame,
    eligible_training_rows,
)


def price_history(start="2025-01-02", periods=280, close=None):
    dates = pd.bdate_range(start=start, periods=periods)
    values = (
        np.arange(100.0, 100.0 + periods)
        if close is None
        else np.asarray(close, dtype=float)
    )
    return pd.DataFrame(
        {
            "Open": values - 0.5,
            "High": values + 1.0,
            "Low": values - 1.0,
            "Close": values,
            "Volume": np.arange(1_000_000.0, 1_000_000.0 + periods),
        },
        index=dates,
    )


class ForecastDatasetTest(unittest.TestCase):
    def test_forward_target_uses_ticker_local_session_positions(self):
        histories = {
            "AAA": price_history(periods=80),
            "BBB": price_history(periods=80, close=np.arange(200.0, 280.0)),
        }
        frame = attach_forward_targets(build_feature_frame(histories), horizons=(5,))

        aaa = frame.xs("AAA", level="ticker")
        self.assertAlmostEqual(aaa["target_return_5"].iloc[7], 112.0 / 107.0 - 1)
        self.assertEqual(aaa["label_end_date_5"].iloc[7], aaa.index[12])
        self.assertTrue(aaa["target_return_5"].iloc[-5:].isna().all())
        self.assertTrue(aaa["label_end_date_5"].iloc[-5:].isna().all())

        bbb = frame.xs("BBB", level="ticker")
        self.assertAlmostEqual(bbb["target_return_5"].iloc[7], 212.0 / 207.0 - 1)

    def test_training_label_must_end_strictly_before_forecast_date(self):
        frame = attach_forward_targets(
            build_feature_frame({"AAA": price_history(periods=80)}), horizons=(5,)
        )
        asof = frame.xs("AAA", level="ticker").index[20]

        eligible = eligible_training_rows(frame, asof, 5)

        self.assertTrue((eligible["label_end_date_5"] < asof).all())
        self.assertEqual(eligible.index.get_level_values("observation_date").max(),
                         frame.xs("AAA", level="ticker").index[14])
        boundary = frame.xs("AAA", level="ticker").index[15]
        self.assertNotIn(("AAA", boundary), eligible.index)

    def test_future_price_spike_cannot_change_features_at_cutoff(self):
        history = price_history(periods=280)
        cutoff = history.index[259]
        trapped = history.copy()
        trapped.loc[trapped.index > cutoff, ["Open", "High", "Low", "Close"]] *= 100

        baseline = build_feature_frame({"AAA": history}).loc[("AAA", cutoff)]
        future_spike = build_feature_frame({"AAA": trapped}).loc[("AAA", cutoff)]

        assert_series_equal(
            baseline.loc[list(FEATURE_COLUMNS)],
            future_spike.loc[list(FEATURE_COLUMNS)],
            check_names=False,
        )

    def test_default_targets_align_all_supported_horizons(self):
        frame = attach_forward_targets(
            build_feature_frame({"AAA": price_history(periods=90)})
        ).xs("AAA", level="ticker")

        for horizon in (5, 20, 60):
            with self.subTest(horizon=horizon):
                self.assertAlmostEqual(
                    frame[f"target_return_{horizon}"].iloc[3],
                    (103.0 + horizon) / 103.0 - 1.0,
                )
                self.assertEqual(
                    frame[f"label_end_date_{horizon}"].iloc[3],
                    frame.index[3 + horizon],
                )
                self.assertTrue(
                    frame[f"target_return_{horizon}"].iloc[-horizon:].isna().all()
                )

    def test_sparse_and_nan_histories_preserve_rows_and_missing_features(self):
        history = price_history(periods=30)
        missing_date = history.index[10]
        history.loc[missing_date, "Close"] = np.nan
        original = history.copy(deep=True)

        frame = build_feature_frame({"SPARSE": history})

        self.assertEqual(len(frame), len(history))
        self.assertTrue(frame.loc[("SPARSE", missing_date), "close"] !=
                        frame.loc[("SPARSE", missing_date), "close"])
        self.assertTrue(frame["mom_3_1"].isna().all())
        self.assertTrue(frame["close_vs_sma200_pct"].isna().all())
        self.assertFalse(np.isinf(frame.select_dtypes(include=["number"])).any(axis=None))
        pd.testing.assert_frame_equal(history, original)

    def test_duplicate_observation_dates_are_rejected(self):
        history = price_history(periods=20)
        duplicate = pd.concat([history, history.iloc[[5]]])

        with self.assertRaisesRegex(ValueError, "duplicate observation dates"):
            build_feature_frame({"AAA": duplicate})

    def test_duplicate_feature_keys_are_rejected_before_labeling(self):
        frame = build_feature_frame({"AAA": price_history(periods=20)})
        duplicated = pd.concat([frame, frame.iloc[[0]]])

        with self.assertRaisesRegex(ValueError, "duplicate"):
            attach_forward_targets(duplicated)

    def test_missing_target_and_invalid_horizon_fail_closed(self):
        frame = build_feature_frame({"AAA": price_history(periods=20)})

        with self.assertRaisesRegex(ValueError, "forward-label"):
            eligible_training_rows(frame, "2026-01-01", 5)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            attach_forward_targets(frame, horizons=(10,))
        with self.assertRaisesRegex(ValueError, "integer"):
            attach_forward_targets(frame, horizons=(5.0,))

    def test_eligibility_rejects_misaligned_or_non_datetime_label_metadata(self):
        frame = attach_forward_targets(
            build_feature_frame({"AAA": price_history(periods=20)}), horizons=(5,)
        )
        asof = frame.index.get_level_values("observation_date")[-1]
        malformed = frame.copy()
        malformed.loc[malformed.index[10], "label_end_date_5"] = malformed.index[5][1]

        with self.assertRaisesRegex(ValueError, "after observation_date"):
            eligible_training_rows(malformed, asof, 5)

        strings = frame.copy()
        strings["label_end_date_5"] = strings["label_end_date_5"].astype(str)
        with self.assertRaisesRegex(ValueError, "datetime"):
            eligible_training_rows(strings, asof, 5)


class ForecastContractTest(unittest.TestCase):
    def test_forecast_result_is_immutable_and_json_safe(self):
        result = ForecastResult(
            ticker="AAA",
            asof_date=pd.Timestamp("2026-07-22"),
            horizon_sessions=np.int64(20),
            direction="unavailable",
            predicted_return=np.nan,
            up_probability=None,
            confidence_status="unavailable",
            training_sample_count=np.int64(0),
            training_cutoff=pd.Timestamp("2026-07-21"),
            model_key="ridge_direction_v1",
            model_version="v1",
            unavailable_reason=UnavailableReason.INSUFFICIENT_HISTORY,
        )

        self.assertEqual(
            result.to_dict(),
            {
                "ticker": "AAA",
                "asof_date": "2026-07-22",
                "horizon_sessions": 20,
                "direction": "unavailable",
                "predicted_return": None,
                "up_probability": None,
                "confidence_status": "unavailable",
                "training_sample_count": 0,
                "training_cutoff": "2026-07-21",
                "model_key": "ridge_direction_v1",
                "model_version": "v1",
                "unavailable_reason": "insufficient_history",
            },
        )
        with self.assertRaises(FrozenInstanceError):
            result.direction = "up"
        json.dumps(result.to_dict(), allow_nan=False)

    def test_forecast_result_rejects_incoherent_availability_states(self):
        common = {
            "ticker": "AAA",
            "asof_date": "2026-07-22",
            "horizon_sessions": 20,
            "training_sample_count": 100,
            "training_cutoff": "2026-07-21",
            "model_key": "ridge_direction_v1",
            "model_version": "v1",
        }
        invalid_states = (
            dict(direction="unavailable", predicted_return=0.1,
                 up_probability=None, confidence_status="unavailable",
                 unavailable_reason="model_error"),
            dict(direction="unavailable", predicted_return=None,
                 up_probability=0.7, confidence_status="calibrated",
                 unavailable_reason="model_error"),
            dict(direction="up", predicted_return=None,
                 up_probability=None, confidence_status="uncalibrated",
                 unavailable_reason=None),
            dict(direction="up", predicted_return=0.1,
                 up_probability=None, confidence_status="calibrated",
                 unavailable_reason=None),
            dict(direction="up", predicted_return=0.1,
                 up_probability=0.7, confidence_status="uncalibrated",
                 unavailable_reason=None),
        )

        for invalid in invalid_states:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ForecastResult(**common, **invalid)

    def test_contracts_reject_non_json_scalars_and_normalize_missing_dates(self):
        with self.assertRaises((TypeError, ValueError)):
            ForecastResult(
                ticker="AAA", asof_date="2026-07-22", horizon_sessions=20,
                direction="up", predicted_return=object(), up_probability=None,
                confidence_status="uncalibrated", training_sample_count=100,
                training_cutoff="2026-07-21", model_key="ridge_direction_v1",
                model_version="v1",
            )

        evaluation = ForecastEvaluation(
            horizon_sessions=20, sample_count=0, coverage=None, mae=None,
            rmse=None, direction_accuracy=None, zero_return_mae=None,
            historical_mean_mae=None, rank_ic=None,
            signal_bucket_returns={}, evaluation_start=pd.NaT,
            evaluation_end=None, model_key="ridge_direction_v1", model_version="v1",
            unavailable_reason="insufficient_training_samples",
        )
        self.assertIsNone(evaluation.to_dict()["evaluation_start"])
        json.dumps(evaluation.to_dict(), allow_nan=False)

        with self.assertRaises((TypeError, ValueError)):
            ForecastEvaluation(
                horizon_sessions=20, sample_count=1, coverage=1.0, mae=0.1,
                rmse=0.1, direction_accuracy=0.5, zero_return_mae=0.1,
                historical_mean_mae=0.1, rank_ic=None,
                signal_bucket_returns={"up": np.array([1.0])},
            )

    def test_evaluation_contract_copies_nested_metrics_immutably(self):
        buckets = {"down": np.float64(-0.02), "up": np.nan}
        evaluation = ForecastEvaluation(
            horizon_sessions=20,
            sample_count=np.int64(40),
            coverage=np.float64(0.5),
            mae=np.float64(0.03),
            rmse=np.float64(0.04),
            direction_accuracy=np.float64(0.6),
            zero_return_mae=np.float64(0.05),
            historical_mean_mae=np.float64(0.045),
            rank_ic=np.nan,
            signal_bucket_returns=buckets,
            evaluation_start=pd.Timestamp("2026-01-02"),
            evaluation_end=pd.Timestamp("2026-07-22"),
            model_key="ridge_direction_v1",
            model_version="v1",
        )
        buckets["down"] = 999

        self.assertEqual(evaluation.signal_bucket_returns["down"], -0.02)
        with self.assertRaises(TypeError):
            evaluation.signal_bucket_returns["down"] = 1
        self.assertEqual(
            evaluation.to_dict(),
            {
                "horizon_sessions": 20,
                "sample_count": 40,
                "coverage": 0.5,
                "mae": 0.03,
                "rmse": 0.04,
                "direction_accuracy": 0.6,
                "zero_return_mae": 0.05,
                "historical_mean_mae": 0.045,
                "rank_ic": None,
                "signal_bucket_returns": {"down": -0.02, "up": None},
                "evaluation_start": "2026-01-02",
                "evaluation_end": "2026-07-22",
                "model_key": "ridge_direction_v1",
                "model_version": "v1",
                "unavailable_reason": None,
            },
        )

    def test_unavailable_reasons_are_typed_and_complete(self):
        self.assertEqual(
            {reason.value for reason in UnavailableReason},
            {
                "insufficient_history",
                "insufficient_training_samples",
                "degenerate_target",
                "model_error",
            },
        )
        with self.assertRaisesRegex(ValueError, "unavailable_reason"):
            ForecastResult(
                ticker="AAA",
                asof_date="2026-07-22",
                horizon_sessions=20,
                direction="unavailable",
                predicted_return=None,
                up_probability=None,
                confidence_status="unavailable",
                training_sample_count=0,
                training_cutoff=None,
                model_key="ridge_direction_v1",
                model_version="v1",
                unavailable_reason="mystery",
            )


if __name__ == "__main__":
    unittest.main()
