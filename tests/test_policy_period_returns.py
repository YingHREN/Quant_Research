import unittest

import numpy as np
import pandas as pd

from research.policy_period_returns import describe_policy_periods


def history(values, dates=None):
    if dates is None:
        dates = (
            "2020-01-02",
            "2020-01-31",
            "2020-02-28",
            "2020-03-31",
        )
    return pd.DataFrame(
        {"Close": values},
        index=pd.to_datetime(dates),
    )


def period(**overrides):
    row = {
        "period_id": "complete-period",
        "catalog_version": "fed-policy-v1",
        "label_zh": "完整时期",
        "label_en": "Complete period",
        "start_date": "2020-01-02",
        "end_date": "2020-03-31",
        "available_at": "2020-03-31T20:00:00+00:00",
    }
    row.update(overrides)
    return row


class PolicyPeriodReturnsTest(unittest.TestCase):
    def test_complete_period_uses_common_spy_endpoints(self):
        periods = pd.DataFrame([period()])
        histories = {
            "SPY": history([100.0, 102.0, 105.0, 110.0]),
            "XLK": history([100.0, 105.0, 110.0, 120.0]),
        }

        result = describe_policy_periods(
            periods,
            histories,
            "2020-04-01T00:00:00+00:00",
        )
        xlk = result.loc[result["ticker"] == "XLK"].iloc[0]

        self.assertEqual(xlk["status"], "complete")
        self.assertEqual(xlk["first_session"], "2020-01-02")
        self.assertEqual(xlk["last_session"], "2020-03-31")
        self.assertEqual(int(xlk["session_count"]), 4)
        self.assertAlmostEqual(float(xlk["total_return"]), 0.20)
        self.assertAlmostEqual(
            float(xlk["relative_spy_return"]),
            (1.20 / 1.10) - 1.0,
        )
        self.assertAlmostEqual(float(xlk["max_drawdown"]), 0.0)
        self.assertAlmostEqual(float(xlk["positive_month_ratio"]), 1.0)
        self.assertGreater(
            float(xlk["annualized_return"]),
            float(xlk["total_return"]),
        )

    def test_incomplete_period_has_no_rankable_metrics(self):
        periods = pd.DataFrame(
            [period(period_id="open-period", end_date=None)]
        )

        result = describe_policy_periods(
            periods,
            {"SPY": history([100, 101, 102, 103])},
            "2020-04-01T00:00:00+00:00",
        )
        row = result.iloc[0]

        self.assertEqual(row["status"], "incomplete")
        self.assertTrue(np.isnan(row["total_return"]))
        self.assertTrue(np.isnan(row["relative_spy_return"]))

    def test_etf_listed_after_period_is_unavailable_without_proxy(self):
        periods = pd.DataFrame([period()])
        histories = {
            "SPY": history([100.0, 102.0, 105.0, 110.0]),
            "XLRE": history(
                [10.0, 11.0],
                dates=("2020-04-01", "2020-04-02"),
            ),
        }

        result = describe_policy_periods(
            periods,
            histories,
            "2020-04-03T00:00:00+00:00",
        )
        xlre = result.loc[result["ticker"] == "XLRE"].iloc[0]

        self.assertEqual(xlre["status"], "not_listed")
        self.assertTrue(np.isnan(xlre["total_return"]))
        self.assertTrue(np.isnan(xlre["relative_spy_return"]))

    def test_future_rows_do_not_change_asof_result(self):
        periods = pd.DataFrame([period()])
        base = {
            "SPY": history([100.0, 102.0, 105.0, 110.0]),
            "XLK": history([100.0, 105.0, 110.0, 120.0]),
        }
        extended = {
            ticker: pd.concat(
                [
                    frame,
                    history(
                        [1.0, 1000.0],
                        dates=("2021-01-04", "2021-01-05"),
                    ),
                ]
            )
            for ticker, frame in base.items()
        }

        first = describe_policy_periods(
            periods,
            base,
            "2020-04-01T00:00:00+00:00",
        )
        second = describe_policy_periods(
            periods,
            extended,
            "2020-04-01T00:00:00+00:00",
        )

        pd.testing.assert_frame_equal(first, second)


if __name__ == "__main__":
    unittest.main()
