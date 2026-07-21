from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

import pandas as pd

from research.vcp import VCPPattern, detect_vcp


NEAR_PIVOT_PCT = 5.0
BREAKOUT_VOLUME_RATIO = 1.4
INVALIDATION_ATR = 1.0
MAX_EVENT_LIFETIME = 60


@dataclass(frozen=True)
class EventTransition:
    date: pd.Timestamp
    stage: str
    pivot: float | None
    close: float
    volume_ratio: float | None = None


@dataclass
class VCPEvent:
    event_id: str
    ticker: str
    base_start: pd.Timestamp
    first_seen: pd.Timestamp
    initial_pattern: VCPPattern
    near_pivot_date: pd.Timestamp | None = None
    breakout_date: pd.Timestamp | None = None
    breakout_pivot: float | None = None
    breakout_volume_ratio: float | None = None
    volume_confirmed: bool = False
    invalidated_date: pd.Timestamp | None = None
    expired_date: pd.Timestamp | None = None
    transitions: list[EventTransition] = field(default_factory=list)


def _event_id(ticker: str, pattern: VCPPattern) -> str:
    pivot = round(float(pattern.pivot or 0.0), 2)
    source = f"{ticker}|{pattern.base_start.date()}|{pivot:.2f}"
    return hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]


def _volume_ratio(history: pd.DataFrame) -> float | None:
    if history.empty:
        return None
    prior = history["Volume"].iloc[-51:-1]
    if prior.empty or float(prior.mean()) == 0:
        return None
    return float(history["Volume"].iloc[-1] / prior.mean())


def _record_transition(
    event: VCPEvent,
    date: pd.Timestamp,
    stage: str,
    pivot: float | None,
    close: float,
    volume_ratio: float | None = None,
) -> None:
    if event.transitions and event.transitions[-1].stage == stage:
        return
    event.transitions.append(
        EventTransition(
            date=pd.Timestamp(date),
            stage=stage,
            pivot=pivot,
            close=float(close),
            volume_ratio=volume_ratio,
        )
    )


def _invalidation_level(event: VCPEvent) -> float:
    legs = event.initial_pattern.legs
    if legs:
        return float(legs[-1].trough)
    return float(event.initial_pattern.pivot or 0.0) * 0.95


def scan_ticker_events(
    ticker: str,
    history: pd.DataFrame,
    min_history: int = 252,
    max_lifetime: int = MAX_EVENT_LIFETIME,
) -> list[VCPEvent]:
    """Scan one ticker sequentially without using future pattern information."""
    history = history.sort_index()
    events: list[VCPEvent] = []
    active: VCPEvent | None = None
    seen_event_ids: set[str] = set()

    for position in range(max(1, min_history), len(history)):
        current = history.iloc[:position + 1]
        date = pd.Timestamp(current.index[-1])
        close = float(current["Close"].iloc[-1])
        previous_close = float(current["Close"].iloc[-2])
        pattern = detect_vcp(current)

        if active is not None:
            age = int((history.index > active.first_seen)[:position + 1].sum())
            known_pivot = active.breakout_pivot
            crossed = bool(
                known_pivot is not None
                and previous_close <= known_pivot
                and close > known_pivot
            )
            if crossed:
                ratio = _volume_ratio(current)
                active.breakout_date = date
                active.breakout_volume_ratio = ratio
                active.volume_confirmed = bool(ratio is not None and ratio >= BREAKOUT_VOLUME_RATIO)
                _record_transition(active, date, "breakout", known_pivot, close, ratio)
                events.append(active)
                active = None
                continue
            if close < _invalidation_level(active) or pattern.reject_reason in {
                "below_ma50",
                "long_trend_not_rising",
            }:
                active.invalidated_date = date
                _record_transition(active, date, "invalidated", known_pivot, close)
                events.append(active)
                active = None
                continue
            if age >= max_lifetime:
                active.expired_date = date
                _record_transition(active, date, "expired", known_pivot, close)
                events.append(active)
                active = None
                continue
            if pattern.accepted:
                stage = pattern.stage
                if stage == "forming" and pattern.distance_to_pivot_pct is not None:
                    if -NEAR_PIVOT_PCT <= pattern.distance_to_pivot_pct <= 0:
                        stage = "near_pivot"
                _record_transition(active, date, stage, known_pivot, close)
                if stage == "near_pivot" and active.near_pivot_date is None:
                    active.near_pivot_date = date
            continue

        if not pattern.accepted or pattern.pivot is None or pattern.base_start is None:
            continue
        identity = _event_id(ticker, pattern)
        if identity in seen_event_ids:
            continue
        active = VCPEvent(
            event_id=identity,
            ticker=ticker,
            base_start=pattern.base_start,
            first_seen=date,
            initial_pattern=pattern,
            breakout_pivot=float(pattern.pivot),
        )
        seen_event_ids.add(identity)
        stage = pattern.stage
        _record_transition(active, date, stage, pattern.pivot, close)
        if stage == "near_pivot":
            active.near_pivot_date = date

    if active is not None:
        events.append(active)
    return events
