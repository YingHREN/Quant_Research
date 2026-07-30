import copy
import unittest

import numpy as np
import pandas as pd

from data.market_behavior import classify_market_behavior
from research.point_in_time_sector_features import (
    PIT_SECTOR_CANDIDATES,
    build_monthly_behavior_assignments,
    build_point_in_time_sector_features,
)


def _prices_from_returns(dates, returns, start=100.0):
    close = [float(start)]
    for value in returns[1:]:
        close.append(close[-1] * (1.0 + float(value)))
    close = pd.Series(close, index=dates, dtype=float)
    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": 1_000_000.0,
        },
        index=dates,
    )


def _synthetic_histories(periods=150, start="2025-01-02"):
    dates = pd.date_range(start, periods=periods, freq="B")
    position = np.arange(periods, dtype=float)
    spy_returns = np.where(position % 2 == 0, 0.002, -0.001)
    technology_residual = np.sin(position / 3.0) * 0.006
    semiconductor_residual = np.cos(position / 4.0) * 0.004
    software_residual = np.sin(position / 7.0) * -0.003
    stock_returns = spy_returns * 1.2 + technology_residual * 1.5
    return {
        "AAA": _prices_from_returns(dates, stock_returns),
        "SPY": _prices_from_returns(dates, spy_returns),
        "QQQ": _prices_from_returns(
            dates,
            spy_returns + technology_residual * 0.4,
        ),
        "XLK": _prices_from_returns(
            dates,
            spy_returns * 1.1 + technology_residual,
        ),
        "SOXX": _prices_from_returns(
            dates,
            spy_returns * 1.2 + semiconductor_residual,
        ),
        "IGV": _prices_from_returns(
            dates,
            spy_returns + software_residual,
        ),
    }


def _price_rows(histories):
    return {
        ticker: [
            (date.date().isoformat(), float(close))
            for date, close in history["Close"].items()
        ]
        for ticker, history in histories.items()
    }


class MonthlyAssignmentTest(unittest.TestCase):
    def test_month_end_assignment_starts_on_next_stock_session(self):
        histories = _synthetic_histories(periods=70)

        assignments = build_monthly_behavior_assignments(
            histories,
            ("AAA",),
            minimum_observations=4,
            maximum_observations=20,
        )

        self.assertFalse(assignments.empty)
        self.assertFalse(
            assignments.duplicated(["ticker", "classification_date"]).any()
        )
        sessions = histories["AAA"].index
        for row in assignments.itertuples(index=False):
            expected = sessions[sessions > row.classification_date][0]
            self.assertEqual(row.effective_from, expected)
            self.assertLess(row.classification_date, row.effective_from)
            self.assertEqual(
                row.expires_after,
                row.classification_date + pd.Timedelta(days=45),
            )

    def test_minimum_observation_boundary_is_inclusive(self):
        histories = _synthetic_histories(periods=150)

        available = build_monthly_behavior_assignments(
            histories,
            ("AAA",),
            minimum_observations=126,
            maximum_observations=126,
        )
        unavailable = build_monthly_behavior_assignments(
            {
                ticker: history.iloc[:125]
                for ticker, history in histories.items()
            },
            ("AAA",),
            minimum_observations=126,
            maximum_observations=126,
        )

        self.assertFalse(available.empty)
        self.assertTrue((available["common_days"] == 126).all())
        self.assertTrue(unavailable.empty)

    def test_selected_month_matches_direct_market_behavior_classifier(self):
        histories = _synthetic_histories(periods=90)
        assignments = build_monthly_behavior_assignments(
            histories,
            ("AAA",),
            minimum_observations=20,
            maximum_observations=40,
        )
        row = assignments.iloc[-1]
        direct = classify_market_behavior(
            _price_rows(histories),
            "AAA",
            {
                key: ticker
                for key, ticker in PIT_SECTOR_CANDIDATES.items()
                if ticker in histories
            },
            sec_sector="",
            asof=row["classification_date"].date().isoformat(),
            min_observations=20,
            max_observations=40,
        )

        self.assertIsNotNone(direct)
        self.assertEqual(row["sector_key"], direct.sector_key)
        self.assertEqual(row["benchmark_ticker"], direct.benchmark_ticker)
        self.assertAlmostEqual(
            row["residual_correlation"],
            direct.residual_correlation,
        )

    def test_appended_future_prices_do_not_change_existing_assignments(self):
        full = _synthetic_histories(periods=120)
        base = {
            ticker: history.iloc[:90].copy()
            for ticker, history in full.items()
        }
        before_inputs = copy.deepcopy(base)

        before = build_monthly_behavior_assignments(
            base,
            ("AAA",),
            minimum_observations=20,
            maximum_observations=40,
        )
        after = build_monthly_behavior_assignments(
            full,
            ("AAA",),
            minimum_observations=20,
            maximum_observations=40,
        )
        comparable = after.loc[
            after["classification_date"]
            <= before["classification_date"].max()
        ].reset_index(drop=True)

        pd.testing.assert_frame_equal(before, comparable)
        for ticker in base:
            pd.testing.assert_frame_equal(base[ticker], before_inputs[ticker])

    def test_duplicate_history_dates_fail_closed(self):
        histories = _synthetic_histories(periods=30)
        duplicate = pd.concat(
            (histories["AAA"], histories["AAA"].iloc[[-1]])
        )
        histories["AAA"] = duplicate

        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_monthly_behavior_assignments(
                histories,
                ("AAA",),
                minimum_observations=4,
                maximum_observations=20,
            )


def _known_feature_histories():
    dates = pd.date_range("2026-01-02", periods=32, freq="B")
    position = np.arange(len(dates), dtype=float)
    return {
        "AAA": pd.DataFrame(
            {"Close": 100.0 * np.power(1.01, position)},
            index=dates,
        ),
        "XLK": pd.DataFrame(
            {"Close": 100.0 * np.power(1.005, position)},
            index=dates,
        ),
        "QQQ": pd.DataFrame(
            {"Close": 100.0 * np.power(1.002, position)},
            index=dates,
        ),
    }


def _assignment(
    *,
    classification_date="2026-01-09",
    effective_from="2026-01-12",
    expires_after="2026-02-23",
    benchmark="XLK",
):
    return pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "classification_date": pd.Timestamp(classification_date),
                "effective_from": pd.Timestamp(effective_from),
                "expires_after": pd.Timestamp(expires_after),
                "sector_key": "technology",
                "benchmark_ticker": benchmark,
                "residual_correlation": 0.73,
                "residual_beta": 1.2,
                "common_days": 126,
                "rule_version": "test",
            }
        ]
    )


def _observation_index(*dates):
    return pd.MultiIndex.from_tuples(
        [("AAA", pd.Timestamp(value)) for value in dates],
        names=["ticker", "observation_date"],
    )


class PointInTimeSectorFeatureTest(unittest.TestCase):
    def test_relative_returns_share_exact_stock_endpoints(self):
        histories = _known_feature_histories()
        observation = histories["AAA"].index[25]

        result = build_point_in_time_sector_features(
            histories,
            _assignment(),
            _observation_index(observation),
        )
        row = result.loc[("AAA", observation)]
        stock_return = 1.01**20 - 1.0
        proxy_return = 1.005**20 - 1.0
        qqq_return = 1.002**20 - 1.0

        self.assertAlmostEqual(
            row["pit_stock_sector_relative_strength_20"],
            stock_return - proxy_return,
        )
        self.assertAlmostEqual(
            row["pit_sector_relative_strength_20"],
            proxy_return - qqq_return,
        )
        self.assertEqual(row["pit_sector_key"], "technology")
        self.assertEqual(row["pit_sector_benchmark"], "XLK")
        self.assertTrue(row["pit_sector_assignment_available"])
        self.assertEqual(row["pit_sector_unavailable_reason"], "")

    def test_assignment_is_not_available_until_effective_session(self):
        histories = _known_feature_histories()
        assignment = _assignment(
            classification_date="2026-01-30",
            effective_from="2026-02-02",
            expires_after="2026-03-16",
        )
        result = build_point_in_time_sector_features(
            histories,
            assignment,
            _observation_index("2026-01-30", "2026-02-02"),
        )

        same_day = result.loc[("AAA", pd.Timestamp("2026-01-30"))]
        next_session = result.loc[("AAA", pd.Timestamp("2026-02-02"))]
        self.assertFalse(same_day["pit_sector_assignment_available"])
        self.assertEqual(
            same_day["pit_sector_unavailable_reason"],
            "no_effective_assignment",
        )
        self.assertTrue(next_session["pit_sector_assignment_available"])

    def test_assignment_age_is_valid_through_day_45_only(self):
        histories = _known_feature_histories()
        extended_dates = pd.date_range("2026-01-02", periods=70, freq="B")
        for ticker, daily_return in (("AAA", 1.01), ("XLK", 1.005), ("QQQ", 1.002)):
            histories[ticker] = pd.DataFrame(
                {
                    "Close": 100.0
                    * np.power(daily_return, np.arange(len(extended_dates)))
                },
                index=extended_dates,
            )
        assignment = _assignment(
            classification_date="2026-01-02",
            effective_from="2026-01-05",
            expires_after="2026-02-16",
        )

        result = build_point_in_time_sector_features(
            histories,
            assignment,
            _observation_index("2026-02-16", "2026-02-17"),
        )

        valid = result.loc[("AAA", pd.Timestamp("2026-02-16"))]
        stale = result.loc[("AAA", pd.Timestamp("2026-02-17"))]
        self.assertTrue(valid["pit_sector_assignment_available"])
        self.assertEqual(valid["pit_sector_assignment_age_days"], 45)
        self.assertFalse(stale["pit_sector_assignment_available"])
        self.assertEqual(stale["pit_sector_unavailable_reason"], "stale_assignment")

    def test_missing_exact_proxy_endpoint_is_not_filled(self):
        histories = _known_feature_histories()
        observation = histories["AAA"].index[25]
        start = histories["AAA"].index[5]
        histories["XLK"] = histories["XLK"].drop(index=start)

        result = build_point_in_time_sector_features(
            histories,
            _assignment(),
            _observation_index(observation),
        )

        row = result.loc[("AAA", observation)]
        self.assertFalse(row["pit_sector_assignment_available"])
        self.assertEqual(
            row["pit_sector_unavailable_reason"],
            "missing_benchmark_endpoint",
        )

    def test_unknown_proxy_and_duplicate_assignment_fail_closed(self):
        histories = _known_feature_histories()
        observation = histories["AAA"].index[25]
        unknown = build_point_in_time_sector_features(
            histories,
            _assignment(benchmark="MISSING"),
            _observation_index(observation),
        )
        self.assertEqual(
            unknown.iloc[0]["pit_sector_unavailable_reason"],
            "unknown_benchmark",
        )

        duplicate = pd.concat((_assignment(), _assignment()), ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_point_in_time_sector_features(
                histories,
                duplicate,
                _observation_index(observation),
            )

    def test_future_append_invariance_and_index_preservation(self):
        full = _known_feature_histories()
        base = {
            ticker: frame.iloc[:28].copy()
            for ticker, frame in full.items()
        }
        observation = base["AAA"].index[25]
        requested = _observation_index(observation)

        before = build_point_in_time_sector_features(
            base,
            _assignment(),
            requested,
        )
        after = build_point_in_time_sector_features(
            full,
            _assignment(),
            requested,
        )

        pd.testing.assert_frame_equal(before, after)
        self.assertTrue(before.index.equals(requested))


if __name__ == "__main__":
    unittest.main()
