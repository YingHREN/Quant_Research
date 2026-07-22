"""Local-only Flask application for the quant research dashboard.

Usage::

    python web/app.py
    # Open http://127.0.0.1:5000
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import os
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from web.contracts import ErrorPayload, iso_date, json_safe
from web.factors.builtin import build_chart_rows, build_default_registry
from web.services.analysis import AnalysisContext
from web.services.market_data import (
    InvalidTicker,
    MarketDataRepository,
    MarketDataUnavailable,
    UnknownTicker,
)
from web.services.scenarios import HistoricalScenarioProvider
from web.services.update_jobs import (
    PriceProvider,
    UpdateAlreadyRunning,
    UpdateJobManager,
)


DEFAULT_DATABASE = PROJECT_ROOT / "data" / "prices.db"


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
    if update_manager is None:
        update_manager = UpdateJobManager(repository, PriceProvider())
    factor_registry = flask_app.config.get("FACTOR_REGISTRY")
    if factor_registry is None:
        factor_registry = build_default_registry()
    scenario_provider = flask_app.config.get("SCENARIO_PROVIDER")
    if scenario_provider is None:
        scenario_provider = HistoricalScenarioProvider()

    flask_app.extensions["dashboard_repository"] = repository
    flask_app.extensions["dashboard_update_manager"] = update_manager
    flask_app.extensions["dashboard_factor_registry"] = factor_registry
    flask_app.extensions["dashboard_scenario_provider"] = scenario_provider

    @flask_app.get("/")
    def index():
        return render_template("index.html", rows=None, mkt_ok=None, query="")

    @flask_app.get("/api/universe")
    def universe():
        freshness = repository.freshness()
        summaries = repository.list_summaries()
        payload = {
            "asof": freshness.get("latest_date"),
            "freshness": freshness,
            "tickers": [_summary_dict(summary) for summary in summaries],
            "factor_groups": _factor_groups(factor_registry),
        }
        return _json_response(payload)

    @flask_app.get("/api/stocks/<path:ticker>")
    def stock(ticker):
        normalized_ticker = ticker.strip().upper()
        history = repository.load_history(normalized_ticker)
        if history.empty:
            raise MarketDataUnavailable()

        history = history.sort_index()
        observation_timestamp = pd.Timestamp(history.index[-1])
        observation_date = iso_date(observation_timestamp)
        summaries = repository.list_summaries()
        selected_summary = next(
            (
                summary
                for summary in summaries
                if summary.ticker == normalized_ticker
            ),
            None,
        )

        peer_histories = {normalized_ticker: history}
        for summary in summaries:
            if summary.ticker != normalized_ticker:
                peer_history = repository.load_history(
                    summary.ticker, asof=observation_timestamp
                )
                if not peer_history.empty:
                    peer_histories[summary.ticker] = peer_history.sort_index()

        warnings = []
        benchmark_history = peer_histories.get("SPY")
        if benchmark_history is None:
            try:
                benchmark_history = repository.load_history(
                    "SPY", asof=observation_timestamp
                ).sort_index()
                if benchmark_history.empty:
                    benchmark_history = None
            except UnknownTicker:
                benchmark_history = None
        if benchmark_history is None:
            warnings.append("missing_benchmark")

        contexts = [
            AnalysisContext(
                ticker=peer_ticker,
                observation_date=observation_timestamp,
                history=peer_history,
                benchmark_history=benchmark_history,
            )
            for peer_ticker, peer_history in peer_histories.items()
        ]
        context_by_ticker = {context.ticker: context for context in contexts}
        factor_rows = factor_registry.evaluate_universe(contexts)[normalized_ticker]
        factor_payload = [result.to_dict() for result in factor_rows]
        context = context_by_ticker[normalized_ticker]
        chart = build_chart_rows(context)

        if selected_summary is not None and selected_summary.inactive:
            warnings.append("inactive_ticker")
        if len(chart) < 200:
            warnings.append("insufficient_indicator_history")

        payload = {
            "ticker": normalized_ticker,
            "observation_date": observation_date,
            "summary": _stock_summary(chart, selected_summary),
            "chart": chart,
            "structures": _structure_payload(factor_payload, chart),
            "factors": factor_payload,
            "scenarios": scenario_provider.build(history, observation_timestamp),
            "warnings": warnings,
        }
        return _json_response(payload)

    @flask_app.post("/api/update")
    def start_update():
        return _json_response(_snapshot_dict(update_manager.start()), status=202)

    @flask_app.get("/api/update/status")
    def update_status():
        return _json_response(_snapshot_dict(update_manager.snapshot()))

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


def _summary_dict(summary):
    if is_dataclass(summary):
        return asdict(summary)
    return {
        "ticker": summary.ticker,
        "latest_date": summary.latest_date,
        "lag_days": summary.lag_days,
        "inactive": summary.inactive,
    }


def _factor_groups(registry):
    return list(dict.fromkeys(factor.group for factor in registry.factors))


def _snapshot_dict(snapshot):
    return snapshot.to_dict() if hasattr(snapshot, "to_dict") else snapshot


def _stock_summary(chart, ticker_summary):
    latest = chart[-1]
    return {
        "close": latest["close"],
        "daily_return": latest["daily_return"],
        "latest_date": latest["time"],
        "lag_days": None if ticker_summary is None else ticker_summary.lag_days,
        "inactive": False if ticker_summary is None else ticker_summary.inactive,
    }


def _structure_payload(factors, chart):
    by_key = {factor["key"]: factor for factor in factors}
    latest = chart[-1]
    return {
        "strict_vcp": by_key.get("strict_vcp", {}).get("raw_value"),
        "tight_platform": by_key.get("tight_platform", {}).get("raw_value"),
        "key_levels": {
            "pivot": latest["pivot"],
            "pivot_distance_pct": latest["pivot_distance_pct"],
            "ema20": latest["ema20"],
            "sma50": latest["sma50"],
            "sma200": latest["sma200"],
            "atr20": latest["atr20"],
        },
    }


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
