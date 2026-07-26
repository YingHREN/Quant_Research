import unittest

import pandas as pd

from research.macro_risk import build_macro_risk


def observations():
    rows = []

    def add(series_id, date, available_at, value, vintage="2026-07-01"):
        rows.append(
            {
                "series_id": series_id,
                "observation_date": date,
                "available_at": available_at,
                "value": value,
                "realtime_start": vintage,
                "realtime_end": "9999-12-31",
            }
        )

    for date, values in (
        (
            "2026-06-01",
            {
                "DGS2": 4.10,
                "DGS10": 4.00,
                "DCOILWTICO": 70.0,
                "BAMLH0A0HYM2": 3.2,
                "VIXCLS": 16.0,
                "DTWEXBGS": 120.0,
            },
        ),
        (
            "2026-07-01",
            {
                "DGS2": 4.75,
                "DGS10": 4.10,
                "DCOILWTICO": 84.0,
                "BAMLH0A0HYM2": 5.4,
                "VIXCLS": 28.0,
                "DTWEXBGS": 124.0,
            },
        ),
    ):
        for series_id, value in values.items():
            add(series_id, date, f"{date}T18:00:00+00:00", value)

    add(
        "CPIAUCSL",
        "2025-06-01",
        "2025-07-15T12:30:00+00:00",
        300.0,
        "2025-07-15",
    )
    add(
        "CPIAUCSL",
        "2026-06-01",
        "2026-07-15T12:30:00+00:00",
        310.5,
        "2026-07-15",
    )
    return pd.DataFrame(rows)


class MacroRiskTest(unittest.TestCase):
    def test_scores_four_independent_risk_groups(self):
        result = build_macro_risk(
            observations(),
            "2026-07-20T23:59:59+00:00",
        )

        self.assertEqual(result["model_key"], "macro_risk_v1")
        self.assertEqual(result["state"], "severe")
        self.assertGreaterEqual(result["score"], 70.0)
        self.assertEqual(
            set(result["components"]),
            {"rates", "inflation_energy", "credit_liquidity", "risk_aversion"},
        )
        self.assertGreaterEqual(result["coverage"], 0.9)
        self.assertIn("two_year_yield_high", result["conditions"])
        self.assertIn("high_yield_spread_stressed", result["conditions"])
        self.assertIn("vix_elevated", result["conditions"])

    def test_never_uses_observation_before_available_at(self):
        before_release = build_macro_risk(
            observations(),
            "2026-07-10T23:59:59+00:00",
        )
        after_release = build_macro_risk(
            observations(),
            "2026-07-20T23:59:59+00:00",
        )

        before_cpi = next(
            row
            for row in before_release["evidence"]
            if row["key"] == "cpi_yoy_elevated"
        )
        after_cpi = next(
            row
            for row in after_release["evidence"]
            if row["key"] == "cpi_yoy_elevated"
        )
        self.assertEqual(before_cpi["state"], "unavailable")
        self.assertEqual(after_cpi["state"], "met")

    def test_future_revision_does_not_rewrite_historical_result(self):
        frame = observations()
        revised = pd.concat(
            [
                frame,
                pd.DataFrame(
                    [
                        {
                            "series_id": "DGS2",
                            "observation_date": "2026-07-01",
                            "available_at": "2026-07-25T18:00:00+00:00",
                            "value": 3.0,
                            "realtime_start": "2026-07-25",
                            "realtime_end": "9999-12-31",
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )

        original = build_macro_risk(frame, "2026-07-20T23:59:59+00:00")
        replayed = build_macro_risk(
            revised,
            "2026-07-20T23:59:59+00:00",
        )

        self.assertEqual(replayed, original)

    def test_insufficient_coverage_is_explicitly_unavailable(self):
        frame = observations().query("series_id == 'VIXCLS'")

        result = build_macro_risk(frame, "2026-07-20T23:59:59+00:00")

        self.assertIsNone(result["score"])
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(
            result["unavailable_reason"],
            "insufficient_macro_coverage",
        )


if __name__ == "__main__":
    unittest.main()
