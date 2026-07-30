from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.market_direction_model import (
    attach_next_open_targets,
    direction_labels,
)
from research.regime_threshold_direction import (
    RegimeThresholdDataUnavailable,
    _ordered_probabilities,
    adjust_regime_log_probabilities,
    attach_absolute_and_qqq_relative_targets,
    attach_qqq_relative_targets,
    fit_regime_priors,
    select_economic_down_threshold,
    threshold_directions,
    walk_forward_qqq_relative_predictions,
    walk_forward_regime_threshold_predictions,
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

    def test_missing_qqq_fails_and_missing_exact_endpoints_stay_null(self):
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
        result = attach_absolute_and_qqq_relative_targets(
            frame,
            {"AAA": stock, "QQQ": qqq},
        )
        self.assertTrue(result["qqq_relative_return_5"].isna().all())

    def test_stock_calendar_gap_uses_its_recorded_exact_qqq_endpoints(self):
        stock = _history(exit_close=102.0, minimum_low=90.0)
        qqq = _history(exit_close=105.0, minimum_low=94.0)
        missing_stock_date = stock.index[3]
        stock = stock.drop(index=missing_stock_date)
        frame = _feature_frame({"AAA": stock})
        observation = stock.index[0]
        entry = stock.index[1]
        exit_date = stock.index[5]
        qqq.loc[entry, "Open"] = 100.0
        qqq.loc[exit_date, "Close"] = 106.0

        result = attach_absolute_and_qqq_relative_targets(
            frame,
            {"AAA": stock, "QQQ": qqq},
        )

        absolute = result.loc[
            ("AAA", observation),
            "absolute_return_5",
        ]
        relative = result.loc[
            ("AAA", observation),
            "qqq_relative_return_5",
        ]
        self.assertAlmostEqual(relative, absolute - 0.06)


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


class StableProbabilityTest(unittest.TestCase):
    def test_extreme_finite_logits_are_normalized_without_estimator_matmul(self):
        class FittedModel:
            classes_ = np.asarray(
                ["down", "neutral", "up"],
                dtype=object,
            )
            coef_ = np.asarray(
                [
                    [1e308, -1e308],
                    [-1e308, 1e308],
                    [1e308, 1e308],
                ]
            )
            intercept_ = np.asarray([0.0, 0.0, 0.0])

            def predict_proba(self, design):
                raise AssertionError("unstable estimator matmul used")

        probabilities = _ordered_probabilities(
            FittedModel(),
            np.asarray([[12.0, 11.0], [-12.0, 11.0]]),
        )

        self.assertTrue(np.isfinite(probabilities).all())
        np.testing.assert_allclose(
            probabilities.sum(axis=1),
            np.ones(2),
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


def _nested_fixture(periods=420):
    dates = pd.bdate_range("2023-01-03", periods=periods)
    rows = []
    keys = []
    for offset, ticker in enumerate(("AAA", "BBB", "CCC")):
        for position, date in enumerate(dates):
            signal = np.sin(position / (4.0 + offset))
            context = np.cos(position / (7.0 + offset))
            target = 0.035 * signal + 0.018 * context
            relative_target = 0.035 * signal - 0.018 * context
            rows.append(
                {
                    "signal": signal,
                    "context": context,
                    "absolute_return_5": target,
                    "qqq_relative_return_5": relative_target,
                    "executable_return_5": target,
                    "executable_label_end_date_5": (
                        date + pd.offsets.BDay(5)
                    ),
                }
            )
            keys.append((ticker, date))
    frame = pd.DataFrame(
        rows,
        index=pd.MultiIndex.from_tuples(
            keys,
            names=("ticker", "observation_date"),
        ),
    ).sort_index()
    regimes = pd.DataFrame(
        {
            "regime": np.where(
                np.arange(periods) % 3 == 0,
                "under_pressure",
                "uptrend",
            )
        },
        index=dates,
    )
    return frame, regimes


class NestedWalkForwardTest(unittest.TestCase):
    def test_global_and_regime_candidates_share_five_causal_folds(self):
        frame, regimes = _nested_fixture()

        predictions, diagnostics = (
            walk_forward_regime_threshold_predictions(
                frame,
                regimes,
                feature_columns=("signal", "context"),
                minimum_samples=30,
            )
        )

        self.assertEqual(set(diagnostics["outer_fold"]), {1, 2, 3, 4, 5})
        self.assertTrue(
            (
                pd.to_datetime(diagnostics["outer_train_label_end_max"])
                < pd.to_datetime(diagnostics["outer_test_start"])
            ).all()
        )
        available = {
            "logistic_global",
            "logistic_regime_prior",
        }
        self.assertTrue(available.issubset(set(predictions["specification"])))
        expected = None
        for specification in sorted(available):
            selected = predictions.loc[
                predictions["specification"] == specification,
                ["ticker", "observation_date", "horizon", "fold"],
            ].reset_index(drop=True)
            if expected is None:
                expected = selected
            else:
                pd.testing.assert_frame_equal(selected, expected)
        for boundaries in diagnostics["inner_fold_boundaries"]:
            self.assertTrue(boundaries)
            for boundary in boundaries:
                self.assertLess(
                    pd.Timestamp(boundary["training_label_end_max"]),
                    pd.Timestamp(boundary["test_start"]),
                )

    def test_outer_outcomes_and_future_regimes_cannot_change_predictions(self):
        frame, regimes = _nested_fixture()
        before, before_diagnostics = (
            walk_forward_regime_threshold_predictions(
                frame,
                regimes,
                feature_columns=("signal", "context"),
                minimum_samples=30,
            )
        )
        changed = frame.copy(deep=True)
        last_test_dates = before.loc[
            before["fold"] == 5,
            "observation_date",
        ].unique()
        selected = changed.index.get_level_values(
            "observation_date"
        ).isin(last_test_dates)
        changed.loc[selected, "absolute_return_5"] *= -50.0
        changed.loc[selected, "executable_return_5"] *= -50.0
        future_regimes = regimes.copy(deep=True)
        future_dates = pd.bdate_range(
            regimes.index[-1] + pd.offsets.BDay(),
            periods=20,
        )
        future_regimes = pd.concat(
            (
                future_regimes,
                pd.DataFrame(
                    {"regime": "acute_selloff"},
                    index=future_dates,
                ),
            )
        )

        after, after_diagnostics = (
            walk_forward_regime_threshold_predictions(
                changed,
                future_regimes,
                feature_columns=("signal", "context"),
                minimum_samples=30,
            )
        )

        columns = [
            "ticker",
            "observation_date",
            "fold",
            "specification",
            "predicted_direction",
        ]
        pd.testing.assert_frame_equal(
            before.loc[before["fold"] == 5, columns].reset_index(drop=True),
            after.loc[after["fold"] == 5, columns].reset_index(drop=True),
        )
        first_thresholds = before_diagnostics.set_index("outer_fold")[
            "selected_threshold"
        ]
        second_thresholds = after_diagnostics.set_index("outer_fold")[
            "selected_threshold"
        ]
        pd.testing.assert_series_equal(first_thresholds, second_thresholds)

    def test_unavailable_threshold_is_explicit_and_not_fabricated(self):
        frame, regimes = _nested_fixture(periods=180)

        predictions, diagnostics = (
            walk_forward_regime_threshold_predictions(
                frame,
                regimes,
                feature_columns=("signal", "context"),
                minimum_samples=20,
            )
        )

        self.assertEqual(
            set(diagnostics["threshold_status"]),
            {"unavailable"},
        )
        self.assertEqual(
            set(diagnostics["threshold_reason"]),
            {"economic_threshold_unavailable"},
        )
        self.assertNotIn(
            "logistic_regime_threshold",
            set(predictions["specification"]),
        )

    def test_missing_direction_classes_emit_diagnostics_not_predictions(self):
        frame, regimes = _nested_fixture(periods=180)
        frame["absolute_return_5"] = 0.03
        frame["executable_return_5"] = 0.03

        predictions, diagnostics = (
            walk_forward_regime_threshold_predictions(
                frame,
                regimes,
                feature_columns=("signal", "context"),
                minimum_samples=20,
            )
        )

        self.assertTrue(predictions.empty)
        self.assertFalse(diagnostics.empty)
        self.assertEqual(
            set(diagnostics["reason"]),
            {"missing_direction_class"},
        )


class RelativeHeadTest(unittest.TestCase):
    def test_relative_head_is_semantically_separate_and_shares_outer_keys(self):
        frame, regimes = _nested_fixture()
        absolute, _ = walk_forward_regime_threshold_predictions(
            frame,
            regimes,
            feature_columns=("signal", "context"),
            minimum_samples=30,
        )

        relative = walk_forward_qqq_relative_predictions(
            frame,
            feature_columns=("signal", "context"),
            minimum_samples=30,
        )

        self.assertEqual(
            set(relative["specification"]),
            {"logistic_qqq_relative"},
        )
        self.assertNotIn("actual_direction", relative.columns)
        self.assertNotIn("predicted_direction", relative.columns)
        absolute_keys = absolute.loc[
            absolute["specification"] == "logistic_global",
            ["ticker", "observation_date", "horizon", "fold"],
        ].reset_index(drop=True)
        relative_keys = relative.loc[
            :,
            ["ticker", "observation_date", "horizon", "fold"],
        ].reset_index(drop=True)
        pd.testing.assert_frame_equal(relative_keys, absolute_keys)
        semantic_example = relative.loc[
            (relative["actual_return"] > 0.01)
            & (relative["actual_relative_return"] < -0.01)
        ].iloc[0]
        self.assertEqual(
            semantic_example["actual_relative_direction"],
            "down",
        )
        self.assertEqual(
            direction_labels(
                pd.Series([semantic_example["actual_return"]]),
                5,
            )[0],
            "up",
        )

    def test_missing_relative_targets_produce_no_fabricated_rows(self):
        frame, _ = _nested_fixture(periods=180)
        frame["qqq_relative_return_5"] = np.nan

        relative = walk_forward_qqq_relative_predictions(
            frame,
            feature_columns=("signal", "context"),
            minimum_samples=20,
        )

        self.assertTrue(relative.empty)
        self.assertIn("actual_relative_direction", relative.columns)
        self.assertNotIn("actual_direction", relative.columns)


def _relative_history(index, *, open_start, close_step):
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
        "AAA": _relative_history(
            qqq_dates,
            open_start=50.0,
            close_step=2.0,
        ),
        "BBB": _relative_history(
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
    qqq = _relative_history(
        qqq_dates,
        open_start=100.0,
        close_step=2.0,
    )
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


if __name__ == "__main__":
    unittest.main()
