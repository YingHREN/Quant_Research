"""Local-only Flask application for the quant research dashboard.

Usage::

    python web/app.py
    # Open http://127.0.0.1:5000
"""

from __future__ import annotations

from collections.abc import Mapping
import inspect
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
from web.forecasts.feature_provenance import (
    default_feature_provenance_registry,
)
from web.forecasts.model_outputs import (
    build_model_outputs,
    default_model_output_registry,
)
from research.canslim_technical import evaluate_technical_gate
from research.expanded_market_data import ExpandedMarketDataRepository
from research.market_context import build_group_score_frame
from research.market_gate import build_market_gate_frame, latest_market_gate
from research.risk_memory import (
    RISK_MEMORY_HALF_LIFE_SESSIONS,
    RISK_MEMORY_WINDOW_SESSIONS,
)
from web.market_calendar import session_offset
from web.market_groups import (
    REFERENCE_TICKERS,
    market_group,
    market_group_for_ticker,
    resolved_market_groups,
)
from web.services.analysis import AnalysisContext
from web.services.forecasts import (
    ForecastRevisionChanged,
    ForecastService,
    unavailable_forecast_bundle,
)
from web.services.forecast_artifacts import ForecastArtifactStore
from web.services.forecast_warmup import ForecastCacheWarmer
from web.services.group_assignments import GroupAssignmentRepository
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
from web.services.macro_history import (
    MacroHistoryService,
    VALID_BENCHMARKS as VALID_MACRO_BENCHMARKS,
    VALID_RANGES as VALID_MACRO_RANGES,
)
from web.services.macro_risk import MacroRiskService
from web.services.universe import UniverseSnapshotService
from web.services.research_classification import ResearchClassificationService
from web.services.research_relative_strength import (
    ResearchRelativeStrengthService,
)
from web.services.research_universe import ResearchUniverseRepository
from web.services.research_universe import (
    InvalidResearchTicker,
    ResearchUniverseDataError,
    UnknownResearchTicker,
)
from web.services.research_pool import (
    InvalidResearchPoolTicker,
    ResearchPoolMembershipStore,
    apply_research_pool_membership,
    apply_stock_research_pool_membership,
    normalize_research_pool_ticker,
)
from web.services.supply_demand import attach_supply_demand_rows
from web.services.intraday import IntradaySnapshotService, IntradayStatusService
from web.services.intraday_subscriptions import (
    IntradaySubscriptionService,
    SubscriptionLimitExceeded,
)
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
        RESEARCH_DATABASE=os.fspath(PROJECT_ROOT / "data" / "research_prices.db"),
        RESEARCH_POOL_MEMBERSHIP_DATABASE=os.fspath(
            PROJECT_ROOT / "data" / "research_pool_membership.db"
        ),
        MACRO_DATABASE=os.fspath(PROJECT_ROOT / "data" / "macro_data.db"),
    )
    if config:
        flask_app.config.update(config)

    if repository is None:
        repository = MarketDataRepository(flask_app.config["MARKET_DATA_DATABASE"])
    group_assignment_repository = flask_app.config.get(
        "GROUP_ASSIGNMENT_REPOSITORY"
    )
    if group_assignment_repository is None:
        group_assignment_repository = GroupAssignmentRepository(
            flask_app.config["RESEARCH_DATABASE"]
        )
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
    research_forecast_service = flask_app.config.get(
        "RESEARCH_FORECAST_SERVICE"
    )
    if research_forecast_service is None:
        research_forecast_service = ForecastService(
            max_cache_size=flask_app.config.get(
                "RESEARCH_FORECAST_CACHE_SIZE",
                16,
            ),
            max_forecast_dates=1,
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
            ForecastCacheWarmer(
                repository,
                forecast_service,
                group_assignment_repository=group_assignment_repository,
            )
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
    macro_risk_service = flask_app.config.get("MACRO_RISK_SERVICE")
    if macro_risk_service is None:
        macro_risk_service = MacroRiskService(
            flask_app.config["MACRO_DATABASE"]
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
            macro_risk_service=macro_risk_service,
        )
    macro_history_service = flask_app.config.get("MACRO_HISTORY_SERVICE")
    if macro_history_service is None:
        research_benchmark_repository = ExpandedMarketDataRepository(
            flask_app.config["RESEARCH_DATABASE"]
        )
        macro_history_service = MacroHistoryService(
            repository,
            macro_risk_service,
            revision_getter=lambda: getattr(
                forecast_service,
                "database_revision",
                0,
            ),
            benchmark_repository=research_benchmark_repository,
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
        classification_service = flask_app.config.get(
            "RESEARCH_CLASSIFICATION_SERVICE"
        )
        if classification_service is None:
            classification_service = ResearchClassificationService(
                flask_app.config["RESEARCH_DATABASE"],
                group_assignment_repository=group_assignment_repository,
            )
        relative_strength_service = flask_app.config.get(
            "RESEARCH_RELATIVE_STRENGTH_SERVICE"
        )
        if relative_strength_service is None:
            relative_strength_service = ResearchRelativeStrengthService(
                flask_app.config["RESEARCH_DATABASE"]
            )
        research_universe_repository = flask_app.config.get(
            "RESEARCH_UNIVERSE_REPOSITORY"
        )
        if research_universe_repository is None:
            research_universe_repository = ResearchUniverseRepository(
                flask_app.config["RESEARCH_DATABASE"]
            )
        universe_service = UniverseSnapshotService(
            repository,
            factor_registry,
            classification_service=classification_service,
            group_assignment_repository=group_assignment_repository,
            relative_strength_service=relative_strength_service,
            research_universe_repository=research_universe_repository,
            revision_getter=lambda: getattr(
                forecast_service,
                "database_revision",
                0,
            ),
            max_cache_size=flask_app.config.get("UNIVERSE_CACHE_SIZE", 4),
        )
    research_universe_repository = getattr(
        universe_service,
        "_research_universe_repository",
        None,
    )
    research_pool_membership_store = flask_app.config.get(
        "RESEARCH_POOL_MEMBERSHIP_STORE"
    )
    if research_pool_membership_store is None:
        research_pool_membership_store = ResearchPoolMembershipStore(
            None
            if flask_app.config.get("TESTING")
            else flask_app.config["RESEARCH_POOL_MEMBERSHIP_DATABASE"]
        )
    scenario_provider = flask_app.config.get("SCENARIO_PROVIDER")
    if scenario_provider is None:
        scenario_provider = HistoricalScenarioProvider()
    intraday_store = IntradayStore(flask_app.config["MARKET_DATA_DATABASE"])
    intraday_status_service = flask_app.config.get("INTRADAY_STATUS_SERVICE")
    if intraday_status_service is None:
        intraday_status_service = IntradayStatusService(
            store=intraday_store,
            stale_after_seconds=flask_app.config.get(
                "INTRADAY_STATUS_STALE_AFTER_SECONDS",
                30,
            ),
        )
    intraday_subscription_service = IntradaySubscriptionService(
        intraday_store,
        status_service=intraday_status_service,
    )
    intraday_snapshot_service = IntradaySnapshotService(
        intraday_store,
        intraday_subscription_service,
    )

    flask_app.extensions["dashboard_repository"] = repository
    flask_app.extensions["dashboard_update_manager"] = update_manager
    flask_app.extensions["dashboard_factor_registry"] = factor_registry
    flask_app.extensions["dashboard_universe_service"] = universe_service
    flask_app.extensions[
        "dashboard_research_classification_service"
    ] = getattr(universe_service, "_classification_service", None)
    flask_app.extensions[
        "dashboard_research_relative_strength_service"
    ] = getattr(universe_service, "_relative_strength_service", None)
    flask_app.extensions[
        "dashboard_research_universe_repository"
    ] = getattr(universe_service, "_research_universe_repository", None)
    flask_app.extensions[
        "dashboard_research_pool_membership_store"
    ] = research_pool_membership_store
    flask_app.extensions[
        "dashboard_group_assignment_repository"
    ] = group_assignment_repository
    flask_app.extensions["dashboard_scenario_provider"] = scenario_provider
    flask_app.extensions["dashboard_forecast_service"] = forecast_service
    flask_app.extensions[
        "dashboard_research_forecast_service"
    ] = research_forecast_service
    flask_app.extensions[
        "dashboard_entry_signal_service"
    ] = entry_signal_service
    flask_app.extensions["dashboard_intraday_status_service"] = intraday_status_service
    flask_app.extensions[
        "dashboard_intraday_subscription_service"
    ] = intraday_subscription_service
    flask_app.extensions[
        "dashboard_intraday_snapshot_service"
    ] = intraday_snapshot_service
    flask_app.extensions[
        "dashboard_market_overview_service"
    ] = market_overview_service
    flask_app.extensions["dashboard_macro_risk_service"] = macro_risk_service
    flask_app.extensions[
        "dashboard_macro_history_service"
    ] = macro_history_service

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

    @flask_app.get("/api/macro-history")
    def macro_history():
        range_key = request.args.get("range", "3y")
        if range_key not in VALID_MACRO_RANGES:
            return _safe_error(
                "invalid_macro_range",
                "Macro history range is invalid",
                400,
            )
        benchmark = request.args.get("benchmark", "SPY")
        if benchmark not in VALID_MACRO_BENCHMARKS:
            return _safe_error(
                "invalid_macro_benchmark",
                "Macro history benchmark is invalid",
                400,
            )
        payload = macro_history_service.build(
            asof=request.args.get("asof"),
            range_key=range_key,
            benchmark=benchmark,
        )
        return _json_response(payload)

    @flask_app.get("/api/universe")
    def universe():
        return _json_response(
            apply_research_pool_membership(
                universe_service.build(),
                research_pool_membership_store,
            )
        )

    @flask_app.route(
        "/api/research-pool/<path:ticker>",
        methods=("PUT", "DELETE"),
    )
    def research_pool_membership(ticker):
        try:
            normalized_ticker = normalize_research_pool_ticker(ticker)
        except InvalidResearchPoolTicker as error:
            raise InvalidTicker(str(error)) from error
        universe_payload = universe_service.build()
        available_tickers = {
            row.get("ticker")
            for row in universe_payload.get("tickers", ())
            if isinstance(row, dict)
        }
        if normalized_ticker not in available_tickers:
            raise UnknownTicker(f"Ticker was not found: {normalized_ticker}")
        included = request.method == "PUT"
        research_pool_membership_store.set_membership(
            normalized_ticker,
            included,
        )
        return _json_response(
            {
                "ticker": normalized_ticker,
                "research": included,
                "state": "included" if included else "excluded",
            }
        )

    @flask_app.get("/api/stocks/<path:ticker>")
    def stock(ticker):
        normalized_ticker = ticker.strip().upper()
        forecast_revision = getattr(forecast_service, "database_revision", None)
        try:
            snapshot = repository.load_analysis_snapshot(normalized_ticker)
        except UnknownTicker as active_error:
            if research_universe_repository is None:
                raise
            try:
                research_snapshot = (
                    research_universe_repository.load_detail_snapshot(
                        normalized_ticker,
                        benchmark_tickers=REFERENCE_TICKERS,
                    )
                )
            except InvalidResearchTicker as error:
                raise InvalidTicker(str(error)) from error
            except (UnknownResearchTicker, ResearchUniverseDataError):
                raise active_error
            research_member = research_pool_membership_store.resolve(
                normalized_ticker,
                default=False,
            )
            forecast_payload = None
            top_risk = None
            (
                research_assignments,
                research_assignment_revision,
            ) = _load_assignment_snapshot(
                group_assignment_repository,
                research_snapshot.histories,
                research_snapshot.asof,
            )
            (
                research_forecast_assignments,
                research_forecast_assignment_revision,
            ) = _load_assignment_history(
                group_assignment_repository,
                research_snapshot.histories,
                research_snapshot.asof,
                fallback_assignments=research_assignments,
                fallback_revision=research_assignment_revision,
            )
            if research_member:
                research_forecast_arguments = (
                    normalized_ticker,
                    tuple(
                        iso_date(timestamp)
                        for timestamp in research_snapshot.histories[
                            normalized_ticker
                        ].index
                    ),
                    research_snapshot.histories,
                )
                try:
                    forecast_payload = _call_forecast_builder(
                        research_forecast_service.build,
                        research_forecast_arguments,
                        research_forecast_assignments,
                        research_forecast_assignment_revision,
                    )
                except Exception as error:
                    flask_app.logger.exception(
                        "Research forecast service failed for %s",
                        normalized_ticker,
                        exc_info=error,
                    )
                    forecast_payload = unavailable_forecast_bundle(
                        getattr(
                            research_forecast_service,
                            "model_key",
                            "ridge_direction_v1",
                        ),
                        getattr(
                            research_forecast_service,
                            "model_version",
                            "v1",
                        ),
                    )
                _attach_forecast_target_dates(
                    forecast_payload,
                    research_snapshot.histories[normalized_ticker].index,
                )
                top_risk = _top_risk_payload(
                    research_forecast_service,
                    research_forecast_arguments,
                    None,
                    assignments=research_forecast_assignments,
                    assignment_revision=(
                        research_forecast_assignment_revision
                    ),
                )
            return _json_response(
                apply_stock_research_pool_membership(
                    _research_stock_payload(
                        normalized_ticker,
                        research_snapshot,
                        scenario_provider,
                        entry_signal_service,
                        forecast_payload=forecast_payload,
                        top_risk=top_risk,
                        research_member=research_member,
                        assignments=research_assignments,
                    ),
                    research_pool_membership_store,
                )
            )
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
        assignments, assignment_revision = _load_assignment_snapshot(
            group_assignment_repository,
            peer_histories,
            observation_date,
        )
        forecast_assignments, forecast_assignment_revision = (
            _load_assignment_history(
                group_assignment_repository,
                peer_histories,
                observation_date,
                fallback_assignments=assignments,
                fallback_revision=assignment_revision,
            )
        )

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
            assignments,
        )
        attach_supply_demand_rows(
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
        technical_gate = evaluate_technical_gate(
            history,
            observation_date,
            stale=bool(
                selected_summary is not None
                and (
                    selected_summary.inactive
                    or selected_summary.lag_days > 0
                )
            ),
        )
        chart[-1]["canslim_technical_gate"] = technical_gate
        market_gate = _attach_market_gate_rows(chart, peer_histories)

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
                forecast_payload = _call_forecast_builder(
                    forecast_service.build,
                    forecast_arguments,
                    forecast_assignments,
                    forecast_assignment_revision,
                )
            else:
                forecast_payload = _call_forecast_builder(
                    forecast_service.build,
                    forecast_arguments,
                    forecast_assignments,
                    forecast_assignment_revision,
                    forecast_revision,
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
        macro_risk_service.attach_chart_rows(
            chart,
            _forecast_observation_dates(forecast_payload),
        )
        _attach_technical_gate_rows(
            chart,
            history,
            _forecast_observation_dates(forecast_payload),
            stale_latest=technical_gate.get("state") == "missing",
        )
        top_risk = _top_risk_payload(
            forecast_service,
            forecast_arguments,
            forecast_revision,
            assignments=forecast_assignments,
            assignment_revision=forecast_assignment_revision,
            update_in_progress=(
                getattr(update_snapshot, "state", None) == "running"
            ),
        )
        structures = _structure_payload(factor_payload, chart, top_risk)
        _attach_model_outputs(forecast_payload, chart, structures)
        payload = {
            "ticker": normalized_ticker,
            "analysis_scope": "active_full",
            "pool_membership": {"active": True, "research": False},
            "observation_date": observation_date,
            "summary": _stock_summary(chart, selected_summary),
            "chart": chart,
            "structures": structures,
            "factors": factor_payload,
            "scenarios": scenario_provider.build(history, observation_timestamp),
            "warnings": warnings,
            "forecasts": forecast_payload["forecasts"],
            "forecast_evaluation": forecast_payload["forecast_evaluation"],
            "feature_provenance_registry": forecast_payload.get(
                "feature_provenance_registry"
            ),
            "model_output_registry": forecast_payload.get(
                "model_output_registry"
            ),
            "top_risk": top_risk,
            "technical_gate": technical_gate,
            "market_gate": market_gate,
            "group_assignment": _assignment_for_ticker(
                assignments,
                normalized_ticker,
            ),
        }
        return _json_response(
            apply_stock_research_pool_membership(
                payload,
                research_pool_membership_store,
            )
        )

    @flask_app.get("/api/stocks/<ticker>/forecasts/<forecast_date>")
    def historical_forecast(ticker, forecast_date):
        normalized_ticker = ticker.strip().upper()
        selected_forecast_service = forecast_service
        revision = getattr(selected_forecast_service, "database_revision", None)
        try:
            snapshot = repository.load_analysis_snapshot(normalized_ticker)
        except UnknownTicker as active_error:
            if (
                research_universe_repository is None
                or not research_pool_membership_store.resolve(
                    normalized_ticker,
                    default=False,
                )
            ):
                raise
            try:
                snapshot = research_universe_repository.load_detail_snapshot(
                    normalized_ticker,
                    benchmark_tickers=REFERENCE_TICKERS,
                )
            except InvalidResearchTicker as error:
                raise InvalidTicker(str(error)) from error
            except (UnknownResearchTicker, ResearchUniverseDataError):
                raise active_error
            selected_forecast_service = research_forecast_service
            revision = None
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
        assignments, assignment_revision = _load_assignment_snapshot(
            group_assignment_repository,
            snapshot.histories,
            forecast_date,
        )

        update_snapshot = update_manager.snapshot()
        if getattr(update_snapshot, "state", None) == "running":
            return _json_response(
                unavailable_forecast_bundle(
                    getattr(
                        selected_forecast_service,
                        "model_key",
                        "ridge_direction_v1",
                    ),
                    getattr(selected_forecast_service, "model_version", "v1"),
                    UnavailableReason.UPDATE_IN_PROGRESS,
                )
            )

        arguments = (
            normalized_ticker,
            (forecast_date,),
            snapshot.histories,
        )
        try:
            payload = _call_forecast_builder(
                selected_forecast_service.build,
                arguments,
                assignments,
                assignment_revision,
                revision,
            )
        except ForecastRevisionChanged:
            payload = unavailable_forecast_bundle(
                getattr(
                    selected_forecast_service,
                    "model_key",
                    "ridge_direction_v1",
                ),
                getattr(selected_forecast_service, "model_version", "v1"),
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
            assignments,
        )
        attach_supply_demand_rows(
            chart,
            normalized_ticker,
            snapshot.histories,
        )
        macro_risk_service.attach_chart_rows(
            chart,
            _forecast_observation_dates(payload),
        )
        _attach_technical_gate_rows(
            chart,
            history,
            _forecast_observation_dates(payload),
        )
        _attach_market_gate_rows(chart, snapshot.histories)
        _attach_model_outputs(payload, chart)
        return _json_response(payload)

    @flask_app.post("/api/update")
    def start_update():
        return _json_response(_snapshot_dict(update_manager.start()), status=202)

    @flask_app.get("/api/update/status")
    def update_status():
        return _json_response(_snapshot_dict(update_manager.snapshot()))

    @flask_app.get("/api/cache/status")
    def cache_status():
        status_builder = getattr(forecast_service, "cache_status", None)
        if not callable(status_builder):
            return _json_response(_unavailable_cache_status())
        try:
            payload = status_builder()
        except Exception as error:
            flask_app.logger.warning(
                "Forecast cache status is unavailable",
                exc_info=error,
            )
            payload = _unavailable_cache_status()
        if not isinstance(payload, dict):
            payload = _unavailable_cache_status()
        return _json_response(payload)

    @flask_app.get("/api/market-data/status")
    def market_data_status():
        return _json_response(intraday_status_service.snapshot())

    @flask_app.get("/api/market-data/subscriptions")
    def market_data_subscriptions():
        return _json_response(intraday_subscription_service.snapshot())

    @flask_app.put("/api/market-data/subscriptions")
    def replace_market_data_subscriptions():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("symbols"), list
        ):
            return _safe_error(
                "invalid_subscription_request",
                "symbols must be a JSON array",
                400,
            )
        try:
            result = intraday_subscription_service.replace(
                payload["symbols"]
            )
        except SubscriptionLimitExceeded:
            return _safe_error(
                "subscription_limit_exceeded",
                "At most 27 user symbols are supported",
                409,
            )
        except ValueError:
            return _safe_error(
                "invalid_subscription_request",
                "A subscription symbol is invalid",
                400,
            )
        return _json_response(result)

    @flask_app.get("/api/intraday/<ticker>")
    def intraday_snapshot(ticker):
        try:
            window = int(request.args.get("window", "120"))
            return _json_response(
                intraday_snapshot_service.snapshot(ticker, window)
            )
        except (TypeError, ValueError):
            return _safe_error(
                "invalid_intraday_request",
                "Ticker or window is invalid",
                400,
            )

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


def _unavailable_cache_status():
    return {
        "state": "unavailable",
        "entry_count": 0,
        "latest_created_at": None,
        "market_asof": None,
        "model_key": None,
        "model_version": None,
        "feature_version": None,
        "risk_context_version": None,
        "format_version": None,
        "size_bytes": 0,
        "last_access": "unavailable",
        "database_revision": 0,
        "memory_ready": False,
        "build_started_at": None,
        "build_finished_at": None,
    }


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


def _forecast_observation_dates(payload):
    return tuple(
        str(raw_date)
        for raw_date, horizons in (
            payload.get("forecasts", {}).get("by_date", {})
        ).items()
        if isinstance(horizons, dict)
    )


def _attach_technical_gate_rows(
    chart,
    history,
    observation_dates,
    *,
    stale_latest=False,
):
    rows = {
        row.get("time"): row
        for row in chart
        if isinstance(row, dict) and isinstance(row.get("time"), str)
    }
    latest_date = chart[-1]["time"] if chart else None
    for raw_date in set(observation_dates) | ({latest_date} if latest_date else set()):
        row = rows.get(raw_date)
        if row is None or "canslim_technical_gate" in row:
            continue
        row["canslim_technical_gate"] = evaluate_technical_gate(
            history,
            raw_date,
            stale=bool(stale_latest and raw_date == latest_date),
        )


def _attach_market_gate_rows(chart, histories):
    """Attach the same-date broad-market gate without altering forecasts."""
    frame = build_market_gate_frame(histories)
    rows = {
        row.get("time"): row
        for row in chart
        if isinstance(row, dict) and isinstance(row.get("time"), str)
    }
    for timestamp, gate_row in frame.iterrows():
        raw_date = timestamp.date().isoformat()
        row = rows.get(raw_date)
        if row is None:
            continue
        row["market_regime_gate"] = {
            "state": gate_row["gate_state"],
            "market_state": gate_row["market_state"],
            "state_start": gate_row["market_state_start"],
            "follow_through_date": gate_row["follow_through_date"],
            "rally_day_count": int(gate_row["rally_day_count"]),
            "distribution_days": int(gate_row["distribution_days"]),
            "breadth_above_ema20": _optional_number(
                gate_row["breadth_above_ema20"]
            ),
            "breadth_above_sma50": _optional_number(
                gate_row["breadth_above_sma50"]
            ),
            "reason_codes": list(gate_row["reason_codes"]),
            "version": gate_row["gate_version"],
        }
    return latest_market_gate(histories)


def _attach_model_outputs(payload, chart, structures=None):
    payload.setdefault(
        "feature_provenance_registry",
        default_feature_provenance_registry().public_contract(),
    )
    by_date = payload.get("forecasts", {}).get("by_date", {})
    evaluations = payload.get("forecast_evaluation", {})
    rows = {
        row.get("time"): row
        for row in chart
        if isinstance(row, dict) and isinstance(row.get("time"), str)
    }
    registry = default_model_output_registry().public_contract()
    for raw_date, horizons in by_date.items():
        if not isinstance(horizons, dict):
            continue
        row = dict(rows.get(raw_date, {}))
        for raw_horizon, forecast in horizons.items():
            if not isinstance(forecast, dict):
                continue
            outputs = build_model_outputs(
                forecast,
                row,
                evaluations.get(str(raw_horizon), {}),
            )
            current_registry = outputs.pop("registry")
            if current_registry != registry:
                raise ValueError("inconsistent model output registry")
            outputs["registry_ref"] = registry["version"]
            forecast["model_outputs"] = outputs
    payload["model_output_registry"] = registry


def _finite_number(value):
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _optional_number(value):
    return float(value) if _finite_number(value) else None


def _load_assignment_snapshot(repository, tickers, asof):
    normalized_tickers = tuple(
        sorted(
            {
                str(ticker).strip().upper()
                for ticker in tickers
                if str(ticker).strip()
            }
        )
    )
    builder = getattr(repository, "build", None)
    if not callable(builder):
        return None, None
    try:
        payload = builder(normalized_tickers, asof=asof)
    except Exception:
        return None, None
    if (
        not isinstance(payload, Mapping)
        or payload.get("status") != "available"
        or not isinstance(payload.get("by_ticker"), Mapping)
    ):
        return None, None
    return payload["by_ticker"], payload.get("revision")


def _load_assignment_history(
    repository,
    histories,
    asof,
    *,
    fallback_assignments,
    fallback_revision,
):
    builder = getattr(repository, "build_history", None)
    if not callable(builder):
        return fallback_assignments, fallback_revision
    normalized_tickers = tuple(
        sorted(
            {
                str(ticker).strip().upper()
                for ticker in histories
                if str(ticker).strip()
            }
        )
    )
    starts = [
        pd.Timestamp(history.index.min()).date().isoformat()
        for history in histories.values()
        if isinstance(history, pd.DataFrame)
        and not history.empty
        and isinstance(history.index, pd.DatetimeIndex)
    ]
    if not starts:
        return fallback_assignments, fallback_revision
    try:
        payload = builder(
            normalized_tickers,
            start_asof=min(starts),
            end_asof=asof,
        )
    except Exception:
        return fallback_assignments, fallback_revision
    if (
        not isinstance(payload, Mapping)
        or payload.get("status") != "available"
        or not isinstance(payload.get("by_ticker"), Mapping)
        or (
            fallback_revision is not None
            and payload.get("revision") != fallback_revision
        )
    ):
        return fallback_assignments, fallback_revision
    return payload["by_ticker"], payload.get("revision")


def _missing_group_assignment(reason):
    return {"state": "missing", "reason": reason}


def _assignment_for_ticker(assignments, ticker):
    if isinstance(assignments, Mapping):
        assignment = assignments.get(ticker)
        if isinstance(assignment, Mapping):
            return dict(assignment)
    return _missing_group_assignment("assignment_repository_unavailable")


def _assignment_options(assignments, assignment_revision):
    if assignments is None:
        return {}
    options = {"assignments": assignments}
    if assignment_revision is not None:
        options["assignment_revision"] = assignment_revision
    return options


def _call_forecast_builder(
    builder,
    arguments,
    assignments,
    assignment_revision,
    expected_revision=None,
):
    options = _assignment_options(assignments, assignment_revision)
    if expected_revision is not None:
        options["expected_revision"] = expected_revision
    try:
        parameters = inspect.signature(builder).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_keywords = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    supported = {
        key: value
        for key, value in options.items()
        if accepts_keywords or key in parameters
    }
    return builder(*arguments, **supported)


def _market_group_for_assignment(ticker, histories, assignments):
    if assignments is None:
        return market_group_for_ticker(ticker)
    normalized = str(ticker).strip().upper()
    return next(
        (
            group
            for group in resolved_market_groups(assignments, histories)
            if normalized in group.constituent_tickers
        ),
        None,
    )


def _attach_market_bearish_risk(
    chart,
    ticker,
    histories,
    assignments=None,
):
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

    group = _market_group_for_assignment(ticker, histories, assignments)
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


def _research_stock_payload(
    ticker,
    snapshot,
    scenario_provider,
    entry_signal_service,
    *,
    forecast_payload=None,
    top_risk=None,
    research_member=False,
    assignments=None,
):
    history = snapshot.histories[ticker].sort_index()
    if history.empty:
        raise UnknownTicker(f"Ticker was not found: {ticker}")
    observation_timestamp = pd.Timestamp(history.index[-1])
    observation_date = iso_date(observation_timestamp)
    benchmark = snapshot.histories.get("SPY")
    if benchmark is not None and benchmark.empty:
        benchmark = None
    context = AnalysisContext(
        ticker=ticker,
        observation_date=observation_timestamp,
        history=history,
        benchmark_history=benchmark,
    )
    chart = build_chart_rows(context)
    chart = merge_entry_signal_rows(
        chart,
        entry_signal_service.build(ticker, history),
    )
    technical_gate = evaluate_technical_gate(
        history,
        observation_date,
        stale=bool(snapshot.stale),
    )
    chart[-1]["canslim_technical_gate"] = technical_gate
    _attach_market_bearish_risk(
        chart,
        ticker,
        snapshot.histories,
        assignments,
    )
    attach_supply_demand_rows(chart, ticker, snapshot.histories)
    market_gate = _attach_market_gate_rows(chart, snapshot.histories)
    if forecast_payload is None:
        forecast_payload = unavailable_forecast_bundle(
            reason=UnavailableReason.RESEARCH_POOL_DIAGNOSTIC_ONLY
        )
    if top_risk is None:
        top_risk = unavailable_top_risk_timeline(
            (
                "service_unsupported"
                if research_member
                else "research_pool_diagnostic_only"
            )
        )
    structures = _structure_payload([], chart, top_risk)
    _attach_model_outputs(forecast_payload, chart, structures)
    summary = _stock_summary(chart, None)
    summary["stale"] = bool(snapshot.stale)
    warnings = [
        (
            "research_pool_on_demand_forecast"
            if research_member
            else "research_pool_limited_analysis"
        )
    ]
    if snapshot.stale:
        warnings.append("stale_ticker")
    if benchmark is None:
        warnings.append("missing_benchmark")
    if len(chart) < 200:
        warnings.append("insufficient_indicator_history")
    return {
        "ticker": ticker,
        "analysis_scope": (
            "research_on_demand"
            if research_member
            else "research_diagnostic"
        ),
        "pool_membership": {
            "active": False,
            "research": bool(research_member),
            "research_catalog": True,
        },
        "observation_date": observation_date,
        "summary": summary,
        "chart": chart,
        "structures": structures,
        "factors": [],
        "scenarios": scenario_provider.build(
            history,
            observation_timestamp,
        ),
        "warnings": warnings,
        "forecasts": forecast_payload["forecasts"],
        "forecast_evaluation": forecast_payload["forecast_evaluation"],
        "feature_provenance_registry": forecast_payload.get(
            "feature_provenance_registry"
        ),
        "model_output_registry": forecast_payload.get(
            "model_output_registry"
        ),
        "top_risk": top_risk,
        "technical_gate": technical_gate,
        "market_gate": market_gate,
        "group_assignment": _assignment_for_ticker(assignments, ticker),
    }


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
    assignments=None,
    assignment_revision=None,
    update_in_progress=False,
):
    if update_in_progress:
        return unavailable_top_risk_timeline("update_in_progress")
    builder = getattr(forecast_service, "build_top_risk_timeline", None)
    if not callable(builder):
        return unavailable_top_risk_timeline("service_unsupported")
    try:
        return _call_forecast_builder(
            builder,
            forecast_arguments,
            assignments,
            assignment_revision,
            expected_revision,
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
        (
            "tight_platform_start",
            "tight_platform",
            "Bullish breakout setup (tight platform)",
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
