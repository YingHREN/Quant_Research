"""Immutable contracts and policies for risk-adjusted forecast decisions."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import math
from numbers import Integral, Real

import pandas as pd

from research.market_context import build_group_score_frame
from web.contracts import json_safe
from web.forecasts.base import FORECAST_DIRECTIONS
from web.market_groups import modeled_market_groups


DECISION_RISK_STATES = frozenset(
    ("low", "watch", "high", "confirmed", "unavailable")
)
DECISION_ACTIONS = frozenset(
    ("retain", "downgrade_to_neutral", "override_to_down")
)
PERSISTENT_RISK_STATES = frozenset(
    ("new", "persistent", "fading", "inactive", "unavailable")
)
RISK_CONTEXT_COLUMNS = (
    "persistent_risk_raw_score",
    "persistent_risk_score",
    "persistent_risk_state",
    "persistent_risk_age_sessions",
)


@dataclass(frozen=True)
class ForecastDecision:
    """Auditable post-model direction decision and its risk provenance."""

    final_direction: str
    risk_state: str
    action: str
    reasons: tuple[str, ...]
    policy_key: str
    policy_version: str
    persistent_risk_score: float | None = None
    persistent_risk_raw_score: float | None = None
    persistent_risk_state: str = "unavailable"
    persistent_risk_age_sessions: int | None = None
    immediate_risk_score: float = 0.0

    def __post_init__(self):
        if self.final_direction not in FORECAST_DIRECTIONS - {"unavailable"}:
            raise ValueError("invalid final_direction")
        if self.risk_state not in DECISION_RISK_STATES:
            raise ValueError("invalid risk_state")
        if self.action not in DECISION_ACTIONS:
            raise ValueError("invalid decision action")
        reasons = tuple(self.reasons)
        if any(not isinstance(reason, str) or not reason for reason in reasons):
            raise ValueError("decision reasons must contain non-empty strings")
        policy_key = _required_string(self.policy_key, "policy_key")
        policy_version = _required_string(self.policy_version, "policy_version")
        persistent_score = _optional_score(
            self.persistent_risk_score,
            "persistent_risk_score",
        )
        persistent_raw_score = _optional_score(
            self.persistent_risk_raw_score,
            "persistent_risk_raw_score",
        )
        if self.persistent_risk_state not in PERSISTENT_RISK_STATES:
            raise ValueError("invalid persistent_risk_state")
        memory_age = self.persistent_risk_age_sessions
        if memory_age is not None:
            if isinstance(memory_age, bool) or not isinstance(memory_age, Integral):
                raise TypeError("persistent_risk_age_sessions must be an integer")
            if int(memory_age) < 0:
                raise ValueError(
                    "persistent_risk_age_sessions must not be negative"
                )
            memory_age = int(memory_age)
        immediate_score = _optional_score(
            self.immediate_risk_score,
            "immediate_risk_score",
        )
        if immediate_score is None:
            raise ValueError("immediate_risk_score is required")
        object.__setattr__(self, "reasons", reasons)
        object.__setattr__(self, "policy_key", policy_key)
        object.__setattr__(self, "policy_version", policy_version)
        object.__setattr__(self, "persistent_risk_score", persistent_score)
        object.__setattr__(
            self,
            "persistent_risk_raw_score",
            persistent_raw_score,
        )
        object.__setattr__(self, "persistent_risk_age_sessions", memory_age)
        object.__setattr__(self, "immediate_risk_score", immediate_score)

    def to_dict(self):
        return {
            "final_direction": self.final_direction,
            "risk_state": self.risk_state,
            "action": self.action,
            "reasons": list(self.reasons),
            "policy_key": self.policy_key,
            "policy_version": self.policy_version,
            "persistent_risk_score": json_safe(self.persistent_risk_score),
            "persistent_risk_raw_score": json_safe(
                self.persistent_risk_raw_score
            ),
            "persistent_risk_state": self.persistent_risk_state,
            "persistent_risk_age_sessions": self.persistent_risk_age_sessions,
            "immediate_risk_score": json_safe(self.immediate_risk_score),
        }


def build_forecast_risk_context(
    histories: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Return causal remembered-risk rows for explicitly modeled tickers."""
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    frames = []
    seen_tickers = set()
    for group in modeled_market_groups():
        mapped = tuple(
            dict.fromkeys(
                (*group.constituent_tickers, *group.related_tickers)
            )
        )
        duplicates = seen_tickers.intersection(mapped)
        if duplicates:
            raise ValueError(
                "duplicate modeled ticker membership: "
                + ", ".join(sorted(duplicates))
            )
        seen_tickers.update(mapped)
        scores = build_group_score_frame(histories, group)
        if scores.empty:
            continue
        present = scores.index.get_level_values("ticker").isin(mapped)
        selected = scores.loc[
            present,
            [
                "downside_risk_score",
                "downside_risk_state_score",
                "downside_risk_state",
                "downside_risk_memory_age_sessions",
            ],
        ].rename(
            columns={
                "downside_risk_score": "persistent_risk_raw_score",
                "downside_risk_state_score": "persistent_risk_score",
                "downside_risk_state": "persistent_risk_state",
                "downside_risk_memory_age_sessions": (
                    "persistent_risk_age_sessions"
                ),
            }
        )
        if not selected.empty:
            frames.append(selected)
    if not frames:
        index = pd.MultiIndex.from_arrays(
            [pd.Index([], dtype=object), pd.DatetimeIndex([])],
            names=("ticker", "observation_date"),
        )
        return pd.DataFrame(columns=RISK_CONTEXT_COLUMNS, index=index)
    result = pd.concat(frames).sort_index()
    if result.index.has_duplicates:
        raise ValueError("duplicate forecast risk context keys")
    return result.loc[:, RISK_CONTEXT_COLUMNS]


def _required_string(value, name):
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _optional_score(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number or None")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 100.0:
        raise ValueError(f"{name} must be between 0 and 100")
    return result
