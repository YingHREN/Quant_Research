import unittest
from unittest import mock

import numpy as np
import pandas as pd

from research.market_context import (
    build_atomic_model_rows,
    build_group_score_frame,
    build_market_context,
    score_evidence,
)
from research.market_pressure import Evidence
from web.market_groups import market_group


def rising(periods=260, slope=0.2, end="2026-07-23"):
    index = pd.bdate_range(end=end, periods=periods)
    close = 100.0 + np.arange(periods) * slope
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(periods, 1_000_000.0),
        },
        index=index,
    )


class MarketContextTest(unittest.TestCase):
    def test_every_market_response_includes_real_theme_heatmap_rows(self):
        histories = {
            ticker: rising(slope=slope)
            for ticker, slope in (
                ("QQQ", 0.2),
                ("SPY", 0.15),
                ("XLK", 0.22),
                ("SOXX", 0.3),
                ("SMH", 0.32),
                ("IGV", 0.18),
                ("XSW", 0.17),
                ("AMD", 0.35),
                ("ADBE", 0.16),
            )
        }

        result = build_market_context(
            histories,
            pd.Timestamp("2026-07-23"),
            market_group("technology"),
            5,
        )

        themes = {row["key"]: row for row in result["theme_groups"]}
        self.assertEqual(set(themes), {"semiconductor", "software"})
        for row in themes.values():
            self.assertIsNotNone(row["relative_return"])
            self.assertIsNotNone(row["downside_risk"]["score"])
            self.assertEqual(row["coverage"], 1.0)

    def test_constituent_classification_is_group_specific(self):
        histories = {
            ticker: rising()
            for ticker in ("QQQ", "SPY", "IGV", "XSW", "ADBE")
        }

        result = build_market_context(
            histories,
            pd.Timestamp("2026-07-23"),
            market_group("software"),
            5,
        )

        self.assertEqual(
            result["constituents"][0]["classification"],
            "software_constituent",
        )

    def test_software_uses_declared_xlk_fallback_without_fabricating_primary_coverage(self):
        histories = {
            ticker: rising()
            for ticker in ("QQQ", "SPY", "XLK", "ADBE")
        }

        result = build_market_context(
            histories,
            pd.Timestamp("2026-07-23"),
            market_group("software"),
            5,
        )

        self.assertEqual(result["selected_group"]["available_benchmarks"], [])
        self.assertEqual(result["selected_group"]["source_tickers"], ["XLK"])
        self.assertEqual(result["selected_group"]["coverage"], 0.0)
        self.assertIsNotNone(
            result["constituents"][0]["downside_risk"]["score"]
        )

    def test_one_semiconductor_proxy_degrades_coverage_without_fabrication(self):
        histories = {
            "QQQ": rising(),
            "SPY": rising(),
            "SOXX": rising(slope=0.3),
            "AMD": rising(slope=0.4),
        }

        result = build_market_context(
            histories,
            pd.Timestamp("2026-07-23"),
            market_group("semiconductor"),
            5,
        )

        selected = result["selected_group"]
        self.assertEqual(selected["available_benchmarks"], ["SOXX"])
        self.assertLess(selected["coverage"], 1.0)
        self.assertNotIn("SMH", selected["available_benchmarks"])

    def test_missing_both_sector_proxies_makes_stock_scores_unavailable(self):
        result = build_market_context(
            {"QQQ": rising(), "SPY": rising(), "AMD": rising()},
            pd.Timestamp("2026-07-23"),
            market_group("semiconductor"),
            20,
        )

        amd = result["constituents"][0]

        self.assertIsNone(amd["reversal_opportunity"]["score"])
        self.assertIsNone(amd["downside_risk"]["score"])
        self.assertEqual(
            amd["reversal_opportunity"]["unavailable_reason"],
            "missing_sector_benchmark",
        )

    def test_opportunity_and_risk_are_independent_not_complements(self):
        histories = {
            "QQQ": rising(),
            "SPY": rising(),
            "SOXX": rising(),
            "SMH": rising(),
            "AMD": rising(),
        }

        result = build_market_context(
            histories,
            pd.Timestamp("2026-07-23"),
            market_group("semiconductor"),
            5,
        )
        amd = result["constituents"][0]
        opportunity = amd["reversal_opportunity"]["score"]
        risk = amd["downside_risk"]["score"]

        self.assertNotEqual(opportunity + risk, 100.0)

    def test_future_append_does_not_change_old_market_context(self):
        histories = {
            name: rising() for name in ("QQQ", "SPY", "SOXX", "SMH", "AMD")
        }
        before = build_market_context(
            histories,
            pd.Timestamp("2026-07-23"),
            market_group("semiconductor"),
            5,
        )
        extended = {}
        for name, frame in histories.items():
            tail = rising(periods=2, slope=-20.0, end="2026-07-27")
            extended[name] = pd.concat(
                [frame, tail.loc[tail.index > frame.index[-1]]]
            )

        after = build_market_context(
            extended,
            pd.Timestamp("2026-07-23"),
            market_group("semiconductor"),
            5,
        )

        self.assertEqual(after, before)

    def test_score_below_eighty_percent_is_unavailable(self):
        rows = (
            Evidence(
                "available",
                1.0,
                0.0,
                "met",
                79.0,
                79.0,
                "1 session",
            ),
            Evidence(
                "missing",
                None,
                None,
                "unavailable",
                0.0,
                21.0,
                "20 sessions",
                "insufficient_history",
            ),
        )

        score = score_evidence(
            rows,
            required_available=True,
            unavailable_reason="insufficient_coverage",
        )

        self.assertIsNone(score.score)
        self.assertAlmostEqual(score.coverage, 0.79)

    def test_later_benchmark_row_is_not_used_for_earlier_stock_asof(self):
        histories = {
            "QQQ": rising(end="2026-07-24"),
            "SPY": rising(end="2026-07-24"),
            "SOXX": rising(end="2026-07-24"),
            "SMH": rising(end="2026-07-24"),
            "AMD": rising(end="2026-07-23"),
        }

        result = build_market_context(
            histories,
            pd.Timestamp("2026-07-23"),
            market_group("semiconductor"),
            5,
        )

        self.assertEqual(result["asof"], "2026-07-23")
        self.assertEqual(
            result["selected_group"]["latest_source_date"],
            "2026-07-23",
        )

    def test_atomic_rows_exclude_composite_scores(self):
        histories = {
            "QQQ": rising(),
            "SOXX": rising(),
            "SMH": rising(),
            "AMD": rising(),
        }

        rows = build_atomic_model_rows(
            histories,
            market_group("semiconductor"),
        )

        self.assertIn("pressure_signed_volume_proxy", rows)
        self.assertIn("prior_high_breakout", rows)
        self.assertNotIn("reversal_opportunity_score", rows)
        self.assertNotIn("downside_risk_score", rows)
        self.assertNotIn("market_posture_score", rows)

    def test_atomic_rows_include_continuous_market_sector_and_early_evidence(self):
        histories = {
            "QQQ": rising(slope=0.15),
            "SOXX": rising(slope=0.25),
            "SMH": rising(slope=0.30),
            "AMD": rising(slope=0.40),
            "OTHER": rising(slope=0.10),
        }

        rows = build_atomic_model_rows(
            histories,
            market_group("semiconductor"),
        )
        latest = rows.loc[("AMD", histories["AMD"].index[-1])]

        for column in (
            "qqq_close_vs_ema20_pct",
            "qqq_return_5",
            "qqq_return_20",
            "qqq_volume_ratio",
            "sector_trend_state",
            "early_prior_session_selloff",
            "early_current_price_acceptance",
            "early_descending_trendline_proximity",
            "early_current_volume_support",
        ):
            self.assertIn(column, rows)
            self.assertTrue(np.isfinite(latest[column]))
        self.assertGreater(latest["qqq_return_20"], latest["qqq_return_5"])
        self.assertEqual(latest["sector_trend_state"], 1.0)
        self.assertTrue(
            rows.loc[:, "early_prior_session_selloff"].dropna().isin((0.0, 1.0)).all()
        )
        self.assertTrue(
            np.isnan(
                rows.loc[
                    ("OTHER", histories["OTHER"].index[-1]),
                    "stock_sector_relative_strength_20",
                ]
            )
        )

    def test_group_score_frame_is_point_in_time_and_multiindexed(self):
        histories = {
            "QQQ": rising(periods=70),
            "SPY": rising(periods=70),
            "SOXX": rising(periods=70),
            "SMH": rising(periods=70),
            "AMD": rising(periods=70),
        }

        rows = build_group_score_frame(
            histories,
            market_group("semiconductor"),
        )

        self.assertEqual(
            rows.index.names,
            ["ticker", "observation_date"],
        )
        self.assertIn("reversal_opportunity_score", rows)
        self.assertIn("downside_risk_score", rows)
        self.assertIn("downside_risk_state_score", rows)
        self.assertIn("downside_risk_state", rows)
        self.assertIn("downside_risk_memory_age_sessions", rows)
        self.assertIn("atr20_pct", rows)
        available = rows["downside_risk_score"].notna()
        self.assertTrue(
            (
                rows.loc[available, "downside_risk_state_score"]
                >= rows.loc[available, "downside_risk_score"]
            ).all()
        )
        self.assertEqual(
            rows.index.get_level_values("observation_date").max(),
            histories["AMD"].index.max(),
        )

    def test_snapshot_exposes_raw_and_remembered_bearish_turn_risk(self):
        histories = {
            "QQQ": rising(),
            "SPY": rising(),
            "SOXX": rising(slope=0.3),
            "SMH": rising(slope=0.3),
            "AMD": rising(slope=0.4),
        }

        result = build_market_context(
            histories,
            pd.Timestamp("2026-07-23"),
            market_group("semiconductor"),
            5,
        )

        risk = result["constituents"][0]["downside_risk"]
        self.assertEqual(risk["raw_score"], risk["score"])
        self.assertGreaterEqual(risk["state_score"], risk["raw_score"])
        self.assertIn(
            risk["state"],
            {"new", "persistent", "fading", "inactive"},
        )
        self.assertEqual(risk["memory_half_life_sessions"], 5)
        self.assertEqual(risk["memory_window_sessions"], 10)
        self.assertEqual(
            risk["model_key"],
            "bearish_turn_risk_rules_v2",
        )

        group_risk = result["selected_group"]["downside_risk"]
        self.assertIn("raw_score", group_risk)
        self.assertIn("state_score", group_risk)
        self.assertEqual(group_risk["score"], group_risk["raw_score"])

    def test_group_score_history_does_not_rebuild_snapshot_scores_per_row(self):
        histories = {
            "QQQ": rising(periods=180),
            "SPY": rising(periods=180),
            "SOXX": rising(periods=180),
            "SMH": rising(periods=180),
            "AMD": rising(periods=180),
        }

        with mock.patch(
            "research.market_context._stock_scores",
            side_effect=AssertionError("row-wise snapshot scorer called"),
        ):
            rows = build_group_score_frame(
                histories,
                market_group("semiconductor"),
            )

        self.assertEqual(len(rows), 180)


if __name__ == "__main__":
    unittest.main()
