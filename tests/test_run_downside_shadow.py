from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from research.run_downside_shadow import (
    ShadowConfig,
    ShadowDependencies,
    ShadowInputSnapshot,
    capture_latest,
    freeze_experiment,
    main,
)


class RunDownsideShadowTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.config = ShadowConfig(
            research_database=root / "prices.db",
            shadow_database=root / "shadow.db",
            model_artifact=root / "model.json",
            max_tickers=2,
            minimum_history=8,
            horizons=(5, 10, 20),
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_freeze_persists_fixed_cohort_models_and_fingerprints(self):
        dependencies = _dependencies(_snapshot("2026-07-24"))

        result = freeze_experiment(self.config, dependencies=dependencies)

        self.assertEqual(result["experiment_id"], "downside-shadow-v1")
        self.assertEqual(result["online_authority"], "none")
        self.assertEqual(result["frozen_market_asof"], "2026-07-24")
        self.assertEqual(result["ticker_count"], 2)
        self.assertEqual(result["model_count"], 8)
        self.assertRegex(result["model_artifact_checksum"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["database_fingerprint"], r"^[0-9a-f]{64}$")

    def test_capture_records_only_latest_new_session_and_retry_is_idempotent(self):
        freeze_experiment(
            self.config,
            dependencies=_dependencies(_snapshot("2026-07-24")),
        )
        current = _snapshot("2026-07-29")
        dependencies = _dependencies(current)

        first = capture_latest(self.config, dependencies=dependencies)
        later_dependencies = replace(
            dependencies,
            now=lambda: datetime(
                2026, 7, 30, 1, 0, tzinfo=timezone.utc
            ),
        )
        second = capture_latest(
            self.config,
            dependencies=later_dependencies,
        )

        self.assertEqual(first["captured_observation_dates"], ["2026-07-29"])
        self.assertEqual(first["inserted_predictions"], 22)
        self.assertEqual(second["inserted_predictions"], 0)
        self.assertEqual(second["captured_observation_dates"], ["2026-07-29"])

    def test_capture_never_replays_missed_sessions(self):
        freeze_experiment(
            self.config,
            dependencies=_dependencies(_snapshot("2026-07-24")),
        )

        result = capture_latest(
            self.config,
            dependencies=_dependencies(_snapshot("2026-07-29")),
        )

        self.assertEqual(result["captured_observation_dates"], ["2026-07-29"])
        self.assertNotIn("2026-07-25", result["captured_observation_dates"])
        self.assertNotIn("2026-07-28", result["captured_observation_dates"])

    def test_capture_marks_missing_stock_unavailable_and_pressure_not_applicable(self):
        freeze_experiment(
            self.config,
            dependencies=_dependencies(_snapshot("2026-07-24")),
        )
        current = _snapshot("2026-07-29", regime="normal")
        histories = dict(current.histories)
        histories["BBB"] = histories["BBB"].iloc[:-1]
        current = replace(current, histories=histories)

        result = capture_latest(
            self.config,
            dependencies=_dependencies(current),
        )

        self.assertGreater(result["unavailable_predictions"], 0)
        self.assertEqual(result["not_applicable_predictions"], 2)
        self.assertLess(result["coverage"], 1.0)

    def test_cli_failure_is_stable_and_does_not_echo_paths_or_secrets(self):
        output = StringIO()
        secret_path = Path(self.temporary.name) / "secret-token-value.db"
        with redirect_stdout(output):
            exit_code = main(
                [
                    "capture",
                    "--research-database",
                    str(secret_path),
                    "--shadow-database",
                    str(secret_path),
                    "--model-artifact",
                    str(secret_path),
                ]
            )

        self.assertEqual(exit_code, 1)
        payload = output.getvalue()
        self.assertIn('"error_code": "shadow_command_failed"', payload)
        self.assertNotIn("secret-token-value", payload)


def _dependencies(snapshot):
    return ShadowDependencies(
        load_inputs=lambda _config: snapshot,
        now=lambda: datetime(2026, 7, 29, 22, 0, tzinfo=timezone.utc),
        code_commit=lambda: "abc123",
        ridge_features=("x1", "x2"),
        direction_features=("x1", "x2"),
        pressure_features=("x1", "x2"),
    )


def _snapshot(latest_date, regime="under_pressure"):
    dates = pd.bdate_range("2026-06-01", latest_date)
    tickers = ("AAA", "BBB")
    index = pd.MultiIndex.from_product(
        (tickers, dates),
        names=("ticker", "observation_date"),
    )
    sequence = np.arange(len(index), dtype=float)
    frame = pd.DataFrame(
        {
            "x1": np.sin(sequence / 2.0),
            "x2": np.cos(sequence / 3.0),
        },
        index=index,
    )
    for horizon, band in ((5, 0.01), (10, 0.015), (20, 0.02)):
        pattern = np.resize(
            np.array((-band * 2.0, 0.0, band * 2.0)),
            len(index),
        )
        frame[f"executable_return_{horizon}"] = pattern
        frame[f"executable_label_end_date_{horizon}"] = (
            frame.index.get_level_values("observation_date")
            + pd.offsets.BDay(horizon)
        )
    for horizon in (5, 20):
        event = np.resize(np.array((0.0, 1.0)), len(index))
        frame[f"downside_event_{horizon}"] = event
        frame[f"downside_label_end_date_{horizon}"] = (
            frame.index.get_level_values("observation_date")
            + pd.offsets.BDay(horizon)
        )

    histories = {
        ticker: _history(dates, offset)
        for ticker, offset in (
            ("AAA", 0.0),
            ("BBB", 5.0),
            ("QQQ", 10.0),
            ("SPY", 20.0),
        )
    }
    assignments = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB"],
            "group_key": ["semiconductor", "software"],
            "effective_from": ["2020-01-01", "2020-01-01"],
            "effective_to": [None, None],
        }
    )
    regimes = pd.DataFrame(
        {
            "observation_date": dates,
            "regime": [regime] * len(dates),
        }
    )
    rule_frame = pd.DataFrame(
        {
            "individual_risk_score": [42.0] * len(index),
            "signal_memory_12": [True] * len(index),
        },
        index=index,
    )
    return ShadowInputSnapshot(
        feature_frame=frame,
        histories=histories,
        assignments=assignments,
        regimes=regimes,
        analysis_tickers=tickers,
        reference_tickers=("QQQ", "SPY"),
        rule_frame=rule_frame,
    )


def _history(dates, offset):
    values = 100.0 + offset + np.arange(len(dates), dtype=float)
    return pd.DataFrame(
        {
            "Open": values,
            "High": values + 2.0,
            "Low": values - 2.0,
            "Close": values + 1.0,
            "Volume": 1_000_000.0 + np.arange(len(dates)),
        },
        index=dates,
    )


if __name__ == "__main__":
    unittest.main()
