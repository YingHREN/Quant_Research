"""Safe factor registration and same-date cross-sectional evaluation."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
import math
from numbers import Real
from threading import RLock

import pandas as pd

from web.contracts import iso_date
from web.factors.base import FactorDefinition, FactorGroup, FactorResult
from web.services.analysis import AnalysisContext


MIN_PERCENTILE_PEERS = 5


class DuplicateFactorKey(ValueError):
    """Raised when registration would overwrite an existing factor."""


class FactorRegistry:
    """Ordered factor collection with isolated per-factor evaluation failures."""

    def __init__(
        self,
        factors=(),
        group_metadata=(),
        max_peer_cache_size=4096,
    ):
        if isinstance(max_peer_cache_size, bool) or not isinstance(
            max_peer_cache_size,
            int,
        ):
            raise TypeError("max_peer_cache_size must be an integer")
        if max_peer_cache_size <= 0:
            raise ValueError("max_peer_cache_size must be positive")
        self._factors = {}
        self._groups = {}
        self._max_peer_cache_size = max_peer_cache_size
        self._peer_cache = OrderedDict()
        self._peer_cache_lock = RLock()
        for group in group_metadata:
            metadata = group if isinstance(group, FactorGroup) else FactorGroup(**group)
            self._groups[metadata.key] = metadata
        for factor in factors:
            self.register(factor)

    @property
    def factors(self):
        return tuple(self._factors.values())

    @property
    def groups(self):
        return tuple(self._groups.values())

    @property
    def peer_cache_size(self):
        with self._peer_cache_lock:
            return len(self._peer_cache)

    def register(self, factor: FactorDefinition):
        if factor.key in self._factors:
            raise DuplicateFactorKey(f"Factor key already registered: {factor.key}")
        self._factors[factor.key] = factor
        if factor.group not in self._groups:
            self._groups[factor.group] = FactorGroup(
                key=factor.group,
                label=getattr(factor, "group_label", None) or _humanize(factor.group),
                methodology=(
                    getattr(factor, "group_methodology", None)
                    or "Point-in-time descriptive diagnostics from registered factors."
                ),
                overview=bool(getattr(factor, "overview", False)),
                i18n=getattr(factor, "group_i18n", None),
            )
        return factor

    def evaluate_one(self, factor: FactorDefinition, context: AnalysisContext):
        """Evaluate one factor without exposing implementation failures to clients."""
        metadata = {
            "key": factor.key,
            "label": factor.label,
            "group": factor.group,
            "direction": factor.direction,
            "description": factor.description,
            "methodology": getattr(factor, "methodology", factor.description),
            "overview": bool(getattr(factor, "overview", False)),
            "version": factor.version,
            "window": getattr(factor, "window", None),
            "i18n": getattr(factor, "i18n", None),
        }
        try:
            value = factor.compute(context)
            if _is_missing(value):
                return FactorResult(
                    raw_value=None,
                    formatted=None,
                    observation_date=context.observation_date,
                    missing=True,
                    missing_reason="missing_value",
                    **metadata,
                )
            return FactorResult(
                raw_value=value,
                formatted=factor.format(value),
                observation_date=context.observation_date,
                missing=False,
                missing_reason=None,
                **metadata,
            )
        except Exception:
            return FactorResult(
                raw_value=None,
                formatted=None,
                observation_date=context.observation_date,
                missing=True,
                missing_reason="factor_error",
                **metadata,
            )

    def evaluate_universe(self, contexts):
        """Evaluate all factors, then add percentiles from same-date peer groups."""
        rows = {
            context.ticker: [self.evaluate_one(factor, context) for factor in self.factors]
            for context in contexts
        }
        return self._add_percentiles(rows)

    def evaluate_selected_with_peers(
        self,
        selected_context,
        peer_contexts,
        cache_namespace=None,
    ):
        """Evaluate all selected factors and only eligible factors for exact-date peers."""
        results = [
            self.evaluate_one(factor, selected_context) for factor in self.factors
        ]
        selected_date = iso_date(selected_context.observation_date)
        exact_date_peers = [
            context
            for context in peer_contexts
            if context.ticker != selected_context.ticker
            and iso_date(context.observation_date) == selected_date
        ]

        for index, (factor, selected_result) in enumerate(zip(self.factors, results)):
            if (
                selected_result.missing
                or not _is_finite_number(selected_result.raw_value)
                or not getattr(factor, "percentile_eligible", True)
                or factor.direction not in {"higher", "lower"}
            ):
                continue
            values = [float(selected_result.raw_value)]
            for peer_context in exact_date_peers:
                peer_result = self._evaluate_peer(
                    factor,
                    peer_context,
                    cache_namespace,
                )
                if not peer_result.missing and _is_finite_number(peer_result.raw_value):
                    values.append(float(peer_result.raw_value))

            peer_count = len(values)
            results[index] = replace(selected_result, peer_count=peer_count)
            if peer_count < MIN_PERCENTILE_PEERS:
                continue
            percentile = float(
                pd.Series(values).rank(method="average", pct=True).iloc[0]
            )
            results[index] = replace(
                selected_result,
                peer_count=peer_count,
                percentile=percentile,
                display_score=_display_score(selected_result.direction, percentile),
            )
        return results

    def _evaluate_peer(self, factor, context, cache_namespace):
        if cache_namespace is None:
            return self.evaluate_one(factor, context)
        key = (
            cache_namespace,
            factor.key,
            factor.version,
            context.ticker,
            iso_date(context.observation_date),
        )
        with self._peer_cache_lock:
            cached = self._peer_cache.get(key)
            if cached is not None:
                self._peer_cache.move_to_end(key)
                return replace(cached)
            result = self.evaluate_one(factor, context)
            self._peer_cache[key] = replace(result)
            self._peer_cache.move_to_end(key)
            while len(self._peer_cache) > self._max_peer_cache_size:
                self._peer_cache.popitem(last=False)
            return replace(result)

    @staticmethod
    def _add_percentiles(rows):
        positions = {}
        for ticker, results in rows.items():
            for index, result in enumerate(results):
                if not result.missing and _is_finite_number(result.raw_value):
                    positions.setdefault((result.key, iso_date(result.observation_date)), []).append(
                        (ticker, index, float(result.raw_value))
                    )

        for peers in positions.values():
            peer_count = len(peers)
            for ticker, index, _ in peers:
                rows[ticker][index] = replace(
                    rows[ticker][index], peer_count=peer_count
                )
            if len(peers) < MIN_PERCENTILE_PEERS:
                continue
            values = pd.Series([value for _, _, value in peers])
            percentiles = values.rank(method="average", pct=True).tolist()
            for (ticker, index, _), percentile in zip(peers, percentiles):
                result = rows[ticker][index]
                rows[ticker][index] = replace(
                    result,
                    percentile=float(percentile),
                    display_score=_display_score(result.direction, float(percentile)),
                )
        return rows


def _is_missing(value):
    if value is None:
        return True
    missing = pd.isna(value)
    return isinstance(missing, bool) and missing


def _is_finite_number(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _display_score(direction, percentile):
    if direction == "higher":
        return percentile * 100
    if direction == "lower":
        return (1 - percentile) * 100
    return None


def _humanize(value):
    return str(value).replace("_", " ").title()
