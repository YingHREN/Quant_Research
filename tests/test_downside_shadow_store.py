from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest

import pandas as pd

from research.downside_shadow_store import (
    DownsideShadowStore,
    ShadowExperiment,
    ShadowOutcome,
    ShadowPrediction,
)


def experiment():
    return ShadowExperiment(
        experiment_id="downside-shadow-v1",
        study_version="unified-downside-walkforward-v2",
        created_at="2026-07-28T08:00:00+00:00",
        frozen_market_asof="2026-07-24",
        universe=("AAA", "BBB"),
        horizons=(5, 10, 20),
        model_artifact_path="reports/downside-shadow-v1-model.json",
        model_artifact_checksum="a" * 64,
        database_fingerprint="b" * 64,
        code_commit="abc1234",
        status="active",
        online_authority="none",
    )


def prediction(date="2026-07-27", ticker="AAA", horizon=5):
    return ShadowPrediction(
        experiment_id="downside-shadow-v1",
        specification="pressure_downside_logistic_v1",
        ticker=ticker,
        observation_date=date,
        horizon=horizon,
        predicted_event=True,
        predicted_score=0.72,
        status="available",
        unavailable_reason=None,
        group_key="semiconductor",
        market_regime="correction",
        model_version="shadow-pressure-logistic-v1",
        risk_rule_version="bearish-turn-risk-rules-v2",
        feature_version="ridge-v4+recency-v1",
        available_at_close=f"{date}T20:00:00+00:00",
        executable_at="next_session_open",
        market_signature="c" * 64,
        recorded_at="2026-07-27T20:05:00+00:00",
    )


def outcome(ticker="AAA", horizon=5):
    return ShadowOutcome(
        experiment_id="downside-shadow-v1",
        specification="pressure_downside_logistic_v1",
        ticker=ticker,
        observation_date="2026-07-27",
        horizon=horizon,
        entry_date="2026-07-28",
        entry_open=101.0,
        label_end_date="2026-08-03",
        terminal_return=-0.04,
        mae=-0.08,
        mfe=0.02,
        actual_event=True,
        matured_at="2026-08-03T20:05:00+00:00",
        market_signature="d" * 64,
    )


class DownsideShadowStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "shadow.db"
        self.store = DownsideShadowStore(self.database)

    def tearDown(self):
        self.temporary.cleanup()

    def test_experiment_is_idempotent_but_conflict_is_rejected(self):
        self.assertTrue(self.store.create_experiment(experiment()))
        self.assertFalse(self.store.create_experiment(experiment()))

        with self.assertRaisesRegex(ValueError, "conflict"):
            self.store.create_experiment(
                replace(experiment(), frozen_market_asof="2026-07-25")
            )

        loaded = self.store.load_experiment("downside-shadow-v1")
        self.assertEqual(loaded, experiment())

    def test_prediction_rejects_freeze_date_and_conflicting_retry(self):
        self.store.create_experiment(experiment())
        with self.assertRaisesRegex(ValueError, "strictly after"):
            self.store.append_predictions(
                "downside-shadow-v1",
                [prediction("2026-07-24")],
            )

        self.assertEqual(
            self.store.append_predictions(
                "downside-shadow-v1",
                [prediction()],
            ),
            1,
        )
        self.assertEqual(
            self.store.append_predictions(
                "downside-shadow-v1",
                [prediction()],
            ),
            0,
        )
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.store.append_predictions(
                "downside-shadow-v1",
                [replace(prediction(), predicted_score=0.31)],
            )

    def test_prediction_batch_is_validated_before_any_rows_are_written(self):
        self.store.create_experiment(experiment())
        with self.assertRaisesRegex(ValueError, "horizon"):
            self.store.append_predictions(
                "downside-shadow-v1",
                [prediction(ticker="AAA"), prediction(ticker="BBB", horizon=7)],
            )

        self.assertTrue(
            self.store.load_predictions("downside-shadow-v1").empty
        )

    def test_outcome_requires_prediction_and_is_append_only(self):
        self.store.create_experiment(experiment())
        with self.assertRaisesRegex(ValueError, "prediction"):
            self.store.append_outcomes(
                "downside-shadow-v1",
                [outcome()],
            )
        self.store.append_predictions(
            "downside-shadow-v1",
            [prediction()],
        )

        self.assertEqual(
            self.store.append_outcomes(
                "downside-shadow-v1",
                [outcome()],
            ),
            1,
        )
        self.assertEqual(
            self.store.append_outcomes(
                "downside-shadow-v1",
                [outcome()],
            ),
            0,
        )
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.store.append_outcomes(
                "downside-shadow-v1",
                [replace(outcome(), mae=-0.11)],
            )

    def test_unavailable_prediction_has_no_fabricated_event_or_score(self):
        self.store.create_experiment(experiment())
        unavailable = replace(
            prediction(),
            predicted_event=None,
            predicted_score=None,
            status="unavailable",
            unavailable_reason="missing_current_bar",
        )

        self.store.append_predictions(
            "downside-shadow-v1",
            [unavailable],
        )
        rows = self.store.load_predictions("downside-shadow-v1")

        self.assertEqual(rows.iloc[0]["status"], "unavailable")
        self.assertTrue(pd.isna(rows.iloc[0]["predicted_event"]))
        self.assertTrue(pd.isna(rows.iloc[0]["predicted_score"]))

    def test_sqlite_failure_rolls_back_entire_prediction_batch(self):
        self.store.create_experiment(experiment())
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_bbb
                BEFORE INSERT ON shadow_predictions
                WHEN NEW.ticker = 'BBB'
                BEGIN
                    SELECT RAISE(ABORT, 'rejected');
                END
                """
            )

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            self.store.append_predictions(
                "downside-shadow-v1",
                [prediction(ticker="AAA"), prediction(ticker="BBB")],
            )

        self.assertTrue(
            self.store.load_predictions("downside-shadow-v1").empty
        )

    def test_reads_reject_tampered_payload_checksums(self):
        self.store.create_experiment(experiment())
        self.store.append_predictions(
            "downside-shadow-v1",
            [prediction()],
        )
        with sqlite3.connect(self.database) as connection:
            connection.execute(
                """
                UPDATE shadow_predictions
                SET payload_json = '{"ticker":"TAMPERED"}'
                """
            )

        with self.assertRaisesRegex(RuntimeError, "checksum"):
            self.store.load_predictions("downside-shadow-v1")


if __name__ == "__main__":
    unittest.main()
