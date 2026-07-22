"""Safe factor registration and same-date cross-sectional evaluation."""

from __future__ import annotations

from dataclasses import replace
import math
from numbers import Real

import pandas as pd

from web.contracts import iso_date
from web.factors.base import FactorDefinition, FactorResult
from web.services.analysis import AnalysisContext


MIN_PERCENTILE_PEERS = 5


class DuplicateFactorKey(ValueError):
    """Raised when registration would overwrite an existing factor."""


class FactorRegistry:
    """Ordered factor collection with isolated per-factor evaluation failures."""

    def __init__(self, factors=()):
        self._factors = {}
        for factor in factors:
            self.register(factor)

    @property
    def factors(self):
        return tuple(self._factors.values())

    def register(self, factor: FactorDefinition):
        if factor.key in self._factors:
            raise DuplicateFactorKey(f"Factor key already registered: {factor.key}")
        self._factors[factor.key] = factor
        return factor

    def evaluate_one(self, factor: FactorDefinition, context: AnalysisContext):
        """Evaluate one factor without exposing implementation failures to clients."""
        metadata = {
            "key": factor.key,
            "label": factor.label,
            "group": factor.group,
            "direction": factor.direction,
            "description": factor.description,
            "version": factor.version,
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
