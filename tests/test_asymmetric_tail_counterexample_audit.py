import unittest

import numpy as np
import pandas as pd

from research.asymmetric_tail_counterexample_audit import (
    attach_point_in_time_context,
    preregistered_feature_hypotheses,
    resolve_point_in_time_groups,
    summarize_counterexamples,
)


def _feature_frame():
    dates = pd.to_datetime(("2026-01-02", "2026-01-05"))
    return pd.DataFrame(
        {
            "close": [4.0, 10.0, 50.0, 150.0, 8.0, 80.0],
            "atr20_pct": [0.10, 0.20, 0.40, 0.80, 0.30, np.nan],
            "realized_vol_63": [0.20, 0.40, 0.80, 1.60, 0.30, np.nan],
        },
        index=pd.MultiIndex.from_tuples(
            (
                ("AAA", dates[0]),
                ("BBB", dates[0]),
                ("CCC", dates[0]),
                ("DDD", dates[0]),
                ("AAA", dates[1]),
                ("BBB", dates[1]),
            ),
            names=("ticker", "observation_date"),
        ),
    )


def _counterexamples():
    dates = pd.to_datetime(("2026-01-02", "2026-01-05"))
    return pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "observation_date": dates,
            "fold": [1, 1],
            "group": ["software", "semiconductor"],
            "regime": ["uptrend", "correction"],
            "calibrated_down_probability": [0.40, 0.60],
            "calibrated_rebound_probability": [0.20, 0.30],
            "actual_terminal_return": [0.10, 1.10],
            "actual_path_mae": [-0.02, -0.08],
            "opening_gap": [-0.04, 0.04],
            "dollar_volume": [5_000_000.0, 2_000_000_000.0],
            "earnings_proximity": [None, "untrusted"],
        }
    )


class PointInTimeCounterexampleAuditTest(unittest.TestCase):
    def test_attaches_exact_date_context_and_fixed_descriptive_bands(self):
        audited = attach_point_in_time_context(
            _counterexamples(),
            _feature_frame(),
        )

        first = audited.iloc[0]
        second = audited.iloc[1]
        self.assertEqual(first["price"], 4.0)
        self.assertEqual(first["realized_volatility_percentile"], 0.25)
        self.assertEqual(first["atr20_percentile"], 0.25)
        self.assertEqual(first["opening_gap_band"], "gap_down_3pct_or_more")
        self.assertEqual(first["realized_volatility_band"], "low_25pct")
        self.assertEqual(first["atr20_band"], "low_25pct")
        self.assertEqual(first["price_band"], "below_5")
        self.assertEqual(first["dollar_volume_band"], "below_10m")
        self.assertEqual(second["opening_gap_band"], "gap_up_3pct_or_more")
        self.assertEqual(second["realized_volatility_band"], "unavailable")
        self.assertEqual(second["atr20_band"], "unavailable")
        self.assertEqual(second["price_band"], "20_to_100")
        self.assertEqual(second["dollar_volume_band"], "at_least_1b")
        self.assertEqual(set(audited["group"]), {"unavailable"})
        self.assertEqual(
            set(audited["point_in_time_group_status"]),
            {"unavailable"},
        )
        self.assertEqual(
            list(audited["published_group"]),
            ["software", "semiconductor"],
        )
        self.assertEqual(
            set(audited["earnings_proximity_status"]),
            {"unavailable"},
        )

    def test_rejects_wrong_sample_definition_and_missing_exact_keys(self):
        below_threshold = _counterexamples()
        below_threshold.loc[0, "calibrated_down_probability"] = 0.39
        with self.assertRaisesRegex(ValueError, "sample definition"):
            attach_point_in_time_context(
                below_threshold,
                _feature_frame(),
            )

        missing_key = _counterexamples()
        missing_key.loc[0, "ticker"] = "ZZZ"
        with self.assertRaisesRegex(ValueError, "feature keys"):
            attach_point_in_time_context(
                missing_key,
                _feature_frame(),
            )

    def test_fixed_band_boundaries_follow_the_published_inequalities(self):
        features = _feature_frame()
        features.loc[("AAA", pd.Timestamp("2026-01-02")), "close"] = 5.0
        features.loc[("BBB", pd.Timestamp("2026-01-05")), "close"] = 100.0
        counterexamples = _counterexamples()
        counterexamples["opening_gap"] = [-0.03, 0.03]
        counterexamples["dollar_volume"] = [
            10_000_000.0,
            1_000_000_000.0,
        ]

        audited = attach_point_in_time_context(
            counterexamples,
            features,
        )

        self.assertEqual(
            list(audited["opening_gap_band"]),
            ["within_3pct", "gap_up_3pct_or_more"],
        )
        self.assertEqual(
            list(audited["price_band"]),
            ["5_to_20", "at_least_100"],
        )
        self.assertEqual(
            list(audited["dollar_volume_band"]),
            ["10m_to_100m", "at_least_1b"],
        )

    def test_summary_preserves_raw_extreme_return_and_counts_each_stratum(self):
        audited = attach_point_in_time_context(
            _counterexamples(),
            _feature_frame(),
        )

        summary = summarize_counterexamples(audited)
        overall = summary.loc[
            (summary["dimension"] == "overall")
            & (summary["stratum"] == "all")
        ].iloc[0]
        groups = summary.loc[summary["dimension"] == "group"]

        self.assertEqual(overall["row_count"], 2)
        self.assertEqual(overall["share"], 1.0)
        self.assertAlmostEqual(overall["mean_terminal_return"], 0.60)
        self.assertEqual(
            dict(zip(groups["stratum"], groups["row_count"])),
            {"unavailable": 2},
        )

    def test_point_in_time_groups_reject_backfill_and_late_observations(self):
        dates = pd.to_datetime(("2026-01-02", "2026-01-05"))
        counterexamples = _counterexamples()
        intervals = pd.DataFrame(
            {
                "ticker": ["AAA", "BBB", "BBB"],
                "effective_from": [
                    "2020-01-01",
                    "2020-01-01",
                    "2020-01-01",
                ],
                "effective_to": [None, None, None],
                "group": ["software", "software", "semiconductor"],
                "source": [
                    "sec_filing",
                    "historical_backfill_assumption/sec_exact",
                    "sec_filing",
                ],
                "observed_at": [
                    "2025-12-31",
                    "2025-12-31",
                    "2026-01-06",
                ],
            }
        )

        groups = resolve_point_in_time_groups(counterexamples, intervals)
        audited = attach_point_in_time_context(
            counterexamples,
            _feature_frame(),
            point_in_time_groups=groups,
        )

        self.assertEqual(audited.loc[0, "group"], "software")
        self.assertEqual(audited.loc[0, "point_in_time_group_status"], "available")
        self.assertEqual(audited.loc[1, "group"], "unavailable")
        self.assertEqual(
            audited.loc[1, "point_in_time_group_status"],
            "unavailable",
        )
        self.assertEqual(list(audited["observation_date"]), list(dates))

    def test_preregistered_hypotheses_cannot_grant_online_authority(self):
        hypotheses = preregistered_feature_hypotheses()

        self.assertGreaterEqual(len(hypotheses), 4)
        self.assertTrue(
            all(item["status"] == "preregistered" for item in hypotheses)
        )
        self.assertTrue(
            all(item["online_authority"] == "none" for item in hypotheses)
        )
        self.assertIn(
            "earnings_calendar_availability",
            {item["name"] for item in hypotheses},
        )


if __name__ == "__main__":
    unittest.main()
