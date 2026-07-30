import copy
import unittest

import numpy as np
import pandas as pd

from data.market_behavior import classify_market_behavior
from research.point_in_time_sector_features import (
    PIT_SECTOR_CANDIDATES,
    build_monthly_behavior_assignments,
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


if __name__ == "__main__":
    unittest.main()
