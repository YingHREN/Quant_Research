from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.regime_threshold_direction import (
    RegimeThresholdDataUnavailable,
    adjust_regime_log_probabilities,
    attach_absolute_and_qqq_relative_targets,
    fit_regime_priors,
    select_economic_down_threshold,
    threshold_directions,
)


def _history(*, exit_close, minimum_low, start="2026-01-02"):
    dates = pd.bdate_range(start, periods=12)
    opens = np.full(len(dates), 100.0)
    closes = np.full(len(dates), 100.0)
    closes[5] = float(exit_close)
    lows = np.full(len(dates), 99.0)
    lows[1:6] = np.asarray(
        [98.0, float(minimum_low), 97.0, 96.0, 95.0]
    )
    return pd.DataFrame(
        {
            "Open": opens,
            "High": np.maximum(opens, closes) + 1.0,
            "Low": lows,
            "Close": closes,
            "Adj Close": closes,
            "Volume": np.full(len(dates), 1_000_000.0),
        },
        index=dates,
    )


def _feature_frame(histories):
    rows = []
    for ticker in ("AAA",):
        for date in histories[ticker].index:
            rows.append(
                {
                    "ticker": ticker,
                    "observation_date": date,
                    "feature": 1.0,
                }
            )
    return pd.DataFrame(rows).set_index(
        ["ticker", "observation_date"]
    )


class TargetAlignmentTest(unittest.TestCase):
    def test_targets_share_exact_entry_exit_and_keep_semantics_separate(self):
        histories = {
            "AAA": _history(exit_close=102.0, minimum_low=90.0),
            "QQQ": _history(exit_close=105.0, minimum_low=94.0),
        }
        frame = _feature_frame(histories)
        frame_before = frame.copy(deep=True)
        histories_before = {
            ticker: history.copy(deep=True)
            for ticker, history in histories.items()
        }
        date = histories["AAA"].index[0]

        result = attach_absolute_and_qqq_relative_targets(frame, histories)

        self.assertAlmostEqual(
            result.loc[("AAA", date), "absolute_return_5"],
            0.02,
        )
        self.assertEqual(
            result.loc[("AAA", date), "absolute_direction_5"],
            "up",
        )
        self.assertAlmostEqual(
            result.loc[("AAA", date), "qqq_relative_return_5"],
            -0.03,
        )
        self.assertEqual(
            result.loc[("AAA", date), "qqq_relative_direction_5"],
            "down",
        )
        self.assertAlmostEqual(
            result.loc[("AAA", date), "maximum_adverse_excursion_5"],
            -0.10,
        )
        self.assertEqual(
            result.loc[("AAA", date), "entry_date_5"],
            histories["AAA"].index[1],
        )
        self.assertEqual(
            result.loc[("AAA", date), "label_end_date_5"],
            histories["AAA"].index[5],
        )
        pd.testing.assert_frame_equal(frame, frame_before)
        for ticker in histories:
            pd.testing.assert_frame_equal(
                histories[ticker],
                histories_before[ticker],
            )

    def test_immature_tail_and_incomplete_low_path_remain_missing(self):
        histories = {
            "AAA": _history(exit_close=102.0, minimum_low=90.0),
            "QQQ": _history(exit_close=105.0, minimum_low=94.0),
        }
        histories["AAA"].loc[
            histories["AAA"].index[3],
            "Low",
        ] = np.nan
        frame = _feature_frame(histories)

        result = attach_absolute_and_qqq_relative_targets(frame, histories)

        first = ("AAA", histories["AAA"].index[0])
        tail = ("AAA", histories["AAA"].index[-3])
        self.assertTrue(
            pd.isna(result.loc[first, "maximum_adverse_excursion_5"])
        )
        self.assertTrue(pd.isna(result.loc[tail, "absolute_return_5"]))
        self.assertTrue(pd.isna(result.loc[tail, "absolute_direction_5"]))
        self.assertTrue(pd.isna(result.loc[tail, "qqq_relative_return_5"]))
        self.assertTrue(
            pd.isna(result.loc[tail, "qqq_relative_direction_5"])
        )

    def test_missing_or_misaligned_qqq_fails_closed(self):
        stock = _history(exit_close=102.0, minimum_low=90.0)
        frame = _feature_frame({"AAA": stock})
        with self.assertRaisesRegex(
            RegimeThresholdDataUnavailable,
            "QQQ",
        ):
            attach_absolute_and_qqq_relative_targets(
                frame,
                {"AAA": stock},
            )

        qqq = _history(
            exit_close=105.0,
            minimum_low=94.0,
            start="2026-03-02",
        )
        with self.assertRaisesRegex(
            RegimeThresholdDataUnavailable,
            "aligned",
        ):
            attach_absolute_and_qqq_relative_targets(
                frame,
                {"AAA": stock, "QQQ": qqq},
            )


class RegimePriorTest(unittest.TestCase):
    def _training_rows(self):
        labels = np.asarray(
            ["down"]
            + ["down"] * 100
            + ["neutral"] * 200
            + ["up"] * 200,
            dtype=object,
        )
        regimes = np.asarray(
            ["small"]
            + ["large"] * 100
            + ["baseline"] * 400,
            dtype=object,
        )
        return labels, regimes

    def test_small_regime_shrinks_more_strongly_than_large_regime(self):
        labels, regimes = self._training_rows()

        priors = fit_regime_priors(
            labels,
            np.ones(len(labels)),
            regimes,
        )

        global_down = priors.global_prior[0]
        small_down = priors.regime_prior("small")[0]
        large_down = priors.regime_prior("large")[0]
        self.assertGreater(small_down, global_down)
        self.assertGreater(large_down, small_down)
        np.testing.assert_allclose(priors.global_prior.sum(), 1.0)
        for regime in ("small", "large", "baseline"):
            np.testing.assert_allclose(
                priors.regime_prior(regime).sum(),
                1.0,
            )

    def test_unseen_or_unavailable_regime_adds_zero_correction(self):
        labels, regimes = self._training_rows()
        priors = fit_regime_priors(
            labels,
            np.ones(len(labels)),
            regimes,
        )
        scores = np.log(
            np.asarray(
                [
                    [0.2, 0.3, 0.5],
                    [0.2, 0.3, 0.5],
                    [0.2, 0.3, 0.5],
                ]
            )
        )

        adjusted = adjust_regime_log_probabilities(
            scores,
            ["large", "unseen", None],
            priors,
        )

        self.assertFalse(np.array_equal(adjusted[0], scores[0]))
        np.testing.assert_allclose(adjusted[1], scores[1])
        np.testing.assert_allclose(adjusted[2], scores[2])

    def test_inputs_are_immutable_and_invalid_weights_fail_closed(self):
        labels, regimes = self._training_rows()
        weights = np.ones(len(labels))
        labels_before = labels.copy()
        regimes_before = regimes.copy()
        weights_before = weights.copy()

        fit_regime_priors(labels, weights, regimes)

        np.testing.assert_array_equal(labels, labels_before)
        np.testing.assert_array_equal(regimes, regimes_before)
        np.testing.assert_array_equal(weights, weights_before)
        invalid = weights.copy()
        invalid[-1] = np.nan
        with self.assertRaisesRegex(ValueError, "weights"):
            fit_regime_priors(labels, invalid, regimes)

    def test_future_rows_do_not_change_already_fitted_prior(self):
        labels, regimes = self._training_rows()
        first = fit_regime_priors(
            labels,
            np.ones(len(labels)),
            regimes,
        )
        extended_labels = np.concatenate(
            (labels, np.asarray(["up"] * 50, dtype=object))
        )
        extended_regimes = np.concatenate(
            (regimes, np.asarray(["large"] * 50, dtype=object))
        )
        fit_regime_priors(
            extended_labels,
            np.ones(len(extended_labels)),
            extended_regimes,
        )

        np.testing.assert_allclose(
            first.regime_prior("large"),
            fit_regime_priors(
                labels,
                np.ones(len(labels)),
                regimes,
            ).regime_prior("large"),
        )


def _threshold_rows(size=1_000):
    actual = np.asarray(["up"] * size, dtype=object)
    returns = np.full(size, 0.02)
    down = np.full(size, 0.10)
    neutral = np.full(size, 0.35)
    up = np.full(size, 0.55)

    actual[:150] = "down"
    returns[:150] = -0.04
    down[:100] = 0.65
    neutral[:100] = 0.20
    up[:100] = 0.15
    down[100:150] = 0.55
    neutral[100:150] = 0.25
    up[100:150] = 0.20

    down[150:170] = 0.65
    neutral[150:170] = 0.20
    up[150:170] = 0.15
    down[170:250] = 0.55
    neutral[170:250] = 0.25
    up[170:250] = 0.20
    return pd.DataFrame(
        {
            "actual_direction": actual,
            "actual_return": returns,
            "down_probability": down,
            "neutral_probability": neutral,
            "up_probability": up,
        }
    )


class EconomicThresholdTest(unittest.TestCase):
    def test_threshold_only_changes_down_boundary(self):
        probabilities = np.asarray(
            [
                [0.65, 0.20, 0.15],
                [0.55, 0.25, 0.20],
                [0.20, 0.45, 0.35],
                [0.20, 0.35, 0.45],
            ]
        )

        result = threshold_directions(probabilities, 0.60)

        self.assertEqual(
            result.tolist(),
            ["down", "neutral", "neutral", "up"],
        )

    def test_selects_highest_threshold_when_best_accuracy_is_tied(self):
        result = select_economic_down_threshold(
            _threshold_rows(),
            minimum_rows=50,
        )

        self.assertEqual(result.status, "available")
        self.assertEqual(result.threshold, 0.60)
        self.assertIsNone(result.reason)
        diagnostics = {
            row["threshold"]: row
            for row in result.diagnostics
        }
        self.assertGreater(
            diagnostics[0.60]["down_precision"],
            diagnostics[0.50]["down_precision"] + 0.02,
        )
        self.assertLess(
            diagnostics[0.60]["mean_return_predicted_down"],
            0.0,
        )

    def test_exact_minimum_rows_and_coverage_boundaries_are_inclusive(self):
        rows = _threshold_rows(size=10_000)
        rows["down_probability"] = 0.10
        rows["neutral_probability"] = 0.35
        rows["up_probability"] = 0.55
        rows.loc[:399, "actual_direction"] = "down"
        rows.loc[:399, "actual_return"] = -0.04
        rows.loc[:399, "down_probability"] = 0.65
        rows.loc[:399, "neutral_probability"] = 0.20
        rows.loc[:399, "up_probability"] = 0.15
        rows.loc[400:499, "down_probability"] = 0.65
        rows.loc[400:499, "neutral_probability"] = 0.20
        rows.loc[400:499, "up_probability"] = 0.15
        rows.loc[500:999, "down_probability"] = 0.55
        rows.loc[500:999, "neutral_probability"] = 0.25
        rows.loc[500:999, "up_probability"] = 0.20

        result = select_economic_down_threshold(rows)

        self.assertEqual(result.status, "available")
        self.assertEqual(result.threshold, 0.60)
        selected = next(
            item
            for item in result.diagnostics
            if item["threshold"] == 0.60
        )
        self.assertEqual(selected["down_count"], 500)
        self.assertAlmostEqual(selected["down_coverage"], 0.05)

    def test_no_eligible_threshold_is_explicitly_unavailable(self):
        rows = _threshold_rows()
        rows["actual_return"] = 0.02

        result = select_economic_down_threshold(
            rows,
            minimum_rows=50,
        )

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.threshold)
        self.assertEqual(
            result.reason,
            "economic_threshold_unavailable",
        )
        self.assertEqual(
            {item["status"] for item in result.diagnostics},
            {"rejected"},
        )

    def test_unrelated_outer_outcomes_cannot_change_selection(self):
        inner = _threshold_rows()
        outer = pd.Series([-0.50, 0.50, -0.30])
        first = select_economic_down_threshold(
            inner,
            minimum_rows=50,
        )
        outer.iloc[:] *= -1.0
        second = select_economic_down_threshold(
            inner,
            minimum_rows=50,
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
