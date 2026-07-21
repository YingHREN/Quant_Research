import unittest
import warnings

import numpy as np
import pandas as pd

from research.regression import (
    coefficient_stability,
    chronological_folds,
    evaluate_specifications,
    linear_predict,
)


def model_fixture(length=260):
    rng = np.random.RandomState(7)
    dates = pd.bdate_range("2020-01-01", periods=length)
    momentum = rng.uniform(size=length)
    ratio = rng.uniform(0.3, 0.8, size=length)
    return pd.DataFrame(
        {
            "observation_date": dates,
            "n_legs": rng.randint(2, 5, size=length),
            "last_first_ratio": ratio,
            "contraction_slope": -rng.uniform(0.1, 5, size=length),
            "terminal_range_pct": rng.uniform(2, 12, size=length),
            "volume_dryup_ratio": rng.uniform(0.4, 1.4, size=length),
            "distance_to_pivot_pct": -rng.uniform(0, 5, size=length),
            "base_depth_pct": rng.uniform(8, 30, size=length),
            "mom_3_1_rank": rng.uniform(size=length),
            "mom_6_1_rank": momentum,
            "mom_12_1_rank": rng.uniform(size=length),
            "ret_1m": rng.normal(0, 0.05, size=length),
            "excess_mom_6_1": rng.normal(0.1, 0.1, size=length),
            "vol_adjusted_mom_6_1": rng.normal(0.5, 0.2, size=length),
            "rel_ret_40": 0.04 * momentum - 0.02 * ratio + rng.normal(0, 0.03, size=length),
        }
    )


class RegressionTest(unittest.TestCase):
    def test_training_outcome_window_ends_before_test_start(self):
        rows = model_fixture()

        for train_index, test_index in chronological_folds(rows, horizon=40, n_folds=5):
            train = rows.iloc[train_index]
            test = rows.iloc[test_index]
            cutoff = test.observation_date.min() - pd.offsets.BDay(40)
            self.assertLess(train.observation_date.max(), cutoff)

    def test_all_specs_use_identical_common_rows(self):
        result = evaluate_specifications(model_fixture(), target="rel_ret_40")

        counts = result.groupby("specification").n_obs.sum()
        self.assertEqual(counts.nunique(), 1)
        self.assertEqual(
            set(result.specification), {"vcp_only", "momentum_only", "vcp_momentum"}
        )

    def test_fitting_emits_no_numeric_warnings(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            result = evaluate_specifications(model_fixture(), target="rel_ret_40")

        self.assertFalse(result.empty)

    def test_linear_predict_matches_explicit_rowwise_dot_products(self):
        matrix = np.arange(12, dtype=float).reshape(4, 3)
        coefficients = np.array([0.5, -0.25, 0.1])

        predicted = linear_predict(matrix, coefficients, 0.03)
        expected = np.array([
            sum(row[index] * coefficients[index] for index in range(3)) + 0.03
            for row in matrix
        ])

        np.testing.assert_allclose(predicted, expected)

    def test_coefficient_table_reports_every_feature_by_fold(self):
        coefficients = coefficient_stability(model_fixture(), target="rel_ret_40")

        momentum = coefficients[coefficients.specification == "momentum_only"]
        self.assertEqual(momentum.fold.nunique(), 4)
        self.assertEqual(
            set(momentum.feature),
            {
                "mom_3_1_rank", "mom_6_1_rank", "mom_12_1_rank", "ret_1m",
                "excess_mom_6_1", "vol_adjusted_mom_6_1",
            },
        )


if __name__ == "__main__":
    unittest.main()
