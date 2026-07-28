import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research.unified_downside_benchmark import (
    attach_next_open_path_targets,
)


def example_prices(extra_rows=0):
    dates = pd.bdate_range("2026-01-02", periods=5 + extra_rows)
    values = [100.0, 102.0, 104.0, 103.0, 105.0, 106.0, 107.0]
    lows = [99.0, 100.0, 95.0, 101.0, 103.0, 104.0, 105.0]
    highs = [101.0, 103.0, 106.0, 105.0, 107.0, 108.0, 109.0]
    frame = pd.DataFrame(
        {
            "ticker": "AAA",
            "observation_date": dates,
            "Open": values[: len(dates)],
            "High": highs[: len(dates)],
            "Low": lows[: len(dates)],
            "Close": values[: len(dates)],
        }
    )
    return frame.set_index(["ticker", "observation_date"])


class UnifiedDownsidePathTargetTest(unittest.TestCase):
    def test_next_open_targets_use_future_open_and_mark_immature_tail(self):
        labeled = attach_next_open_path_targets(
            example_prices(),
            horizons=(2,),
            adverse_thresholds={2: -0.05},
        )

        row = labeled.loc[("AAA", pd.Timestamp("2026-01-02"), 2)]

        self.assertEqual(row["entry_open"], 102.0)
        self.assertAlmostEqual(row["terminal_return"], 104.0 / 102.0 - 1.0)
        self.assertAlmostEqual(row["mae"], 95.0 / 102.0 - 1.0)
        self.assertAlmostEqual(row["mfe"], 106.0 / 102.0 - 1.0)
        self.assertTrue(row["actual_event"])
        self.assertEqual(int(labeled["immature"].sum()), 2)
        self.assertTrue(labeled.loc[("AAA", slice(None), 2), "mature"].iloc[:-2].all())

    def test_appending_future_rows_does_not_change_mature_prefix_labels(self):
        before = attach_next_open_path_targets(
            example_prices(),
            horizons=(2,),
            adverse_thresholds={2: -0.05},
        )
        after = attach_next_open_path_targets(
            example_prices(extra_rows=2),
            horizons=(2,),
            adverse_thresholds={2: -0.05},
        )
        mature = before.loc[before["mature"]]

        assert_frame_equal(
            mature,
            after.loc[mature.index],
        )

    def test_invalid_prices_and_duplicate_keys_fail_closed(self):
        duplicate = pd.concat([example_prices(), example_prices().iloc[[0]]])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            attach_next_open_path_targets(
                duplicate,
                horizons=(2,),
                adverse_thresholds={2: -0.05},
            )

        invalid = example_prices()
        invalid.loc[("AAA", invalid.index.get_level_values(1)[1]), "Open"] = 0.0
        labeled = attach_next_open_path_targets(
            invalid,
            horizons=(2,),
            adverse_thresholds={2: -0.05},
        )
        first = labeled.loc[("AAA", pd.Timestamp("2026-01-02"), 2)]
        self.assertFalse(first["mature"])
        self.assertEqual(first["unavailable_reason"], "invalid_future_path")
        self.assertTrue(np.isnan(first["mae"]))

    def test_thresholds_and_horizons_are_strictly_validated(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            attach_next_open_path_targets(
                example_prices(),
                horizons=(0,),
                adverse_thresholds={0: -0.05},
            )
        with self.assertRaisesRegex(ValueError, "threshold"):
            attach_next_open_path_targets(
                example_prices(),
                horizons=(2,),
                adverse_thresholds={2: 0.0},
            )
        with self.assertRaisesRegex(ValueError, "missing"):
            attach_next_open_path_targets(
                example_prices(),
                horizons=(2,),
                adverse_thresholds={5: -0.05},
            )


if __name__ == "__main__":
    unittest.main()
