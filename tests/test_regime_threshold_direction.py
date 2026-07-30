from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.market_direction_model import (
    attach_next_open_targets,
    direction_labels,
)
from research.regime_threshold_direction import attach_qqq_relative_targets


def _history(index, *, open_start, close_step):
    positions = np.arange(len(index), dtype=float)
    opens = open_start + positions
    closes = open_start + close_step * positions
    return pd.DataFrame(
        {
            "Open": opens,
            "High": np.maximum(opens, closes) + 1.0,
            "Low": np.minimum(opens, closes) - 1.0,
            "Close": closes,
            "Volume": np.full(len(index), 1_000_000.0),
        },
        index=index,
    )


def _absolute_target_frame():
    qqq_dates = pd.bdate_range("2026-01-02", periods=8)
    histories = {
        "AAA": _history(qqq_dates, open_start=50.0, close_step=2.0),
        "BBB": _history(
            qqq_dates.delete(2),
            open_start=70.0,
            close_step=1.5,
        ),
    }
    index = pd.MultiIndex.from_tuples(
        [
            (ticker, date)
            for ticker, history in histories.items()
            for date in history.index
        ],
        names=("ticker", "observation_date"),
    )
    features = pd.DataFrame({"feature": 1.0}, index=index)
    absolute = attach_next_open_targets(features, histories, horizons=(5,))
    qqq = _history(qqq_dates, open_start=100.0, close_step=2.0)
    return absolute, qqq


class QQQRelativeTargetTest(unittest.TestCase):
    def test_uses_each_stock_rows_exact_entry_and_exit_dates(self):
        absolute, qqq = _absolute_target_frame()
        before = absolute.copy(deep=True)
        observation = pd.Timestamp("2026-01-02")

        result = attach_qqq_relative_targets(absolute, qqq, horizon=5)

        aaa_benchmark = 110.0 / 101.0 - 1.0
        bbb_benchmark = 112.0 / 101.0 - 1.0
        self.assertAlmostEqual(
            result.loc[
                ("AAA", observation),
                "qqq_executable_return_5",
            ],
            aaa_benchmark,
        )
        self.assertAlmostEqual(
            result.loc[
                ("BBB", observation),
                "qqq_executable_return_5",
            ],
            bbb_benchmark,
        )
        self.assertNotAlmostEqual(aaa_benchmark, bbb_benchmark)
        self.assertAlmostEqual(
            result.loc[
                ("BBB", observation),
                "qqq_relative_return_5",
            ],
            (
                absolute.loc[
                    ("BBB", observation),
                    "executable_return_5",
                ]
                - bbb_benchmark
            ),
        )
        pd.testing.assert_frame_equal(absolute, before)

    def test_missing_exact_qqq_endpoint_returns_missing_not_shifted_value(self):
        absolute, qqq = _absolute_target_frame()
        observation = pd.Timestamp("2026-01-02")
        missing_exit = absolute.loc[
            ("BBB", observation),
            "executable_label_end_date_5",
        ]

        result = attach_qqq_relative_targets(
            absolute,
            qqq.drop(index=missing_exit),
            horizon=5,
        )

        self.assertTrue(
            pd.isna(
                result.loc[
                    ("BBB", observation),
                    "qqq_executable_return_5",
                ]
            )
        )
        self.assertTrue(
            pd.isna(
                result.loc[
                    ("BBB", observation),
                    "qqq_relative_return_5",
                ]
            )
        )

    def test_relative_weakness_remains_separate_from_absolute_direction(self):
        observation = pd.Timestamp("2026-01-02")
        entry = pd.Timestamp("2026-01-05")
        exit_date = pd.Timestamp("2026-01-09")
        index = pd.MultiIndex.from_tuples(
            [("AAA", observation)],
            names=("ticker", "observation_date"),
        )
        absolute = pd.DataFrame(
            {
                "executable_return_5": [0.02],
                "executable_entry_date_5": [entry],
                "executable_label_end_date_5": [exit_date],
            },
            index=index,
        )
        qqq = pd.DataFrame(
            {
                "Open": [100.0, 107.0],
                "Close": [101.0, 108.0],
            },
            index=pd.DatetimeIndex([entry, exit_date]),
        )

        result = attach_qqq_relative_targets(absolute, qqq, horizon=5)

        self.assertAlmostEqual(result.iloc[0]["executable_return_5"], 0.02)
        self.assertAlmostEqual(result.iloc[0]["qqq_relative_return_5"], -0.06)
        self.assertEqual(
            direction_labels(result["executable_return_5"], 5).tolist(),
            ["up"],
        )
        self.assertEqual(
            direction_labels(result["qqq_relative_return_5"], 5).tolist(),
            ["down"],
        )

    def test_invalid_contracts_fail_closed(self):
        absolute, qqq = _absolute_target_frame()

        with self.assertRaisesRegex(ValueError, "only horizon 5"):
            attach_qqq_relative_targets(absolute, qqq, horizon=20)
        with self.assertRaisesRegex(ValueError, "required columns"):
            attach_qqq_relative_targets(
                absolute.drop(columns=["executable_entry_date_5"]),
                qqq,
                horizon=5,
            )
        with self.assertRaisesRegex(ValueError, "Open and Close"):
            attach_qqq_relative_targets(
                absolute,
                qqq.drop(columns=["Open"]),
                horizon=5,
            )

        duplicate_dates = qqq.iloc[:2].copy()
        duplicate_dates.index = pd.DatetimeIndex(
            ["2026-01-02 09:30", "2026-01-02 16:00"]
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            attach_qqq_relative_targets(
                absolute,
                duplicate_dates,
                horizon=5,
            )
