"""Causal downtrend-bottoming and bullish-reversal state model."""

from __future__ import annotations

from collections.abc import Iterable
import math

import numpy as np
import pandas as pd


BOTTOM_MODEL_KEY = "bottoming_reversal_state_v1"
BOTTOM_MODEL_VERSION = "v1"
MEMORY_SESSIONS = 10
FAILURE_MEMORY_SESSIONS = 3
MIN_HISTORY = 63

REQUIRED_PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
POSITIVE_STATES = (
    "potential_support",
    "seller_exhaustion_watch",
    "early_bullish_reversal_watch",
    "bullish_structure_confirmed",
    "breakout_retest_confirmed",
)
STATE_RANK = {
    "unavailable": -1,
    "downtrend_continuation": 0,
    "potential_support": 1,
    "seller_exhaustion_watch": 2,
    "early_bullish_reversal_watch": 3,
    "bullish_structure_confirmed": 4,
    "breakout_retest_confirmed": 5,
    "bottom_failed": 6,
}


def build_bottom_state_rows(
    history: pd.DataFrame,
    evidence: pd.DataFrame,
) -> pd.DataFrame:
    """Return one point-in-time bottoming-state diagnosis per session."""
    _validate_inputs(history, evidence)
    if history.empty:
        return pd.DataFrame(index=history.index)

    frame = history.sort_index().loc[:, REQUIRED_PRICE_COLUMNS].astype(float)
    aligned = evidence.reindex(frame.index).copy(deep=True)
    close = frame["Close"]
    volume = frame["Volume"]
    ema20 = close.ewm(span=20, adjust=False).mean()
    return20 = close.pct_change(20)
    peak63 = close.rolling(MIN_HISTORY).max()
    drawdown63 = close / peak63 - 1.0
    ema20_slope5 = ema20 / ema20.shift(5) - 1.0
    volume_ratio20 = volume / volume.rolling(20).mean()
    prior_low10 = frame["Low"].shift(1).rolling(10).min()
    downtrend_votes = (
        (close < ema20).astype(int)
        + (ema20_slope5 < 0.0).astype(int)
        + (return20 <= -0.08).astype(int)
        + (drawdown63 <= -0.15).astype(int)
    )
    downtrend = (downtrend_votes >= 2) & peak63.notna()
    recent_downtrend = (
        downtrend.astype(int).rolling(MEMORY_SESSIONS, min_periods=1).max()
        > 0
    )

    rows = []
    remembered_state = "unavailable"
    remembered_age = None
    frozen_invalidation = None
    prior_output_state = "unavailable"

    for position, observation_date in enumerate(frame.index):
        source = frame.iloc[position]
        proof = aligned.iloc[position]
        conditions = []
        counter_conditions = []

        location_score, location_available = _location_score(
            source,
            proof,
            conditions,
        )
        exhaustion_score, exhaustion_available = _exhaustion_score(
            proof,
            conditions,
        )
        demand_score, demand_available = _demand_score(
            proof,
            conditions,
        )
        structure_score, structure_available = _structure_score(
            proof,
            conditions,
        )
        environment_score, environment_available = _environment_score(
            proof,
            conditions,
        )
        coverage = sum(
            (
                True,
                location_available,
                exhaustion_available,
                demand_available,
                structure_available or environment_available,
            )
        ) / 5.0
        score = min(
            100.0,
            location_score
            + exhaustion_score
            + demand_score
            + structure_score
            + environment_score,
        )

        support_lower = _number(proof.get("near_support_lower"))
        candidate_invalidation = (
            support_lower
            if support_lower is not None
            else _number(prior_low10.iloc[position])
        )
        failure_eligible = (
            remembered_state in POSITIVE_STATES
            and STATE_RANK.get(remembered_state, 0)
            >= STATE_RANK["seller_exhaustion_watch"]
        )
        failure = failure_eligible and _failure_evidence(
            source=source,
            proof=proof,
            prior_low=_number(prior_low10.iloc[position]),
            volume_ratio=_number(volume_ratio20.iloc[position]),
            frozen_invalidation=frozen_invalidation,
            counter_conditions=counter_conditions,
        )
        has_recent_bottom_context = (
            remembered_state in POSITIVE_STATES
            and remembered_age is not None
            and remembered_age < MEMORY_SESSIONS
        )
        raw_state, unavailable_reason = _raw_state(
            enough_history=position + 1 >= MIN_HISTORY,
            downtrend=bool(downtrend.iloc[position]),
            recent_downtrend=bool(recent_downtrend.iloc[position]),
            coverage=coverage,
            location_score=location_score,
            exhaustion_score=exhaustion_score,
            demand_score=demand_score,
            structure_score=structure_score,
            early_watch=_truthy(proof.get("early_reversal_watch")),
            higher_low=_truthy(proof.get("higher_low_confirmed")),
            breakout=bool(
                _truthy(proof.get("prior_high_breakout"))
                or _truthy(proof.get("trendline_breakout"))
            ),
            demand_conditions=_conditions(
                proof.get("demand_confirmation_conditions")
            ),
            has_recent_bottom_context=has_recent_bottom_context,
        )

        output_state = raw_state
        output_age = 0 if raw_state != "unavailable" else None
        if failure and has_recent_bottom_context:
            output_state = "bottom_failed"
            output_age = 0
            remembered_state = output_state
            remembered_age = 0
        elif remembered_state == "bottom_failed":
            next_age = int(remembered_age or 0) + 1
            if next_age <= FAILURE_MEMORY_SESSIONS:
                output_state = "bottom_failed"
                output_age = next_age
                remembered_age = next_age
            else:
                remembered_state = raw_state
                remembered_age = output_age
                frozen_invalidation = None
        elif raw_state in POSITIVE_STATES:
            current_rank = STATE_RANK.get(remembered_state, -1)
            raw_rank = STATE_RANK[raw_state]
            if (
                remembered_state not in POSITIVE_STATES
                or raw_rank > current_rank
            ):
                remembered_state = raw_state
                remembered_age = 0
                output_state = raw_state
                output_age = 0
                if candidate_invalidation is not None:
                    frozen_invalidation = candidate_invalidation
            elif raw_rank == current_rank:
                output_state = remembered_state
                output_age = 0
                remembered_age = 0
                if candidate_invalidation is not None:
                    frozen_invalidation = candidate_invalidation
            else:
                next_age = int(remembered_age or 0) + 1
                if next_age <= MEMORY_SESSIONS:
                    output_state = remembered_state
                    output_age = next_age
                    remembered_age = next_age
                else:
                    remembered_state = raw_state
                    remembered_age = 0
                    output_state = raw_state
                    output_age = 0
        elif has_recent_bottom_context:
            next_age = int(remembered_age or 0) + 1
            if next_age <= MEMORY_SESSIONS:
                output_state = remembered_state
                output_age = next_age
                remembered_age = next_age
            else:
                remembered_state = raw_state
                remembered_age = output_age
                frozen_invalidation = None
        else:
            remembered_state = raw_state
            remembered_age = output_age
            if raw_state not in POSITIVE_STATES:
                frozen_invalidation = None

        transition = (
            output_state != prior_output_state
            and output_state != "unavailable"
        )
        prior_output_state = output_state
        rows.append(
            {
                "bottom_model_key": BOTTOM_MODEL_KEY,
                "bottom_model_version": BOTTOM_MODEL_VERSION,
                "bottom_state": output_state,
                "bottom_raw_state": raw_state,
                "bottom_score": round(score, 2),
                "bottom_coverage": round(coverage, 4),
                "bottom_state_age_sessions": output_age,
                "bottom_state_transition": transition,
                "bottom_location_score": round(location_score, 2),
                "bottom_exhaustion_score": round(exhaustion_score, 2),
                "bottom_demand_score": round(demand_score, 2),
                "bottom_structure_score": round(structure_score, 2),
                "bottom_environment_score": round(environment_score, 2),
                "bottom_conditions": list(dict.fromkeys(conditions)),
                "bottom_counter_conditions": list(
                    dict.fromkeys(counter_conditions)
                ),
                "bottom_invalidation_level": (
                    frozen_invalidation
                    if output_state in POSITIVE_STATES
                    else None
                ),
                "bottom_unavailable_reason": (
                    unavailable_reason
                    if output_state == "unavailable"
                    else None
                ),
            }
        )
    return pd.DataFrame(rows, index=frame.index)


def _validate_inputs(history, evidence):
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    missing = [column for column in REQUIRED_PRICE_COLUMNS if column not in history]
    if missing:
        raise ValueError(f"history is missing required columns: {missing}")
    if not isinstance(evidence, pd.DataFrame):
        raise TypeError("evidence must be a DataFrame")
    if history.index.has_duplicates or evidence.index.has_duplicates:
        raise ValueError("history and evidence indexes must be unique")


def _location_score(source, proof, conditions):
    support_state = proof.get("near_support_state")
    support_score = _number(proof.get("near_support_score"))
    support_upper = _number(proof.get("near_support_upper"))
    close = _number(source.get("Close"))
    available = support_score is not None and support_upper is not None
    if not available or close is None or close <= 0.0:
        return 0.0, False
    distance_pct = max(0.0, (close / support_upper - 1.0) * 100.0)
    near = support_state in {"inside", "testing"} or distance_pct <= 3.0
    if not near:
        return min(6.0, support_score * 0.06), True
    conditions.append("near_support_zone")
    if support_score >= 60.0:
        conditions.append("strong_support_zone")
    return min(20.0, 10.0 + support_score * 0.1), True


def _exhaustion_score(proof, conditions):
    demand_conditions = set(
        _conditions(proof.get("demand_confirmation_conditions"))
    )
    known = {
        "seller_exhaustion": 10.0,
        "buyer_absorption": 8.0,
        "low_volume_higher_low": 7.0,
    }
    matched = [key for key in known if key in demand_conditions]
    conditions.extend(matched)
    coverage = _number(proof.get("demand_confirmation_coverage"))
    return sum(known[key] for key in matched), coverage is not None and coverage > 0


def _demand_score(proof, conditions):
    score = _number(proof.get("demand_confirmation_score"))
    coverage = _number(proof.get("demand_confirmation_coverage"))
    if score is None or coverage is None or coverage <= 0.0:
        return 0.0, False
    demand_conditions = set(
        _conditions(proof.get("demand_confirmation_conditions"))
    )
    recognized = (
        "positive_signed_volume",
        "up_volume_confirmation",
        "breakout_acceptance",
        "breakout_follow_through",
    )
    conditions.extend(key for key in recognized if key in demand_conditions)
    return min(25.0, score * 0.25), True


def _structure_score(proof, conditions):
    score = 0.0
    if _truthy(proof.get("early_reversal_watch")):
        score += 5.0
        conditions.append("early_bullish_reversal")
    if _truthy(proof.get("higher_low_confirmed")):
        score += 7.0
        conditions.append("higher_low_confirmed")
    if _truthy(proof.get("prior_high_breakout")):
        score += 8.0
        conditions.append("prior_high_breakout")
    if _truthy(proof.get("trendline_breakout")):
        score += 8.0
        conditions.append("trendline_breakout")
    return min(20.0, score), True


def _environment_score(proof, conditions):
    state = proof.get("market_regime_state")
    if state in (None, "", "unavailable"):
        return 0.0, False
    if state == "confirmed_uptrend":
        conditions.append("market_confirmed_uptrend")
        return 10.0, True
    if state in {"rally_attempt", "uptrend_under_pressure"}:
        conditions.append("market_stabilizing")
        return 5.0, True
    return 0.0, True


def _failure_evidence(
    *,
    source,
    proof,
    prior_low,
    volume_ratio,
    frozen_invalidation,
    counter_conditions,
):
    close = _number(source.get("Close"))
    low = _number(source.get("Low"))
    supply_score = _number(proof.get("supply_pressure_score"))
    if (
        low is not None
        and prior_low is not None
        and low < prior_low
        and volume_ratio is not None
        and volume_ratio >= 1.2
    ):
        counter_conditions.append("volume_expanded_new_low")
    if (
        close is not None
        and frozen_invalidation is not None
        and close < frozen_invalidation * 0.99
    ):
        counter_conditions.append("support_invalidation_break")
    if supply_score is not None and supply_score >= 70.0:
        counter_conditions.append("strong_supply_pressure")
    return bool(
        "volume_expanded_new_low" in counter_conditions
        or "support_invalidation_break" in counter_conditions
    )


def _raw_state(
    *,
    enough_history,
    downtrend,
    recent_downtrend,
    coverage,
    location_score,
    exhaustion_score,
    demand_score,
    structure_score,
    early_watch,
    higher_low,
    breakout,
    demand_conditions,
    has_recent_bottom_context,
):
    if not enough_history:
        return "unavailable", "insufficient_history"
    context = downtrend or recent_downtrend or has_recent_bottom_context
    if not context:
        return "unavailable", "no_downtrend_context"
    if coverage < 0.6:
        return "unavailable", "insufficient_evidence_coverage"
    if (
        has_recent_bottom_context
        and "breakout_follow_through" in demand_conditions
        and (higher_low or location_score >= 10.0)
    ):
        return "breakout_retest_confirmed", None
    if higher_low and breakout:
        return "bullish_structure_confirmed", None
    if early_watch and demand_score >= 12.5:
        return "early_bullish_reversal_watch", None
    if location_score >= 10.0 and exhaustion_score >= 8.0:
        return "seller_exhaustion_watch", None
    if location_score >= 10.0:
        return "potential_support", None
    return "downtrend_continuation", None


def _conditions(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(item) for item in value if item]
    return []


def _number(value):
    if isinstance(value, (bool, np.bool_)):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _truthy(value) -> bool:
    return isinstance(value, (bool, np.bool_)) and bool(value)
