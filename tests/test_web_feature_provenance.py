import json
import unittest

from web.forecasts.dataset import RIDGE_V4_FEATURE_COLUMNS
from web.forecasts.feature_provenance import (
    FeatureDefinition,
    FeatureProvenanceRegistry,
    default_feature_provenance_registry,
)


class FeatureProvenanceRegistryTest(unittest.TestCase):
    def test_default_registry_covers_every_ridge_v4_feature_once(self):
        registry = default_feature_provenance_registry()
        contract = registry.public_contract()

        self.assertEqual(
            [row["key"] for row in contract["features"]],
            list(RIDGE_V4_FEATURE_COLUMNS),
        )
        self.assertEqual(contract["version"], "feature_provenance_registry_v1")
        self.assertEqual(contract["feature_version"], "ridge-features-v2")
        self.assertEqual(len(contract["features"]), len(set(RIDGE_V4_FEATURE_COLUMNS)))
        json.dumps(contract, allow_nan=False)

    def test_snapshot_uses_new_york_close_and_handles_daylight_saving(self):
        registry = default_feature_provenance_registry()

        winter = registry.snapshot("2026-01-15", "a" * 64)
        summer = registry.snapshot("2026-07-23", "b" * 64)

        self.assertEqual(winter["available_at"], "2026-01-15T16:00:00-05:00")
        self.assertEqual(summer["available_at"], "2026-07-23T16:00:00-04:00")
        self.assertEqual(summer["observed_through"], "2026-07-23")
        self.assertEqual(summer["source_cutoff"], "2026-07-23")
        self.assertEqual(
            summer["registry_ref"],
            "feature_provenance_registry_v1",
        )
        self.assertEqual(summer["feature_version"], "ridge-features-v2")
        self.assertEqual(summer["data_version"], "b" * 64)

    def test_registry_rejects_duplicate_features_and_invalid_snapshots(self):
        feature = FeatureDefinition(
            key="momentum",
            source="daily_ohlcv",
            availability="session_close",
            execution_timing="next_session_open",
        )
        with self.assertRaises(ValueError):
            FeatureProvenanceRegistry(
                "registry-v1",
                "features-v1",
                (feature, feature),
            )

        registry = FeatureProvenanceRegistry(
            "registry-v1",
            "features-v1",
            (feature,),
        )
        invalid_cases = (
            {"source_cutoff": "2026-07-24"},
            {"available_at": "2026-07-23T15:59:59-04:00"},
            {"available_at": "2026-07-24T16:00:00-04:00"},
            {"data_version": "not-a-content-hash"},
        )
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                registry.snapshot(
                    "2026-07-23",
                    overrides.pop("data_version", "c" * 64),
                    **overrides,
                )


if __name__ == "__main__":
    unittest.main()
