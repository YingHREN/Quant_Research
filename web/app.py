"""Local-only Flask application for the quant research dashboard.

Usage::

    python web/app.py
    # Open http://127.0.0.1:5000
"""

from __future__ import annotations

import math
from numbers import Real
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from flask import Flask, abort, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from web.contracts import ErrorPayload, iso_date, json_safe
from web.factors.builtin import build_chart_rows, build_default_registry
from web.forecasts.base import SUPPORTED_HORIZONS, UnavailableReason
from web.forecasts.model_outputs import build_model_outputs
from research.market_context import build_group_score_frame
from research.risk_memory import (
    RISK_MEMORY_HALF_LIFE_SESSIONS,
    RISK_MEMORY_WINDOW_SESSIONS,
)
from web.market_calendar import session_offset
from web.market_groups import (
    REFERENCE_TICKERS,
    market_group,
    market_group_for_ticker,
)
from web.services.analysis import AnalysisContext
from web.services.forecasts import (
    ForecastRevisionChanged,
    ForecastService,
    unavailable_forecast_bundle,
)
from web.services.forecast_artifacts import ForecastArtifactStore
from web.services.forecast_warmup import ForecastCacheWarmer
from web.services.top_risk_timeline import unavailable_top_risk_timeline
from web.services.entry_signals import (
    EntrySignalArtifactStore,
    EntrySignalService,
    merge_entry_signal_rows,
)
from web.services.market_data import (
    InvalidTicker,
    MarketDataRepository,
    MarketDataUnavailable,
    UnknownTicker,
)
from web.services.market_overview import MarketOverviewService
from web.services.universe import UniverseSnapshotService
from web.services.intraday import IntradayStatusService
from web.services.scenarios import HistoricalScenarioProvider
from web.services.update_jobs import (
    PriceProvider,
    UpdateAlreadyRunning,
    UpdateJobManager,
)
from marketdata.paths import DEFAULT_MARKET_DATA_DATABASE
from marketdata.storage import IntradayStore


DEFAULT_DATABASE = DEFAULT_MARKET_DATA_DATABASE
def create_app(config=None, repository=None, update_manager=None) -> Flask:
    """Build the dashboard app with optional repository and job-manager fakes."""
    supplied_config = {} if config is None else dict(config)
    flask_app = Flask(__name__)
    flask_app.config.from_mapping(
        MARKET_DATA_DATABASE=os.fspath(DEFAULT_DATABASE),
        FORECAST_ARTIFACT_CACHE_ENABLED=True,
        FORECAST_ARTIFACT_CACHE_PATH=os.fspath(
            PROJECT_ROOT / "data" / "analysis_cache.db"
        ),
        FORECAST_ARTIFACT_CACHE_ENTRIES=2,
        ENTRY_SIGNAL_CACHE_SIZE=16,
        ENTRY_SIGNAL_ARTIFACT_CACHE_ENABLED=True,
        ENTRY_SIGNAL_ARTIFACT_CACHE_PATH=os.fspath(
            PROJECT_ROOT / "data" / "analysis_cache.db"
        ),
        ENTRY_SIGNAL_ARTIFACT_CACHE_ENTRIES=64,
    )
    if config:
        flask_app.config.update(config)

    if repository is None:
        repository = MarketDataRepository(flask_app.config["MARKET_DATA_DATABASE"])
    forecast_service = flask_app.config.get("FORECAST_SERVICE")
    if forecast_service is None:
        persistent_cache_enabled = bool(
            flask_app.config["FORECAST_ARTIFACT_CACHE_ENABLED"]
        ) and (
            not flask_app.config.get("TESTING")
            or "FORECAST_ARTIFACT_CACHE_PATH" in supplied_config
        )
        artifact_store = (
            ForecastArtifactStore(
                flask_app.config["FORECAST_ARTIFACT_CACHE_PATH"],
                max_entries=flask_app.config[
                    "FORECAST_ARTIFACT_CACHE_ENTRIES"
                ],
            )
            if persistent_cache_enabled
            else None
        )
        forecast_service = ForecastService(
            max_cache_size=flask_app.config.get("FORECAST_CACHE_SIZE", 16),
            artifact_store=artifact_store,
        )
    entry_signal_service = flask_app.config.get("ENTRY_SIGNAL_SERVICE")
    if entry_signal_service is None:
        persistent_entry_cache_enabled = bool(
            flask_app.config["ENTRY_SIGNAL_ARTIFACT_CACHE_ENABLED"]
        ) and (
            not flask_app.config.get("TESTING")
            or "ENTRY_SIGNAL_ARTIFACT_CACHE_PATH" in supplied_config
            or "ENTRY_SIGNAL_ARTIFACT_CACHE_ENABLED" in supplied_config
        )
        entry_artifact_store = (
            EntrySignalArtifactStore(
                flask_app.config["ENTRY_SIGNAL_ARTIFACT_CACHE_PATH"],
                max_entries=flask_app.config[
                    "ENTRY_SIGNAL_ARTIFACT_CACHE_ENTRIES"
                ],
            )
            if persistent_entry_cache_enabled
            else None
        )
        entry_signal_service = EntrySignalService(
            max_cache_size=flask_app.config["ENTRY_SIGNAL_CACHE_SIZE"],
            artifact_store=entry_artifact_store,
        )
    if update_manager is None:
        cache_warmer = (
            ForecastCacheWarmer(repository, forecast_service)
            if callable(getattr(forecast_service, "prewarm", None))
            else None
        )
        update_manager = UpdateJobManager(
            repository,
            PriceProvider(),
            on_success=getattr(forecast_service, "invalidate", None),
            on_cache_warmup=cache_warmer,
            reference_tickers=REFERENCE_TICKERS,
        )
    market_overview_service = flask_app.config.get(
        "MARKET_OVERVIEW_SERVICE"
    )
    if market_overview_service is None:
        market_overview_service = MarketOverviewService(
            repository,
            revision_getter=lambda: getattr(
                forecast_service,
                "database_revision",
                0,
            ),
        )
    factor_registry = flask_app.config.get("FACTOR_REGISTRY")
    if factor_registry is None:
        factor_registry = build_default_registry(
            max_peer_cache_size=flask_app.config.get(
                "FACTOR_PEER_CACHE_SIZE",
                4096,
            )
        )
    universe_service = flask_app.config.get("UNIVERSE_SERVICE")
    if universe_service is None:
        universe_service = UniverseSnapshotService(
            repository,
            factor_registry,
            revision_getter=lambda: getattr(
                forecast_service,
                "database_revision",
                0,
            ),
            max_cache_size=flask_app.config.get("UNIVERSE_CACHE_SIZE", 4),
        )
    scenario_provider = flask_app.config.get("SCENARIO_PROVIDER")
    if scenario_provider is None:
        scenario_provider = HistoricalScenarioProvider()
    intraday_status_service = flask_app.config.get("INTRADAY_STATUS_SERVICE")
    if intraday_status_service is None:
        intraday_status_service = IntradayStatusService(
            store=IntradayStore(
                flask_app.config["MARKET_DATA_DATABASE"]
            ),
            stale_after_seconds=flask_app.config.get(
                "INTRADAY_STATUS_STALE_AFTER_SECONDS",
                30,
            ),
        )

    flask_app.extensions["dashboard_repository"] = repository
    flask_app.extensions["dashboard_update_manager"] = update_manager
    flask_app.extensions["dashboard_factor_registry"] = factor_registry
    flask_app.extensions["dashboard_universe_service"] = universe_service
    flask_app.extensions["dashboard_scenario_provider"] = scenario_provider
    flask_app.extensions["dashboard_forecast_service"] = forecast_service
    flask_app.extensions[
        "dashboard_entry_signal_service"
    ] = entry_signal_service
    flask_app.extensions["dashboard_intraday_status_service"] = intraday_status_service
    flask_app.extensions[
        "dashboard_market_overview_service"
    ] = market_overview_service

    @flask_app.get("/")
    def index():
        return render_template("index.html", rows=None, mkt_ok=None, query="")

    @flask_app.get("/market")
    def market_dashboard():
        return render_template("market.html")

    @flask_app.get("/api/market-overview")
    def market_overview():
        raw_horizon = request.args.get("horizon", "5")
        try:
            horizon = int(raw_horizon)
        except (TypeError, ValueError):
            return _safe_error(
                "invalid_horizon",
                "Market horizon is invalid",
                400,
            )
        if horizon not in SUPPORTED_HORIZONS:
            return _safe_error(
                "invalid_horizon",
                "Market horizon is invalid",
                400,
            )
        sector = request.args.get("sector", "semiconductor")
        try:
            market_group(sector)
        except ValueError:
            return _safe_error(
                "invalid_sector",
                "Market sector is invalid",
                400,
            )
        payload = market_overview_service.build(
            asof=request.args.get("asof"),
            horizon=horizon,
            sector=sector,
        )
        return _json_response(payload)

    @flask_app.get("/api/universe")
    def universe():
        return _json_response(universe_service.build())

    @flask_app.get("/api/stocks/<path:ticker>")
    def stock(ticker):
        normalized_ticker = ticker.strip().upper()
        forecast_revision = getattr(forecast_service, "database_revision", None)
        snapshot = repository.load_analysis_snapshot(normalized_ticker)
        history = snapshot.histories[normalized_ticker]
        if history.empty:
            raise MarketDataUnavailable()

        history = history.sort_index()
        observation_timestamp = pd.Timestamp(history.index[-1])
        observation_date = iso_date(observation_timestamp)
        summaries = snapshot.summaries
        selected_summary = next(
            (
                summary
                for summary in summaries
                if summary.ticker == normalized_ticker
            ),
            None,
        )

        peer_histories = snapshot.histories

        warnings = []
        benchmark_history = peer_histories.get("SPY")
        if benchmark_history is None or benchmark_history.empty:
            benchmark_history = None
            warnings.append("missing_benchmark")

        peer_contexts = [
            AnalysisContext(
                ticker=peer_ticker,
                observation_date=pd.Timestamp(peer_history.index[-1]),
                history=peer_history,
                benchmark_history=benchmark_history,
            )
            for peer_ticker, peer_history in peer_histories.items()
            if not peer_history.empty
        ]
        context = next(
            context for context in peer_contexts
            if context.ticker == normalized_ticker
        )
        factor_rows = factor_registry.evaluate_selected_with_peers(
            context,
            peer_contexts,
            cache_namespace=forecast_revision,
        )
        factor_payload = [result.to_dict() for result in factor_rows]
        chart = build_chart_rows(context)
        chart = merge_entry_signal_rows(
            chart,
            entry_signal_service.build(normalized_ticker, history),
        )
        _attach_market_bearish_risk(
            chart,
            normalized_ticker,
            peer_histories,
        )

        if selected_summary is not None and selected_summary.inactive:
            warnings.append("inactive_ticker")
        elif selected_summary is not None and selected_summary.lag_days > 0:
            warnings.append("stale_ticker")
        if len(chart) < 200:
            warnings.append("insufficient_indicator_history")

        try:
            forecast_arguments = (
                normalized_ticker,
                tuple(row["time"] for row in chart),
                peer_histories,
            )
            update_snapshot = update_manager.snapshot()
            if getattr(update_snapshot, "state", None) == "running":
                forecast_payload = unavailable_forecast_bundle(
                    getattr(forecast_service, "model_key", "ridge_direction_v1"),
                    getattr(forecast_service, "model_version", "v1"),
                    UnavailableReason.UPDATE_IN_PROGRESS,
                )
            elif forecast_revision is None:
                forecast_payload = forecast_service.build(*forecast_arguments)
            else:
                forecast_payload = forecast_service.build(
                    *forecast_arguments,
                    expected_revision=forecast_revision,
                )
        except ForecastRevisionChanged:
            forecast_payload = unavailable_forecast_bundle(
                getattr(forecast_service, "model_key", "ridge_direction_v1"),
                getattr(forecast_service, "model_version", "v1"),
                UnavailableReason.UPDATE_IN_PROGRESS,
            )
        except Exception as error:
            flask_app.logger.exception(
                "Forecast service failed for %s", normalized_ticker, exc_info=error
            )
            forecast_payload = unavailable_forecast_bundle(
                getattr(forecast_service, "model_key", "ridge_direction_v1"),
                getattr(forecast_service, "model_version", "v1"),
            )

        _attach_forecast_target_dates(
            forecast_payload, snapshot.histories[normalized_ticker].index
        )
        top_risk = _top_risk_payload(
            forecast_service,
            forecast_arguments,
            forecast_revision,
            update_in_progress=(
                getattr(update_snapshot, "state", None) == "running"
            ),
        )
        structures = _structure_payload(factor_payload, chart, top_risk)
        _attach_model_outputs(forecast_payload, chart, structures)
        payload = {
            "ticker": normalized_ticker,
            "observation_date": observation_date,
            "summary": _stock_summary(chart, selected_summary),
            "chart": chart,
            "structures": structures,
            "factors": factor_payload,
            "scenarios": scenario_provider.build(history, observation_timestamp),
            "warnings": warnings,
            "forecasts": forecast_payload["forecasts"],
            "forecast_evaluation": forecast_payload["forecast_evaluation"],
            "top_risk": top_risk,
        }
        return _json_response(payload)

    @flask_app.get("/api/stocks/<ticker>/forecasts/<forecast_date>")
    def historical_forecast(ticker, forecast_date):
        normalized_ticker = ticker.strip().upper()
        revision = getattr(forecast_service, "database_revision", None)
        snapshot = repository.load_analysis_snapshot(normalized_ticker)
        try:
            timestamp = pd.Timestamp(forecast_date)
        except (TypeError, ValueError):
            abort(404)
        if (
            pd.isna(timestamp)
            or timestamp.tz is not None
            or timestamp.date().isoformat() != forecast_date
            or timestamp not in snapshot.histories[normalized_ticker].index
        ):
            abort(404)

        update_snapshot = update_manager.snapshot()
        if getattr(update_snapshot, "state", None) == "running":
            return _json_response(
                unavailable_forecast_bundle(
                    getattr(forecast_service, "model_key", "ridge_direction_v1"),
                    getattr(forecast_service, "model_version", "v1"),
                    UnavailableReason.UPDATE_IN_PROGRESS,
                )
            )

        arguments = (
            normalized_ticker,
            (forecast_date,),
            snapshot.histories,
        )
        try:
            payload = (
                forecast_service.build(*arguments)
                if revision is None
                else forecast_service.build(
                    *arguments, expected_revision=revision
                )
            )
        except ForecastRevisionChanged:
            payload = unavailable_forecast_bundle(
                getattr(forecast_service, "model_key", "ridge_direction_v1"),
                getattr(forecast_service, "model_version", "v1"),
                UnavailableReason.UPDATE_IN_PROGRESS,
            )
        _attach_forecast_target_dates(
            payload, snapshot.histories[normalized_ticker].index
        )
        history = snapshot.histories[normalized_ticker]
        benchmark = snapshot.histories.get("SPY")
        context = AnalysisContext(
            ticker=normalized_ticker,
            observation_date=timestamp,
            history=history,
            benchmark_history=benchmark,
        )
        chart = build_chart_rows(context)
        chart = merge_entry_signal_rows(
            chart,
            entry_signal_service.build(normalized_ticker, history),
        )
        _attach_market_bearish_risk(
            chart,
            normalized_ticker,
            snapshot.histories,
        )
        _attach_model_outputs(payload, chart)
        return _json_response(payload)

    @flask_app.post("/api/update")
    def start_update():
        return _json_response(_snapshot_dict(update_manager.start()), status=202)

    @flask_app.get("/api/update/status")
    def update_status():
        return _json_response(_snapshot_dict(update_manager.snapshot()))

    @flask_app.get("/api/market-data/status")
    def market_data_status():
        return _json_response(intraday_status_service.snapshot())

    @flask_app.errorhandler(InvalidTicker)
    def invalid_ticker(_error):
        return _safe_error("invalid_ticker", "Ticker format is invalid", 400)

    @flask_app.errorhandler(UnknownTicker)
    def unknown_ticker(_error):
        return _safe_error("unknown_ticker", "Ticker was not found", 404)

    @flask_app.errorhandler(UpdateAlreadyRunning)
    def update_in_progress(_error):
        return _safe_error("update_in_progress", "An update is already running", 409)

    @flask_app.errorhandler(MarketDataUnavailable)
    def market_data_unavailable(_error):
        return _safe_error(
            "market_data_unavailable", "Market data is unavailable", 503
        )

    @flask_app.errorhandler(HTTPException)
    def http_error(error):
        if not request.path.startswith("/api/"):
            return error
        code = "not_found" if error.code == 404 else "http_error"
        message = "Resource was not found" if error.code == 404 else "Request failed"
        return _safe_error(code, message, error.code or 500)

    @flask_app.errorhandler(Exception)
    def internal_error(error):
        flask_app.logger.exception("Unhandled dashboard request failure", exc_info=error)
        return _safe_error("internal_error", "An internal error occurred", 500)

    return flask_app


def _json_response(payload, status=200):
    return jsonify(json_safe(payload)), status


def _safe_error(code, message, status):
    return _json_response(ErrorPayload(code, message).to_dict(), status=status)


def _attach_forecast_target_dates(payload, known_sessions):
    by_date = payload.get("forecasts", {}).get("by_date", {})
    for raw_date, horizons in by_date.items():
        if not isinstance(horizons, dict):
            continue
        for raw_horizon, forecast in horizons.items():
            if not isinstance(forecast, dict):
                continue
            try:
                horizon = int(raw_horizon)
                projection_dates = [
                    session_offset(
                        pd.Timestamp(raw_date),
                        offset,
                        known_sessions=pd.DatetimeIndex(known_sessions),
                    ).date().isoformat()
                    for offset in range(1, horizon + 1)
                ]
            except (TypeError, ValueError):
                continue
            forecast["projection_dates"] = projection_dates
            forecast["target_date"] = projection_dates[-1] if projection_dates else raw_date


def _attach_model_outputs(payload, chart, structures=None):
    by_date = payload.get("forecasts", {}).get("by_date", {})
    evaluations = payload.get("forecast_evaluation", {})
    rows = {
        row.get("time"): row
        for row in chart
        if isinstance(row, dict) and isinstance(row.get("time"), str)
    }
    for raw_date, horizons in by_date.items():
        if not isinstance(horizons, dict):
            continue
        row = dict(rows.get(raw_date, {}))
        for raw_horizon, forecast in horizons.items():
            if not isinstance(forecast, dict):
                continue
            forecast["model_outputs"] = build_model_outputs(
                forecast,
                row,
                evaluations.get(str(raw_horizon), {}),
            )


def _finite_number(value):
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _optional_number(value):
    return float(value) if _finite_number(value) else None


def _attach_market_bearish_risk(chart, ticker, histories):
    defaults = {
        "market_bearish_turn_raw_score": None,
        "market_bearish_turn_state_score": None,
        "market_bearish_turn_state": "unavailable",
        "market_bearish_turn_memory_age_sessions": None,
        "market_bearish_turn_memory_half_life_sessions": (
            RISK_MEMORY_HALF_LIFE_SESSIONS
        ),
        "market_bearish_turn_memory_window_sessions": (
            RISK_MEMORY_WINDOW_SESSIONS
        ),
        "market_bearish_turn_model_key": "bearish_turn_risk_rules_v2",
    }
    for row in chart:
        row.update(defaults)

    group = market_group_for_ticker(ticker)
    if group is None or not chart:
        return
    required_tickers = {
        ticker,
        "QQQ",
        *group.benchmark_tickers,
    }
    bounded_histories = {
        symbol: histories[symbol]
        for symbol in required_tickers
        if symbol in histories
    }
    scores = build_group_score_frame(bounded_histories, group)
    if scores.empty or ticker not in scores.index.get_level_values("ticker"):
        return
    ticker_scores = scores.xs(ticker, level="ticker")
    by_date = {
        iso_date(timestamp): row
        for timestamp, row in ticker_scores.iterrows()
    }
    for chart_row in chart:
        score_row = by_date.get(chart_row["time"])
        if score_row is None:
            continue
        state = score_row["downside_risk_state"]
        chart_row.update(
            {
                "market_bearish_turn_raw_score": _optional_number(
                    score_row["downside_risk_score"]
                ),
                "market_bearish_turn_state_score": _optional_number(
                    score_row["downside_risk_state_score"]
                ),
                "market_bearish_turn_state": (
                    str(state) if isinstance(state, str) else "unavailable"
                ),
                "market_bearish_turn_memory_age_sessions": (
                    int(score_row["downside_risk_memory_age_sessions"])
                    if _finite_number(
                        score_row["downside_risk_memory_age_sessions"]
                    )
                    else None
                ),
            }
        )


def _snapshot_dict(snapshot):
    return snapshot.to_dict() if hasattr(snapshot, "to_dict") else snapshot


def _stock_summary(chart, ticker_summary):
    latest = chart[-1]
    return {
        "close": latest["close"],
        "daily_return": latest["daily_return"],
        "daily_return_unit": "fraction",
        "latest_date": latest["time"],
        "lag_days": None if ticker_summary is None else ticker_summary.lag_days,
        "inactive": False if ticker_summary is None else ticker_summary.inactive,
        "stale": (
            False
            if ticker_summary is None
            else not ticker_summary.inactive and ticker_summary.lag_days > 0
        ),
    }


def _top_risk_payload(
    forecast_service,
    forecast_arguments,
    expected_revision,
    *,
    update_in_progress=False,
):
    if update_in_progress:
        return unavailable_top_risk_timeline("update_in_progress")
    builder = getattr(forecast_service, "build_top_risk_timeline", None)
    if not callable(builder):
        return unavailable_top_risk_timeline("service_unsupported")
    try:
        if expected_revision is None:
            return builder(*forecast_arguments)
        return builder(
            *forecast_arguments,
            expected_revision=expected_revision,
        )
    except ForecastRevisionChanged:
        return unavailable_top_risk_timeline("update_in_progress")
    except Exception:
        return unavailable_top_risk_timeline("model_error")


def _structure_payload(factors, chart, top_risk=None):
    by_key = {factor["key"]: factor for factor in factors}
    latest = chart[-1]
    strict_vcp = latest.get("strict_vcp_evidence")
    tight_platform = latest.get("tight_platform_evidence")
    if not isinstance(strict_vcp, dict):
        strict_vcp = by_key.get("strict_vcp", {}).get("raw_value")
    if not isinstance(tight_platform, dict):
        tight_platform = by_key.get("tight_platform", {}).get("raw_value")
    strict_vcp = dict(strict_vcp) if isinstance(strict_vcp, dict) else None
    tight_platform = (
        dict(tight_platform) if isinstance(tight_platform, dict) else None
    )
    if strict_vcp is not None:
        strict_vcp.setdefault(
            "rejection_reason_code",
            strict_vcp.get("reject_reason"),
        )
    if tight_platform is not None:
        tight_platform.setdefault(
            "rejection_reason_code",
            tight_platform.get("reject_reason") or tight_platform.get("reason"),
        )
    strict_vcp_pivot = (
        latest.get("strict_vcp_pivot")
        if latest.get("strict_vcp_active") is True
        else None
    )
    tight_platform_pivot = (
        latest.get("tight_platform_pivot")
        if latest.get("tight_platform_active") is True
        else None
    )
    if "strict_vcp_active" not in latest:
        strict_vcp_pivot = (
            strict_vcp.get("vcp_pivot")
            if strict_vcp and strict_vcp.get("reject_reason") is None
            else None
        )
    if "tight_platform_active" not in latest:
        tight_platform_pivot = (
            tight_platform.get("platform_pivot")
            if tight_platform and tight_platform.get("is_platform")
            else None
        )
    annotations = []
    entry_annotations = (
        (
            "strict_vcp_start",
            "strict_vcp_start",
            "Strict VCP setup detected",
        ),
        (
            "vcp_breakout_confirmed",
            "vcp_breakout_confirmed",
            "VCP breakout confirmed",
        ),
        ("pocket_pivot", "pocket_pivot", "Pocket Pivot"),
    )
    if any(field in latest for field, _, _ in entry_annotations):
        for row in chart:
            for field, annotation_type, label in entry_annotations:
                if row.get(field) is True:
                    annotations.append(
                        {
                            "time": row["time"],
                            "type": annotation_type,
                            "label": label,
                        }
                    )
    else:
        if _finite_number(strict_vcp_pivot):
            annotations.append(
                {
                    "time": latest["time"],
                    "type": "strict_vcp",
                    "label": "Bullish breakout setup (Strict VCP)",
                }
            )
        if _finite_number(tight_platform_pivot):
            annotations.append(
                {
                    "time": latest["time"],
                    "type": "tight_platform",
                    "label": "Bullish breakout setup (tight platform)",
                }
            )
    annotations.extend(_top_risk_annotations(top_risk, chart))
    return {
        "strict_vcp": strict_vcp,
        "tight_platform": tight_platform,
        "annotations": annotations,
        "key_levels": {
            "pivot": latest["pivot"],
            "strict_vcp_pivot": strict_vcp_pivot,
            "tight_platform_pivot": tight_platform_pivot,
            "pivot_distance_pct": latest.get("pivot_distance_pct"),
            "pivot_distance_change_pct": latest.get(
                "pivot_distance_change_pct"
            ),
            "ema20": latest.get("ema20"),
            "sma50": latest.get("sma50"),
            "sma200": latest.get("sma200"),
            "atr20": latest.get("atr20"),
        },
    }


def _top_risk_annotations(top_risk, chart):
    if not isinstance(top_risk, dict) or top_risk.get("status") != "available":
        return []
    chart_dates = {
        row.get("time")
        for row in chart
        if isinstance(row, dict) and isinstance(row.get("time"), str)
    }
    labels = {
        "top_risk_watch": "Top downside risk watch",
        "top_risk_high": "Top downside risk high",
        "top_risk_confirmed": "Top downside risk confirmed",
        "top_risk_recovery": "Top downside risk cleared",
    }
    annotations = []
    for event in top_risk.get("events", ()):
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        event_time = event.get("time")
        if event_type not in labels or event_time not in chart_dates:
            continue
        annotations.append(
            {
                "time": event_time,
                "type": event_type,
                "label": labels[event_type],
                "score": _optional_number(event.get("score")),
                "state": event.get("state"),
            }
        )
    return annotations


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
