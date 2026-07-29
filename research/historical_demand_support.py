"""Causal historical-demand support zones from daily evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite

import numpy as np
import pandas as pd


MODEL_KEY = "historical_demand_support_v1"
MODEL_VERSION = "v1"
HALF_LIFE_SESSIONS = 40
MAXIMUM_AGE_SESSIONS = 120
REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
STATES = frozenset(
    {
        "unavailable",
        "active_untested",
        "approaching",
        "testing",
        "accepted",
        "weakened",
        "invalidated",
    }
)
EVENT_PRIORITY = (
    "breakout_follow_through",
    "breakout_acceptance",
    "buyer_absorption",
    "pocket_pivot",
    "up_volume_confirmation",
)
EVENT_QUALITY = {
    "breakout_follow_through": 30.0,
    "breakout_acceptance": 27.0,
    "buyer_absorption": 25.0,
    "pocket_pivot": 23.0,
    "up_volume_confirmation": 20.0,
}


@dataclass(frozen=True)
class DemandEvent:
    date: pd.Timestamp
    position: int
    event_type: str
    center: float
    lower: float
    upper: float
    atr20: float
    volume_ratio: float
    close_location: float
    environment_confirmed: bool | None


def build_historical_demand_support_rows(
    history: pd.DataFrame,
    *,
    demand_rows: pd.DataFrame,
    entry_signal_rows: Sequence[Mapping[str, object]],
    qqq_close: pd.Series | None = None,
    sector_close: pd.Series | None = None,
) -> pd.DataFrame:
    """Return one point-in-time historical-demand support row per session."""
    frame = _validated_history(history)
    _validate_evidence(frame, demand_rows, entry_signal_rows)
    metrics = _metrics(frame)
    qqq_return = _context_return(qqq_close, frame.index)
    sector_return = _context_return(sector_close, frame.index)
    events: list[DemandEvent] = []
    invalidated_positions: set[int] = set()
    rows = []
    for position, timestamp in enumerate(frame.index):
        atr20 = metrics["atr20"].iloc[position]
        if not _finite_positive(atr20):
            rows.append(_unavailable_row("insufficient_atr_history"))
            continue
        event = _event_for_session(
            frame,
            position,
            timestamp,
            demand_rows.iloc[position],
            entry_signal_rows[position],
            metrics,
            qqq_return,
            sector_return,
        )
        if event is not None:
            events.append(event)
        active = [
            item
            for item in events
            if position - item.position <= MAXIMUM_AGE_SESSIONS
            and item.position not in invalidated_positions
        ]
        if not active:
            rows.append(_unavailable_row("no_qualifying_demand_event"))
            continue
        clusters = _event_clusters(active, float(atr20))
        selected = _select_cluster(
            clusters,
            close=float(frame["Close"].iloc[position]),
            atr20=float(atr20),
        )
        row, invalidated = _row_for_cluster(
            selected,
            frame=frame,
            metrics=metrics,
            demand_rows=demand_rows,
            current_position=position,
        )
        rows.append(row)
        if invalidated:
            invalidated_positions.update(
                item.position for item in selected
            )
    return pd.DataFrame(rows, index=frame.index)


def _metrics(frame: pd.DataFrame) -> dict[str, pd.Series]:
    previous_close = frame["Close"].shift(1)
    true_range = pd.concat(
        (
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    candle_range = (frame["High"] - frame["Low"]).replace(0.0, np.nan)
    close_location = (
        (frame["Close"] - frame["Low"]) / candle_range - 0.5
    ) * 2.0
    return {
        "atr20": true_range.rolling(20, min_periods=20).mean(),
        "volume_ratio": (
            frame["Volume"]
            / frame["Volume"].rolling(20, min_periods=20).mean()
        ),
        "close_location": close_location,
        "stock_return_20": frame["Close"].pct_change(
            20,
            fill_method=None,
        ),
        "prior_low_5": frame["Low"].shift(1).rolling(5).min(),
        "prior_pivot_20": frame["Close"].shift(1).rolling(20).max(),
    }


def _event_for_session(
    frame: pd.DataFrame,
    position: int,
    timestamp: pd.Timestamp,
    demand_row: pd.Series,
    entry_row: Mapping[str, object],
    metrics: Mapping[str, pd.Series],
    qqq_return: pd.Series | None,
    sector_return: pd.Series | None,
) -> DemandEvent | None:
    conditions = _condition_set(
        demand_row.get("demand_confirmation_conditions")
    )
    if entry_row.get("pocket_pivot") is True:
        conditions.add("pocket_pivot")
    event_type = next(
        (key for key in EVENT_PRIORITY if key in conditions),
        None,
    )
    if event_type is None:
        return None
    atr20 = float(metrics["atr20"].iloc[position])
    volume_ratio = metrics["volume_ratio"].iloc[position]
    close_location = metrics["close_location"].iloc[position]
    if not _finite_positive(volume_ratio) or not _finite(close_location):
        return None
    source = frame.iloc[position]
    center = _event_center(
        event_type,
        source,
        metrics,
        position,
    )
    half_width = atr20 * 0.15
    environment = _environment_confirmation(
        metrics["stock_return_20"].iloc[position],
        None if qqq_return is None else qqq_return.iloc[position],
        None if sector_return is None else sector_return.iloc[position],
    )
    return DemandEvent(
        date=pd.Timestamp(timestamp),
        position=position,
        event_type=event_type,
        center=float(center),
        lower=float(max(0.01, center - half_width)),
        upper=float(center + half_width),
        atr20=atr20,
        volume_ratio=float(volume_ratio),
        close_location=float(close_location),
        environment_confirmed=environment,
    )


def _event_center(
    event_type: str,
    source: pd.Series,
    metrics: Mapping[str, pd.Series],
    position: int,
) -> float:
    body_mid = (float(source["Open"]) + float(source["Close"])) / 2.0
    if event_type in {"breakout_acceptance", "breakout_follow_through"}:
        pivot = metrics["prior_pivot_20"].iloc[position]
        if _finite_positive(pivot):
            return float(pivot)
    if event_type == "buyer_absorption":
        prior_low = metrics["prior_low_5"].iloc[position]
        if _finite_positive(prior_low):
            return (float(prior_low) + body_mid) / 2.0
    return body_mid


def _event_clusters(
    events: Sequence[DemandEvent],
    atr20: float,
) -> list[list[DemandEvent]]:
    clusters: list[list[DemandEvent]] = []
    for event in sorted(events, key=lambda item: (item.center, item.position)):
        if (
            not clusters
            or event.center - clusters[-1][-1].center > atr20 * 0.5
        ):
            clusters.append([event])
        else:
            clusters[-1].append(event)
    return clusters


def _select_cluster(
    clusters: Sequence[Sequence[DemandEvent]],
    *,
    close: float,
    atr20: float,
) -> list[DemandEvent]:
    eligible = [
        list(cluster)
        for cluster in clusters
        if min(item.center for item in cluster) <= close + atr20
    ]
    candidates = eligible or [list(cluster) for cluster in clusters]
    return min(
        candidates,
        key=lambda cluster: (
            abs(close - max(item.upper for item in cluster)),
            -max(item.position for item in cluster),
        ),
    )


def _row_for_cluster(
    cluster: Sequence[DemandEvent],
    *,
    frame: pd.DataFrame,
    metrics: Mapping[str, pd.Series],
    demand_rows: pd.DataFrame,
    current_position: int,
) -> tuple[dict[str, object], bool]:
    first_event = min(cluster, key=lambda item: item.position)
    latest_event = max(cluster, key=lambda item: item.position)
    lower = min(item.lower for item in cluster)
    upper = max(item.upper for item in cluster)
    center = (
        sum(item.center for item in cluster) / float(len(cluster))
    )
    close = float(frame["Close"].iloc[current_position])
    atr20 = float(metrics["atr20"].iloc[current_position])
    latest_age = current_position - latest_event.position
    recency = 0.5 ** (latest_age / HALF_LIFE_SESSIONS)
    volume_ratio = max(item.volume_ratio for item in cluster)
    volume_points = min(
        20.0,
        max(0.0, (volume_ratio - 1.0) / 1.5 * 20.0),
    )
    environment_points = (
        10.0
        if any(item.environment_confirmed is True for item in cluster)
        else 0.0
    )
    retest_dates = _accepted_retest_dates(
        frame,
        start_position=first_event.position,
        end_position=current_position,
        lower=lower,
        upper=upper,
    )
    overlap_points = min(20.0, max(0, len(cluster) - 1) * 10.0)
    retest_points = min(20.0, len(retest_dates) * 10.0)
    score = (
        max(EVENT_QUALITY[item.event_type] for item in cluster)
        + volume_points
        + overlap_points
        + retest_points
        + environment_points
    ) * recency
    event_types = [
        key
        for key in EVENT_PRIORITY
        if any(item.event_type == key for item in cluster)
    ]
    conditions = list(event_types)
    if any(item.environment_confirmed is True for item in cluster):
        conditions.append("environment_confirmed")
    if len(cluster) > 1:
        conditions.append("multiple_demand_events")
    if retest_dates:
        conditions.append("support_retest_accepted")
    counter_conditions: list[str] = []
    current_volume_ratio = metrics["volume_ratio"].iloc[current_position]
    high_volume_break = (
        _finite(current_volume_ratio)
        and float(current_volume_ratio) >= 1.2
        and close <= lower - atr20 * 0.5
    )
    consecutive_break = (
        current_position > first_event.position
        and current_position > 0
        and close < lower
        and float(frame["Close"].iloc[current_position - 1]) < lower
    )
    high_volume_new_low = _high_volume_new_low(
        frame,
        demand_rows,
        metrics,
        current_position,
    )
    invalidated = high_volume_break or consecutive_break or high_volume_new_low
    if high_volume_break:
        counter_conditions.append("high_volume_support_break")
    if consecutive_break:
        counter_conditions.append("consecutive_closes_below_support")
    if high_volume_new_low:
        counter_conditions.append("high_volume_new_low")
    if invalidated:
        state = "invalidated"
    elif close < lower:
        state = "weakened"
        counter_conditions.append("close_below_support")
    elif (
        retest_dates
        and retest_dates[-1] == frame.index[current_position]
    ):
        state = "accepted"
    elif (
        float(frame["Low"].iloc[current_position]) <= upper
        and float(frame["High"].iloc[current_position]) >= lower
    ):
        state = "testing"
    elif close > upper and close - upper <= atr20 * 0.5:
        state = "approaching"
    else:
        state = "active_untested"
    distance = max(0.0, close / upper - 1.0) * 100.0
    last_confirmed = (
        retest_dates[-1]
        if retest_dates
        else latest_event.date
    )
    available_environment = any(
        item.environment_confirmed is not None for item in cluster
    )
    row = {
        "historical_demand_support_model_key": MODEL_KEY,
        "historical_demand_support_model_version": MODEL_VERSION,
        "historical_demand_support_state": state,
        "historical_demand_support_lower": lower,
        "historical_demand_support_upper": upper,
        "historical_demand_support_mid": center,
        "historical_demand_support_distance_pct": distance,
        "historical_demand_support_score": min(100.0, score),
        "historical_demand_support_first_date": (
            first_event.date.date().isoformat()
        ),
        "historical_demand_support_last_confirmed_date": (
            pd.Timestamp(last_confirmed).date().isoformat()
        ),
        "historical_demand_support_age_sessions": latest_age,
        "historical_demand_support_event_types": event_types,
        "historical_demand_support_event_count": len(cluster),
        "historical_demand_support_retest_count": len(retest_dates),
        "historical_demand_support_volume_ratio": volume_ratio,
        "historical_demand_support_invalidation_level": (
            lower - atr20 * 0.5
        ),
        "historical_demand_support_conditions": conditions,
        "historical_demand_support_counter_conditions": counter_conditions,
        "historical_demand_support_coverage": (
            1.0 if available_environment else 0.8
        ),
        "historical_demand_support_unavailable_reason": None,
    }
    return row, invalidated


def _accepted_retest_dates(
    frame: pd.DataFrame,
    *,
    start_position: int,
    end_position: int,
    lower: float,
    upper: float,
) -> list[pd.Timestamp]:
    dates = []
    for position in range(start_position + 1, end_position + 1):
        prior_close = float(frame["Close"].iloc[position - 1])
        source = frame.iloc[position]
        touched = (
            float(source["Low"]) <= upper
            and float(source["High"]) >= lower
        )
        if (
            prior_close > upper
            and touched
            and float(source["Close"]) >= upper
        ):
            dates.append(pd.Timestamp(frame.index[position]))
    return dates


def _high_volume_new_low(
    frame: pd.DataFrame,
    demand_rows: pd.DataFrame,
    metrics: Mapping[str, pd.Series],
    position: int,
) -> bool:
    if position < 10:
        return False
    conditions = _condition_set(
        demand_rows.iloc[position].get("supply_pressure_conditions")
    )
    supply_confirmed = bool(
        conditions
        & {
            "distribution_day",
            "negative_signed_volume",
            "volume_confirmed_ema20_break",
        }
    )
    volume_ratio = metrics["volume_ratio"].iloc[position]
    prior_low = float(
        frame["Low"].iloc[position - 10 : position].min()
    )
    return (
        supply_confirmed
        and _finite(volume_ratio)
        and float(volume_ratio) >= 1.2
        and float(frame["Low"].iloc[position]) < prior_low
    )


def _condition_set(value) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(item) for item in value if item not in (None, "")}


def _context_return(
    close: pd.Series | None,
    index: pd.Index,
) -> pd.Series | None:
    if close is None:
        return None
    if not isinstance(close, pd.Series):
        raise TypeError("context closes must be Series")
    numeric = pd.to_numeric(close, errors="coerce").reindex(index)
    return numeric.pct_change(20, fill_method=None)


def _environment_confirmation(
    stock_return,
    qqq_return,
    sector_return,
) -> bool | None:
    contexts = [
        float(value)
        for value in (qqq_return, sector_return)
        if _finite(value)
    ]
    if not _finite(stock_return) or not contexts:
        return None
    return all(float(stock_return) >= value for value in contexts)


def _finite(value) -> bool:
    try:
        return value is not None and isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _finite_positive(value) -> bool:
    return _finite(value) and float(value) > 0.0


def _validated_history(history: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    missing = [column for column in REQUIRED_COLUMNS if column not in history]
    if missing:
        raise ValueError(f"history is missing required columns: {missing}")
    if not history.index.is_unique:
        raise ValueError("history dates must be unique")
    if not history.index.is_monotonic_increasing:
        raise ValueError("history dates must be increasing")
    frame = history.loc[:, REQUIRED_COLUMNS].astype(float)
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("history OHLCV must be finite")
    if (frame[["Open", "High", "Low", "Close"]] <= 0.0).any().any():
        raise ValueError("history prices must be positive")
    if (frame["Volume"] < 0.0).any():
        raise ValueError("history volume must be non-negative")
    return frame


def _validate_evidence(
    history: pd.DataFrame,
    demand_rows: pd.DataFrame,
    entry_signal_rows: Sequence[Mapping[str, object]],
) -> None:
    if not isinstance(demand_rows, pd.DataFrame):
        raise TypeError("demand_rows must be a DataFrame")
    if len(demand_rows) != len(history) or not demand_rows.index.equals(
        history.index
    ):
        raise ValueError("demand_rows must align one-to-one with history")
    if not isinstance(entry_signal_rows, Sequence) or isinstance(
        entry_signal_rows, (str, bytes)
    ):
        raise TypeError("entry_signal_rows must be a sequence")
    if len(entry_signal_rows) != len(history):
        raise ValueError(
            "entry_signal_rows must align one-to-one with history"
        )
    if any(not isinstance(row, Mapping) for row in entry_signal_rows):
        raise TypeError("entry_signal_rows entries must be mappings")


def _unavailable_row(reason: str) -> dict[str, object]:
    return {
        "historical_demand_support_model_key": MODEL_KEY,
        "historical_demand_support_model_version": MODEL_VERSION,
        "historical_demand_support_state": "unavailable",
        "historical_demand_support_lower": None,
        "historical_demand_support_upper": None,
        "historical_demand_support_mid": None,
        "historical_demand_support_distance_pct": None,
        "historical_demand_support_score": None,
        "historical_demand_support_first_date": None,
        "historical_demand_support_last_confirmed_date": None,
        "historical_demand_support_age_sessions": None,
        "historical_demand_support_event_types": [],
        "historical_demand_support_event_count": 0,
        "historical_demand_support_retest_count": 0,
        "historical_demand_support_volume_ratio": None,
        "historical_demand_support_invalidation_level": None,
        "historical_demand_support_conditions": [],
        "historical_demand_support_counter_conditions": [],
        "historical_demand_support_coverage": 0.0,
        "historical_demand_support_unavailable_reason": reason,
    }
