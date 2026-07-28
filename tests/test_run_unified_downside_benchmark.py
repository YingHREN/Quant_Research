import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import pandas as pd

import research.run_unified_downside_benchmark as benchmark_runner
from research.run_unified_downside_benchmark import (
    BenchmarkArtifacts,
    BenchmarkConfig,
    BenchmarkDependencies,
    BenchmarkInputs,
    _load_assignments,
    _attach_direction_targets,
    _prediction_frame_from_direction,
    _ridge_predictions,
    _rule_prediction_frames,
    render_markdown,
    run_benchmark,
)


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


def regimes():
    return pd.DataFrame(
        {
            "observation_date": pd.bdate_range("2026-01-02", periods=8),
            "regime": "correction",
        }
    )


def prediction_bundle():
    dates = pd.bdate_range("2026-01-02", periods=2)
    ridge = pd.DataFrame(
        {
            "ticker": ["AAA", "AAA"],
            "observation_date": dates,
            "horizon": [5, 5],
            "fold": [1, 1],
            "predicted_event": [True, False],
            "predicted_score": [-0.08, 0.03],
            "model_version": ["ridge_direction_v1"] * 2,
        }
    )
    risk = ridge.assign(
        predicted_event=[True, True],
        predicted_score=pd.NA,
        model_version="toprisk_v1",
    )
    return {"ridge_down": ridge, "toprisk_stateful": risk}


class RunUnifiedDownsideBenchmarkTest(unittest.TestCase):
    def test_target_adapter_builds_specialist_regime_indicators(self):
        dates = pd.bdate_range("2026-01-02", periods=8)
        history = prices().set_index("observation_date")
        index = pd.MultiIndex.from_product(
            [["AAA"], dates],
            names=["ticker", "observation_date"],
        )
        inputs = BenchmarkInputs(
            prices=prices(),
            assignments=assignments(),
            regimes=pd.DataFrame(
                {
                    "observation_date": dates,
                    "regime": [
                        "correction",
                        "acute_selloff",
                        "uptrend",
                        "uptrend",
                        "range_bound",
                        "under_pressure",
                        "correction",
                        "uptrend",
                    ],
                }
            ),
            histories={"AAA": history},
            feature_frame=pd.DataFrame({"feature": 1.0}, index=index),
            analysis_tickers=("AAA",),
        )
        config = BenchmarkConfig(
            database=Path("research.db"),
            horizons=(5,),
            minimum_group_samples=1,
        )

        result = _attach_direction_targets(inputs, config)

        self.assertEqual(result["regime_is_correction"].iloc[0], 1.0)
        self.assertEqual(result["regime_is_acute_selloff"].iloc[1], 1.0)
        self.assertEqual(result["regime_is_correction"].iloc[2], 0.0)

    def test_runner_fold_count_means_five_out_of_sample_test_folds(self):
        config = BenchmarkConfig(
            database=Path("research.db"),
            folds=5,
            minimum_group_samples=1,
        )
        with mock.patch(
            "research.market_direction_model.walk_forward_ridge_predictions",
            return_value=pd.DataFrame(),
        ) as runner:
            _ridge_predictions(pd.DataFrame(), 5, config)

        self.assertEqual(runner.call_args.kwargs["n_folds"], 6)

    def test_runner_atomically_publishes_auditable_outputs(self):
        inputs = BenchmarkInputs(
            prices=prices(),
            assignments=assignments(),
            regimes=regimes(),
        )
        dependencies = BenchmarkDependencies(
            load_inputs=lambda _: inputs,
            build_predictions=lambda loaded, checked: prediction_bundle(),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = BenchmarkConfig(
                database=root / "research.db",
                start_date="2018-01-01",
                max_tickers=2,
                folds=2,
                horizons=(5,),
                minimum_group_samples=1,
                output_directory=root,
            )

            artifacts = run_benchmark(config, dependencies=dependencies)

            self.assertEqual(
                artifacts.manifest["study_version"],
                "unified-downside-walkforward-v2",
            )
            self.assertEqual(artifacts.manifest["online_authority"], "none")
            self.assertEqual(artifacts.manifest["matched_test_key_count"], 2)
            self.assertTrue(all(path.exists() for path in artifacts.output_paths))
            payload = json.loads(artifacts.output_paths[0].read_text())
            self.assertEqual(
                payload["study_version"],
                "unified-downside-walkforward-v2",
            )
            self.assertFalse(list(root.glob("*.tmp")))

    def test_runner_records_nonnegative_stage_timings_and_progress(self):
        inputs = BenchmarkInputs(
            prices=prices(),
            assignments=assignments(),
            regimes=regimes(),
        )
        clock_values = iter(float(value) for value in range(20))
        progress = []
        dependencies = BenchmarkDependencies(
            load_inputs=lambda _: inputs,
            build_predictions=lambda loaded, checked: prediction_bundle(),
            monotonic=lambda: next(clock_values),
            progress=progress.append,
        )
        with tempfile.TemporaryDirectory() as directory:
            config = BenchmarkConfig(
                database=Path(directory) / "research.db",
                folds=2,
                horizons=(5,),
                minimum_group_samples=1,
                output_directory=Path(directory),
            )

            artifacts = run_benchmark(config, dependencies=dependencies)
            published = json.loads(
                artifacts.output_paths[0].read_text()
            )

        timings = artifacts.manifest["stage_timings_seconds"]
        self.assertEqual(
            set(timings),
            {
                "load_inputs",
                "build_statistical_predictions",
                "build_rule_context",
                "label_and_align",
                "evaluate",
                "publish",
                "total",
            },
        )
        self.assertTrue(all(value >= 0.0 for value in timings.values()))
        self.assertGreaterEqual(
            timings["total"],
            max(
                value
                for key, value in timings.items()
                if key != "total"
            ),
        )
        self.assertTrue(
            any("load_inputs" in message for message in progress)
        )
        self.assertEqual(published["stage_timings_seconds"], timings)

    def test_report_states_same_rows_groups_and_blocked_authority(self):
        artifacts = BenchmarkArtifacts(
            manifest={
                "study_version": "unified-downside-walkforward-v2",
                "online_authority": "none",
                "matched_test_key_count": 20,
                "ticker_count": 2,
                "start_date": "2018-01-01",
                "latest_date": "2026-07-27",
                "promotion_gate": {
                    "passed": False,
                    "reasons": ["fold_majority_not_won"],
                },
            },
            metrics=pd.DataFrame(
                [
                    {
                        "scope": "semiconductor",
                        "regime_scope": "all",
                        "horizon": 5,
                        "sample_mode": "non_overlapping",
                        "fold": "all",
                        "specification": "ridge_down",
                        "status": "ok",
                        "sample_count": 10,
                        "precision": 0.4,
                        "recall": 0.5,
                        "specificity": 0.6,
                        "balanced_accuracy": 0.55,
                    },
                    {
                        "scope": "software_cloud",
                        "regime_scope": "all",
                        "horizon": 5,
                        "sample_mode": "non_overlapping",
                        "fold": "all",
                        "specification": "toprisk_stateful",
                        "status": "ok",
                        "sample_count": 10,
                        "precision": 0.5,
                        "recall": 0.6,
                        "specificity": 0.6,
                        "balanced_accuracy": 0.6,
                    },
                ]
            ),
            fold_comparisons=pd.DataFrame(),
            ablations=pd.DataFrame(),
            overlaps=pd.DataFrame(),
            output_paths=(),
        )

        markdown = render_markdown(artifacts)

        self.assertIn("完全相同的测试行", markdown)
        self.assertIn("不具备线上否决权", markdown)
        self.assertIn("半导体", markdown)
        self.assertIn("软件与云服务", markdown)
        self.assertIn("fold_majority_not_won", markdown)

    def test_direction_and_rule_adapters_preserve_exact_keys(self):
        direction = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "observation_date": pd.bdate_range("2026-01-02", periods=2),
                "horizon": [5, 5],
                "fold": [1, 1],
                "predicted_direction": ["down", "up"],
                "predicted_return": [-0.06, 0.04],
            }
        )
        ridge = _prediction_frame_from_direction(
            direction,
            model_version="ridge_direction_v1",
        )
        risk_context = pd.DataFrame(
            {
                "signal_immediate_8": [True, False],
                "signal_memory_12": [True, False],
                "signal_toprisk_confirmed": [False, False],
                "signal_toprisk_stateful": [True, False],
                "individual_risk_score": [35.0, 0.0],
                "high_level_distribution_score": [80.0, 0.0],
            },
            index=pd.MultiIndex.from_arrays(
                [
                    direction["ticker"],
                    direction["observation_date"],
                ],
                names=["ticker", "observation_date"],
            ),
        )

        rules = _rule_prediction_frames(risk_context, direction)

        self.assertEqual(ridge["predicted_event"].tolist(), [True, False])
        self.assertEqual(ridge["predicted_score"].tolist(), [0.06, -0.04])
        self.assertEqual(
            set(rules),
            {
                "immediate_8",
                "memory_12",
                "toprisk_confirmed",
                "toprisk_stateful",
                "ridge_plus_toprisk",
            },
        )
        self.assertEqual(
            rules["ridge_plus_toprisk"]["predicted_event"].tolist(),
            [True, False],
        )
        for frame in rules.values():
            self.assertEqual(len(frame), len(direction))

    def test_assignment_loader_decodes_persisted_half_open_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "research.db"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """
                    CREATE TABLE group_assignments (
                        ticker TEXT,
                        effective_from TEXT,
                        effective_to TEXT,
                        theme_keys_json TEXT,
                        primary_model_group TEXT,
                        classification_state TEXT,
                        source TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO group_assignments VALUES
                    ('AAA', '2025-01-01', '2026-01-10',
                     '["semiconductor"]', 'semiconductor',
                     'classified', 'override')
                    """
                )

            rows = _load_assignments(database)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]["theme_keys"], ("semiconductor",))
        self.assertEqual(
            rows.iloc[0]["effective_to"],
            pd.Timestamp("2026-01-10"),
        )

    def test_real_prediction_adapter_combines_statistical_and_rule_outputs(self):
        dates = pd.bdate_range("2026-01-02", periods=2)
        index = pd.MultiIndex.from_product(
            [["AAA"], dates],
            names=["ticker", "observation_date"],
        )
        feature_frame = pd.DataFrame(
            {"feature": [1.0, 2.0]},
            index=index,
        )
        direction = pd.DataFrame(
            {
                "ticker": ["AAA", "AAA"],
                "observation_date": dates,
                "horizon": [5, 5],
                "fold": [1, 1],
                "predicted_direction": ["down", "up"],
                "predicted_return": [-0.05, 0.03],
            }
        )
        risk = pd.DataFrame(
            {
                "signal_immediate_8": [True, False],
                "signal_memory_12": [True, False],
                "signal_toprisk_confirmed": [False, False],
                "signal_toprisk_stateful": [True, False],
            },
            index=index,
        )
        inputs = BenchmarkInputs(
            prices=prices(),
            assignments=assignments(),
            regimes=regimes(),
            histories={"AAA": prices().set_index("observation_date")},
            feature_frame=feature_frame,
            analysis_tickers=("AAA",),
        )
        config = BenchmarkConfig(
            database=Path("research.db"),
            horizons=(5,),
            minimum_group_samples=1,
        )
        logistic = direction.assign(predicted_return=pd.NA)
        specialist = direction.assign(predicted_return=pd.NA)
        with mock.patch.object(
            benchmark_runner,
            "_attach_direction_targets",
            return_value=feature_frame,
        ), mock.patch.object(
            benchmark_runner,
            "_ridge_predictions",
            return_value=direction,
        ), mock.patch.object(
            benchmark_runner,
            "_general_logistic_predictions",
            return_value=logistic,
        ), mock.patch.object(
            benchmark_runner,
            "_specialist_predictions",
            return_value=specialist,
        ), mock.patch.object(
            benchmark_runner,
            "_build_rule_context",
            return_value=risk,
        ):
            outputs = benchmark_runner._build_real_predictions(inputs, config)

        self.assertEqual(
            set(outputs),
            {
                "ridge_down",
                "general_logistic_down",
                "pressure_downside_logistic_v1",
                "immediate_8",
                "memory_12",
                "toprisk_confirmed",
                "toprisk_stateful",
                "ridge_plus_toprisk",
            },
        )
        self.assertEqual(len(outputs["ridge_down"]), 2)
        self.assertEqual(len(outputs["toprisk_stateful"]), 2)


if __name__ == "__main__":
    unittest.main()
