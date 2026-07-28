import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
import sqlite3
from unittest import mock

import pandas as pd
from pandas.testing import assert_frame_equal

import research.run_unified_downside_benchmark as runner
from research.run_unified_downside_benchmark import (
    BenchmarkConfig,
    BenchmarkDependencies,
    BenchmarkInputs,
    _assignment_fingerprint,
    _config_fingerprint,
    _database_fingerprint,
    _validate_config,
    run_benchmark,
)
from research.unified_benchmark_cache import UnifiedBenchmarkCacheStore


def prices():
    dates = pd.bdate_range("2026-01-02", periods=8)
    close = [100.0, 101.0, 99.0, 98.0, 96.0, 94.0, 95.0, 97.0]
    return pd.DataFrame(
        {
            "ticker": "AAA",
            "observation_date": dates,
            "Open": close,
            "High": [value + 2.0 for value in close],
            "Low": [value - 2.0 for value in close],
            "Close": close,
        }
    )


def assignments():
    return pd.DataFrame(
        {
            "ticker": ["AAA"],
            "theme_keys": [("semiconductor",)],
            "primary_model_group": ["semiconductor"],
            "classification_state": ["classified"],
            "effective_from": [pd.Timestamp("2020-01-01")],
            "effective_to": [pd.NaT],
            "source": ["override"],
        }
    )


def inputs():
    return BenchmarkInputs(
        prices=prices(),
        assignments=assignments(),
        regimes=pd.DataFrame(
            {
                "observation_date": pd.bdate_range("2026-01-02", periods=8),
                "regime": "correction",
            }
        ),
        analysis_tickers=("AAA",),
    )


def statistical_predictions():
    dates = pd.bdate_range("2026-01-02", periods=2)
    return {
        "ridge_down": pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "observation_date": dates,
                "horizon": pd.Series([5, 5], dtype="int64"),
                "fold": pd.Series([1, 1], dtype="int64"),
                "predicted_event": pd.Series([True, False], dtype="boolean"),
                "predicted_score": [0.08, -0.03],
                "model_version": ["ridge_direction_v1"] * 2,
            }
        )
    }


def rule_predictions(statistical):
    anchor = statistical["ridge_down"]
    definitions = {
        "immediate_8": "bearish_turn_immediate_v1",
        "memory_12": "bearish_turn_risk_rules_v2",
        "toprisk_confirmed": "toprisk_v1",
        "toprisk_stateful": "toprisk_v1",
        "ridge_plus_toprisk": "forecast_decision_policy_toprisk_v1",
    }
    return {
        specification: anchor.assign(
            predicted_event=pd.Series([True, True], dtype="boolean"),
            predicted_score=[80.0, 70.0],
            model_version=version,
        )
        for specification, version in definitions.items()
    }


class UnifiedBenchmarkCacheIntegrationTest(unittest.TestCase):
    def test_database_fingerprint_changes_for_same_shape_price_revision(self):
        first = inputs()
        revised_prices = first.prices.copy()
        revised_prices.loc[0, "Close"] += 0.01
        second = replace(first, prices=revised_prices)

        self.assertNotEqual(
            _database_fingerprint(first),
            _database_fingerprint(second),
        )
        self.assertEqual(
            _assignment_fingerprint(first),
            _assignment_fingerprint(second),
        )

    def test_assignment_fingerprint_changes_for_interval_revision(self):
        first = inputs()
        revised = first.assignments.copy()
        revised.loc[0, "effective_from"] = pd.Timestamp("2021-01-01")

        self.assertNotEqual(
            _assignment_fingerprint(first),
            _assignment_fingerprint(replace(first, assignments=revised)),
        )

    def test_stage_config_fingerprint_invalidates_models_not_evaluation_threshold(self):
        loaded = inputs()
        baseline = BenchmarkConfig(database=Path("research.db"), folds=2)

        self.assertNotEqual(
            _config_fingerprint(
                baseline, loaded, "statistical_predictions"
            ),
            _config_fingerprint(
                replace(baseline, folds=3),
                loaded,
                "statistical_predictions",
            ),
        )
        self.assertEqual(
            _config_fingerprint(
                baseline, loaded, "statistical_predictions"
            ),
            _config_fingerprint(
                replace(baseline, minimum_group_samples=999),
                loaded,
                "statistical_predictions",
            ),
        )

    def test_hot_run_skips_builders_and_matches_cold_results(self):
        calls = Counter()
        loaded = inputs()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = BenchmarkConfig(
                database=root / "research.db",
                cache_database=root / "cache.db",
                folds=2,
                horizons=(5,),
                minimum_group_samples=1,
                output_directory=root / "reports",
            )

            def build_statistical(_inputs, _config):
                calls["statistical"] += 1
                return statistical_predictions()

            def build_rules(_inputs, _config, statistical):
                calls["rules"] += 1
                return rule_predictions(statistical)

            dependencies = BenchmarkDependencies(
                load_inputs=lambda _: loaded,
                build_predictions=build_statistical,
                build_rule_predictions=build_rules,
                code_fingerprint=lambda: ("e" * 64, False),
            )
            cold = run_benchmark(config, dependencies=dependencies)
            self.assertEqual(calls, Counter(statistical=1, rules=1))
            calls.clear()

            hot = run_benchmark(config, dependencies=dependencies)

            self.assertEqual(calls, Counter())
            assert_frame_equal(hot.metrics, cold.metrics)
            assert_frame_equal(hot.fold_comparisons, cold.fold_comparisons)
            self.assertEqual(
                hot.manifest["promotion_gate"],
                cold.manifest["promotion_gate"],
            )
            self.assertEqual(
                hot.manifest["cache"]["statistical_predictions"]["status"],
                "hit",
            )
            self.assertEqual(
                hot.manifest["cache"]["rule_predictions"]["status"],
                "hit",
            )
            self.assertEqual(len(UnifiedBenchmarkCacheStore(root / "cache.db").status()), 2)

    def test_evaluation_only_change_reuses_predictions_and_recomputes_metrics(self):
        calls = Counter()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = BenchmarkConfig(
                database=root / "research.db",
                cache_database=root / "cache.db",
                folds=2,
                horizons=(5,),
                minimum_group_samples=1,
                output_directory=root / "reports",
            )

            def build_statistical(*_):
                calls["statistical"] += 1
                return statistical_predictions()

            def build_rules(_inputs, _config, statistical):
                calls["rules"] += 1
                return rule_predictions(statistical)

            dependencies = BenchmarkDependencies(
                load_inputs=lambda _: inputs(),
                build_predictions=build_statistical,
                build_rule_predictions=build_rules,
                code_fingerprint=lambda: ("e" * 64, False),
            )
            run_benchmark(config, dependencies=dependencies)
            calls.clear()

            changed = run_benchmark(
                replace(config, minimum_group_samples=999),
                dependencies=dependencies,
            )

            self.assertEqual(calls, Counter())
            self.assertEqual(changed.manifest["minimum_group_samples"], 999)
            self.assertTrue((changed.metrics["status"] != "ok").all())

    def test_corrupt_statistical_rebuilds_both_and_corrupt_rule_rebuilds_only_rule(self):
        calls = Counter()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = BenchmarkConfig(
                database=root / "research.db",
                cache_database=root / "cache.db",
                folds=2,
                horizons=(5,),
                minimum_group_samples=1,
                output_directory=root / "reports",
            )

            def build_statistical(*_):
                calls["statistical"] += 1
                return statistical_predictions()

            def build_rules(_inputs, _config, statistical):
                calls["rules"] += 1
                return rule_predictions(statistical)

            dependencies = BenchmarkDependencies(
                load_inputs=lambda _: inputs(),
                build_predictions=build_statistical,
                build_rule_predictions=build_rules,
                code_fingerprint=lambda: ("e" * 64, False),
            )
            run_benchmark(config, dependencies=dependencies)
            calls.clear()
            with sqlite3.connect(config.cache_database) as connection:
                connection.execute(
                    """
                    UPDATE benchmark_cache_artifacts
                    SET payload_checksum = ?
                    WHERE stage = 'statistical_predictions'
                    """,
                    ("0" * 64,),
                )

            rebuilt = run_benchmark(config, dependencies=dependencies)

            self.assertEqual(calls, Counter(statistical=1, rules=1))
            self.assertEqual(
                rebuilt.manifest["cache"]["statistical_predictions"]["status"],
                "miss_corrupt",
            )
            with sqlite3.connect(config.cache_database) as connection:
                connection.execute(
                    """
                    UPDATE benchmark_cache_artifacts
                    SET payload_checksum = ?
                    WHERE stage = 'statistical_predictions'
                    """,
                    (
                        statistical_checksum(config.cache_database),
                    ),
                )
                connection.execute(
                    """
                    UPDATE benchmark_cache_artifacts
                    SET payload_checksum = ?
                    WHERE stage = 'rule_predictions'
                    """,
                    ("0" * 64,),
                )
            calls.clear()

            rebuilt_rule = run_benchmark(config, dependencies=dependencies)

            self.assertEqual(calls, Counter(rules=1))
            self.assertEqual(
                rebuilt_rule.manifest["cache"]["statistical_predictions"][
                    "status"
                ],
                "hit",
            )
            self.assertEqual(
                rebuilt_rule.manifest["cache"]["rule_predictions"]["status"],
                "miss_corrupt",
            )

    def test_dirty_worktree_disables_reads_and_writes(self):
        calls = Counter()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = BenchmarkConfig(
                database=root / "research.db",
                cache_database=root / "cache.db",
                folds=2,
                horizons=(5,),
                minimum_group_samples=1,
                output_directory=root / "reports",
            )
            dependencies = BenchmarkDependencies(
                load_inputs=lambda _: inputs(),
                build_predictions=lambda *_: (
                    calls.update(statistical=1) or statistical_predictions()
                ),
                code_fingerprint=lambda: ("e" * 64, True),
            )

            artifacts = run_benchmark(config, dependencies=dependencies)

            self.assertEqual(calls["statistical"], 1)
            self.assertEqual(
                artifacts.manifest["cache"]["mode"],
                "disabled_dirty_worktree",
            )
            self.assertEqual(len(UnifiedBenchmarkCacheStore(root / "cache.db").status()), 0)

    def test_publish_failure_leaves_cache_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = BenchmarkConfig(
                database=root / "research.db",
                cache_database=root / "cache.db",
                folds=2,
                horizons=(5,),
                minimum_group_samples=1,
                output_directory=root / "reports",
            )
            dependencies = BenchmarkDependencies(
                load_inputs=lambda _: inputs(),
                build_predictions=lambda *_: statistical_predictions(),
                code_fingerprint=lambda: ("e" * 64, False),
            )
            with mock.patch.object(
                runner,
                "_publish_atomic",
                side_effect=RuntimeError("publish failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "publish failed"):
                    run_benchmark(config, dependencies=dependencies)

            self.assertEqual(len(UnifiedBenchmarkCacheStore(root / "cache.db").status()), 0)

    def test_evaluation_failure_and_invalid_flag_combination_do_not_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = BenchmarkConfig(
                database=root / "research.db",
                cache_database=root / "cache.db",
                folds=2,
                horizons=(5,),
                minimum_group_samples=1,
                output_directory=root / "reports",
            )
            dependencies = BenchmarkDependencies(
                load_inputs=lambda _: inputs(),
                build_predictions=lambda *_: statistical_predictions(),
                code_fingerprint=lambda: ("e" * 64, False),
            )
            with mock.patch.object(
                runner,
                "_evaluate_aligned",
                side_effect=RuntimeError("evaluation failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "evaluation failed"):
                    run_benchmark(config, dependencies=dependencies)

            self.assertEqual(
                len(UnifiedBenchmarkCacheStore(root / "cache.db").status()),
                0,
            )
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            _validate_config(
                BenchmarkConfig(
                    database=Path("research.db"),
                    cache_enabled=False,
                    rebuild_cache=True,
                )
            )


def statistical_checksum(database):
    with sqlite3.connect(database) as connection:
        payload = connection.execute(
            """
            SELECT payload
            FROM benchmark_cache_artifacts
            WHERE stage = 'statistical_predictions'
            """
        ).fetchone()[0]
    import hashlib

    return hashlib.sha256(payload).hexdigest()


if __name__ == "__main__":
    unittest.main()
