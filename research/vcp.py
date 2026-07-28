from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ContractionLeg:
    peak_date: pd.Timestamp
    trough_date: pd.Timestamp
    peak: float
    trough: float
    depth_pct: float
    mean_volume: float
    confirmed: bool = True


@dataclass(frozen=True)
class VCPPattern:
    asof_date: pd.Timestamp
    accepted: bool
    stage: str
    base_start: pd.Timestamp | None
    base_end: pd.Timestamp
    legs: tuple[ContractionLeg, ...]
    pending_leg: ContractionLeg | None
    pivot: float | None
    pivot_date: pd.Timestamp | None
    distance_to_pivot_pct: float | None
    reject_reason: str | None
    metrics: dict[str, float]


def pattern_evidence(pattern: VCPPattern) -> dict:
    """Return one JSON-ready, factor-compatible view of a VCP pattern."""

    def date_text(value):
        return None if value is None else pd.Timestamp(value).date().isoformat()

    def leg_evidence(leg):
        return {
            "peak_date": date_text(leg.peak_date),
            "trough_date": date_text(leg.trough_date),
            "peak": float(leg.peak),
            "trough": float(leg.trough),
            "depth_pct": float(leg.depth_pct),
            "mean_volume": float(leg.mean_volume),
            "confirmed": bool(leg.confirmed),
        }

    legs = [leg_evidence(leg) for leg in pattern.legs]
    pending = (
        None
        if pattern.pending_leg is None
        else leg_evidence(pattern.pending_leg)
    )
    depths = [leg["depth_pct"] for leg in legs]
    leg_volumes = [leg["mean_volume"] for leg in legs]
    volume_ratio = pattern.metrics.get("volume_dryup_ratio")
    terminal_range = pattern.metrics.get("terminal_range_pct")
    base_depth = pattern.metrics.get("base_depth_pct")
    distance = pattern.distance_to_pivot_pct
    return {
        "accepted": bool(pattern.accepted),
        "stage": pattern.stage,
        "asof_date": date_text(pattern.asof_date),
        "base_start": date_text(pattern.base_start),
        "base_end": date_text(pattern.base_end),
        "pivot": (
            None if pattern.pivot is None else float(pattern.pivot)
        ),
        "vcp_pivot": (
            None if pattern.pivot is None else float(pattern.pivot)
        ),
        "pivot_date": date_text(pattern.pivot_date),
        "distance_to_pivot_pct": (
            None if distance is None else float(distance)
        ),
        "contractions": depths,
        "contraction_legs": legs,
        "n_contractions": len(legs),
        "pending_leg": pending,
        "is_decreasing": bool(pattern.accepted and len(legs) >= 2),
        "vol_dryup": (
            None if volume_ratio is None else float(volume_ratio) < 1.0
        ),
        "vola_contract": (
            False
            if terminal_range is None or base_depth is None
            else float(terminal_range) < float(base_depth)
        ),
        "tightness": (
            None if terminal_range is None else float(terminal_range)
        ),
        "leg_vols_decreasing": (
            len(leg_volumes) >= 2
            and all(
                current >= following
                for current, following in zip(
                    leg_volumes[:-1],
                    leg_volumes[1:],
                )
            )
        ),
        "is_extended": bool(distance is not None and distance > 5.0),
        "reject_reason": pattern.reject_reason,
        **{key: float(value) for key, value in pattern.metrics.items()},
    }


def _rejected(history: pd.DataFrame, reason: str) -> VCPPattern:
    asof = pd.Timestamp(history.index[-1])
    return VCPPattern(
        asof_date=asof,
        accepted=False,
        stage="none",
        base_start=None,
        base_end=asof,
        legs=(),
        pending_leg=None,
        pivot=None,
        pivot_date=None,
        distance_to_pivot_pct=None,
        reject_reason=reason,
        metrics={},
    )


def _atr_pct(history: pd.DataFrame, periods: int = 20) -> float:
    sample = history.iloc[-(periods + 1):]
    previous = sample["Close"].shift(1)
    true_range = pd.concat(
        [
            sample["High"] - sample["Low"],
            (sample["High"] - previous).abs(),
            (sample["Low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    close = float(sample["Close"].iloc[-1])
    return float(true_range.iloc[-periods:].mean() / close * 100) if close else 3.0


def _zigzag_indices(close: np.ndarray, threshold_pct: float):
    """Return confirmed close-based pivot indices and the live extremum.

    Confirmed indices provide timing. Leg prices are measured from High/Low in
    `_legs_from_pivots`, so close is not the sole source of swing geometry.
    """
    threshold = threshold_pct / 100.0
    pivots: list[tuple[int, str]] = []
    trend = 0
    extreme_index = 0
    extreme_price = float(close[0])
    high_index = low_index = 0
    high_price = low_price = float(close[0])

    for index in range(1, len(close)):
        price = float(close[index])
        if trend == 0:
            if price > high_price:
                high_index, high_price = index, price
            if price < low_price:
                low_index, low_price = index, price
            if low_price and (price - low_price) / low_price >= threshold:
                pivots.append((low_index, "L"))
                trend = 1
                extreme_index, extreme_price = index, price
            elif high_price and (high_price - price) / high_price >= threshold:
                pivots.append((high_index, "H"))
                trend = -1
                extreme_index, extreme_price = index, price
            continue
        if trend == 1:
            if price > extreme_price:
                extreme_index, extreme_price = index, price
            if extreme_price and (extreme_price - price) / extreme_price >= threshold:
                pivots.append((extreme_index, "H"))
                trend = -1
                extreme_index, extreme_price = index, price
                continue
        if trend == -1:
            if price < extreme_price:
                extreme_index, extreme_price = index, price
            if extreme_price and (price - extreme_price) / extreme_price >= threshold:
                pivots.append((extreme_index, "L"))
                trend = 1
                extreme_index, extreme_price = index, price

    live_kind = "H" if trend >= 0 else "L"
    return pivots, (extreme_index, live_kind)


def _legs_from_pivots(
    frame: pd.DataFrame, pivots: list[tuple[int, str]]
) -> tuple[ContractionLeg, ...]:
    legs = []
    for (peak_index, first_kind), (trough_index, second_kind) in zip(pivots[:-1], pivots[1:]):
        if first_kind != "H" or second_kind != "L" or trough_index <= peak_index:
            continue
        peak = float(frame["High"].iloc[peak_index])
        trough = float(frame["Low"].iloc[trough_index])
        depth = (peak - trough) / peak * 100 if peak else 0.0
        if depth < 2.0:
            continue
        mean_volume = float(frame["Volume"].iloc[peak_index:trough_index + 1].mean())
        legs.append(
            ContractionLeg(
                peak_date=pd.Timestamp(frame.index[peak_index]),
                trough_date=pd.Timestamp(frame.index[trough_index]),
                peak=peak,
                trough=trough,
                depth_pct=depth,
                mean_volume=mean_volume,
            )
        )
    return tuple(legs[-4:])


def _pending_leg(
    frame: pd.DataFrame,
    pivots: list[tuple[int, str]],
    live: tuple[int, str],
) -> ContractionLeg | None:
    if not pivots:
        return None
    last_index, last_kind = pivots[-1]
    live_index, live_kind = live
    if last_kind == "L" and live_kind == "H" and live_index > last_index:
        trough_after_peak = frame["Low"].iloc[live_index:]
        if len(trough_after_peak) == 0:
            return None
        trough_date = pd.Timestamp(trough_after_peak.idxmin())
        peak = float(frame["High"].iloc[live_index])
        trough = float(trough_after_peak.min())
        return ContractionLeg(
            peak_date=pd.Timestamp(frame.index[live_index]),
            trough_date=trough_date,
            peak=peak,
            trough=trough,
            depth_pct=(peak - trough) / peak * 100 if peak else 0.0,
            mean_volume=float(frame.loc[frame.index[live_index]:trough_date, "Volume"].mean()),
            confirmed=False,
        )
    if last_kind == "H" and live_kind == "L" and live_index > last_index:
        peak = float(frame["High"].iloc[last_index])
        trough = float(frame["Low"].iloc[live_index])
        return ContractionLeg(
            peak_date=pd.Timestamp(frame.index[last_index]),
            trough_date=pd.Timestamp(frame.index[live_index]),
            peak=peak,
            trough=trough,
            depth_pct=(peak - trough) / peak * 100 if peak else 0.0,
            mean_volume=float(frame["Volume"].iloc[last_index:live_index + 1].mean()),
            confirmed=False,
        )
    return None


def detect_vcp(history: pd.DataFrame, asof: pd.Timestamp | None = None) -> VCPPattern:
    """Detect an interpretable VCP candidate using information through `asof`."""
    if asof is not None:
        history = history.loc[history.index <= pd.Timestamp(asof)]
    history = history.sort_index()
    if len(history) < 60:
        return _rejected(history, "insufficient_history")

    close = history["Close"].astype(float)
    price = float(close.iloc[-1])
    ma50 = float(close.iloc[-50:].mean())
    ma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else None
    if price <= ma50:
        return _rejected(history, "below_ma50")
    if ma200 is not None and ma50 < ma200:
        return _rejected(history, "long_trend_not_rising")

    threshold_pct = float(np.clip(_atr_pct(history) * 1.5, 3.0, 10.0))
    candidates = []
    saw_multiple_swings = False
    saw_non_decreasing = False

    for length in range(20, min(80, len(history)) + 1):
        frame = history.iloc[-length:]
        high = float(frame["High"].max())
        low = float(frame["Low"].min())
        depth_pct = (high - low) / high * 100 if high else 100.0
        if depth_pct > 35.0:
            continue
        values = frame["Close"].to_numpy(dtype=float)
        total_travel = float(np.abs(np.diff(values)).sum())
        base_return = values[-1] / values[0] - 1 if values[0] else 0.0
        efficiency = abs(values[-1] - values[0]) / total_travel if total_travel else 1.0
        if base_return > 0.15 and efficiency > 0.50:
            continue

        pivots, live = _zigzag_indices(values, threshold_pct)
        legs = _legs_from_pivots(frame, pivots)
        if len(legs) < 2:
            continue
        saw_multiple_swings = True
        depths = [leg.depth_pct for leg in legs]
        decreasing = all(depths[index + 1] <= depths[index] * 0.95 for index in range(len(depths) - 1))
        last_first_ratio = depths[-1] / depths[0]
        if not decreasing or last_first_ratio > 0.75 or depths[0] - depths[-1] < 3.0:
            saw_non_decreasing = True
            continue

        terminal_range_pct = (
            (float(frame["High"].iloc[-10:].max()) - float(frame["Low"].iloc[-10:].min()))
            / price
            * 100
        )
        last_pivot_date = max(leg.trough_date for leg in legs)
        days_since_last_pivot = int((frame.index > last_pivot_date).sum())
        score = (
            len(legs),
            -last_first_ratio,
            -terminal_range_pct,
            -days_since_last_pivot,
            -length,
        )
        candidates.append((score, frame, pivots, live, legs, depth_pct, terminal_range_pct))

    if not candidates:
        if saw_non_decreasing:
            return _rejected(history, "contractions_not_decreasing")
        if saw_multiple_swings:
            return _rejected(history, "contractions_not_decreasing")
        values = history["Close"].iloc[-80:].to_numpy(dtype=float)
        travel = float(np.abs(np.diff(values)).sum())
        efficiency = abs(values[-1] - values[0]) / travel if travel else 1.0
        reason = "monotonic_rally" if efficiency > 0.80 else "insufficient_swings"
        return _rejected(history, reason)

    _, frame, pivots, live, legs, base_depth_pct, terminal_range_pct = max(
        candidates, key=lambda item: (item[0], -history.index.get_loc(item[1].index[0]))
    )
    highs = [(index, kind) for index, kind in pivots if kind == "H"]
    pivot_index = highs[-1][0]
    pivot = float(frame["High"].iloc[pivot_index])
    pivot_date = pd.Timestamp(frame.index[pivot_index])
    distance = (price - pivot) / pivot * 100 if pivot else None
    pending = _pending_leg(frame, pivots, live)
    if distance is not None and distance > 5.0:
        accepted = False
        stage = "extended"
        reject_reason = "extended_above_buy_zone"
    elif distance is not None and distance > 0.0:
        accepted = False
        stage = "breakout"
        reject_reason = "already_above_pivot"
    else:
        accepted = True
        stage = (
            "near_pivot"
            if distance is not None and -5.0 <= distance <= 0.0
            else "forming"
        )
        reject_reason = None
    depths = np.asarray([leg.depth_pct for leg in legs], dtype=float)
    volume_ratio = float(frame["Volume"].iloc[-10:].mean() / frame["Volume"].iloc[-50:].mean())

    return VCPPattern(
        asof_date=pd.Timestamp(history.index[-1]),
        accepted=accepted,
        stage=stage,
        base_start=pd.Timestamp(frame.index[0]),
        base_end=pd.Timestamp(frame.index[-1]),
        legs=legs,
        pending_leg=pending,
        pivot=pivot,
        pivot_date=pivot_date,
        distance_to_pivot_pct=distance,
        reject_reason=reject_reason,
        metrics={
            "adaptive_pct": threshold_pct,
            "base_length": float(len(frame)),
            "base_depth_pct": base_depth_pct,
            "last_first_ratio": float(depths[-1] / depths[0]),
            "contraction_slope": float(np.polyfit(np.arange(len(depths)), depths, 1)[0]),
            "terminal_range_pct": terminal_range_pct,
            "volume_dryup_ratio": volume_ratio,
        },
    )
