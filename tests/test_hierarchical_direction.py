from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from research.hierarchical_direction import (
    DIRECTION_CLASSES,
    HALF_LIFE_BY_HORIZON,
    _ordered_log_probabilities,
    adjust_log_probabilities,
    fit_hierarchical_priors,
    freeze_behavior_groups,
    recency_class_weights,
    walk_forward_hierarchical_predictions,
)


class RecencyWeightTest(unittest.TestCase):
    def test_half_life_ratio_is_exact_and_weights_decay_monotonically(self):
        dates = pd.bdate_range("2025-01-02", periods=127)
        labels = np.repeat("up", len(dates))

        weights, diagnostics = recency_class_weights(
            dates,
            labels,
            5,
            minimum_effective_samples=1,
            minimum_class_effective_samples=1,
        )

        self.assertEqual(HALF_LIFE_BY_HORIZON[5], 126)
        self.assertEqual(diagnostics["status"], "available")
        self.assertAlmostEqual(weights[0] / weights[-1], 0.5, places=12)
        self.assertTrue(np.all(np.diff(weights) > 0.0))
        self.assertAlmostEqual(float(weights.mean()), 1.0, places=12)

    def test_time_weighted_class_balancing_equalizes_class_weight_sums(self):
        dates = pd.bdate_range("2024-01-02", periods=400)
        labels = np.asarray(
            ["down"] * 40 + ["neutral"] * 120 + ["up"] * 240,
            dtype=object,
        )

        weights, diagnostics = recency_class_weights(
            dates,
            labels,
            20,
            minimum_effective_samples=1,
            minimum_class_effective_samples=1,
        )

        sums = {
            label: float(weights[labels == label].sum())
            for label in ("down", "neutral", "up")
        }
        self.assertAlmostEqual(sums["down"], sums["neutral"], places=10)
        self.assertAlmostEqual(sums["neutral"], sums["up"], places=10)
        self.assertEqual(
            set(diagnostics["class_effective_sample_size"]),
            {"down", "neutral", "up"},
        )
        self.assertGreater(diagnostics["effective_sample_size"], 0.0)

    def test_inputs_are_immutable_and_invalid_labels_fail_closed(self):
        dates = pd.bdate_range("2025-01-02", periods=12)
        labels = np.asarray(["up", "neutral", "down"] * 4, dtype=object)
        labels_before = labels.copy()
        dates_before = dates.copy()

        recency_class_weights(
            dates,
            labels,
            60,
            minimum_effective_samples=1,
            minimum_class_effective_samples=1,
        )

        np.testing.assert_array_equal(labels, labels_before)
        self.assertTrue(dates.equals(dates_before))
        with self.assertRaisesRegex(ValueError, "direction labels"):
            recency_class_weights(
                dates,
                np.asarray(["unknown"] * len(dates), dtype=object),
                60,
                minimum_effective_samples=1,
                minimum_class_effective_samples=1,
            )
        with self.assertRaisesRegex(ValueError, "supported"):
            recency_class_weights(
                dates,
                labels,
                7,
                minimum_effective_samples=1,
                minimum_class_effective_samples=1,
            )

    def test_effective_sample_gates_return_typed_unavailable_diagnostics(self):
        dates = pd.bdate_range("2025-01-02", periods=120)
        labels = np.asarray(["up", "neutral", "down"] * 40, dtype=object)

        weights, diagnostics = recency_class_weights(dates, labels, 5)

        self.assertIsNone(weights)
        self.assertEqual(diagnostics["status"], "unavailable")
        self.assertEqual(
            diagnostics["reason"],
            "insufficient_effective_samples",
        )

        imbalanced = np.asarray(["down"] + ["up"] * 119, dtype=object)
        weights, diagnostics = recency_class_weights(
            dates,
            imbalanced,
            5,
            minimum_effective_samples=1,
        )
        self.assertIsNone(weights)
        self.assertEqual(
            diagnostics["reason"],
            "insufficient_class_effective_samples",
        )


class StableProbabilityTest(unittest.TestCase):
    def test_log_probabilities_use_stable_finite_logits(self):
        class FittedModel:
            classes_ = np.asarray(["down", "neutral", "up"], dtype=object)
            coef_ = np.linspace(-1.1, 1.1, 3 * 48).reshape(3, 48)
            intercept_ = np.asarray([-0.2, -0.5, -0.1])

            def predict_log_proba(self, design):
                raise AssertionError("unstable estimator matmul path used")

        design = np.linspace(-10.9, 10.9, 396 * 48).reshape(396, 48)

        result = _ordered_log_probabilities(FittedModel(), design)

        self.assertEqual(result.shape, (396, 3))
        self.assertTrue(np.isfinite(result).all())
        np.testing.assert_allclose(
            np.exp(result).sum(axis=1),
            np.ones(len(result)),
            rtol=1e-12,
            atol=1e-12,
        )


def _price_frame(returns, start="2025-01-02"):
    prices = [100.0]
    for value in returns:
        prices.append(prices[-1] * (1.0 + value))
    dates = pd.bdate_range(start, periods=len(prices))
    return pd.DataFrame(
        {
            "Open": prices,
            "High": np.asarray(prices) + 1.0,
            "Low": np.asarray(prices) - 1.0,
            "Close": prices,
            "Adj Close": prices,
            "Volume": np.full(len(prices), 1_000_000.0),
        },
        index=dates,
    )


def _behavior_histories(periods=180):
    market = np.asarray(
        [0.001 if index % 2 == 0 else -0.0007 for index in range(periods)]
    )
    technology_residual = np.asarray(
        [0.008 if index % 5 in (0, 1) else -0.004 for index in range(periods)]
    )
    energy_residual = np.asarray(
        [0.007 if index % 7 in (0, 3) else -0.002 for index in range(periods)]
    )
    return {
        "ZZZ": _price_frame(market * 1.1 + technology_residual * 1.25),
        "SPY": _price_frame(market),
        "XLK": _price_frame(market * 0.9 + technology_residual),
        "XLE": _price_frame(market * 0.8 + energy_residual),
    }


class FoldFrozenGroupTest(unittest.TestCase):
    def test_behavior_groups_are_deterministic_and_cutoff_causal(self):
        histories = _behavior_histories()
        cutoff = histories["ZZZ"].index[160]
        before = {
            ticker: frame.copy(deep=True)
            for ticker, frame in histories.items()
        }

        groups, diagnostics = freeze_behavior_groups(
            histories,
            ("ZZZ",),
            cutoff,
            sector_etfs={"technology": "XLK", "energy": "XLE"},
        )

        self.assertEqual(groups, {"ZZZ": "technology"})
        self.assertEqual(diagnostics["classified_count"], 1)
        self.assertEqual(diagnostics["unavailable_count"], 0)
        self.assertEqual(diagnostics["rule_version"], "market_behavior_v1")
        for ticker in histories:
            pd.testing.assert_frame_equal(histories[ticker], before[ticker])

        for ticker, frame in histories.items():
            future_date = frame.index[-1] + pd.offsets.BDay()
            changed = frame.iloc[-1].copy()
            changed.loc[["Close", "Adj Close"]] *= 20.0
            histories[ticker].loc[future_date] = changed
        after_groups, after_diagnostics = freeze_behavior_groups(
            histories,
            ("ZZZ",),
            cutoff,
            sector_etfs={"technology": "XLK", "energy": "XLE"},
        )
        self.assertEqual(after_groups, groups)
        self.assertEqual(
            after_diagnostics["sector_counts"],
            diagnostics["sector_counts"],
        )

    def test_missing_or_short_reference_history_is_explicitly_unavailable(self):
        histories = _behavior_histories(periods=40)

        groups, diagnostics = freeze_behavior_groups(
            histories,
            ("ZZZ", "AAA"),
            histories["ZZZ"].index[-1],
            sector_etfs={"technology": "XLK"},
        )

        self.assertEqual(list(groups), ["AAA", "ZZZ"])
        self.assertEqual(groups, {"AAA": None, "ZZZ": None})
        self.assertEqual(diagnostics["classified_count"], 0)
        self.assertEqual(diagnostics["unavailable_count"], 2)

        without_spy = dict(histories)
        del without_spy["SPY"]
        groups, diagnostics = freeze_behavior_groups(
            without_spy,
            ("ZZZ",),
            histories["ZZZ"].index[-1],
            sector_etfs={"technology": "XLK"},
        )
        self.assertEqual(groups["ZZZ"], None)
        self.assertEqual(diagnostics["unavailable_count"], 1)


class HierarchicalPriorTest(unittest.TestCase):
    def priors(self):
        labels = np.asarray(
            ["down"]
            + ["down"] * 100
            + ["neutral"] * 200
            + ["up"] * 200,
            dtype=object,
        )
        tickers = np.asarray(
            ["SMALL"]
            + ["LARGE"] * 100
            + ["BASE"] * 400,
            dtype=object,
        )
        groups = np.asarray(
            ["small_group"]
            + ["large_group"] * 100
            + ["base_group"] * 400,
            dtype=object,
        )
        return fit_hierarchical_priors(
            labels,
            np.ones(len(labels)),
            tickers,
            groups,
            DIRECTION_CLASSES,
        )

    def test_missing_levels_fall_back_to_parent_priors(self):
        priors = self.priors()

        np.testing.assert_allclose(
            priors.group_prior("missing"),
            priors.global_prior,
        )
        np.testing.assert_allclose(
            priors.ticker_prior("missing", "large_group"),
            priors.group_prior("large_group"),
        )
        self.assertAlmostEqual(float(priors.global_prior.sum()), 1.0)
        for values in priors.group_priors.values():
            self.assertAlmostEqual(float(np.asarray(values).sum()), 1.0)
        for values in priors.ticker_priors.values():
            self.assertAlmostEqual(float(np.asarray(values).sum()), 1.0)

    def test_large_matching_sample_moves_further_from_global_than_small_sample(self):
        priors = self.priors()
        global_down = priors.global_prior[0]
        small_delta = priors.group_prior("small_group")[0] - global_down
        large_delta = priors.group_prior("large_group")[0] - global_down

        self.assertGreater(small_delta, 0.0)
        self.assertGreater(large_delta, small_delta)

    def test_unseen_rows_keep_global_scores_and_ticker_layer_is_incremental(self):
        labels = np.asarray(
            ["down"] * 60 + ["neutral"] * 60 + ["up"] * 60,
            dtype=object,
        )
        tickers = np.asarray(
            ["DOWN"] * 60 + ["MIX"] * 60 + ["UP"] * 60,
            dtype=object,
        )
        groups = np.asarray(["one"] * 180, dtype=object)
        priors = fit_hierarchical_priors(
            labels,
            np.ones(len(labels)),
            tickers,
            groups,
            DIRECTION_CLASSES,
        )
        base = np.log(np.asarray([[0.30, 0.40, 0.30]]))

        unseen = adjust_log_probabilities(
            base,
            np.asarray(["NEW"]),
            np.asarray([None], dtype=object),
            priors,
            include_group=True,
            include_ticker=True,
        )
        np.testing.assert_allclose(unseen, base)

        group_only = adjust_log_probabilities(
            base,
            np.asarray(["DOWN"]),
            np.asarray(["one"]),
            priors,
            include_group=True,
            include_ticker=False,
        )
        with_ticker = adjust_log_probabilities(
            base,
            np.asarray(["DOWN"]),
            np.asarray(["one"]),
            priors,
            include_group=True,
            include_ticker=True,
        )
        self.assertFalse(np.allclose(group_only, with_ticker))
        self.assertGreater(with_ticker[0, 0], group_only[0, 0])


def _walk_forward_fixture(periods=330):
    dates = pd.bdate_range("2023-01-03", periods=periods)
    market_returns = 0.002 * np.sin(np.arange(periods - 1) / 5.0)
    tech_returns = market_returns + 0.006 * np.sin(
        np.arange(periods - 1) / 3.0
    )
    energy_returns = market_returns + 0.005 * np.cos(
        np.arange(periods - 1) / 4.0
    )
    histories = {
        "SPY": _price_frame(market_returns, start=str(dates[0].date())),
        "XLK": _price_frame(tech_returns, start=str(dates[0].date())),
        "XLE": _price_frame(energy_returns, start=str(dates[0].date())),
    }
    rows = []
    keys = []
    for offset, ticker in enumerate(("AAA", "BBB", "CCC")):
        stock_returns = (
            market_returns
            + 0.008 * np.sin(
                np.arange(periods - 1) / (3.0 + offset)
            )
        )
        histories[ticker] = _price_frame(
            stock_returns,
            start=str(dates[0].date()),
        )
        for position, date in enumerate(dates):
            signal = np.sin(position / (5.0 + offset))
            context = np.cos(position / 11.0)
            executable_return = 0.035 * signal + 0.012 * context
            rows.append(
                {
                    "signal": signal,
                    "context": context,
                    "executable_return_5": executable_return,
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
    return frame, histories


class HierarchicalWalkForwardTest(unittest.TestCase):
    def test_all_ablation_candidates_share_five_causal_test_folds(self):
        frame, histories = _walk_forward_fixture()

        predictions, weights, groups = (
            walk_forward_hierarchical_predictions(
                frame,
                histories,
                horizon=5,
                feature_columns=("signal", "context"),
                n_test_folds=5,
                minimum_samples=30,
            )
        )

        specifications = {
            "logistic_global",
            "logistic_time",
            "logistic_group",
            "logistic_time_group",
            "logistic_time_group_ticker",
        }
        self.assertEqual(set(predictions["specification"]), specifications)
        self.assertEqual(set(predictions["fold"]), {1, 2, 3, 4, 5})
        expected = None
        for specification in sorted(specifications):
            selected = predictions.loc[
                predictions["specification"] == specification,
                ["ticker", "observation_date", "horizon", "fold"],
            ].reset_index(drop=True)
            if expected is None:
                expected = selected
            else:
                pd.testing.assert_frame_equal(selected, expected)
        self.assertTrue(
            (
                pd.to_datetime(predictions["training_label_end_max"])
                < pd.to_datetime(predictions["test_start"])
            ).all()
        )
        self.assertEqual(set(weights["status"]), {"available"})
        self.assertEqual(set(groups["fold"]), {1, 2, 3, 4, 5})

    def test_last_fold_outcomes_cannot_change_its_predictions(self):
        frame, histories = _walk_forward_fixture()
        before, _, _ = walk_forward_hierarchical_predictions(
            frame,
            histories,
            horizon=5,
            feature_columns=("signal", "context"),
            n_test_folds=5,
            minimum_samples=30,
        )
        last_keys = before.loc[
            before["fold"] == 5,
            ["ticker", "observation_date"],
        ].drop_duplicates()
        changed = frame.copy(deep=True)
        for row in last_keys.itertuples(index=False):
            changed.loc[
                (row.ticker, row.observation_date),
                "executable_return_5",
            ] *= -50.0

        after, _, _ = walk_forward_hierarchical_predictions(
            changed,
            histories,
            horizon=5,
            feature_columns=("signal", "context"),
            n_test_folds=5,
            minimum_samples=30,
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

    def test_insufficient_effective_samples_emit_diagnostics_not_predictions(self):
        frame, histories = _walk_forward_fixture(periods=120)

        predictions, weights, groups = (
            walk_forward_hierarchical_predictions(
                frame,
                histories,
                horizon=5,
                feature_columns=("signal", "context"),
                n_test_folds=5,
                minimum_samples=10_000,
            )
        )

        self.assertTrue(predictions.empty)
        self.assertFalse(weights.empty)
        self.assertTrue(
            weights["reason"].isin(
                {
                    "insufficient_training_samples",
                    "insufficient_effective_samples",
                    "insufficient_class_effective_samples",
                }
            ).all()
        )
        self.assertFalse(groups.empty)


if __name__ == "__main__":
    unittest.main()
