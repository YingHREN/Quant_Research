import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.downside_shadow import (
    FrozenLinearArtifact,
    fit_frozen_binary_logistic,
    fit_frozen_direction_logistic,
    fit_frozen_ridge,
    predict_frozen_linear,
    read_shadow_model_bundle,
    write_shadow_model_bundle,
)


def training_frame():
    dates = pd.bdate_range("2026-07-01", periods=8)
    return pd.DataFrame(
        {
            "x1": [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0],
            "x2": [4.0, 3.0, 2.0, 1.0, 0.0, -1.0, -2.0, -3.0],
            "downside_event_5": [1, 1, 0, 0, 0, 1, 0, 1],
            "executable_return_5": [
                -0.08,
                -0.04,
                0.0,
                0.05,
                0.08,
                -0.03,
                0.02,
                0.07,
            ],
            "label_end": pd.to_datetime(
                [
                    "2026-07-10",
                    "2026-07-13",
                    "2026-07-14",
                    "2026-07-24",
                    "2026-07-27",
                    "2026-07-28",
                    "2026-07-29",
                    "2026-07-30",
                ]
            ),
        },
        index=pd.MultiIndex.from_arrays(
            [["AAA"] * len(dates), dates],
            names=["ticker", "observation_date"],
        ),
    )


class DownsideShadowArtifactTest(unittest.TestCase):
    def test_frozen_fit_uses_only_labels_ending_by_cutoff(self):
        artifact = fit_frozen_binary_logistic(
            training_frame(),
            feature_columns=("x1", "x2"),
            target_column="downside_event_5",
            label_end_column="label_end",
            frozen_market_asof="2026-07-24",
            specification="pressure_downside_logistic_v1",
            horizon=5,
        )

        self.assertEqual(artifact.training_samples, 4)
        self.assertEqual(artifact.training_label_end_max, "2026-07-24")
        self.assertEqual(artifact.model_kind, "binary_logistic")
        self.assertEqual(artifact.classes, ("0", "1"))

    def test_ridge_and_direction_models_freeze_distinct_target_semantics(self):
        frame = training_frame()
        ridge = fit_frozen_ridge(
            frame,
            feature_columns=("x1", "x2"),
            target_column="executable_return_5",
            label_end_column="label_end",
            frozen_market_asof="2026-07-24",
            specification="ridge_current",
            horizon=5,
            neutral_band=0.01,
        )
        direction = fit_frozen_direction_logistic(
            frame,
            feature_columns=("x1", "x2"),
            target_column="executable_return_5",
            label_end_column="label_end",
            frozen_market_asof="2026-07-24",
            specification="general_logistic",
            horizon=5,
            neutral_band=0.01,
        )

        self.assertEqual(ridge.model_kind, "ridge")
        self.assertEqual(ridge.classes, ())
        self.assertEqual(direction.model_kind, "direction_logistic")
        self.assertEqual(set(direction.classes), {"down", "neutral", "up"})

    def test_frozen_prediction_is_deterministic_and_keeps_source_index(self):
        artifact = fit_frozen_binary_logistic(
            training_frame(),
            feature_columns=("x1", "x2"),
            target_column="downside_event_5",
            label_end_column="label_end",
            frozen_market_asof="2026-07-24",
            specification="pressure_downside_logistic_v1",
            horizon=5,
        )
        source = training_frame().iloc[:2, :2]

        first = predict_frozen_linear(artifact, source)
        second = predict_frozen_linear(artifact, source)

        pd.testing.assert_frame_equal(first, second)
        self.assertTrue(first.index.equals(source.index))
        self.assertEqual(
            list(first.columns),
            [
                "predicted_event",
                "predicted_score",
                "predicted_direction",
                "predicted_return",
            ],
        )
        self.assertTrue(first["predicted_score"].between(0.0, 1.0).all())

    def test_prediction_rejects_missing_or_nonfinite_features(self):
        artifact = fit_frozen_binary_logistic(
            training_frame(),
            feature_columns=("x1", "x2"),
            target_column="downside_event_5",
            label_end_column="label_end",
            frozen_market_asof="2026-07-24",
            specification="pressure_downside_logistic_v1",
            horizon=5,
        )
        with self.assertRaisesRegex(ValueError, "missing features"):
            predict_frozen_linear(artifact, training_frame().loc[:, ["x1"]])
        invalid = training_frame().iloc[:1, :2].copy()
        invalid.iloc[0, 0] = np.inf
        prediction = predict_frozen_linear(artifact, invalid)
        self.assertTrue(np.isfinite(prediction["predicted_score"]).all())

    def test_all_missing_training_feature_is_frozen_as_zero_not_dropped(self):
        frame = training_frame()
        frame["optional_context"] = np.nan

        artifact = fit_frozen_binary_logistic(
            frame,
            feature_columns=("x1", "optional_context"),
            target_column="downside_event_5",
            label_end_column="label_end",
            frozen_market_asof="2026-07-24",
            specification="pressure_downside_logistic_v1",
            horizon=5,
        )

        self.assertEqual(
            artifact.feature_columns,
            ("x1", "optional_context"),
        )
        self.assertEqual(artifact.imputation_values[1], 0.0)

    def test_direction_inference_matches_liblinear_ovr_probability(self):
        artifact = FrozenLinearArtifact(
            specification="general_logistic",
            horizon=5,
            model_kind="direction_logistic",
            feature_columns=("x1",),
            imputation_values=(0.0,),
            centers=(0.0,),
            scales=(1.0,),
            coefficients=((0.0,), (1.0,), (2.0,)),
            intercepts=(0.0, 0.0, 0.0),
            classes=("down", "neutral", "up"),
            event_threshold=0.5,
            neutral_band=0.01,
            training_samples=10,
            training_event_rate=0.3,
            training_label_end_max="2026-07-24",
            frozen_market_asof="2026-07-24",
            model_version="shadow-direction-logistic-v1",
        )

        prediction = predict_frozen_linear(
            artifact,
            pd.DataFrame({"x1": [1.0]}),
        )

        sigmoid = 1.0 / (1.0 + np.exp(-np.asarray([0.0, 1.0, 2.0])))
        expected = sigmoid[0] / sigmoid.sum()
        self.assertAlmostEqual(prediction.iloc[0]["predicted_score"], expected)

    def test_model_bundle_round_trip_verifies_checksum_and_schema(self):
        artifact = fit_frozen_binary_logistic(
            training_frame(),
            feature_columns=("x1", "x2"),
            target_column="downside_event_5",
            label_end_column="label_end",
            frozen_market_asof="2026-07-24",
            specification="pressure_downside_logistic_v1",
            horizon=5,
        )
        bundle = {
            "schema_version": "downside-shadow-model-v1",
            "experiment_id": "downside-shadow-v1",
            "online_authority": "none",
            "models": [artifact],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            checksum = write_shadow_model_bundle(path, bundle)
            loaded = read_shadow_model_bundle(
                path,
                expected_checksum=checksum,
            )

            self.assertEqual(loaded["experiment_id"], "downside-shadow-v1")
            self.assertIsInstance(loaded["models"][0], FrozenLinearArtifact)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["online_authority"] = "override"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "checksum"):
                read_shadow_model_bundle(path, expected_checksum=checksum)

    def test_artifact_rejects_mismatched_parameter_shapes(self):
        with self.assertRaisesRegex(ValueError, "shape"):
            FrozenLinearArtifact(
                specification="ridge_current",
                horizon=5,
                model_kind="ridge",
                feature_columns=("x1", "x2"),
                imputation_values=(0.0, 0.0),
                centers=(0.0, 0.0),
                scales=(1.0, 1.0),
                coefficients=((1.0,),),
                intercepts=(0.0,),
                classes=(),
                event_threshold=0.5,
                neutral_band=0.01,
                training_samples=10,
                training_event_rate=None,
                training_label_end_max="2026-07-24",
                frozen_market_asof="2026-07-24",
                model_version="shadow-ridge-v1",
            )


if __name__ == "__main__":
    unittest.main()
