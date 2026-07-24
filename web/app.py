"""Local-only Flask application for the quant research dashboard.

Usage::

    python web/app.py
    # Open http://127.0.0.1:5000
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
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
from web.factors.registry import FactorRegistry
from web.forecasts.base import UnavailableReason
from web.market_calendar import session_offset
from web.services.analysis import AnalysisContext
from web.services.forecasts import (
    ForecastRevisionChanged,
    ForecastService,
    unavailable_forecast_bundle,
)
from web.services.market_data import (
    InvalidTicker,
    MarketDataRepository,
    MarketDataUnavailable,
    UnknownTicker,
)
from web.services.intraday import IntradayStatusService
from web.services.scenarios import HistoricalScenarioProvider
from web.services.update_jobs import (
    PriceProvider,
    UpdateAlreadyRunning,
    UpdateJobManager,
)


DEFAULT_DATABASE = PROJECT_ROOT / "data" / "prices.db"
UNIVERSE_FACTOR_KEYS = (
    "strict_vcp",
    "tight_platform",
    "pivot_distance_pct",
    "mom_12_1",
    "realized_vol_63",
)
UNIVERSE_MOMENTUM_FACTOR_KEY = "mom_12_1"
UNIVERSE_VOLATILITY_FACTOR_KEY = "realized_vol_63"
NEAR_PIVOT_ABS_PCT = 5.0


def create_app(config=None, repository=None, update_manager=None) -> Flask:
    """Build the dashboard app with optional repository and job-manager fakes."""
    flask_app = Flask(__name__)
    flask_app.config.from_mapping(
        MARKET_DATA_DATABASE=os.fspath(DEFAULT_DATABASE),
    )
    if config:
        flask_app.config.update(config)

    if repository is None:
        repository = MarketDataRepository(flask_app.config["MARKET_DATA_DATABASE"])
    forecast_service = flask_app.config.get("FORECAST_SERVICE")
    if forecast_service is None:
        forecast_service = ForecastService(
            max_cache_size=flask_app.config.get("FORECAST_CACHE_SIZE", 16)
        )
    if update_manager is None:
        update_manager = UpdateJobManager(
            repository,
            PriceProvider(),
            on_success=getattr(forecast_service, "invalidate", None),
        )
    factor_registry = flask_app.config.get("FACTOR_REGISTRY")
    if factor_registry is None:
        factor_registry = build_default_registry()
    scenario_provider = flask_app.config.get("SCENARIO_PROVIDER")
    if scenario_provider is None:
        scenario_provider = HistoricalScenarioProvider()
    intraday_status_service = flask_app.config.get("INTRADAY_STATUS_SERVICE")
    if intraday_status_service is None:
        intraday_status_service = IntradayStatusService()

    flask_app.extensions["dashboard_repository"] = repository
    flask_app.extensions["dashboard_update_manager"] = update_manager
    flask_app.extensions["dashboard_factor_registry"] = factor_registry
    flask_app.extensions["dashboard_scenario_provider"] = scenario_provider
    flask_app.extensions["dashboard_forecast_service"] = forecast_service
    flask_app.extensions["dashboard_intraday_status_service"] = intraday_status_service

    @flask_app.get("/")
    def index():
        return render_template("index.html", rows=None, mkt_ok=None, query="")

    @flask_app.get("/api/universe")
    def universe():
        freshness = repository.freshness()
        summaries = repository.list_summaries()
        asof = freshness.get("latest_date")
        histories = repository.load_universe_histories(
            None if asof is None else pd.Timestamp(asof)
        )
        payload = {
            "asof": asof,
            "freshness": freshness,
            "tickers": _universe_rows(
                summaries, histories, asof, factor_registry
            ),
            "factor_groups": _factor_groups(factor_registry),
        }
        return _json_response(payload)

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
            context, peer_contexts
        )
        factor_payload = [result.to_dict() for result in factor_rows]
        chart = build_chart_rows(context)

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
        payload = {
            "ticker": normalized_ticker,
            "observation_date": observation_date,
            "summary": _stock_summary(chart, selected_summary),
            "chart": chart,
            "structures": _structure_payload(factor_payload, chart),
            "factors": factor_payload,
            "scenarios": scenario_provider.build(history, observation_timestamp),
            "warnings": warnings,
            "forecasts": forecast_payload["forecasts"],
            "forecast_evaluation": forecast_payload["forecast_evaluation"],
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


def _summary_dict(summary):
    if is_dataclass(summary):
        return asdict(summary)
    return {
        "ticker": summary.ticker,
        "latest_date": summary.latest_date,
        "lag_days": summary.lag_days,
        "inactive": summary.inactive,
    }


def _universe_rows(summaries, histories, asof, registry):
    """Build diagnostics at each ticker's real last bar from one bulk snapshot."""
    benchmark = histories.get("SPY")
    contexts = [
        AnalysisContext(
            ticker=summary.ticker,
            observation_date=pd.Timestamp(histories[summary.ticker].index[-1]),
            history=histories[summary.ticker],
            benchmark_history=benchmark,
        )
        for summary in summaries
        if summary.ticker in histories and not histories[summary.ticker].empty
    ]
    selected_factors = [
        factor for factor in registry.factors if factor.key in UNIVERSE_FACTOR_KEYS
    ]
    evaluated = FactorRegistry(selected_factors).evaluate_universe(contexts)

    rows = []
    for summary in summaries:
        results = {
            result.key: result for result in evaluated.get(summary.ticker, ())
        }
        strict_vcp = _strict_vcp_present(results.get("strict_vcp"))
        tight_platform = _tight_platform_present(results.get("tight_platform"))
        near_pivot = _near_pivot(results.get("pivot_distance_pct"))
        momentum = _percentile_0_100(results.get(UNIVERSE_MOMENTUM_FACTOR_KEY))
        volatility = _annualized_percent(results.get(UNIVERSE_VOLATILITY_FACTOR_KEY))
        inactive = bool(summary.inactive)
        stale = not inactive and summary.lag_days > 0

        row = _summary_dict(summary)
        row.update(
            {
                "fresh": not inactive and summary.lag_days == 0,
                "stale": stale,
                "data_status": "inactive" if inactive else "stale" if stale else "current",
                "strict_vcp": strict_vcp,
                "tight_platform": tight_platform,
                "near_pivot": near_pivot,
                "shape_state": _shape_state(strict_vcp, tight_platform, near_pivot),
                "momentum_percentile": momentum,
                "momentum_factor_key": UNIVERSE_MOMENTUM_FACTOR_KEY,
                "momentum_percentile_unit": "percentile_0_100",
                "volatility": volatility,
                "volatility_factor_key": UNIVERSE_VOLATILITY_FACTOR_KEY,
                "volatility_unit": "annualized_percent",
            }
        )
        rows.append(row)
    return rows


def _strict_vcp_present(result):
    if result is None or result.missing or not isinstance(result.raw_value, dict):
        return False
    return result.raw_value.get("reject_reason") is None


def _tight_platform_present(result):
    if result is None or result.missing or not isinstance(result.raw_value, dict):
        return False
    return bool(result.raw_value.get("is_platform"))


def _near_pivot(result):
    value = None if result is None or result.missing else result.raw_value
    return _finite_number(value) and abs(float(value)) <= NEAR_PIVOT_ABS_PCT


def _percentile_0_100(result):
    if result is None or result.percentile is None:
        return None
    return round(float(result.percentile) * 100, 2)


def _annualized_percent(result):
    value = None if result is None or result.missing else result.raw_value
    return round(float(value) * 100, 2) if _finite_number(value) else None


def _finite_number(value):
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _shape_state(strict_vcp, tight_platform, near_pivot):
    if strict_vcp:
        return "strict_vcp"
    if tight_platform:
        return "tight_platform"
    if near_pivot:
        return "near_pivot"
    return "none"


def _factor_groups(registry):
    groups = getattr(registry, "groups", ())
    if groups:
        return [group.to_dict() for group in groups]
    return [
        {
            "key": group,
            "label": group.replace("_", " ").title(),
            "methodology": "Point-in-time descriptive diagnostics.",
            "overview": False,
        }
        for group in dict.fromkeys(factor.group for factor in registry.factors)
    ]


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


def _structure_payload(factors, chart):
    by_key = {factor["key"]: factor for factor in factors}
    latest = chart[-1]
    strict_vcp = by_key.get("strict_vcp", {}).get("raw_value")
    tight_platform = by_key.get("tight_platform", {}).get("raw_value")
    strict_vcp = strict_vcp if isinstance(strict_vcp, dict) else None
    tight_platform = tight_platform if isinstance(tight_platform, dict) else None
    strict_vcp_pivot = (
        strict_vcp.get("vcp_pivot")
        if strict_vcp and strict_vcp.get("reject_reason") is None
        else None
    )
    tight_platform_pivot = (
        tight_platform.get("platform_pivot")
        if tight_platform and tight_platform.get("is_platform")
        else None
    )
    annotations = []
    if _finite_number(strict_vcp_pivot):
        annotations.append(
            {"time": latest["time"], "type": "strict_vcp", "label": "Strict VCP"}
        )
    if _finite_number(tight_platform_pivot):
        annotations.append(
            {
                "time": latest["time"],
                "type": "tight_platform",
                "label": "Tight platform",
            }
        )
    return {
        "strict_vcp": strict_vcp,
        "tight_platform": tight_platform,
        "annotations": annotations,
        "key_levels": {
            "pivot": latest["pivot"],
            "strict_vcp_pivot": strict_vcp_pivot,
            "tight_platform_pivot": tight_platform_pivot,
            "pivot_distance_pct": latest["pivot_distance_pct"],
            "pivot_distance_change_pct": latest["pivot_distance_change_pct"],
            "ema20": latest["ema20"],
            "sma50": latest["sma50"],
            "sma200": latest["sma200"],
            "atr20": latest["atr20"],
        },
    }


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
