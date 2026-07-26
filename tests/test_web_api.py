from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from web.app import (
    _attach_forecast_target_dates,
    _attach_model_outputs,
    _structure_payload,
    create_app,
)
from web.services.entry_signals import EntrySignalService
from web.factors.registry import FactorRegistry
from web.forecasts.base import ForecastEvaluation, ForecastResult, UnavailableReason
from web.forecasts.dataset import build_feature_frame
from web.services.forecasts import ForecastRevisionChanged, ForecastService
from web.services.forecast_artifacts import ForecastArtifactStore
from web.services.forecast_warmup import ForecastCacheWarmer
from web.services.market_data import (
    InvalidTicker,
    MarketDataUnavailable,
    UnknownTicker,
)
from web.services.update_jobs import UpdateAlreadyRunning, UpdateJobManager


def price_history(periods=260, end="2026-07-21", offset=0.0):
    index = pd.bdate_range(end=end, periods=periods)
    close = np.linspace(100 + offset, 140 + offset, periods)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.linspace(1_000_000, 1_200_000, periods),
        },
        index=index,
    )


@dataclass(frozen=True)
class FakeSnapshot:
    state: str = "idle"

    def to_dict(self):
        return {
            "state": self.state,
            "started_at": None,
            "finished_at": None,
            "total": 0,
            "completed": 0,
            "updated": 0,
            "current_ticker": None,
            "error": None,
            "resumable": False,
            "cache_warmup_state": "idle",
            "cache_warmup_error": None,
            "cache_warmup_started_at": None,
            "cache_warmup_finished_at": None,
            "cache_warmup_cohorts": [],
        }


class FakeRepository:
    def __init__(self, failure=None, include_benchmark=True):
        self.failure = failure
        self.calls = []
        self.histories = {
            "AAA": price_history(offset=0),
            "BBB": price_history(offset=10),
        }
        if include_benchmark:
            self.histories["SPY"] = price_history(offset=20)

    def freshness(self):
        self.calls.append(("freshness",))
        if self.failure is not None:
            raise self.failure
        return {
            "latest_date": "2026-07-21",
            "by_date": [{"date": "2026-07-21", "tickers": len(self.histories)}],
        }

    def list_summaries(self):
        self.calls.append(("list_summaries",))
        if self.failure is not None:
            raise self.failure
        return [
            SimpleNamespace(
                ticker=ticker,
                latest_date=history.index[-1].date().isoformat(),
                lag_days=0,
                inactive=False,
            )
            for ticker, history in sorted(self.histories.items())
        ]

    def load_history(self, ticker, asof=None):
        self.calls.append(("load_history", ticker, asof))
        if self.failure is not None:
            raise self.failure
        if not isinstance(ticker, str) or not ticker or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for character in ticker
        ):
            raise InvalidTicker("unsafe detail /Users/alice/env.sh")
        try:
            history = self.histories[ticker]
        except KeyError as error:
            raise UnknownTicker("unsafe detail /Users/alice/prices.db") from error
        if asof is None:
            return history.copy()
        return history.loc[history.index <= pd.Timestamp(asof)].copy()

    def load_universe_histories(self, asof=None):
        self.calls.append(("load_universe_histories", asof))
        if self.failure is not None:
            raise self.failure
        return {
            ticker: (
                history.copy()
                if asof is None
                else history.loc[history.index <= pd.Timestamp(asof)].copy()
            )
            for ticker, history in self.histories.items()
        }

    def load_analysis_snapshot(self, ticker):
        self.calls.append(("load_analysis_snapshot", ticker))
        if self.failure is not None:
            raise self.failure
        if not isinstance(ticker, str) or not ticker or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
            for character in ticker
        ):
            raise InvalidTicker("unsafe detail /Users/alice/env.sh")
        if ticker not in self.histories:
            raise UnknownTicker("unsafe detail /Users/alice/prices.db")
        observation = self.histories[ticker].index[-1]
        histories = {
            symbol: history.loc[history.index <= observation].copy()
            for symbol, history in self.histories.items()
        }
        summaries = self.list_summaries()
        return SimpleNamespace(
            histories=histories,
            summaries=summaries,
            observation_date=observation.date().isoformat(),
        )


class FalseyRepository(FakeRepository):
    def __bool__(self):
        return False


class FakeResearchClassificationService:
    def build(self, tickers):
        return {
            "status": "available",
            "asof": "2026-07-24",
            "research_universe_count": 1014,
            "sector_counts": {
                "sec": {"technology": 237},
                "market_behavior": {"technology": 154},
            },
            "by_ticker": {
                ticker: {
                    "state": "unclassified",
                    "sec": None,
                    "market_behavior": None,
                }
                for ticker in tickers
            },
        }


class StaleSelectionRepository(FakeRepository):
    def __init__(self):
        super().__init__()
        self.histories = {
            "OLD": price_history(end="2026-07-15", offset=0),
            **{
                f"P{number}": price_history(
                    end="2026-07-21", offset=number * 10
                )
                for number in range(1, 6)
            },
            "SPY": price_history(end="2026-07-21", offset=70),
        }

    def list_summaries(self):
        summaries = super().list_summaries()
        return [
            SimpleNamespace(
                ticker=summary.ticker,
                latest_date=summary.latest_date,
                lag_days=6 if summary.ticker == "OLD" else 0,
                inactive=summary.ticker == "OLD",
            )
            for summary in summaries
        ]


class MappedFactor:
    def __init__(self, key, group, direction, values):
        self.key = key
        self.label = key.replace("_", " ").title()
        self.group = group
        self.direction = direction
        self.description = "API cohort fixture"
        self.methodology = "Use the exact-date API fixture value."
        self.overview = True
        self.version = "test-v1"
        self.values = values

    def compute(self, context):
        return self.values.get(context.ticker)

    def format(self, value):
        return str(value)


class CountingMappedFactor(MappedFactor):
    def __init__(self, key, group, direction, values):
        super().__init__(key, group, direction, values)
        self.calls = []

    def compute(self, context):
        self.calls.append(context.ticker)
        return super().compute(context)


class UniverseCohortRepository(FakeRepository):
    def __init__(self):
        super().__init__()
        current = ("AAA", "BBB", "CCC", "DDD", "EEE", "SPY")
        self.histories = {
            ticker: price_history(offset=number * 10)
            for number, ticker in enumerate(current)
        }
        self.histories["OLD"] = price_history(end="2026-06-01", offset=70)
        self.histories["STALE"] = price_history(end="2026-07-20", offset=80)

    def list_summaries(self):
        self.calls.append(("list_summaries",))
        return [
            SimpleNamespace(
                ticker=ticker,
                latest_date=history.index[-1].date().isoformat(),
                lag_days=50 if ticker == "OLD" else 1 if ticker == "STALE" else 0,
                inactive=ticker == "OLD",
            )
            for ticker, history in sorted(self.histories.items())
        ]

    def freshness(self):
        self.calls.append(("freshness",))
        return {
            "latest_date": "2026-07-21",
            "by_date": [
                {"date": "2026-07-21", "tickers": 6},
                {"date": "2026-07-20", "tickers": 1},
                {"date": "2026-06-01", "tickers": 1},
            ],
        }


def universe_registry():
    current = ("AAA", "BBB", "CCC", "DDD", "EEE", "SPY")
    return FactorRegistry(
        [
            MappedFactor(
                "strict_vcp",
                "structure",
                "neutral",
                {
                    ticker: {"reject_reason": None if ticker == "AAA" else "none"}
                    for ticker in current
                } | {"STALE": {"reject_reason": None}},
            ),
            MappedFactor(
                "tight_platform",
                "structure",
                "neutral",
                {
                    ticker: {"is_platform": ticker == "BBB"}
                    for ticker in current
                } | {"STALE": {"is_platform": False}},
            ),
            MappedFactor(
                "pivot_distance_pct",
                "structure",
                "neutral",
                {ticker: 2.0 if ticker == "CCC" else 12.0 for ticker in current}
                | {"STALE": 3.0},
            ),
            MappedFactor(
                "mom_12_1",
                "momentum",
                "higher",
                {
                    ticker: value
                    for ticker, value in zip(current, (60, 50, 40, 30, 20, 10))
                } | {"STALE": 35},
            ),
            MappedFactor(
                "realized_vol_63",
                "risk",
                "lower",
                {
                    ticker: value
                    for ticker, value in zip(
                        current, (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
                    )
                } | {"STALE": 0.25},
            ),
        ]
    )


class ExactDatePeerRepository(FakeRepository):
    def __init__(self):
        super().__init__(include_benchmark=False)
        self.histories = {
            ticker: price_history(end="2026-07-21", offset=position * 10)
            for position, ticker in enumerate(("AAA", "BBB", "CCC", "DDD"))
        }
        self.histories["OLDER"] = price_history(end="2026-07-20", offset=50)


class FakeManager:
    def __init__(self, start_error=None):
        self.start_error = start_error
        self.start_calls = 0
        self.snapshot_calls = 0

    def start(self):
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error
        return FakeSnapshot("running")

    def snapshot(self):
        self.snapshot_calls += 1
        return FakeSnapshot("idle")


class StatefulFakeManager(FakeManager):
    def __init__(self, state="idle"):
        super().__init__()
        self.state = state

    def snapshot(self):
        self.snapshot_calls += 1
        return FakeSnapshot(self.state)


class FakeForecastProvider:
    model_key = "fake_direction"
    model_version = "test-v2"

    def __init__(self):
        self.calls = []

    def forecast_series(self, ticker, dates, horizons):
        dates = tuple(pd.Timestamp(value).normalize() for value in dates)
        horizons = tuple(horizons)
        self.calls.append((ticker, dates, horizons))
        results = []
        for asof in dates:
            for horizon in horizons:
                if asof.day % 2:
                    results.append(
                        ForecastResult(
                            ticker=ticker,
                            asof_date=asof,
                            horizon_sessions=horizon,
                            direction="unavailable",
                            predicted_return=None,
                            up_probability=None,
                            confidence_status="unavailable",
                            confidence_reason=None,
                            training_sample_count=0,
                            training_cutoff=None,
                            model_key=self.model_key,
                            model_version=self.model_version,
                            unavailable_reason=UnavailableReason.INSUFFICIENT_HISTORY,
                        )
                    )
                    continue
                results.append(
                    ForecastResult(
                        ticker=ticker,
                        asof_date=asof,
                        horizon_sessions=horizon,
                        direction="up",
                        predicted_return=horizon / 1000,
                        up_probability=None,
                        confidence_status="uncalibrated",
                        confidence_reason="insufficient_calibration_samples",
                        training_sample_count=40,
                        training_cutoff=asof - pd.offsets.BDay(1),
                        model_key=self.model_key,
                        model_version=self.model_version,
                    )
                )
        return results


class FakeForecastFactory:
    model_key = FakeForecastProvider.model_key
    model_version = FakeForecastProvider.model_version

    def __init__(self, error=None):
        self.error = error
        self.providers = []

    def __call__(self, _frame):
        if self.error is not None:
            raise self.error
        provider = FakeForecastProvider()
        self.providers.append(provider)
        return provider


class FalseyForecastFactory(FakeForecastFactory):
    def __bool__(self):
        return False


def fake_forecast_evaluation(_frame, horizon, provider):
    return ForecastEvaluation(
        horizon_sessions=horizon,
        sample_count=12,
        coverage=0.5,
        mae=0.01,
        rmse=0.02,
        direction_accuracy=0.75,
        zero_return_mae=0.03,
        historical_mean_mae=0.025,
        rank_ic=None,
        signal_bucket_returns={"down": None, "neutral": 0.0, "up": 0.02},
        evaluation_start="2026-06-01",
        evaluation_end="2026-07-01",
        model_key=provider.model_key,
        model_version=provider.model_version,
    )


class InjectedForecastService:
    model_key = "injected"
    model_version = "test-v1"

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def build(self, ticker, chart_dates, histories):
        self.calls.append((ticker, tuple(chart_dates), histories))
        if self.error is not None:
            raise self.error
        return {
            "forecasts": {
                "model": {
                    "key": self.model_key,
                    "version": self.model_version,
                    "status": "available",
                    "unavailable_reason": None,
                },
                "horizons": [5, 20, 60],
                "by_date": {},
            },
            "forecast_evaluation": {},
        }


class RevisionAwareInjectedForecastService(InjectedForecastService):
    database_revision = 7

    def build(self, ticker, chart_dates, histories, *, expected_revision=None):
        payload = super().build(ticker, chart_dates, histories)
        self.calls[-1] = (*self.calls[-1], expected_revision)
        return payload


class InjectedEntrySignalService:
    def __init__(self, active_latest=False):
        self.active_latest = active_latest
        self.calls = []

    def build(self, ticker, history):
        self.calls.append((ticker, history.copy()))
        rows = []
        for position, timestamp in enumerate(history.index):
            active = self.active_latest and position == len(history) - 1
            rows.append(
                {
                    "time": pd.Timestamp(timestamp).date().isoformat(),
                    "strict_vcp_active": active,
                    "strict_vcp_start": active,
                    "strict_vcp_stage": "near_pivot" if active else "none",
                    "strict_vcp_pivot": 141.5 if active else None,
                    "strict_vcp_pivot_date": (
                        pd.Timestamp(timestamp).date().isoformat()
                        if active
                        else None
                    ),
                    "strict_vcp_reject_reason": (
                        None if active else "insufficient_swings"
                    ),
                    "strict_vcp_evidence": {
                        "accepted": active,
                        "reject_reason": (
                            None if active else "insufficient_swings"
                        ),
                        "vcp_pivot": 141.5 if active else None,
                    },
                    "tight_platform_active": False,
                    "tight_platform_start": False,
                    "tight_platform_pivot": None,
                    "tight_platform_reject_reason": "not_sideways",
                    "tight_platform_evidence": {
                        "active": False,
                        "reject_reason": "not_sideways",
                    },
                    "vcp_breakout_confirmed": False,
                    "vcp_breakout_price_confirmed": False,
                    "vcp_breakout_volume_confirmed": False,
                    "vcp_breakout_buy_zone_confirmed": False,
                    "vcp_breakout_pivot": None,
                    "vcp_breakout_volume_ratio": None,
                    "vcp_breakout_pct_over_pivot": None,
                    "vcp_breakout_reject_reason": "no_prior_vcp_pivot",
                    "pocket_pivot": False,
                    "pocket_pivot_current_volume": None,
                    "pocket_pivot_prior_down_volume": None,
                    "pocket_pivot_down_day_count": 0,
                    "pocket_pivot_reject_reason": "insufficient_history",
                    "pocket_pivot_evidence": {
                        "available": False,
                        "active": False,
                        "reject_reason": "insufficient_history",
                    },
                }
            )
        return rows


class FakeRelativeStrengthService:
    def build(self, tickers):
        return {
            "status": "available",
            "asof": "2026-07-21",
            "sample_count": 1000,
            "model_version": "cross_sectional_rs_v1",
            "by_ticker": {
                ticker: {
                    "rs_rating": 91 if ticker == "AAA" else 60,
                    "rs_asof": "2026-07-21",
                    "rs_sample_count": 1000,
                    "rs_model_version": "cross_sectional_rs_v1",
                }
                for ticker in tickers
            },
        }


def test_config(**overrides):
    config = {
        "TESTING": True,
        "FORECAST_SERVICE": InjectedForecastService(),
        "ENTRY_SIGNAL_SERVICE": InjectedEntrySignalService(),
        "RESEARCH_CLASSIFICATION_SERVICE": FakeResearchClassificationService(),
        "RESEARCH_RELATIVE_STRENGTH_SERVICE": FakeRelativeStrengthService(),
    }
    config.update(overrides)
    return config


class WebApiTest(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()
        self.manager = FakeManager()
        self.app = create_app(
            test_config(), self.repository, self.manager
        )
        self.client = self.app.test_client()

    def test_universe_schema_and_repository_calls(self):
        response = self.client.get("/api/universe")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json),
            {
                "asof",
                "freshness",
                "tickers",
                "factor_groups",
                "classification_summary",
                "relative_strength_summary",
            },
        )
        self.assertEqual(response.json["asof"], "2026-07-21")
        self.assertTrue(response.json["factor_groups"])
        self.assertEqual(
            set(response.json["factor_groups"][0]),
            {"key", "label", "methodology", "overview", "i18n"},
        )
        self.assertEqual(
            set(response.json["factor_groups"][0]["i18n"]["zh-CN"]),
            {"label", "description", "methodology", "window", "direction"},
        )
        self.assertEqual(
            set(response.json["tickers"][0]),
            {
                "ticker",
                "latest_date",
                "lag_days",
                "inactive",
                "stale",
                "data_status",
                "fresh",
                "strict_vcp",
                "tight_platform",
                "near_pivot",
                "shape_state",
                "momentum_percentile",
                "momentum_factor_key",
                "momentum_percentile_unit",
                "volatility",
                "volatility_factor_key",
                "volatility_unit",
                "sector_classification",
                "rs_rating",
                "rs_asof",
                "rs_sample_count",
                "rs_model_version",
            },
        )
        self.assertEqual(
            response.json["relative_strength_summary"]["model_version"],
            "cross_sectional_rs_v1",
        )
        self.assertEqual(
            response.json["classification_summary"]["research_universe_count"],
            1014,
        )
        self.assertEqual(self.repository.calls.count(("freshness",)), 1)
        self.assertEqual(self.repository.calls.count(("list_summaries",)), 1)
        self.assertEqual(
            self.repository.calls.count(
                ("load_universe_histories", pd.Timestamp("2026-07-21"))
            ),
            1,
        )
        self.assertFalse(
            any(call[0] == "load_history" for call in self.repository.calls)
        )

    def test_cache_status_returns_safe_service_telemetry(self):
        expected = {
            "state": "ready",
            "entry_count": 2,
            "latest_created_at": "2026-07-24T10:00:00+00:00",
            "market_asof": "2026-07-24",
            "model_key": "ridge_direction_v1",
            "model_version": "v4",
            "feature_version": "ridge-features-v1",
            "risk_context_version": "forecast-risk-context-v1",
            "format_version": "forecast-artifact-v1",
            "size_bytes": 1234,
            "last_access": "disk_hit",
            "database_revision": 7,
            "memory_ready": True,
            "build_started_at": None,
            "build_finished_at": "2026-07-24T10:00:01+00:00",
        }

        class StatusService(RevisionAwareInjectedForecastService):
            def cache_status(self):
                return dict(expected)

        app = create_app(
            test_config(FORECAST_SERVICE=StatusService()),
            self.repository,
            self.manager,
        )

        response = app.test_client().get("/api/cache/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, expected)

    def test_cache_status_degrades_when_injected_service_has_no_status(self):
        response = self.client.get("/api/cache/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["state"], "unavailable")
        self.assertEqual(response.json["last_access"], "unavailable")
        self.assertFalse(response.json["memory_ready"])

    def test_stock_merges_injected_entry_rows_by_date(self):
        service = InjectedEntrySignalService(active_latest=True)
        app = create_app(
            test_config(ENTRY_SIGNAL_SERVICE=service),
            self.repository,
            self.manager,
        )

        response = app.test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0][0], "AAA")
        self.assertTrue(response.json["chart"][-1]["strict_vcp_active"])
        self.assertEqual(
            response.json["chart"][-1]["strict_vcp_pivot"],
            141.5,
        )
        self.assertIs(
            app.extensions["dashboard_entry_signal_service"],
            service,
        )

    def test_universe_never_calls_entry_signal_service(self):
        service = InjectedEntrySignalService()
        app = create_app(
            test_config(ENTRY_SIGNAL_SERVICE=service),
            self.repository,
            self.manager,
        )

        response = app.test_client().get("/api/universe")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.calls, [])

    def test_default_forecast_artifact_store_respects_factory_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            explicit_path = Path(temporary) / "explicit-cache.db"
            testing_app = create_app(
                {
                    "TESTING": True,
                    "MARKET_DATA_DATABASE": "unused.db",
                },
                FakeRepository(),
                FakeManager(),
            )
            explicit_app = create_app(
                {
                    "TESTING": True,
                    "MARKET_DATA_DATABASE": "unused.db",
                    "FORECAST_ARTIFACT_CACHE_PATH": explicit_path,
                },
                FakeRepository(),
                FakeManager(),
            )
            disabled_app = create_app(
                {
                    "TESTING": False,
                    "MARKET_DATA_DATABASE": "unused.db",
                    "FORECAST_ARTIFACT_CACHE_ENABLED": False,
                },
                FakeRepository(),
                FakeManager(),
            )

        testing_service = testing_app.extensions["dashboard_forecast_service"]
        explicit_service = explicit_app.extensions["dashboard_forecast_service"]
        disabled_service = disabled_app.extensions["dashboard_forecast_service"]
        self.assertIsNone(testing_service._artifact_store)
        self.assertEqual(explicit_service._artifact_store.path, explicit_path)
        self.assertIsNone(disabled_service._artifact_store)

    def test_universe_never_computes_heavy_structures(self):
        class RaisingStructuralFactor(MappedFactor):
            def compute(self, context):
                raise AssertionError(f"heavy structure ran for {context.ticker}")

        current = ("AAA", "BBB", "CCC", "DDD", "EEE", "SPY")
        registry = FactorRegistry(
            [
                RaisingStructuralFactor(
                    "strict_vcp", "structure", "neutral", {}
                ),
                RaisingStructuralFactor(
                    "tight_platform", "structure", "neutral", {}
                ),
                RaisingStructuralFactor(
                    "pivot_distance_pct", "structure", "neutral", {}
                ),
                MappedFactor(
                    "mom_12_1",
                    "momentum",
                    "higher",
                    {ticker: value for ticker, value in zip(current, range(6))},
                ),
                MappedFactor(
                    "realized_vol_63",
                    "risk",
                    "lower",
                    {ticker: 0.1 + index / 100 for index, ticker in enumerate(current)},
                ),
            ]
        )
        app = create_app(
            test_config(FACTOR_REGISTRY=registry),
            UniverseCohortRepository(),
            FakeManager(),
        )

        response = app.test_client().get("/api/universe")

        self.assertEqual(response.status_code, 200)
        current_row = next(
            row for row in response.json["tickers"] if row["ticker"] == "AAA"
        )
        self.assertIsNotNone(current_row["momentum_percentile"])
        self.assertIsNotNone(current_row["volatility"])
        self.assertIsNone(current_row["strict_vcp"])
        self.assertIsNone(current_row["tight_platform"])
        self.assertIsNone(current_row["near_pivot"])
        self.assertEqual(current_row["shape_state"], "unavailable")

    def test_universe_diagnostics_feed_real_filter_and_sort_pipeline(self):
        repository = UniverseCohortRepository()
        app = create_app(
            test_config(FACTOR_REGISTRY=universe_registry()),
            repository,
            FakeManager(),
        )

        response = app.test_client().get("/api/universe")

        self.assertEqual(response.status_code, 200)
        by_ticker = {row["ticker"]: row for row in response.json["tickers"]}
        self.assertTrue(
            all(row["shape_state"] == "unavailable" for row in by_ticker.values())
        )
        self.assertTrue(
            all(row["strict_vcp"] is None for row in by_ticker.values())
        )
        self.assertTrue(
            all(row["tight_platform"] is None for row in by_ticker.values())
        )
        self.assertTrue(
            all(row["near_pivot"] is None for row in by_ticker.values())
        )
        self.assertTrue(by_ticker["OLD"]["inactive"])
        self.assertFalse(by_ticker["OLD"]["stale"])
        self.assertEqual(by_ticker["OLD"]["data_status"], "inactive")
        self.assertEqual(by_ticker["AAA"]["momentum_percentile"], 100.0)
        self.assertEqual(by_ticker["AAA"]["momentum_factor_key"], "mom_12_1")
        self.assertEqual(
            by_ticker["AAA"]["momentum_percentile_unit"], "percentile_0_100"
        )
        self.assertEqual(by_ticker["AAA"]["volatility"], 10.0)
        self.assertEqual(
            by_ticker["AAA"]["volatility_unit"], "annualized_percent"
        )
        self.assertIsNone(by_ticker["OLD"]["momentum_percentile"])
        self.assertIsNone(by_ticker["OLD"]["volatility"])
        self.assertTrue(by_ticker["STALE"]["stale"])
        self.assertFalse(by_ticker["STALE"]["inactive"])
        self.assertEqual(by_ticker["STALE"]["data_status"], "stale")
        self.assertEqual(by_ticker["STALE"]["shape_state"], "unavailable")
        self.assertEqual(by_ticker["STALE"]["volatility"], 25.0)

        module_uri = (
            Path(__file__).resolve().parents[1] / "web/static/js/universe.js"
        ).as_uri()
        script = f"""
            import {{ filterTickers, sortTickers }} from {json.dumps(module_uri)};
            const rows = {json.dumps(response.json["tickers"])};
            console.log(JSON.stringify({{
              strict: filterTickers(rows, '', {{strictVcp: true}}).map(row => row.ticker),
              tight: filterTickers(rows, '', {{tightPlatform: true}}).map(row => row.ticker),
              near: filterTickers(rows, '', {{nearPivot: true}}).map(row => row.ticker),
              momentum: sortTickers(rows, 'momentum_percentile', 'desc').map(row => row.ticker),
              volatility: sortTickers(rows, 'volatility', 'asc').map(row => row.ticker)
            }}));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        actual = json.loads(result.stdout)
        self.assertEqual(actual["strict"], [])
        self.assertEqual(actual["tight"], [])
        self.assertEqual(actual["near"], [])
        self.assertEqual(actual["momentum"][:3], ["AAA", "BBB", "CCC"])
        self.assertEqual(actual["volatility"][:3], ["AAA", "BBB", "STALE"])

    def test_factory_preserves_falsey_injected_dependencies(self):
        repository = FalseyRepository()
        manager = FakeManager()

        app = create_app(test_config(), repository, manager)
        response = app.test_client().get("/api/universe")

        self.assertEqual(response.status_code, 200)
        self.assertIs(app.extensions["dashboard_repository"], repository)
        self.assertIs(app.extensions["dashboard_update_manager"], manager)

    def test_stock_payload_has_one_consistent_observation_date(self):
        response = self.client.get("/api/stocks/AAA")
        payload = response.json

        self.assertEqual(response.status_code, 200)
        legacy_keys = {
            "ticker",
            "observation_date",
            "summary",
            "chart",
            "structures",
            "factors",
            "scenarios",
            "warnings",
        }
        self.assertEqual(
            set(payload),
            legacy_keys
            | {
                "forecasts",
                "forecast_evaluation",
                "feature_provenance_registry",
                "model_output_registry",
                "top_risk",
            },
        )
        self.assertEqual(
            payload["model_output_registry"]["version"],
            "model_output_registry_v1",
        )
        self.assertEqual(
            payload["feature_provenance_registry"]["version"],
            "feature_provenance_registry_v1",
        )
        for horizons in payload["forecasts"]["by_date"].values():
            for forecast in horizons.values():
                outputs = forecast["model_outputs"]
                self.assertNotIn("registry", outputs)
                self.assertEqual(
                    outputs["registry_ref"],
                    payload["model_output_registry"]["version"],
                )
        self.assertEqual(payload["ticker"], "AAA")
        self.assertEqual(payload["chart"][-1]["time"], payload["observation_date"])
        self.assertEqual(
            {factor["observation_date"] for factor in payload["factors"]},
            {payload["observation_date"]},
        )
        for factor in payload["factors"]:
            self.assertTrue(factor.get("methodology"))
            self.assertIsInstance(factor.get("overview"), bool)
        self.assertEqual(
            payload["scenarios"]["observation_date"], payload["observation_date"]
        )
        self.assertEqual(
            self.repository.calls.count(("load_analysis_snapshot", "AAA")),
            1,
        )
        self.assertFalse(any(call[0] == "load_history" for call in self.repository.calls))
        self.assertFalse(
            any(call[0] == "load_universe_histories" for call in self.repository.calls)
        )
        self.assertEqual(payload["summary"]["daily_return_unit"], "fraction")
        self.assertIn("strict_vcp_pivot", payload["structures"]["key_levels"])
        self.assertIn("tight_platform_pivot", payload["structures"]["key_levels"])
        self.assertIn("annotations", payload["structures"])
        reversal_keys = (
            "prior_high_resistance",
            "prior_high_breakout_pct",
            "prior_high_breakout",
            "descending_trendline",
            "trendline_breakout",
            "trendline_high_1_date",
            "trendline_high_2_date",
            "latest_confirmed_high_date",
            "latest_confirmed_high_confirmed_date",
            "latest_confirmed_low_date",
            "latest_confirmed_low_price",
            "latest_confirmed_low_confirmed_date",
            "higher_low_confirmed",
            "higher_low_previous_date",
            "higher_low_previous_price",
            "higher_low_latest_date",
            "higher_low_latest_price",
            "higher_low_confirmation_date",
            "reversal_signal_count",
            "reversal_candidate",
            "early_reversal_score",
            "early_reversal_watch",
            "early_reversal_conditions",
            "early_prior_session_selloff",
            "early_current_price_acceptance",
            "early_descending_trendline_proximity",
            "early_current_volume_support",
            "near_support_lower",
            "near_support_upper",
            "near_support_mid",
            "near_support_distance_pct",
            "near_support_score",
            "near_support_sources",
            "near_support_state",
        )
        for row in payload["chart"]:
            for key in reversal_keys:
                self.assertIn(key, row)
            self.assertIn(row["reversal_signal_count"], (0, 1, 2, 3))
            self.assertIn(row["early_reversal_score"], (0, 25, 50, 75, 100))
            self.assertIsInstance(row["early_reversal_watch"], bool)
            self.assertIsInstance(row["early_reversal_conditions"], list)
            self.assertIsInstance(row["near_support_sources"], list)
            self.assertIn(
                row["near_support_state"],
                {"above", "testing", "inside", "unavailable"},
            )
            for key in (
                "near_support_lower",
                "near_support_upper",
                "near_support_mid",
                "near_support_distance_pct",
                "near_support_score",
            ):
                self.assertTrue(
                    row[key] is None or isinstance(row[key], (int, float)),
                    key,
                )

    def test_semiconductor_stock_chart_exposes_historical_risk_memory(self):
        repository = FakeRepository()
        repository.histories = {
            "MU": price_history(end="2026-07-23"),
            "QQQ": price_history(end="2026-07-23", offset=5),
            "SOXX": price_history(end="2026-07-23", offset=10),
            "SMH": price_history(end="2026-07-23", offset=15),
            "SPY": price_history(end="2026-07-23", offset=20),
        }
        app = create_app(
            test_config(),
            repository,
            FakeManager(),
        )

        response = app.test_client().get("/api/stocks/MU")

        self.assertEqual(response.status_code, 200)
        latest = response.json["chart"][-1]
        self.assertEqual(
            latest["market_bearish_turn_model_key"],
            "bearish_turn_risk_rules_v2",
        )
        self.assertIn(
            latest["market_bearish_turn_state"],
            {"new", "persistent", "fading", "inactive"},
        )
        self.assertGreaterEqual(
            latest["market_bearish_turn_state_score"],
            latest["market_bearish_turn_raw_score"],
        )
        self.assertEqual(
            latest["market_bearish_turn_memory_half_life_sessions"],
            5,
        )
        self.assertEqual(
            latest["market_bearish_turn_memory_window_sessions"],
            10,
        )

    def test_stock_without_group_keeps_market_risk_memory_unavailable(self):
        response = self.client.get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        latest = response.json["chart"][-1]
        self.assertIsNone(latest["market_bearish_turn_state_score"])
        self.assertEqual(
            latest["market_bearish_turn_state"],
            "unavailable",
        )

    def test_stock_payload_includes_top_risk_summary_and_annotations(self):
        event_date = (
            self.repository.histories["AAA"].index[-3].date().isoformat()
        )
        latest_date = (
            self.repository.histories["AAA"].index[-1].date().isoformat()
        )

        class TimelineForecastService(InjectedForecastService):
            def build_top_risk_timeline(
                self,
                ticker,
                chart_dates,
                histories,
                *,
                expected_revision=None,
            ):
                return {
                    "model_key": "high_level_distribution_risk_v1",
                    "model_version": "v1",
                    "status": "available",
                    "unavailable_reason": None,
                    "latest": {
                        "time": latest_date,
                        "score": 72.0,
                        "raw_score": 72.0,
                        "state": "confirmed",
                        "raw_state": "confirmed",
                        "memory_age_sessions": 0,
                    },
                    "events": [
                        {
                            "time": event_date,
                            "type": "top_risk_confirmed",
                            "score": 72.0,
                            "state": "confirmed",
                        }
                    ],
                }

        app = create_app(
            test_config(FORECAST_SERVICE=TimelineForecastService()),
            self.repository,
            self.manager,
        )

        response = app.test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["top_risk"]["latest"]["state"],
            "confirmed",
        )
        self.assertIn(
            {
                "time": event_date,
                "type": "top_risk_confirmed",
                "label": "Top downside risk confirmed",
                "score": 72.0,
                "state": "confirmed",
            },
            response.json["structures"]["annotations"],
        )

    def test_legacy_forecast_service_keeps_top_risk_unavailable(self):
        response = self.client.get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["top_risk"],
            {
                "model_key": "high_level_distribution_risk_v1",
                "model_version": "v1",
                "status": "unavailable",
                "unavailable_reason": "service_unsupported",
                "latest": None,
                "events": [],
            },
        )

    def test_top_risk_timeline_failure_does_not_fail_stock_endpoint(self):
        class BrokenTimelineForecastService(InjectedForecastService):
            def build_top_risk_timeline(self, *args, **kwargs):
                raise RuntimeError("private timeline failure")

        app = create_app(
            test_config(FORECAST_SERVICE=BrokenTimelineForecastService()),
            self.repository,
            self.manager,
        )

        response = app.test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["top_risk"]["status"], "unavailable")
        self.assertEqual(
            response.json["top_risk"]["unavailable_reason"],
            "model_error",
        )

    def test_top_risk_annotations_filter_unknown_types_and_non_chart_dates(self):
        latest_date = (
            self.repository.histories["AAA"].index[-1].date().isoformat()
        )

        class InvalidEventForecastService(InjectedForecastService):
            def build_top_risk_timeline(self, *args, **kwargs):
                return {
                    "model_key": "high_level_distribution_risk_v1",
                    "model_version": "v1",
                    "status": "available",
                    "unavailable_reason": None,
                    "latest": {
                        "time": latest_date,
                        "score": 45.0,
                        "raw_score": 45.0,
                        "state": "watch",
                        "raw_state": "watch",
                        "memory_age_sessions": 0,
                    },
                    "events": [
                        {
                            "time": latest_date,
                            "type": "unknown_top_risk",
                            "score": 45.0,
                            "state": "watch",
                        },
                        {
                            "time": "1999-01-01",
                            "type": "top_risk_watch",
                            "score": 45.0,
                            "state": "watch",
                        },
                    ],
                }

        app = create_app(
            test_config(FORECAST_SERVICE=InvalidEventForecastService()),
            self.repository,
            self.manager,
        )

        response = app.test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["top_risk"]["status"], "available")
        self.assertFalse(
            any(
                annotation["type"].startswith("top_risk")
                for annotation in response.json["structures"]["annotations"]
            )
        )

    def test_historical_forecast_endpoint_computes_only_requested_date(self):
        requested = self.repository.histories["AAA"].index[-20].date().isoformat()

        response = self.client.get(f"/api/stocks/AAA/forecasts/{requested}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json),
            {
                "forecasts",
                "forecast_evaluation",
                "feature_provenance_registry",
                "model_output_registry",
            },
        )
        self.assertEqual(
            response.json["model_output_registry"]["version"],
            "model_output_registry_v1",
        )
        ticker, dates, histories = self.app.config["FORECAST_SERVICE"].calls[-1]
        self.assertEqual(ticker, "AAA")
        self.assertEqual(dates, (requested,))
        pd.testing.assert_frame_equal(histories["AAA"], self.repository.histories["AAA"])

    def test_forecast_target_dates_include_each_projected_session(self):
        payload = {
            "forecasts": {
                "by_date": {
                    "2026-07-02": {"5": {"predicted_return": 0.02}},
                },
            },
        }

        _attach_forecast_target_dates(
            payload, pd.DatetimeIndex(["2026-07-01", "2026-07-02"])
        )

        forecast = payload["forecasts"]["by_date"]["2026-07-02"]["5"]
        self.assertEqual(
            forecast["projection_dates"],
            ["2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09", "2026-07-10"],
        )
        self.assertEqual(forecast["target_date"], "2026-07-10")

    def test_model_outputs_attach_same_date_chart_evidence(self):
        payload = {
            "forecasts": {
                "by_date": {
                    "2026-07-01": {
                        "5": {
                            "model_key": "ridge_direction_v1",
                            "model_version": "v4",
                            "horizon_sessions": 5,
                            "predicted_return": 0.03,
                            "raw_direction": "up",
                            "direction": "up",
                            "training_sample_count": 100,
                            "training_cutoff": "2026-06-30",
                            "confidence_status": "uncalibrated",
                            "confidence_reason": "insufficient_calibration_samples",
                            "bearish_turn_score": 0,
                            "bearish_turn_conditions": [],
                            "decision": None,
                        }
                    }
                }
            },
            "forecast_evaluation": {"5": {"evidence_status": "unproven"}},
        }
        chart = [
            {
                "time": "2026-07-01",
                "reversal_signal_count": 2,
                "reversal_candidate": True,
                "prior_high_breakout": True,
                "trendline_breakout": True,
                "higher_low_confirmed": False,
                "early_reversal_score": 0,
                "early_reversal_watch": False,
                "early_reversal_conditions": [],
            }
        ]

        _attach_model_outputs(payload, chart)

        outputs = payload["forecasts"]["by_date"]["2026-07-01"]["5"][
            "model_outputs"
        ]
        self.assertEqual(outputs["primary"][0]["evidence_status"], "unproven")
        self.assertEqual(outputs["bullish_structure"][0]["score"], 2)
        self.assertEqual(
            payload["model_output_registry"]["version"],
            "model_output_registry_v1",
        )
        self.assertEqual(
            outputs["registry_ref"],
            "model_output_registry_v1",
        )
        self.assertNotIn("registry", outputs)

    def test_historical_forecast_endpoint_rejects_non_session_date(self):
        response = self.client.get("/api/stocks/AAA/forecasts/2026-07-19")

        self.assertEqual(response.status_code, 404)

    def test_historical_forecast_uses_same_entry_signal_history(self):
        service = InjectedEntrySignalService(active_latest=True)
        app = create_app(
            test_config(ENTRY_SIGNAL_SERVICE=service),
            self.repository,
            self.manager,
        )
        requested = self.repository.histories["AAA"].index[-20].date().isoformat()

        response = app.test_client().get(
            f"/api/stocks/AAA/forecasts/{requested}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0][0], "AAA")
        self.assertEqual(
            len(service.calls[0][1]),
            len(self.repository.histories["AAA"]),
        )

    def test_historical_forecast_rejects_snapshot_from_prior_revision(self):
        class RacingService(InjectedForecastService):
            database_revision = 3

            def build(self, ticker, chart_dates, histories, *, expected_revision=None):
                if expected_revision != self.database_revision:
                    raise ForecastRevisionChanged("revision changed")
                return super().build(ticker, chart_dates, histories)

        service = RacingService()

        class RacingRepository(FakeRepository):
            def load_analysis_snapshot(self, ticker):
                snapshot = super().load_analysis_snapshot(ticker)
                service.database_revision += 1
                return snapshot

        app = create_app(
            {"TESTING": True, "FORECAST_SERVICE": service},
            RacingRepository(),
            FakeManager(),
        )
        requested = price_history().index[-20].date().isoformat()

        response = app.test_client().get(
            f"/api/stocks/AAA/forecasts/{requested}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json["forecasts"]["model"]["unavailable_reason"],
            "update_in_progress",
        )

    def test_injected_forecast_service_receives_existing_snapshot_and_chart_dates(self):
        service = InjectedForecastService()
        app = create_app(
            {"TESTING": True, "FORECAST_SERVICE": service},
            self.repository,
            self.manager,
        )

        response = app.test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertIs(app.extensions["dashboard_forecast_service"], service)
        self.assertEqual(len(service.calls), 1)
        ticker, chart_dates, histories = service.calls[0]
        self.assertEqual(ticker, "AAA")
        self.assertEqual(chart_dates, tuple(row["time"] for row in response.json["chart"]))
        self.assertEqual(set(histories), {"AAA", "BBB", "SPY"})
        self.assertEqual(
            self.repository.calls.count(("load_analysis_snapshot", "AAA")), 1
        )
        self.assertFalse(
            any(call[0] == "load_universe_histories" for call in self.repository.calls)
        )

    def test_stock_binds_repository_snapshot_to_forecast_revision(self):
        service = RevisionAwareInjectedForecastService()
        app = create_app(
            {"TESTING": True, "FORECAST_SERVICE": service},
            self.repository,
            self.manager,
        )

        response = app.test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.calls[0][3], 7)

    def test_stock_suppresses_forecast_if_update_starts_during_snapshot(self):
        manager = StatefulFakeManager()
        repository = FakeRepository()
        service = RevisionAwareInjectedForecastService()
        original_load = repository.load_analysis_snapshot

        def load_committed_partial_snapshot(ticker):
            close_column = repository.histories["AAA"].columns.get_loc("Close")
            repository.histories["AAA"].iloc[-1, close_column] += 0.25
            snapshot = original_load(ticker)
            manager.state = "running"
            return snapshot

        repository.load_analysis_snapshot = load_committed_partial_snapshot
        app = create_app(
            {"TESTING": True, "FORECAST_SERVICE": service},
            repository,
            manager,
        )

        response = app.test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["chart"][-1]["close"], 140.25)
        self.assertEqual(service.calls, [])
        self.assertEqual(manager.snapshot_calls, 1)
        self.assertEqual(
            response.json["forecasts"]["model"]["unavailable_reason"],
            "update_in_progress",
        )
        self.assertEqual(
            {
                row["unavailable_reason"]
                for row in response.json["forecast_evaluation"].values()
            },
            {"update_in_progress"},
        )

    def test_stock_rejects_snapshot_if_update_finishes_before_forecast_barrier(self):
        manager = StatefulFakeManager("completed")
        repository = FakeRepository()
        factory = FakeForecastFactory()
        service = ForecastService(provider_factory=factory)
        original_load = repository.load_analysis_snapshot

        def load_then_publish_new_revision(ticker):
            snapshot = original_load(ticker)
            service.invalidate()
            return snapshot

        repository.load_analysis_snapshot = load_then_publish_new_revision
        app = create_app(
            {"TESTING": True, "FORECAST_SERVICE": service},
            repository,
            manager,
        )

        response = app.test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(factory.providers, [])
        self.assertEqual(
            response.json["forecasts"]["model"]["unavailable_reason"],
            "update_in_progress",
        )
        self.assertEqual(manager.snapshot_calls, 1)

    def test_injected_forecast_service_does_not_require_update_cache_hook(self):
        service = InjectedForecastService()

        app = create_app(
            {"TESTING": True, "FORECAST_SERVICE": service},
            self.repository,
        )

        self.assertIs(app.extensions["dashboard_forecast_service"], service)
        self.assertIsInstance(
            app.extensions["dashboard_update_manager"], UpdateJobManager
        )
        self.assertIsNone(
            app.extensions["dashboard_update_manager"]._on_cache_warmup
        )

    def test_default_forecast_service_wires_automatic_cache_warmup(self):
        app = create_app(
            {"TESTING": True},
            FakeRepository(),
        )

        manager = app.extensions["dashboard_update_manager"]
        warmer = manager._on_cache_warmup
        self.assertIsInstance(warmer, ForecastCacheWarmer)
        self.assertIs(
            warmer._forecast_service,
            app.extensions["dashboard_forecast_service"],
        )

    def test_forecast_failure_isolated_with_typed_unavailable_payload(self):
        secret = "/Users/alice/model.bin?token=secret"
        service = InjectedForecastService(RuntimeError(secret))
        app = create_app(
            {"TESTING": True, "FORECAST_SERVICE": service},
            self.repository,
            self.manager,
        )

        with self.assertLogs(app.logger.name, level="ERROR") as logs:
            response = app.test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["ticker"], "AAA")
        self.assertTrue(response.json["chart"])
        self.assertEqual(response.json["forecasts"]["by_date"], {})
        self.assertEqual(
            response.json["forecasts"]["model"]["unavailable_reason"],
            "model_error",
        )
        self.assertEqual(
            {
                row["unavailable_reason"]
                for row in response.json["forecast_evaluation"].values()
            },
            {"model_error"},
        )
        self.assertNotIn(secret, response.get_data(as_text=True))
        self.assertIn(secret, "\n".join(logs.output))
    def test_stock_percentile_excludes_peer_without_exact_observation_bar(self):
        repository = ExactDatePeerRepository()
        factor = MappedFactor(
            "exact_date_value",
            "momentum",
            "higher",
            {ticker: value for value, ticker in enumerate(repository.histories, start=1)},
        )
        client = create_app(
            test_config(FACTOR_REGISTRY=FactorRegistry([factor])),
            repository,
            FakeManager(),
        ).test_client()

        response = client.get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        result = response.json["factors"][0]
        self.assertEqual(result["observation_date"], "2026-07-21")
        self.assertEqual(result["peer_count"], 4)
        self.assertIsNone(result["percentile"])
        self.assertEqual(repository.calls.count(("load_analysis_snapshot", "AAA")), 1)

    def test_stock_detail_evaluates_full_registry_only_for_selected_ticker(self):
        repository = ExactDatePeerRepository()
        numeric = CountingMappedFactor(
            "numeric",
            "momentum",
            "higher",
            {ticker: value for value, ticker in enumerate(repository.histories, start=1)},
        )
        structured = CountingMappedFactor(
            "structured",
            "structure",
            "neutral",
            {ticker: {"state": ticker} for ticker in repository.histories},
        )
        response = create_app(
            test_config(FACTOR_REGISTRY=FactorRegistry([numeric, structured])),
            repository,
            FakeManager(),
        ).test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(numeric.calls, ["AAA", "BBB", "CCC", "DDD"])
        self.assertEqual(structured.calls, ["AAA"])

    def test_stock_structures_prefer_causal_chart_evidence_over_latest_factor(self):
        repository = FakeRepository()
        registry = FactorRegistry(
            [
                MappedFactor(
                    "strict_vcp",
                    "structure",
                    "neutral",
                    {"AAA": {"reject_reason": None, "vcp_pivot": 141.5}},
                ),
                MappedFactor(
                    "tight_platform",
                    "structure",
                    "neutral",
                    {"AAA": {"is_platform": True, "reason": None, "platform_pivot": 142.0}},
                ),
            ]
        )
        response = create_app(
            test_config(
                FACTOR_REGISTRY=registry,
                ENTRY_SIGNAL_SERVICE=InjectedEntrySignalService(
                    active_latest=True
                ),
            ),
            repository,
            FakeManager(),
        ).test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        structures = response.json["structures"]
        self.assertEqual(structures["key_levels"].get("strict_vcp_pivot"), 141.5)
        self.assertIsNone(
            structures["key_levels"].get("tight_platform_pivot")
        )
        self.assertEqual(
            [(item["type"], item["label"]) for item in structures["annotations"]],
            [
                ("strict_vcp_start", "Strict VCP setup detected"),
            ],
        )
        self.assertTrue(
            all(item["time"] == response.json["observation_date"] for item in structures["annotations"])
        )

    def test_structures_use_causal_historical_entry_rows(self):
        chart = [
            {
                "time": "2026-07-01",
                "strict_vcp_start": True,
                "strict_vcp_active": True,
                "strict_vcp_pivot": 100.0,
                "strict_vcp_evidence": {
                    "accepted": True,
                    "vcp_pivot": 100.0,
                },
                "tight_platform_active": False,
                "tight_platform_evidence": {"active": False},
                "vcp_breakout_confirmed": False,
                "pocket_pivot": False,
                "pivot": 99.0,
            },
            {
                "time": "2026-07-02",
                "strict_vcp_start": False,
                "strict_vcp_active": False,
                "strict_vcp_pivot": 100.0,
                "strict_vcp_evidence": {
                    "accepted": False,
                    "vcp_pivot": 100.0,
                },
                "tight_platform_active": False,
                "tight_platform_evidence": {"active": False},
                "vcp_breakout_confirmed": True,
                "pocket_pivot": False,
                "pivot": 101.0,
            },
            {
                "time": "2026-07-03",
                "strict_vcp_start": False,
                "strict_vcp_active": False,
                "strict_vcp_pivot": None,
                "strict_vcp_evidence": {
                    "accepted": False,
                    "vcp_pivot": None,
                },
                "tight_platform_active": False,
                "tight_platform_evidence": {"active": False},
                "vcp_breakout_confirmed": False,
                "pocket_pivot": True,
                "pivot": 102.0,
            },
        ]

        structures = _structure_payload([], chart)

        self.assertEqual(
            [(item["time"], item["type"]) for item in structures["annotations"]],
            [
                ("2026-07-01", "strict_vcp_start"),
                ("2026-07-02", "vcp_breakout_confirmed"),
                ("2026-07-03", "pocket_pivot"),
            ],
        )
        self.assertEqual(
            structures["strict_vcp"],
            {
                **chart[-1]["strict_vcp_evidence"],
                "rejection_reason_code": None,
            },
        )
        self.assertEqual(
            structures["tight_platform"],
            {
                **chart[-1]["tight_platform_evidence"],
                "rejection_reason_code": None,
            },
        )

    def test_stock_exposes_stable_codes_for_structure_rejections(self):
        repository = FakeRepository()
        repository.histories["AAA"] = price_history(periods=40)
        response = create_app(
            test_config(ENTRY_SIGNAL_SERVICE=EntrySignalService()),
            repository,
            FakeManager(),
        ).test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        factors = {factor["key"]: factor for factor in response.json["factors"]}
        self.assertEqual(
            factors["strict_vcp"]["raw_value"]["reject_reason"],
            "insufficient_history",
        )
        self.assertEqual(
            factors["strict_vcp"]["raw_value"]["rejection_reason_code"],
            "insufficient_history",
        )
        self.assertEqual(
            factors["tight_platform"]["raw_value"]["reason"], "历史不足"
        )
        self.assertEqual(
            factors["tight_platform"]["raw_value"]["rejection_reason_code"],
            "insufficient_history",
        )
        self.assertEqual(
            response.json["structures"]["strict_vcp"]["rejection_reason_code"],
            "insufficient_history",
        )
        self.assertEqual(
            response.json["structures"]["tight_platform"]["rejection_reason_code"],
            "insufficient_history",
        )

    def test_stale_stock_uses_peers_truncated_to_its_observation_date(self):
        repository = StaleSelectionRepository()
        client = create_app(
            test_config(), repository, FakeManager()
        ).test_client()

        response = client.get("/api/stocks/OLD")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["observation_date"], "2026-07-15")
        factor = next(
            factor
            for factor in response.json["factors"]
            if factor["key"] == "close_vs_ema20_pct"
        )
        self.assertEqual(factor["observation_date"], "2026-07-15")
        self.assertIsNotNone(factor["percentile"])
        self.assertIn("inactive_ticker", response.json["warnings"])
        self.assertEqual(repository.calls.count(("load_analysis_snapshot", "OLD")), 1)

    def test_stale_active_stock_has_distinct_summary_and_warning(self):
        repository = FakeRepository()
        repository.histories["AAA"] = price_history(end="2026-07-20")
        original_summaries = repository.list_summaries

        def stale_summaries():
            return [
                SimpleNamespace(
                    ticker=summary.ticker,
                    latest_date=summary.latest_date,
                    lag_days=2 if summary.ticker == "AAA" else 0,
                    inactive=False,
                )
                for summary in original_summaries()
            ]

        repository.list_summaries = stale_summaries
        response = create_app(
            test_config(), repository, FakeManager()
        ).test_client().get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["summary"].get("stale"))
        self.assertFalse(response.json["summary"]["inactive"])
        self.assertIn("stale_ticker", response.json["warnings"])

    def test_stock_ticker_is_normalized_before_repository_access(self):
        response = self.client.get("/api/stocks/%20aaa%20")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["ticker"], "AAA")
        self.assertIn(("load_analysis_snapshot", "AAA"), self.repository.calls)

    def test_missing_benchmark_degrades_to_warning(self):
        repository = FakeRepository(include_benchmark=False)
        client = create_app(
            test_config(), repository, FakeManager()
        ).test_client()

        response = client.get("/api/stocks/AAA")

        self.assertEqual(response.status_code, 200)
        self.assertIn("missing_benchmark", response.json["warnings"])

    def test_benchmark_starting_after_selected_date_degrades_to_warning(self):
        repository = FakeRepository()
        repository.histories = {
            "OLD": price_history(periods=80, end="2024-01-31"),
            "SPY": price_history(periods=80, end="2026-07-21"),
        }
        response = create_app(
            test_config(), repository, FakeManager()
        ).test_client().get("/api/stocks/OLD")

        self.assertEqual(response.status_code, 200)
        self.assertIn("missing_benchmark", response.json["warnings"])

    def test_safe_unknown_ticker_error(self):
        response = self.client.get("/api/stocks/NOPE")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json["error"]["code"], "unknown_ticker")
        self.assertNotIn("/Users/", response.get_data(as_text=True))

    def test_safe_invalid_ticker_error(self):
        response = self.client.get("/api/stocks/AAA%2Fetc")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error"]["code"], "invalid_ticker")
        self.assertNotIn("/Users/", response.get_data(as_text=True))

    def test_market_data_failure_has_safe_service_unavailable_envelope(self):
        secret = "/Users/alice/prices.db?token=secret"
        repository = FakeRepository(MarketDataUnavailable())
        client = create_app(
            {"TESTING": True}, repository, FakeManager()
        ).test_client()

        response = client.get("/api/universe")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["error"]["code"], "market_data_unavailable")
        self.assertNotIn(secret, response.get_data(as_text=True))

    def test_unexpected_failure_is_redacted(self):
        secret = "/Users/alice/env.sh?token=secret"
        repository = FakeRepository(RuntimeError(secret))
        app = create_app(test_config(), repository, FakeManager())

        with self.assertLogs(app.logger.name, level="ERROR"):
            response = app.test_client().get("/api/universe")

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json["error"]["code"], "internal_error")
        self.assertNotIn(secret, body)
        self.assertNotIn("Traceback", body)

    def test_update_start_and_status_use_injected_manager(self):
        started = self.client.post("/api/update")
        status = self.client.get("/api/update/status")

        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.json["state"], "running")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json["state"], "idle")
        self.assertEqual(self.manager.start_calls, 1)
        self.assertEqual(self.manager.snapshot_calls, 1)

    def test_concurrent_update_has_conflict_envelope(self):
        manager = FakeManager(
            UpdateAlreadyRunning("unsafe detail /Users/alice/thread.log")
        )
        client = create_app(
            test_config(), FakeRepository(), manager
        ).test_client()

        response = client.post("/api/update")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json["error"]["code"], "update_in_progress")
        self.assertNotIn("/Users/", response.get_data(as_text=True))
        self.assertEqual(manager.start_calls, 1)

    def test_root_route_preserves_template_compatibility(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/html")
        self.assertIn("<html", response.get_data(as_text=True))

    def test_market_overview_route_validates_horizon_and_sector(self):
        service = mock.Mock()
        app = create_app(
            test_config(MARKET_OVERVIEW_SERVICE=service),
            repository=FakeRepository(),
            update_manager=FakeManager(),
        )
        client = app.test_client()

        bad_horizon = client.get("/api/market-overview?horizon=7")
        bad_sector = client.get(
            "/api/market-overview?sector=secret/path"
        )

        self.assertEqual(bad_horizon.status_code, 400)
        self.assertEqual(
            bad_horizon.get_json()["error"]["code"],
            "invalid_horizon",
        )
        self.assertEqual(bad_sector.status_code, 400)
        self.assertEqual(
            bad_sector.get_json()["error"]["code"],
            "invalid_sector",
        )
        service.build.assert_not_called()

    def test_market_page_and_api_keep_daily_proxy_state_honest(self):
        service = mock.Mock()
        service.build.return_value = {
            "asof": "2026-07-23",
            "requested_horizon": 5,
            "selected_sector": "semiconductor",
            "evidence_tier": "daily_proxy",
            "intraday": {
                "state": "unavailable",
                "reason": "intraday_not_integrated",
            },
            "market_posture": {
                "score": None,
                "coverage": 0.0,
                "unavailable_reason": "missing_market_benchmark",
                "evidence": [],
            },
            "sectors": [],
            "selected_group": {
                "key": "semiconductor",
                "coverage": 0.0,
            },
            "constituents": [],
            "changed_events": [],
            "calibration": {},
        }
        app = create_app(
            test_config(MARKET_OVERVIEW_SERVICE=service),
            repository=FakeRepository(),
            update_manager=FakeManager(),
        )
        client = app.test_client()

        page = client.get("/market")
        payload = client.get(
            "/api/market-overview?horizon=5&sector=semiconductor"
        )

        self.assertEqual(page.status_code, 200)
        self.assertEqual(payload.status_code, 200)
        self.assertEqual(payload.get_json()["evidence_tier"], "daily_proxy")
        self.assertEqual(
            payload.get_json()["intraday"]["state"],
            "unavailable",
        )


class ForecastServiceTest(unittest.TestCase):
    def setUp(self):
        self.histories = {
            "AAA": price_history(periods=80),
            "BBB": price_history(periods=80, offset=10),
        }
        self.chart_dates = (
            self.histories["AAA"].index[-2].date().isoformat(),
            self.histories["AAA"].index[-1].date().isoformat(),
        )

    def test_serializes_all_horizons_and_contract_fields_with_sparse_dates(self):
        factory = FakeForecastFactory()
        service = ForecastService(
            provider_factory=factory,
            evaluator=fake_forecast_evaluation,
            max_cache_size=2,
            max_forecast_dates=None,
        )

        payload = service.build("AAA", self.chart_dates, self.histories)

        forecasts = payload["forecasts"]
        self.assertEqual(forecasts["horizons"], [5, 20, 60])
        self.assertEqual(set(forecasts["by_date"]), {self.chart_dates[0]})
        self.assertEqual(
            set(forecasts["by_date"][self.chart_dates[0]]), {"5", "20", "60"}
        )
        self.assertEqual(
            set(forecasts["by_date"][self.chart_dates[0]]["20"]),
            {
                "ticker",
                "asof_date",
                "horizon_sessions",
                "direction",
                "raw_direction",
                "predicted_return",
                "up_probability",
                "confidence_status",
                "confidence_reason",
                "training_sample_count",
                "training_cutoff",
                "model_key",
                "model_version",
                "bearish_turn_score",
                "direction_adjustment_reason",
                "bearish_turn_conditions",
                "unavailable_reason",
                "decision",
                "feature_provenance",
            },
        )
        provenance_registry = payload["feature_provenance_registry"]
        self.assertEqual(
            provenance_registry["version"],
            "feature_provenance_registry_v1",
        )
        self.assertEqual(
            provenance_registry["feature_version"],
            "ridge-features-v2",
        )
        provenance = forecasts["by_date"][self.chart_dates[0]]["20"][
            "feature_provenance"
        ]
        self.assertEqual(
            provenance["registry_ref"],
            provenance_registry["version"],
        )
        self.assertEqual(
            provenance["source_cutoff"],
            self.chart_dates[0],
        )
        self.assertEqual(
            provenance["observed_through"],
            self.chart_dates[0],
        )
        self.assertRegex(provenance["data_version"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            provenance["execution_timing"],
            "next_session_open",
        )
        self.assertEqual(set(payload["forecast_evaluation"]), {"5", "20", "60"})
        self.assertEqual(factory.providers[0].calls[0][0], "AAA")
        self.assertEqual(factory.providers[0].calls[0][2], (5, 20, 60))

    def test_default_service_bounds_request_work_and_marks_evaluation_not_precomputed(self):
        factory = FakeForecastFactory()
        service = ForecastService(provider_factory=factory)

        payload = service.build("AAA", self.chart_dates, self.histories)

        self.assertEqual(
            factory.providers[0].calls,
            [
                (
                    "AAA",
                    (pd.Timestamp(self.chart_dates[-1]),),
                    (5, 20, 60),
                )
            ],
        )
        self.assertEqual(
            payload["forecasts"]["date_coverage"],
            {
                "requested_date_count": 2,
                "computed_date_count": 1,
                "computed_dates": [self.chart_dates[-1]],
                "policy": "latest_only_synchronous",
                "omitted_reason": "not_precomputed",
            },
        )
        self.assertEqual(
            {
                row["unavailable_reason"]
                for row in payload["forecast_evaluation"].values()
            },
            {"not_precomputed"},
        )
        self.assertTrue(
            all(
                row["sample_count"] == 0
                for row in payload["forecast_evaluation"].values()
            )
        )

    def test_preserves_falsey_injected_provider_factory(self):
        factory = FalseyForecastFactory()
        service = ForecastService(
            provider_factory=factory,
            evaluator=fake_forecast_evaluation,
        )

        payload = service.build("AAA", self.chart_dates, self.histories)

        self.assertEqual(payload["forecasts"]["model"]["key"], "fake_direction")
        self.assertEqual(len(factory.providers), 1)

    def test_cache_key_is_exact_five_field_versioned_identity(self):
        service = ForecastService(
            provider_factory=FakeForecastFactory(),
            evaluator=fake_forecast_evaluation,
            max_cache_size=2,
        )

        service.build("AAA", self.chart_dates, self.histories)

        cache_key = next(iter(service._cache))
        self.assertEqual(len(cache_key), 5)
        self.assertEqual(
            cache_key,
            (
                service.database_revision,
                "AAA",
                pd.Timestamp(self.chart_dates[0]),
                pd.Timestamp(self.chart_dates[-1]),
                service.model_version,
            ),
        )

    def test_cache_is_bounded_exact_and_invalidated_by_revision(self):
        factory = FakeForecastFactory()
        service = ForecastService(
            provider_factory=factory,
            evaluator=fake_forecast_evaluation,
            max_cache_size=2,
        )

        first = service.build("AAA", self.chart_dates, self.histories)
        repeated = service.build("AAA", self.chart_dates, self.histories)
        service.build("BBB", self.chart_dates, self.histories)
        service.build("AAA", self.chart_dates[-1:], self.histories)
        service.build("AAA", self.chart_dates, self.histories)
        service.invalidate()
        after_invalidation = service.build("AAA", self.chart_dates, self.histories)

        self.assertEqual(first, repeated)
        self.assertIsNot(first, repeated)
        self.assertIsNot(first["forecasts"], repeated["forecasts"])
        self.assertEqual(len(factory.providers), 2)
        self.assertEqual(first, after_invalidation)

    def test_revision_artifacts_are_built_once_across_distinct_bundle_keys(self):
        factory = FakeForecastFactory()
        service = ForecastService(
            provider_factory=factory,
            evaluator=fake_forecast_evaluation,
        )

        with mock.patch(
            "web.services.forecasts.build_feature_frame",
            wraps=build_feature_frame,
        ) as builder:
            service.build("AAA", self.chart_dates, self.histories)
            service.build("BBB", self.chart_dates, self.histories)
            service.build("AAA", self.chart_dates[-1:], self.histories)
            self.assertEqual(builder.call_count, 1)
            self.assertEqual(len(factory.providers), 1)

            service.invalidate()
            service.build("AAA", self.chart_dates, self.histories)

        self.assertEqual(builder.call_count, 2)
        self.assertEqual(len(factory.providers), 2)

    def test_top_risk_timeline_reuses_revision_artifact(self):
        factory = FakeForecastFactory()
        service = ForecastService(provider_factory=factory)
        risk_index = pd.MultiIndex.from_product(
            [["AAA"], pd.to_datetime(self.chart_dates)],
            names=("ticker", "observation_date"),
        )
        risk_context = pd.DataFrame(
            {
                "high_level_distribution_score": [45.0, 72.0],
                "high_level_distribution_raw_score": [45.0, 72.0],
                "high_level_distribution_state": ["watch", "confirmed"],
                "high_level_distribution_raw_state": ["watch", "confirmed"],
                "high_level_distribution_age_sessions": [0, 0],
                "top_risk_recovery": [False, False],
            },
            index=risk_index,
        )

        with mock.patch(
            "web.services.forecasts.build_forecast_risk_context",
            return_value=risk_context,
        ) as risk_builder:
            first = service.build_top_risk_timeline(
                "AAA",
                self.chart_dates,
                self.histories,
            )
            second = service.build_top_risk_timeline(
                "AAA",
                self.chart_dates,
                self.histories,
            )

        self.assertEqual(risk_builder.call_count, 1)
        self.assertEqual(first, second)
        self.assertEqual(
            [event["type"] for event in first["events"]],
            ["top_risk_watch", "top_risk_confirmed"],
        )

    def test_top_risk_timeline_checks_expected_revision(self):
        service = ForecastService(provider_factory=FakeForecastFactory())

        with self.assertRaises(ForecastRevisionChanged):
            service.build_top_risk_timeline(
                "AAA",
                self.chart_dates,
                self.histories,
                expected_revision=99,
            )

    def test_new_service_restores_persistent_revision_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ForecastArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            first_service = ForecastService(artifact_store=store)
            second_service = ForecastService(artifact_store=store)

            with mock.patch(
                "web.services.forecasts.build_feature_frame",
                wraps=build_feature_frame,
            ) as feature_builder, mock.patch(
                "web.services.forecasts.build_forecast_risk_context",
                wraps=__import__(
                    "web.forecasts.decision",
                    fromlist=["build_forecast_risk_context"],
                ).build_forecast_risk_context,
            ) as risk_builder:
                first = first_service.build(
                    "AAA",
                    self.chart_dates,
                    self.histories,
                )
                second = second_service.build(
                    "AAA",
                    self.chart_dates,
                    self.histories,
                )

            self.assertEqual(first, second)
            self.assertEqual(feature_builder.call_count, 1)
            self.assertEqual(risk_builder.call_count, 1)
            self.assertEqual(store.entry_count(), 1)
            self.assertIsNot(
                first_service._artifact_provider,
                second_service._artifact_provider,
            )

    def test_cache_status_tracks_rebuild_memory_hit_and_disk_hit(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ForecastArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            first_service = ForecastService(artifact_store=store)

            empty = first_service.cache_status()
            self.assertEqual(empty["state"], "empty")
            self.assertEqual(empty["last_access"], "miss")
            self.assertFalse(empty["memory_ready"])

            first_service.prewarm(self.histories)
            rebuilt = first_service.cache_status()
            self.assertEqual(rebuilt["state"], "ready")
            self.assertEqual(rebuilt["last_access"], "rebuilt")
            self.assertTrue(rebuilt["memory_ready"])
            self.assertIsNotNone(rebuilt["build_started_at"])
            self.assertIsNotNone(rebuilt["build_finished_at"])
            self.assertEqual(rebuilt["market_asof"], "2026-07-21")

            first_service.prewarm(self.histories)
            self.assertEqual(
                first_service.cache_status()["last_access"],
                "memory_hit",
            )

            second_service = ForecastService(artifact_store=store)
            second_service.prewarm(self.histories)
            restored = second_service.cache_status()
            self.assertEqual(restored["last_access"], "disk_hit")
            self.assertTrue(restored["memory_ready"])
            self.assertEqual(restored["database_revision"], 0)

    def test_cache_status_is_safe_when_store_status_fails(self):
        class FailingStatusStore:
            def load(self, identity, market_signature):
                return None

            def save(self, identity, market_signature, artifact):
                return False

            def status(self):
                raise RuntimeError("secret cache path")

        status = ForecastService(
            artifact_store=FailingStatusStore()
        ).cache_status()

        self.assertEqual(status["state"], "unavailable")
        self.assertEqual(status["last_access"], "miss")
        self.assertNotIn("error", status)

    def test_top_risk_timeline_restores_persistent_risk_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ForecastArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            first_service = ForecastService(artifact_store=store)
            first_service.prewarm(self.histories)

            with mock.patch(
                "web.services.forecasts.build_feature_frame",
                side_effect=AssertionError("persistent hit rebuilt features"),
            ), mock.patch(
                "web.services.forecasts.build_forecast_risk_context",
                side_effect=AssertionError("persistent hit rebuilt risk"),
            ):
                result = ForecastService(
                    artifact_store=store
                ).build_top_risk_timeline(
                    "AAA",
                    self.chart_dates,
                    self.histories,
                )

            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["unavailable_reason"], "not_available")

    def test_persistent_artifact_misses_for_history_and_version_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ForecastArtifactStore(
                Path(temporary) / "analysis_cache.db",
                max_entries=8,
            )
            corrected = {
                ticker: history.copy(deep=True)
                for ticker, history in self.histories.items()
            }
            corrected["AAA"].iloc[
                -5,
                corrected["AAA"].columns.get_loc("Close"),
            ] += 0.25
            extended = {}
            for ticker, history in self.histories.items():
                appended = history.iloc[-1:].copy()
                appended.index = pd.DatetimeIndex(
                    [history.index[-1] + pd.offsets.BDay(1)]
                )
                extended[ticker] = pd.concat([history, appended])

            with mock.patch(
                "web.services.forecasts.build_feature_frame",
                wraps=build_feature_frame,
            ) as builder:
                ForecastService(artifact_store=store).build(
                    "AAA", self.chart_dates, self.histories
                )
                ForecastService(artifact_store=store).build(
                    "AAA", self.chart_dates, corrected
                )
                ForecastService(artifact_store=store).build(
                    "AAA", self.chart_dates, extended
                )
                with mock.patch(
                    "web.services.forecasts.FORECAST_FEATURE_VERSION",
                    "ridge-features-v3",
                ):
                    ForecastService(artifact_store=store).build(
                        "AAA", self.chart_dates, self.histories
                    )
                with mock.patch(
                    "web.services.forecasts.FORECAST_RISK_CONTEXT_VERSION",
                    "forecast-risk-context-v2",
                ):
                    ForecastService(artifact_store=store).build(
                        "AAA", self.chart_dates, self.histories
                    )

            self.assertEqual(builder.call_count, 5)

    def test_prewarm_persists_without_forecasting_and_store_failures_are_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ForecastArtifactStore(
                Path(temporary) / "analysis_cache.db"
            )
            service = ForecastService(artifact_store=store)

            summary = service.prewarm(self.histories)

            self.assertGreater(summary["row_count"], 0)
            self.assertEqual(summary["database_revision"], 0)
            self.assertEqual(store.entry_count(), 1)
            with mock.patch(
                "web.services.forecasts.build_feature_frame",
                side_effect=AssertionError("persistent hit rebuilt features"),
            ), mock.patch(
                "web.services.forecasts.build_forecast_risk_context",
                side_effect=AssertionError("persistent hit rebuilt risk"),
            ):
                payload = ForecastService(artifact_store=store).build(
                    "AAA",
                    self.chart_dates,
                    self.histories,
                )
            self.assertEqual(payload["forecasts"]["model"]["status"], "available")

        class FailingStore:
            def load(self, identity, market_signature):
                return None

            def save(self, identity, market_signature, artifact):
                return False

        payload = ForecastService(artifact_store=FailingStore()).build(
            "AAA",
            self.chart_dates,
            self.histories,
        )
        self.assertEqual(payload["forecasts"]["model"]["status"], "available")

    def test_applies_point_in_time_risk_context_to_every_available_forecast(self):
        factory = FakeForecastFactory()
        service = ForecastService(
            provider_factory=factory,
            evaluator=fake_forecast_evaluation,
            max_forecast_dates=None,
        )
        risk_index = pd.MultiIndex.from_product(
            [["AAA"], pd.to_datetime(self.chart_dates)],
            names=["ticker", "observation_date"],
        )
        risk_context = pd.DataFrame(
            {
                "persistent_risk_raw_score": [34.0, 34.0],
                "persistent_risk_score": [34.0, 34.0],
                "persistent_risk_state": ["new", "persistent"],
                "persistent_risk_age_sessions": [0, 1],
            },
            index=risk_index,
        )

        with mock.patch(
            "web.services.forecasts.build_forecast_risk_context",
            return_value=risk_context,
        ) as builder:
            payload = service.build("AAA", self.chart_dates, self.histories)

        builder.assert_called_once_with(self.histories)
        rows = payload["forecasts"]["by_date"][self.chart_dates[0]]
        self.assertEqual(rows["5"]["raw_direction"], "up")
        self.assertEqual(rows["5"]["direction"], "neutral")
        self.assertEqual(
            rows["5"]["decision"],
            {
                "final_direction": "neutral",
                "risk_state": "high",
                "action": "downgrade_to_neutral",
                "reasons": ["persistent_bearish_risk"],
                "policy_key": "forecast_decision_policy",
                "policy_version": "v2",
                "persistent_risk_score": 34.0,
                "persistent_risk_raw_score": 34.0,
                "persistent_risk_state": "new",
                "persistent_risk_age_sessions": 0,
                "immediate_risk_score": 0.0,
                "persistent_risk_sources": [],
                "individual_risk_score": None,
                "group_risk_score": None,
                "slow_decline_risk_score": None,
                "high_level_distribution_score": None,
                "high_level_distribution_raw_score": None,
                "high_level_distribution_state": "unavailable",
                "high_level_distribution_raw_state": "unavailable",
                "high_level_distribution_age_sessions": None,
                "high_level_context_score": None,
                "distribution_pressure_score": None,
                "structure_damage_score": None,
                "high_level_distribution_conditions": [],
                "distribution_count_5": None,
                "distribution_count_10": None,
                "distribution_count_20": None,
                "churning_count_10": None,
                "churning_cluster": None,
                "climax_run_score": None,
                "climax_run_candidate": None,
                "climax_run_conditions": [],
                "top_risk_recovery": None,
                "top_risk_recovery_conditions": [],
            },
        )

    def test_missing_risk_context_is_explicit_and_retains_raw_direction(self):
        service = ForecastService(
            provider_factory=FakeForecastFactory(),
            evaluator=fake_forecast_evaluation,
            max_forecast_dates=None,
        )

        payload = service.build("AAA", self.chart_dates, self.histories)

        row = payload["forecasts"]["by_date"][self.chart_dates[0]]["5"]
        self.assertEqual(row["direction"], "up")
        self.assertEqual(row["decision"]["risk_state"], "unavailable")
        self.assertEqual(row["decision"]["action"], "retain")

    def test_revision_artifacts_rebuild_when_a_later_snapshot_is_more_complete(self):
        factory = FakeForecastFactory()
        service = ForecastService(provider_factory=factory)
        short_histories = {
            ticker: history.iloc[:-10]
            for ticker, history in self.histories.items()
        }
        short_dates = tuple(value.index[-1] for value in short_histories.values())

        service.build("AAA", short_dates[:1], short_histories)
        service.build("AAA", self.chart_dates, self.histories)

        self.assertEqual(len(factory.providers), 2)

    def test_same_shape_corrected_snapshot_rebuilds_artifacts_and_exact_bundle(self):
        factory = FakeForecastFactory()
        service = ForecastService(provider_factory=factory)
        corrected = {
            ticker: history.copy(deep=True)
            for ticker, history in self.histories.items()
        }
        corrected["AAA"].iloc[-5, corrected["AAA"].columns.get_loc("Close")] += 0.25

        service.build("AAA", self.chart_dates, self.histories)
        service.build("AAA", self.chart_dates, corrected)

        self.assertEqual(len(factory.providers), 2)

    def test_snapshot_from_an_older_revision_is_never_cached_under_a_new_revision(self):
        factory = FakeForecastFactory()
        service = ForecastService(provider_factory=factory)
        observed_revision = service.database_revision
        service.invalidate()

        with self.assertRaises(ForecastRevisionChanged):
            service.build(
                "AAA",
                self.chart_dates,
                self.histories,
                expected_revision=observed_revision,
            )

        self.assertEqual(len(factory.providers), 0)
        self.assertEqual(service._cache, {})

    def test_generated_revision_frame_skips_revalidating_labels_per_live_fit(self):
        service = ForecastService()

        with mock.patch(
            "web.forecasts.dataset._validate_label_dates",
            side_effect=AssertionError("generated labels were already validated"),
        ):
            payload = service.build("AAA", self.chart_dates, self.histories)

        self.assertEqual(payload["forecasts"]["model"]["status"], "available")
        self.assertEqual(len(payload["forecasts"]["by_date"]), 1)

    def test_same_key_concurrent_requests_compute_once(self):
        factory = FakeForecastFactory()
        service = ForecastService(
            provider_factory=factory,
            evaluator=fake_forecast_evaluation,
            max_cache_size=2,
        )
        barrier = threading.Barrier(3)
        payloads = []

        def request_bundle():
            barrier.wait(timeout=2)
            payloads.append(service.build("AAA", self.chart_dates, self.histories))

        threads = [threading.Thread(target=request_bundle) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=2)
        for thread in threads:
            thread.join(timeout=2)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0], payloads[1])
        self.assertEqual(len(factory.providers), 1)


if __name__ == "__main__":
    unittest.main()
