"""Causal state memory for daily bearish-turn risk scores."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


RISK_MEMORY_HALF_LIFE_SESSIONS = 5
RISK_MEMORY_WINDOW_SESSIONS = 10
RISK_MEMORY_ACTIVE_THRESHOLD = 20.0


def build_risk_memory_state(
    raw_scores: pd.Series,
    *,
    half_life_sessions: int = RISK_MEMORY_HALF_LIFE_SESSIONS,
    window_sessions: int = RISK_MEMORY_WINDOW_SESSIONS,
    active_threshold: float = RISK_MEMORY_ACTIVE_THRESHOLD,
) -> pd.DataFrame:
    """Return a causal, exponentially decaying state for daily risk scores."""
    if half_life_sessions <= 0:
        raise ValueError("half_life_sessions must be positive")
    if window_sessions <= 0:
        raise ValueError("window_sessions must be positive")

    raw = pd.to_numeric(raw_scores, errors="coerce").astype(float)
    decay = math.pow(0.5, 1.0 / float(half_life_sessions))
    state_scores: list[float] = []
    state_labels: list[str] = []
    memory_ages: list[float] = []
    previous_state = np.nan
    previous_age = 0

    for current_raw in raw:
        if not np.isfinite(current_raw):
            state_scores.append(np.nan)
            state_labels.append("unavailable")
            memory_ages.append(np.nan)
            continue

        previous_active = (
            np.isfinite(previous_state)
            and previous_state >= active_threshold
        )
        next_age = previous_age + 1
        remembered = (
            previous_state * decay
            if np.isfinite(previous_state) and next_age < window_sessions
            else -np.inf
        )
        if current_raw >= remembered:
            current_state = current_raw
            current_age = 0
        else:
            current_state = remembered
            current_age = next_age

        if current_state < active_threshold:
            label = "inactive"
        elif current_age > 0:
            label = "fading"
        elif previous_active:
            label = "persistent"
        else:
            label = "new"

        state_scores.append(float(current_state))
        state_labels.append(label)
        memory_ages.append(float(current_age))
        previous_state = current_state
        previous_age = current_age

    return pd.DataFrame(
        {
            "raw_score": raw,
            "state_score": pd.Series(state_scores, index=raw.index),
            "state": pd.Series(state_labels, index=raw.index, dtype=object),
            "memory_age_sessions": pd.Series(
                memory_ages,
                index=raw.index,
                dtype=float,
            ),
        },
        index=raw.index,
    )
