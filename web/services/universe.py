"""Lightweight universe summaries with revision-scoped bounded caching."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import math
from numbers import Real
from threading import RLock

import pandas as pd

from factors.compute import tight_platform
from research.canslim_technical import (
    evaluate_technical_gate,
    unavailable_technical_gate,
)
from research.market_gate import latest_market_gate
from research.vcp import detect_vcp
from web.factors.registry import FactorRegistry
from web.services.analysis import AnalysisContext


UNIVERSE_FACTOR_KEYS = ("mom_12_1", "realized_vol_63")
UNIVERSE_MOMENTUM_FACTOR_KEY = "mom_12_1"
UNIVERSE_VOLATILITY_FACTOR_KEY = "realized_vol_63"
UNIVERSE_ALGORITHM_VERSION = "universe_summary_v6"


class UniverseSnapshotService:
    """Build and cache the inexpensive stock-picker summary payload."""

    def __init__(
        self,
        repository,
        factor_registry,
        classification_service=None,
        group_assignment_repository=None,
        relative_strength_service=None,
        research_universe_repository=None,
        technical_gate_evaluator=evaluate_technical_gate,
        revision_getter=lambda: 0,
        max_cache_size=4,
    ):
        if not callable(revision_getter):
            raise TypeError("revision_getter must be callable")
        if isinstance(max_cache_size, bool) or not isinstance(max_cache_size, int):
            raise TypeError("max_cache_size must be an integer")
        if max_cache_size <= 0:
            raise ValueError("max_cache_size must be positive")
        self._repository = repository
        self._factor_registry = factor_registry
        self._classification_service = classification_service
        self._group_assignment_repository = group_assignment_repository
        self._relative_strength_service = relative_strength_service
        self._research_universe_repository = research_universe_repository
        self._technical_gate_evaluator = technical_gate_evaluator
        self._revision_getter = revision_getter
        self._max_cache_size = max_cache_size
        self._cache = OrderedDict()
        self._lock = RLock()

    @property
    def cache_size(self):
        with self._lock:
            return len(self._cache)

    def build(self):
        freshness = self._repository.freshness()
        asof = freshness.get("latest_date")
        revision = int(self._revision_getter())
        research_revision = (
            self._research_universe_repository.revision()
            if self._research_universe_repository is not None
            else None
        )
        key = (
            revision,
            research_revision,
            asof,
            UNIVERSE_ALGORITHM_VERSION,
        )

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return deepcopy(cached)

            summaries = self._repository.list_summaries()
            histories = self._repository.load_universe_histories(
                None if asof is None else pd.Timestamp(asof)
            )
            rows = build_universe_rows(
                summaries,
                histories,
                self._factor_registry,
            )
            market_gate = latest_market_gate(histories)
            research_snapshot = self._build_research_snapshot(asof)
            rows, pool_summary = merge_research_pool(
                rows,
                histories,
                research_snapshot,
                asof,
                self._technical_gate_evaluator,
            )
            tickers = [row["ticker"] for row in rows]
            classifications = self._build_classifications(tickers, asof)
            merge_sector_classifications(rows, classifications)
            assignments = self._build_group_assignments(tickers, asof)
            if assignments is not None:
                merge_group_assignments(rows, assignments)
                merge_group_assignment_summary(classifications, assignments)
            relative_strength = self._build_relative_strength(
                tickers
            )
            merge_relative_strength(rows, relative_strength)
            for row in rows:
                row["market_gate_state"] = market_gate["state"]
                technical_state = row.get("technical_gate", {}).get("state")
                row["formal_candidate_state"] = (
                    "pass"
                    if technical_state == "pass"
                    and market_gate["state"] == "pass"
                    else "missing"
                    if technical_state == "missing"
                    or market_gate["state"] == "missing"
                    else "fail"
                )
            payload = {
                "asof": asof,
                "freshness": freshness,
                "tickers": rows,
                "pool_summary": pool_summary,
                "research_pool_status": _research_status(research_snapshot),
                "market_gate": market_gate,
                "factor_groups": factor_groups(self._factor_registry),
                "classification_summary": {
                    key: deepcopy(value)
                    for key, value in classifications.items()
                    if key != "by_ticker"
                },
                "relative_strength_summary": {
                    key: deepcopy(value)
                    for key, value in relative_strength.items()
                    if key != "by_ticker"
                },
            }
            self._cache[key] = deepcopy(payload)
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cache_size:
                self._cache.popitem(last=False)
            return deepcopy(payload)

    def _build_research_snapshot(self, asof):
        if self._research_universe_repository is None:
            return None
        try:
            return self._research_universe_repository.snapshot(asof, sessions=260)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def _build_classifications(self, tickers, asof):
        if self._classification_service is None:
            return _unavailable_classifications(tickers)
        try:
            return self._classification_service.build(tickers, asof=asof)
        except (OSError, RuntimeError, TypeError, ValueError):
            return _unavailable_classifications(tickers)

    def _build_group_assignments(self, tickers, asof):
        if self._group_assignment_repository is None:
            return None
        try:
            payload = self._group_assignment_repository.build(
                tickers,
                asof=asof,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            return _unavailable_group_assignments(tickers, asof)
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("by_ticker"), dict)
        ):
            return _unavailable_group_assignments(tickers, asof)
        return payload

    def _build_relative_strength(self, tickers):
        if self._relative_strength_service is None:
            return _unavailable_relative_strength(tickers)
        try:
            return self._relative_strength_service.build(tickers)
        except (OSError, RuntimeError, TypeError, ValueError):
            return _unavailable_relative_strength(tickers)


def build_universe_rows(summaries, histories, registry):
    """Build lightweight diagnostics at each ticker's real last bar."""
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
        momentum = _percentile_0_100(
            results.get(UNIVERSE_MOMENTUM_FACTOR_KEY)
        )
        volatility = _annualized_percent(
            results.get(UNIVERSE_VOLATILITY_FACTOR_KEY)
        )
        inactive = bool(summary.inactive)
        stale = not inactive and summary.lag_days > 0
        structure = build_structure_summary(histories.get(summary.ticker))
        row = _summary_dict(summary)
        row.update(
            {
                "fresh": not inactive and summary.lag_days == 0,
                "stale": stale,
                "data_status": (
                    "inactive" if inactive else "stale" if stale else "current"
                ),
                **structure,
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


def build_structure_summary(history):
    """Return the latest lightweight structure state used by universe filters."""
    if history is None or history.empty:
        return {
            "strict_vcp": None,
            "tight_platform": None,
            "near_pivot": None,
            "shape_state": "unavailable",
        }

    try:
        pattern = detect_vcp(history)
        platform = tight_platform(history)
    except (KeyError, TypeError, ValueError):
        return {
            "strict_vcp": None,
            "tight_platform": None,
            "near_pivot": None,
            "shape_state": "unavailable",
        }

    strict_vcp = bool(
        pattern.accepted
        and (
            pattern.distance_to_pivot_pct is None
            or pattern.distance_to_pivot_pct <= 0.0
        )
    )
    platform_active = bool(platform.get("is_platform"))
    near_pivot = bool(strict_vcp and pattern.stage == "near_pivot")
    if near_pivot:
        shape_state = "near_pivot"
    elif strict_vcp:
        shape_state = "strict_vcp"
    elif platform_active:
        shape_state = "tight_platform"
    else:
        shape_state = "none"
    return {
        "strict_vcp": strict_vcp,
        "tight_platform": platform_active,
        "near_pivot": near_pivot,
        "shape_state": shape_state,
    }


def merge_research_pool(
    active_rows,
    active_histories,
    research_snapshot,
    asof,
    technical_gate_evaluator=evaluate_technical_gate,
):
    """Merge lightweight research diagnostics without running active-only models."""
    by_ticker = {row["ticker"]: row for row in active_rows}
    for row in active_rows:
        row["pool_membership"] = {"active": True, "research": False}
        history = active_histories.get(row["ticker"])
        row["technical_gate"] = _evaluate_gate(
            technical_gate_evaluator,
            history,
            asof,
            stale=row.get("data_status") != "current",
        )

    research_members = ()
    research_histories = {}
    if research_snapshot is not None and research_snapshot.status == "available":
        research_members = research_snapshot.members
        research_histories = research_snapshot.histories

    research_tickers = {member.ticker for member in research_members}
    overlap_count = len(research_tickers.intersection(by_ticker))
    for member in research_members:
        existing = by_ticker.get(member.ticker)
        if existing is not None:
            existing["pool_membership"]["research"] = True
            if existing.get("data_status") != "current" and not member.stale:
                existing["technical_gate"] = _evaluate_gate(
                    technical_gate_evaluator,
                    research_histories.get(member.ticker),
                    asof,
                    stale=False,
                )
            continue
        history = research_histories.get(member.ticker)
        row = _research_only_row(
            member,
            history,
            asof,
            technical_gate_evaluator,
        )
        active_rows.append(row)
        by_ticker[member.ticker] = row

    active_rows.sort(key=lambda row: row["ticker"])
    return active_rows, {
        "active_count": sum(
            bool(row["pool_membership"]["active"]) for row in active_rows
        ),
        "research_count": len(research_tickers),
        "overlap_count": overlap_count,
    }


def _research_only_row(member, history, asof, technical_gate_evaluator):
    latest_date = member.latest_date
    lag_days = (
        max(
            0,
            (pd.Timestamp(asof) - pd.Timestamp(latest_date)).days,
        )
        if asof is not None and latest_date is not None
        else 0
    )
    stale = bool(member.stale)
    structure = build_structure_summary(history)
    return {
        "ticker": member.ticker,
        "latest_date": latest_date,
        "lag_days": lag_days,
        "inactive": False,
        "fresh": not stale,
        "stale": stale,
        "data_status": "stale" if stale else "current",
        "name": member.name,
        "exchange": member.exchange,
        **structure,
        "momentum_percentile": None,
        "momentum_factor_key": UNIVERSE_MOMENTUM_FACTOR_KEY,
        "momentum_percentile_unit": "percentile_0_100",
        "volatility": None,
        "volatility_factor_key": UNIVERSE_VOLATILITY_FACTOR_KEY,
        "volatility_unit": "annualized_percent",
        "pool_membership": {"active": False, "research": True},
        "technical_gate": _evaluate_gate(
            technical_gate_evaluator,
            history,
            asof,
            stale=stale,
        ),
    }


def _evaluate_gate(evaluator, history, asof, stale):
    if history is None or history.empty:
        return unavailable_technical_gate(asof, "history_unavailable")
    try:
        return evaluator(history, asof, stale=stale)
    except (KeyError, TypeError, ValueError):
        return unavailable_technical_gate(asof, "evaluation_failed")


def _research_status(snapshot):
    if snapshot is None:
        return {
            "status": "unavailable",
            "asof": None,
            "revision": None,
            "reason": "not_configured_or_unavailable",
        }
    return {
        "status": snapshot.status,
        "asof": snapshot.asof,
        "revision": snapshot.revision,
        "reason": snapshot.reason,
    }


def merge_sector_classifications(rows, payload):
    by_ticker = payload.get("by_ticker", {})
    for row in rows:
        classification = deepcopy(
            by_ticker.get(
                row["ticker"],
                {
                    "state": "unclassified",
                    "sec": None,
                    "market_behavior": None,
                },
            )
        )
        row["group_assignment"] = classification.pop(
            "group_assignment",
            _missing_group_assignment("assignment_repository_unavailable"),
        )
        row["sector_classification"] = classification
    return rows


def merge_group_assignments(rows, payload):
    by_ticker = payload.get("by_ticker", {})
    for row in rows:
        row["group_assignment"] = deepcopy(
            by_ticker.get(
                row["ticker"],
                _missing_group_assignment(
                    "no_assignment_effective_at_asof"
                ),
            )
        )
    return rows


def merge_group_assignment_summary(classifications, assignments):
    for target, source in (
        ("group_assignment_status", "status"),
        ("group_assignment_asof", "asof"),
        ("group_assignment_revision", "revision"),
        ("group_assignment_coverage", "coverage"),
        ("group_assignment_review_count", "review_count"),
    ):
        classifications[target] = deepcopy(assignments.get(source))
    return classifications


def merge_relative_strength(rows, payload):
    by_ticker = payload.get("by_ticker", {})
    for row in rows:
        rating = by_ticker.get(row["ticker"], {})
        row.update(
            {
                "rs_rating": rating.get("rs_rating"),
                "rs_asof": rating.get("rs_asof"),
                "rs_sample_count": rating.get("rs_sample_count"),
                "rs_model_version": rating.get("rs_model_version"),
            }
        )
    return rows


def _unavailable_classifications(tickers):
    return {
        "status": "unavailable",
        "asof": None,
        "research_universe_count": 0,
        "sector_counts": {},
        "by_ticker": {
            ticker: {
                "state": "unclassified",
                "sec": None,
                "market_behavior": None,
                "group_assignment": _missing_group_assignment(
                    "assignment_repository_unavailable"
                ),
            }
            for ticker in tickers
        },
    }


def _missing_group_assignment(reason):
    return {"state": "missing", "reason": reason}


def _unavailable_group_assignments(tickers, asof):
    return {
        "status": "unavailable",
        "asof": asof,
        "revision": None,
        "coverage": 0.0,
        "review_count": 0,
        "by_ticker": {
            ticker: _missing_group_assignment(
                "assignment_repository_unavailable"
            )
            for ticker in tickers
        },
    }


def _unavailable_relative_strength(tickers):
    return {
        "status": "unavailable",
        "asof": None,
        "sample_count": 0,
        "model_version": "cross_sectional_rs_v1",
        "by_ticker": {
            ticker: {
                "rs_rating": None,
                "rs_asof": None,
                "rs_sample_count": None,
                "rs_model_version": "cross_sectional_rs_v1",
            }
            for ticker in tickers
        },
    }


def factor_groups(registry):
    groups = getattr(registry, "groups", ())
    if groups:
        return [group.to_dict() for group in groups]
    return [
        {
            "key": key,
            "label": key.replace("_", " ").title(),
            "methodology": "Registered point-in-time factor diagnostics.",
            "overview": True,
            "i18n": {},
        }
        for key in dict.fromkeys(factor.group for factor in registry.factors)
    ]


def _summary_dict(summary):
    if is_dataclass(summary):
        return asdict(summary)
    return {
        "ticker": summary.ticker,
        "latest_date": summary.latest_date,
        "lag_days": summary.lag_days,
        "inactive": summary.inactive,
    }


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
