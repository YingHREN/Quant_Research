import unittest

import pandas as pd

from research.policy_context import (
    POLICY_SERIES_IDS,
    build_policy_context,
)


def observation(series_id, date, available_at, value, vintage=None):
    return {
        "series_id": series_id,
        "observation_date": date,
        "available_at": available_at,
        "value": value,
        "realtime_start": vintage or date,
        "realtime_end": "9999-12-31",
        "source": "test",
    }


def policy_observations():
    rows = [
        observation(
            "DFEDTARL",
            "2026-04-01",
            "2026-04-01T23:59:59+00:00",
            3.50,
        ),
        observation(
            "DFEDTARU",
            "2026-04-01",
            "2026-04-01T23:59:59+00:00",
            3.75,
        ),
        observation(
            "DFEDTARL",
            "2026-07-15",
            "2026-07-15T23:59:59+00:00",
            3.50,
        ),
        observation(
            "DFEDTARU",
            "2026-07-15",
            "2026-07-15T23:59:59+00:00",
            3.75,
        ),
        observation(
            "WALCL",
            "2026-04-15",
            "2026-04-16T23:59:59+00:00",
            6_500_000,
        ),
        observation(
            "WALCL",
            "2026-07-15",
            "2026-07-16T23:59:59+00:00",
            6_600_000,
        ),
        observation(
            "WRESBAL",
            "2026-04-15",
            "2026-04-16T23:59:59+00:00",
            3_000_000,
        ),
        observation(
            "WRESBAL",
            "2026-07-15",
            "2026-07-16T23:59:59+00:00",
            3_060_000,
        ),
        observation(
            "DFII10",
            "2026-04-15",
            "2026-04-15T23:59:59+00:00",
            1.60,
        ),
        observation(
            "DFII10",
            "2026-07-15",
            "2026-07-15T23:59:59+00:00",
            1.90,
        ),
    ]
    for month, headline, core, released in (
        ("2025-03-01", 124.0, 123.0, "2025-04-15"),
        ("2025-06-01", 125.0, 124.0, "2025-07-15"),
        ("2026-03-01", 128.0, 127.0, "2026-04-15"),
        ("2026-06-01", 130.0, 129.0, "2026-07-15"),
    ):
        rows.extend(
            [
                observation(
                    "PCEPI",
                    month,
                    f"{released}T23:59:59+00:00",
                    headline,
                    released,
                ),
                observation(
                    "PCEPILFE",
                    month,
                    f"{released}T23:59:59+00:00",
                    core,
                    released,
                ),
            ]
        )
    return rows


class PolicySeriesCatalogTest(unittest.TestCase):
    def test_catalog_covers_policy_liquidity_real_rate_and_pce_inputs(self):
        self.assertEqual(
            POLICY_SERIES_IDS,
            (
                "DFEDTARL",
                "DFEDTARU",
                "WALCL",
                "WSHOSHO",
                "WSHOMCB",
                "WRESBAL",
                "WTREGEN",
                "RRPONTSYD",
                "DFII10",
                "PCEPI",
                "PCEPILFE",
            ),
        )


class PolicyContextTest(unittest.TestCase):
    def test_builds_restrictive_rate_with_expanding_liquidity_context(self):
        result = build_policy_context(
            policy_observations(),
            "2026-07-20",
        )

        self.assertEqual(result["model_key"], "macro_policy_context_v1")
        self.assertEqual(
            result["state"],
            "rate_restrictive_liquidity_support",
        )
        self.assertEqual(
            result["dimensions"]["policy_rate"]["level"],
            "restrictive",
        )
        self.assertEqual(
            result["dimensions"]["policy_rate"]["direction"],
            "flat",
        )
        self.assertEqual(
            result["dimensions"]["liquidity"]["direction"],
            "expanding",
        )
        self.assertEqual(
            result["dimensions"]["real_rate"]["direction"],
            "rising",
        )
        self.assertEqual(
            result["dimensions"]["policy_rate"]["lower"],
            3.5,
        )
        self.assertEqual(
            result["dimensions"]["policy_rate"]["upper"],
            3.75,
        )
        self.assertGreaterEqual(result["coverage"], 0.8)
        self.assertEqual(result["online_authority"], "none")
        self.assertTrue(result["point_in_time"])

    def test_future_release_does_not_change_historical_context(self):
        original = policy_observations()
        revised = original + [
            observation(
                "WALCL",
                "2026-07-15",
                "2026-07-25T23:59:59+00:00",
                5_500_000,
                "2026-07-25",
            )
        ]

        self.assertEqual(
            build_policy_context(original, "2026-07-20"),
            build_policy_context(revised, "2026-07-20"),
        )

    def test_missing_required_dimensions_returns_unavailable_not_neutral(self):
        result = build_policy_context(
            [
                observation(
                    "DFEDTARU",
                    "2026-07-01",
                    "2026-07-01T23:59:59+00:00",
                    3.75,
                )
            ],
            "2026-07-20",
        )

        self.assertEqual(result["state"], "unavailable")
        self.assertIsNone(
            result["dimensions"]["liquidity"]["direction"]
        )
        self.assertEqual(
            result["unavailable_reason"],
            "insufficient_policy_coverage",
        )

    def test_date_only_asof_includes_rows_released_that_day(self):
        result = build_policy_context(
            policy_observations(),
            pd.Timestamp("2026-07-16").date(),
        )

        self.assertEqual(
            result["dimensions"]["liquidity"]["observation_date"],
            "2026-07-15",
        )


if __name__ == "__main__":
    unittest.main()
