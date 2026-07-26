"""Immutable contracts and policies for risk-adjusted forecast decisions."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
import math
from numbers import Integral, Real

import numpy as np
import pandas as pd

from research.market_context import build_group_score_frame
from research.group_regime import build_group_regime_state
from research.high_level_distribution import (
    build_high_level_distribution_state,
)
from research.slow_decline import build_slow_decline_state
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
    "persistent_risk_sources",
    "individual_risk_score",
    "group_risk_score",
    "slow_decline_risk_score",
    "high_level_distribution_score",
    "high_level_distribution_raw_score",
    "high_level_distribution_state",
    "high_level_distribution_raw_state",
    "high_level_distribution_age_sessions",
    "high_level_context_score",
    "distribution_pressure_score",
    "structure_damage_score",
    "high_level_distribution_conditions",
    "distribution_count_5",
    "distribution_count_10",
    "distribution_count_20",
    "churning_count_10",
    "churning_cluster",
    "climax_run_score",
    "climax_run_candidate",
    "climax_run_conditions",
    "top_risk_recovery",
    "top_risk_recovery_conditions",
)
PERSISTENT_RISK_SOURCES = frozenset(("individual", "group", "slow_decline"))
SOURCE_WATCH_THRESHOLDS = {
    "individual": 20.0,
    "group": 40.0,
    "slow_decline": 50.0,
}
SOURCE_HIGH_THRESHOLDS = {
    "individual": 30.0,
    "group": 60.0,
    "slow_decline": 70.0,
}


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
    persistent_risk_sources: tuple[str, ...] = ()
    individual_risk_score: float | None = None
    group_risk_score: float | None = None
    slow_decline_risk_score: float | None = None
    high_level_distribution_score: float | None = None
    high_level_distribution_raw_score: float | None = None
    high_level_distribution_state: str = "unavailable"
    high_level_distribution_raw_state: str = "unavailable"
    high_level_distribution_age_sessions: int | None = None
    high_level_context_score: float | None = None
    distribution_pressure_score: float | None = None
    structure_damage_score: float | None = None
    high_level_distribution_conditions: tuple[str, ...] = ()
    distribution_count_5: int | None = None
    distribution_count_10: int | None = None
    distribution_count_20: int | None = None
    churning_count_10: int | None = None
    churning_cluster: bool | None = None
    climax_run_score: float | None = None
    climax_run_candidate: bool | None = None
    climax_run_conditions: tuple[str, ...] = ()
    top_risk_recovery: bool | None = None
    top_risk_recovery_conditions: tuple[str, ...] = ()

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
        sources = tuple(self.persistent_risk_sources)
        if any(source not in PERSISTENT_RISK_SOURCES for source in sources):
            raise ValueError("invalid persistent_risk_sources")
        if len(sources) != len(set(sources)):
            raise ValueError("persistent_risk_sources must be unique")
        individual_score = _optional_score(
            self.individual_risk_score,
            "individual_risk_score",
        )
        group_score = _optional_score(
            self.group_risk_score,
            "group_risk_score",
        )
        slow_decline_score = _optional_score(
            self.slow_decline_risk_score,
            "slow_decline_risk_score",
        )
        top_score = _optional_score(
            self.high_level_distribution_score,
            "high_level_distribution_score",
        )
        top_raw_score = _optional_score(
            self.high_level_distribution_raw_score,
            "high_level_distribution_raw_score",
        )
        valid_top_states = {
            "low", "inactive", "watch", "high", "confirmed",
            "fading", "unavailable",
        }
        if self.high_level_distribution_state not in valid_top_states:
            raise ValueError("invalid high_level_distribution_state")
        if self.high_level_distribution_raw_state not in valid_top_states:
            raise ValueError("invalid high_level_distribution_raw_state")
        top_age = self.high_level_distribution_age_sessions
        if top_age is not None:
            if isinstance(top_age, bool) or not isinstance(top_age, Integral):
                raise TypeError(
                    "high_level_distribution_age_sessions must be an integer"
                )
            if int(top_age) < 0:
                raise ValueError(
                    "high_level_distribution_age_sessions must not be negative"
                )
            top_age = int(top_age)
        top_context = _optional_score(
            self.high_level_context_score,
            "high_level_context_score",
        )
        top_supply = _optional_score(
            self.distribution_pressure_score,
            "distribution_pressure_score",
        )
        top_structure = _optional_score(
            self.structure_damage_score,
            "structure_damage_score",
        )
        top_conditions = tuple(self.high_level_distribution_conditions)
        if any(
            not isinstance(condition, str) or not condition
            for condition in top_conditions
        ):
            raise ValueError(
                "high_level_distribution_conditions must contain strings"
            )
        distribution_counts = tuple(
            _optional_count(value, name)
            for value, name in (
                (self.distribution_count_5, "distribution_count_5"),
                (self.distribution_count_10, "distribution_count_10"),
                (self.distribution_count_20, "distribution_count_20"),
                (self.churning_count_10, "churning_count_10"),
            )
        )
        churning_cluster = _optional_boolean(
            self.churning_cluster,
            "churning_cluster",
        )
        climax_score = _optional_score(
            self.climax_run_score,
            "climax_run_score",
        )
        climax_candidate = _optional_boolean(
            self.climax_run_candidate,
            "climax_run_candidate",
        )
        climax_conditions = tuple(self.climax_run_conditions)
        if any(
            not isinstance(condition, str) or not condition
            for condition in climax_conditions
        ):
            raise ValueError("climax_run_conditions must contain strings")
        top_recovery = _optional_boolean(
            self.top_risk_recovery,
            "top_risk_recovery",
        )
        recovery_conditions = tuple(self.top_risk_recovery_conditions)
        if any(
            not isinstance(condition, str) or not condition
            for condition in recovery_conditions
        ):
            raise ValueError(
                "top_risk_recovery_conditions must contain strings"
            )
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
        object.__setattr__(self, "persistent_risk_sources", sources)
        object.__setattr__(self, "individual_risk_score", individual_score)
        object.__setattr__(self, "group_risk_score", group_score)
        object.__setattr__(
            self,
            "slow_decline_risk_score",
            slow_decline_score,
        )
        object.__setattr__(self, "high_level_distribution_score", top_score)
        object.__setattr__(
            self,
            "high_level_distribution_raw_score",
            top_raw_score,
        )
        object.__setattr__(
            self,
            "high_level_distribution_age_sessions",
            top_age,
        )
        object.__setattr__(self, "high_level_context_score", top_context)
        object.__setattr__(self, "distribution_pressure_score", top_supply)
        object.__setattr__(self, "structure_damage_score", top_structure)
        object.__setattr__(
            self,
            "high_level_distribution_conditions",
            top_conditions,
        )
        for name, value in zip(
            (
                "distribution_count_5",
                "distribution_count_10",
                "distribution_count_20",
                "churning_count_10",
            ),
            distribution_counts,
        ):
            object.__setattr__(self, name, value)
        object.__setattr__(self, "churning_cluster", churning_cluster)
        object.__setattr__(self, "climax_run_score", climax_score)
        object.__setattr__(
            self,
            "climax_run_candidate",
            climax_candidate,
        )
        object.__setattr__(
            self,
            "climax_run_conditions",
            climax_conditions,
        )
        object.__setattr__(self, "top_risk_recovery", top_recovery)
        object.__setattr__(
            self,
            "top_risk_recovery_conditions",
            recovery_conditions,
        )

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
            "persistent_risk_sources": list(self.persistent_risk_sources),
            "individual_risk_score": json_safe(self.individual_risk_score),
            "group_risk_score": json_safe(self.group_risk_score),
            "slow_decline_risk_score": json_safe(
                self.slow_decline_risk_score
            ),
            "high_level_distribution_score": json_safe(
                self.high_level_distribution_score
            ),
            "high_level_distribution_raw_score": json_safe(
                self.high_level_distribution_raw_score
            ),
            "high_level_distribution_state": (
                self.high_level_distribution_state
            ),
            "high_level_distribution_raw_state": (
                self.high_level_distribution_raw_state
            ),
            "high_level_distribution_age_sessions": (
                self.high_level_distribution_age_sessions
            ),
            "high_level_context_score": json_safe(
                self.high_level_context_score
            ),
            "distribution_pressure_score": json_safe(
                self.distribution_pressure_score
            ),
            "structure_damage_score": json_safe(
                self.structure_damage_score
            ),
            "high_level_distribution_conditions": list(
                self.high_level_distribution_conditions
            ),
            "distribution_count_5": self.distribution_count_5,
            "distribution_count_10": self.distribution_count_10,
            "distribution_count_20": self.distribution_count_20,
            "churning_count_10": self.churning_count_10,
            "churning_cluster": self.churning_cluster,
            "climax_run_score": json_safe(self.climax_run_score),
            "climax_run_candidate": self.climax_run_candidate,
            "climax_run_conditions": list(self.climax_run_conditions),
            "top_risk_recovery": self.top_risk_recovery,
            "top_risk_recovery_conditions": list(
                self.top_risk_recovery_conditions
            ),
        }


class ForecastDecisionPolicy:
    """Versioned asymmetric policy combining persistent and immediate risk."""

    policy_key = "forecast_decision_policy"

    def __init__(
        self,
        *,
        watch_threshold=20.0,
        high_threshold=30.0,
        immediate_confirm_threshold=70.0,
        joint_immediate_threshold=40.0,
        group_watch_threshold=SOURCE_WATCH_THRESHOLDS["group"],
        group_high_threshold=SOURCE_HIGH_THRESHOLDS["group"],
        slow_decline_watch_threshold=SOURCE_WATCH_THRESHOLDS["slow_decline"],
        slow_decline_high_threshold=SOURCE_HIGH_THRESHOLDS["slow_decline"],
        policy_version="v2",
    ):
        self.watch_threshold = _required_score(
            watch_threshold,
            "watch_threshold",
        )
        self.high_threshold = _required_score(
            high_threshold,
            "high_threshold",
        )
        self.immediate_confirm_threshold = _required_score(
            immediate_confirm_threshold,
            "immediate_confirm_threshold",
        )
        self.joint_immediate_threshold = _required_score(
            joint_immediate_threshold,
            "joint_immediate_threshold",
        )
        self.group_watch_threshold = _required_score(
            group_watch_threshold,
            "group_watch_threshold",
        )
        self.group_high_threshold = _required_score(
            group_high_threshold,
            "group_high_threshold",
        )
        self.slow_decline_watch_threshold = _required_score(
            slow_decline_watch_threshold,
            "slow_decline_watch_threshold",
        )
        self.slow_decline_high_threshold = _required_score(
            slow_decline_high_threshold,
            "slow_decline_high_threshold",
        )
        if self.watch_threshold > self.high_threshold:
            raise ValueError("watch_threshold must not exceed high_threshold")
        if self.joint_immediate_threshold > self.immediate_confirm_threshold:
            raise ValueError(
                "joint_immediate_threshold must not exceed "
                "immediate_confirm_threshold"
            )
        if self.group_watch_threshold > self.group_high_threshold:
            raise ValueError(
                "group_watch_threshold must not exceed group_high_threshold"
            )
        if (
            self.slow_decline_watch_threshold
            > self.slow_decline_high_threshold
        ):
            raise ValueError(
                "slow_decline_watch_threshold must not exceed "
                "slow_decline_high_threshold"
            )
        self.policy_version = _required_string(
            policy_version,
            "policy_version",
        )

    def decide(self, forecast, context_row):
        """Return a forecast with one auditable final decision attached."""
        from web.forecasts.base import ForecastResult

        if not isinstance(forecast, ForecastResult):
            raise TypeError("forecast must be a ForecastResult")
        if forecast.direction == "unavailable":
            return forecast

        persistent = _risk_context(context_row)
        immediate = float(forecast.bearish_turn_score)
        reasons = []
        final_direction = forecast.raw_direction
        risk_state = "unavailable" if persistent is None else "low"
        persistent_level = self._persistent_level(persistent)
        top_state = (
            "unavailable"
            if persistent is None
            else persistent["top_state"]
        )
        top_raw_state = (
            "unavailable"
            if persistent is None
            else persistent["top_raw_state"]
        )

        immediate_confirmed = immediate >= self.immediate_confirm_threshold
        top_confirmed = (
            top_state == "confirmed" and top_raw_state == "confirmed"
        )
        top_high = top_state in {"high", "fading", "confirmed"}
        confluence = (
            persistent is not None
            and persistent_level == "high"
            and immediate >= self.joint_immediate_threshold
        )
        if immediate_confirmed:
            risk_state = "confirmed"
            reasons.append("immediate_bearish_confirmation")
            final_direction = "down"
        elif top_confirmed:
            risk_state = "confirmed"
            reasons.extend(
                (
                    "high_level_distribution_risk",
                    "high_level_structure_damage_confirmation",
                )
            )
            final_direction = "down"
        elif confluence:
            risk_state = "confirmed"
            reasons.extend(
                (
                    "persistent_bearish_risk",
                    "persistent_immediate_confluence",
                )
            )
            final_direction = "down"
        elif persistent_level == "high":
            risk_state = "high"
            reasons.append("persistent_bearish_risk")
            if forecast.raw_direction == "up":
                final_direction = "neutral"
        elif top_high:
            risk_state = "high"
            reasons.append("high_level_distribution_risk")
            if forecast.raw_direction == "up":
                final_direction = "neutral"
        elif persistent_level == "watch":
            risk_state = "watch"
            reasons.append("persistent_bearish_risk")

        if persistent is not None and risk_state in {"watch", "high", "confirmed"}:
            reasons.extend(
                f"{source}_bearish_risk"
                for source in persistent["sources"]
            )

        if final_direction == forecast.raw_direction:
            action = "retain"
        elif final_direction == "neutral":
            action = "downgrade_to_neutral"
        else:
            action = "override_to_down"

        decision = ForecastDecision(
            final_direction=final_direction,
            risk_state=risk_state,
            action=action,
            reasons=tuple(reasons),
            policy_key=self.policy_key,
            policy_version=self.policy_version,
            persistent_risk_score=(
                None if persistent is None else persistent["score"]
            ),
            persistent_risk_raw_score=(
                None if persistent is None else persistent["raw_score"]
            ),
            persistent_risk_state=(
                "unavailable" if persistent is None else persistent["state"]
            ),
            persistent_risk_age_sessions=(
                None if persistent is None else persistent["age"]
            ),
            immediate_risk_score=immediate,
            persistent_risk_sources=(
                () if persistent is None else persistent["sources"]
            ),
            individual_risk_score=(
                None if persistent is None else persistent["individual_score"]
            ),
            group_risk_score=(
                None if persistent is None else persistent["group_score"]
            ),
            slow_decline_risk_score=(
                None
                if persistent is None
                else persistent["slow_decline_score"]
            ),
            high_level_distribution_score=(
                None if persistent is None else persistent["top_score"]
            ),
            high_level_distribution_raw_score=(
                None if persistent is None else persistent["top_raw_score"]
            ),
            high_level_distribution_state=top_state,
            high_level_distribution_raw_state=top_raw_state,
            high_level_distribution_age_sessions=(
                None if persistent is None else persistent["top_age"]
            ),
            high_level_context_score=(
                None if persistent is None else persistent["top_context"]
            ),
            distribution_pressure_score=(
                None if persistent is None else persistent["top_supply"]
            ),
            structure_damage_score=(
                None if persistent is None else persistent["top_structure"]
            ),
            high_level_distribution_conditions=(
                () if persistent is None else persistent["top_conditions"]
            ),
            distribution_count_5=(
                None if persistent is None else persistent["distribution_5"]
            ),
            distribution_count_10=(
                None if persistent is None else persistent["distribution_10"]
            ),
            distribution_count_20=(
                None if persistent is None else persistent["distribution_20"]
            ),
            churning_count_10=(
                None if persistent is None else persistent["churning_10"]
            ),
            churning_cluster=(
                None if persistent is None else persistent["churning_cluster"]
            ),
            climax_run_score=(
                None if persistent is None else persistent["climax_score"]
            ),
            climax_run_candidate=(
                None if persistent is None else persistent["climax_candidate"]
            ),
            climax_run_conditions=(
                () if persistent is None else persistent["climax_conditions"]
            ),
            top_risk_recovery=(
                None if persistent is None else persistent["top_recovery"]
            ),
            top_risk_recovery_conditions=(
                ()
                if persistent is None
                else persistent["top_recovery_conditions"]
            ),
        )
        return forecast.with_decision(decision)

    def _persistent_level(self, persistent):
        if persistent is None:
            return "unavailable"
        scores = (
            (
                persistent["individual_score"],
                self.watch_threshold,
                self.high_threshold,
            ),
            (
                persistent["group_score"],
                self.group_watch_threshold,
                self.group_high_threshold,
            ),
            (
                persistent["slow_decline_score"],
                self.slow_decline_watch_threshold,
                self.slow_decline_high_threshold,
            ),
        )
        available = False
        watch = False
        for score, watch_threshold, high_threshold in scores:
            if score is None:
                continue
            available = True
            if score >= high_threshold:
                return "high"
            if score >= watch_threshold:
                watch = True
        if not available:
            if persistent["score"] >= self.high_threshold:
                return "high"
            if persistent["score"] >= self.watch_threshold:
                return "watch"
            return "low"
        return "watch" if watch else "low"


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
                "downside_risk_score": "individual_risk_raw_score",
                "downside_risk_state_score": "individual_risk_score",
                "downside_risk_state": "individual_risk_state",
                "downside_risk_memory_age_sessions": "individual_risk_age",
            }
        )
        selected = _attach_additional_risk_sources(
            selected,
            histories,
            group,
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


def _attach_additional_risk_sources(selected, histories, group):
    group_state = build_group_regime_state(histories, group)
    slow_state = build_slow_decline_state(histories, group)
    top_state = _build_high_level_states(histories, group, group_state)
    dates = selected.index.get_level_values("observation_date")
    selected["group_risk_raw_score"] = group_state["raw_score"].reindex(
        dates
    ).to_numpy()
    selected["group_risk_score"] = group_state["state_score"].reindex(
        dates
    ).to_numpy()
    selected["group_risk_state"] = group_state["state"].reindex(
        dates
    ).to_numpy()
    selected["group_risk_age"] = group_state[
        "memory_age_sessions"
    ].reindex(dates).to_numpy()
    aligned_slow = slow_state.reindex(selected.index)
    selected["slow_decline_risk_raw_score"] = aligned_slow["raw_score"]
    selected["slow_decline_risk_score"] = aligned_slow["state_score"]
    selected["slow_decline_risk_state"] = aligned_slow["state"]
    selected["slow_decline_risk_age"] = aligned_slow[
        "memory_age_sessions"
    ]
    aligned_top = top_state.reindex(selected.index)
    selected["high_level_distribution_score"] = aligned_top[
        "high_level_distribution_state_score"
    ]
    selected["high_level_distribution_raw_score"] = aligned_top[
        "high_level_distribution_raw_score"
    ]
    selected["high_level_distribution_state"] = aligned_top[
        "high_level_distribution_state"
    ]
    selected["high_level_distribution_raw_state"] = aligned_top[
        "high_level_distribution_raw_state"
    ]
    selected["high_level_distribution_age_sessions"] = aligned_top[
        "high_level_distribution_memory_age_sessions"
    ]
    selected["high_level_context_score"] = aligned_top[
        "high_level_context_score"
    ]
    selected["distribution_pressure_score"] = aligned_top[
        "distribution_pressure_score"
    ]
    selected["structure_damage_score"] = aligned_top[
        "structure_damage_score"
    ]
    selected["high_level_distribution_conditions"] = aligned_top[
        "high_level_distribution_conditions"
    ]
    for field in (
        "distribution_count_5",
        "distribution_count_10",
        "distribution_count_20",
        "churning_count_10",
        "churning_cluster",
        "climax_run_score",
        "climax_run_candidate",
        "climax_run_conditions",
        "risk_recovery",
        "risk_recovery_conditions",
    ):
        selected[field] = aligned_top[field]
    selected = selected.rename(
        columns={
            "risk_recovery": "top_risk_recovery",
            "risk_recovery_conditions": "top_risk_recovery_conditions",
        }
    )
    selected["persistent_risk_raw_score"] = selected[
        [
            "individual_risk_raw_score",
            "group_risk_raw_score",
            "slow_decline_risk_raw_score",
        ]
    ].max(axis=1, skipna=True)
    source_columns = {
        "individual": "individual_risk_score",
        "group": "group_risk_score",
        "slow_decline": "slow_decline_risk_score",
    }
    selected["persistent_risk_score"] = selected[
        list(source_columns.values())
    ].max(axis=1, skipna=True)

    def source_values(row):
        available = {
            source: row[column]
            for source, column in source_columns.items()
            if pd.notna(row[column])
        }
        if not available:
            return "unavailable", None, ()
        maximum_source = max(available, key=available.get)
        active = tuple(
            source
            for source, value in available.items()
            if float(value) >= SOURCE_WATCH_THRESHOLDS[source]
        )
        return maximum_source, float(available[maximum_source]), active

    combined = selected.apply(source_values, axis=1)
    selected["persistent_risk_sources"] = combined.map(lambda value: value[2])
    source_name = combined.map(lambda value: value[0])
    selected["persistent_risk_state"] = [
        (
            "unavailable"
            if source == "unavailable"
            else row[f"{source}_risk_state"]
        )
        for source, (_, row) in zip(source_name, selected.iterrows())
    ]
    selected["persistent_risk_age_sessions"] = [
        (
            np.nan
            if source == "unavailable"
            else row[f"{source}_risk_age"]
        )
        for source, (_, row) in zip(source_name, selected.iterrows())
    ]
    return selected


def _build_high_level_states(histories, group, group_state):
    benchmark_close = _group_benchmark_close(histories, group)
    qqq_history = histories.get("QQQ")
    frames = {}
    for ticker in dict.fromkeys(
        (*group.constituent_tickers, *group.related_tickers)
    ):
        history = histories.get(ticker)
        if not isinstance(history, pd.DataFrame) or history.empty:
            continue
        state = build_high_level_distribution_state(
            history,
            sector_close=benchmark_close,
            qqq_history=qqq_history,
            group_supply=group_state,
        )
        state = state.copy()
        state["high_level_distribution_conditions"] = [
            tuple(
                dict.fromkeys(
                    (
                        *context,
                        *supply,
                        *structure,
                    )
                )
            )
            for context, supply, structure in zip(
                state["high_level_context_conditions"],
                state["distribution_pressure_conditions"],
                state["structure_damage_conditions"],
            )
        ]
        frames[ticker] = state
    if not frames:
        index = pd.MultiIndex.from_arrays(
            [pd.Index([], dtype=object), pd.DatetimeIndex([])],
            names=("ticker", "observation_date"),
        )
        return pd.DataFrame(index=index)
    return pd.concat(
        frames,
        names=("ticker", "observation_date"),
    ).sort_index()


def _group_benchmark_close(histories, group):
    normalized = []
    tickers = group.benchmark_tickers or group.fallback_benchmark_tickers
    for ticker in tickers:
        history = histories.get(ticker)
        if (
            not isinstance(history, pd.DataFrame)
            or history.empty
            or "Close" not in history
        ):
            continue
        close = pd.to_numeric(history["Close"], errors="coerce")
        first = close.dropna()
        if first.empty or float(first.iloc[0]) == 0.0:
            continue
        normalized.append(close / float(first.iloc[0]) * 100.0)
    if not normalized:
        return None
    return pd.concat(normalized, axis=1).mean(axis=1, skipna=True)


def _risk_context(row):
    if row is None:
        return None
    try:
        score_value = row.get("persistent_risk_score")
        raw_score_value = row.get("persistent_risk_raw_score")
        state = row.get("persistent_risk_state")
        age = row.get("persistent_risk_age_sessions")
        sources = tuple(row.get("persistent_risk_sources") or ())
        individual_score = _optional_context_score(
            row.get("individual_risk_score"),
            "individual_risk_score",
        )
        group_score = _optional_context_score(
            row.get("group_risk_score"),
            "group_risk_score",
        )
        slow_decline_score = _optional_context_score(
            row.get("slow_decline_risk_score"),
            "slow_decline_risk_score",
        )
        top_score = _optional_context_score(
            row.get("high_level_distribution_score"),
            "high_level_distribution_score",
        )
        top_raw_score = _optional_context_score(
            row.get("high_level_distribution_raw_score"),
            "high_level_distribution_raw_score",
        )
        top_state = row.get("high_level_distribution_state")
        top_raw_state = row.get("high_level_distribution_raw_state")
        top_age = row.get("high_level_distribution_age_sessions")
        top_context = _optional_context_score(
            row.get("high_level_context_score"),
            "high_level_context_score",
        )
        top_supply = _optional_context_score(
            row.get("distribution_pressure_score"),
            "distribution_pressure_score",
        )
        top_structure = _optional_context_score(
            row.get("structure_damage_score"),
            "structure_damage_score",
        )
        top_conditions = tuple(
            row.get("high_level_distribution_conditions") or ()
        )
        distribution_5 = _optional_context_count(
            row.get("distribution_count_5"),
            "distribution_count_5",
        )
        distribution_10 = _optional_context_count(
            row.get("distribution_count_10"),
            "distribution_count_10",
        )
        distribution_20 = _optional_context_count(
            row.get("distribution_count_20"),
            "distribution_count_20",
        )
        churning_10 = _optional_context_count(
            row.get("churning_count_10"),
            "churning_count_10",
        )
        churning_cluster = _optional_context_boolean(
            row.get("churning_cluster"),
        )
        climax_score = _optional_context_score(
            row.get("climax_run_score"),
            "climax_run_score",
        )
        climax_candidate = _optional_context_boolean(
            row.get("climax_run_candidate"),
        )
        climax_conditions = tuple(row.get("climax_run_conditions") or ())
        top_recovery = _optional_context_boolean(
            row.get("top_risk_recovery"),
        )
        top_recovery_conditions = tuple(
            row.get("top_risk_recovery_conditions") or ()
        )
        if any(
            pd.isna(value)
            for value in (score_value, raw_score_value, state, age)
        ):
            return None
        score = _optional_score(
            score_value,
            "persistent_risk_score",
        )
        raw_score = _optional_score(
            raw_score_value,
            "persistent_risk_raw_score",
        )
    except AttributeError as exc:
        raise TypeError("context_row must be a mapping or None") from exc
    if score is None or raw_score is None:
        return None
    if state not in PERSISTENT_RISK_STATES - {"unavailable"}:
        raise ValueError("invalid persistent_risk_state")
    if age is not None:
        if isinstance(age, bool) or not isinstance(age, Real):
            raise TypeError("persistent_risk_age_sessions must be numeric")
        if not math.isfinite(float(age)) or float(age) < 0.0:
            raise ValueError(
                "persistent_risk_age_sessions must not be negative"
            )
        age = int(age)
    if any(source not in PERSISTENT_RISK_SOURCES for source in sources):
        raise ValueError("invalid persistent_risk_sources")
    valid_top_states = {
        "low", "inactive", "watch", "high", "confirmed",
        "fading", "unavailable",
    }
    if pd.isna(top_state):
        top_state = "unavailable"
    if pd.isna(top_raw_state):
        top_raw_state = "unavailable"
    if top_state not in valid_top_states or top_raw_state not in valid_top_states:
        raise ValueError("invalid high-level distribution state")
    if top_age is None or pd.isna(top_age):
        top_age = None
    else:
        if (
            isinstance(top_age, bool)
            or not isinstance(top_age, Real)
            or not math.isfinite(float(top_age))
            or float(top_age) < 0.0
        ):
            raise ValueError(
                "high_level_distribution_age_sessions must be non-negative"
            )
        top_age = int(top_age)
    return {
        "score": score,
        "raw_score": raw_score,
        "state": state,
        "age": age,
        "sources": sources,
        "individual_score": individual_score,
        "group_score": group_score,
        "slow_decline_score": slow_decline_score,
        "top_score": top_score,
        "top_raw_score": top_raw_score,
        "top_state": top_state,
        "top_raw_state": top_raw_state,
        "top_age": top_age,
        "top_context": top_context,
        "top_supply": top_supply,
        "top_structure": top_structure,
        "top_conditions": top_conditions,
        "distribution_5": distribution_5,
        "distribution_10": distribution_10,
        "distribution_20": distribution_20,
        "churning_10": churning_10,
        "churning_cluster": churning_cluster,
        "climax_score": climax_score,
        "climax_candidate": climax_candidate,
        "climax_conditions": climax_conditions,
        "top_recovery": top_recovery,
        "top_recovery_conditions": top_recovery_conditions,
    }


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


def _optional_context_score(value, name):
    if value is None or pd.isna(value):
        return None
    return _optional_score(value, name)


def _optional_count(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name} must be an integer or None")
    result = int(value)
    if result < 0:
        raise ValueError(f"{name} must not be negative")
    return result


def _optional_context_count(value, name):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be numeric")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0.0 or not numeric.is_integer():
        raise ValueError(f"{name} must be a non-negative integer")
    return int(numeric)


def _optional_boolean(value, name):
    if value is None:
        return None
    if not isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{name} must be a boolean or None")
    return bool(value)


def _optional_context_boolean(value):
    if value is None or pd.isna(value):
        return None
    return _optional_boolean(value, "context boolean")


def _required_score(value, name):
    result = _optional_score(value, name)
    if result is None:
        raise ValueError(f"{name} is required")
    return result
