"""Causal near-resistance zones derived from daily price structure."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = ("Open", "High", "Low", "Close", "Volume")
OUTPUT_KEYS = (
    "near_resistance_lower",
    "near_resistance_upper",
    "near_resistance_mid",
    "near_resistance_distance_pct",
    "near_resistance_score",
    "near_resistance_sources",
    "far_resistance",
)
SOURCE_ORDER = (
    "ema20",
    "sma50",
    "sma200",
    "recent_high_10",
    "confirmed_swing_high",
    "descending_trendline",
    "twenty_session_pivot",
)
FAR_SOURCES = frozenset(
    ("confirmed_swing_high", "descending_trendline", "twenty_session_pivot")
)


def _empty_row() -> dict[str, object]:
    return {
        "near_resistance_lower": None,
        "near_resistance_upper": None,
        "near_resistance_mid": None,
        "near_resistance_distance_pct": None,
        "near_resistance_score": None,
        "near_resistance_sources": [],
        "far_resistance": None,
    }


def _finite(value) -> bool:
    try:
        return value is not None and isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _candidate(source: str, value, close: float):
    if not _finite(value):
        return None
    price = float(value)
    return (price, source) if price > close else None


def _clusters(candidates, maximum_gap: float):
    groups = []
    for candidate in sorted(candidates, key=lambda item: (item[0], item[1])):
        if not groups or candidate[0] - groups[-1][-1][0] > maximum_gap:
            groups.append([candidate])
        else:
            groups[-1].append(candidate)
    return groups


def _resolved_swing_high(
    reversal_row: Mapping[str, object],
    close: pd.Series,
):
    raw_date = reversal_row.get("latest_confirmed_high_date")
    if not raw_date:
        return None
    try:
        timestamp = pd.Timestamp(raw_date)
    except (TypeError, ValueError):
        return None
    if timestamp not in close.index:
        return None
    return float(close.loc[timestamp])


def _strength_score(
    frame: pd.DataFrame,
    position: int,
    lower: float,
    upper: float,
    atr: float,
    source_count: int,
    volume_ratio: pd.Series,
) -> int:
    start = max(0, position - 19)
    recent = frame.iloc[start : position + 1]
    tolerance = atr * 0.25
    touched = (recent["High"] >= lower - tolerance) & (
        recent["Low"] <= upper + tolerance
    )
    rejection = touched & (recent["Close"] < lower)
    touch_points = min(30, int(touched.sum()) * 10)
    rejection_points = min(20, int(rejection.sum()) * 10)

    confirmation = False
    for offset, rejected in enumerate(rejection):
        if not bool(rejected):
            continue
        row_position = start + offset
        source = frame.iloc[row_position]
        candle_range = float(source["High"] - source["Low"])
        upper_wick = float(
            source["High"] - max(source["Open"], source["Close"])
        )
        wick_confirmed = candle_range > 0.0 and upper_wick / candle_range >= 0.4
        row_volume_ratio = volume_ratio.iloc[row_position]
        volume_confirmed = _finite(row_volume_ratio) and row_volume_ratio >= 1.2
        if wick_confirmed or volume_confirmed:
            confirmation = True
            break

    return min(
        100,
        min(40, source_count * 15)
        + touch_points
        + rejection_points
        + (10 if confirmation else 0),
    )


def build_near_resistance_rows(
    history: pd.DataFrame,
    reversal_rows: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Return a causal near-resistance diagnosis for every daily bar."""
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history must be a DataFrame")
    missing = [column for column in REQUIRED_COLUMNS if column not in history]
    if missing:
        raise ValueError(f"history is missing required columns: {missing}")
    if len(reversal_rows) != len(history):
        raise ValueError("reversal_rows must align one-to-one with history")
    if history.empty:
        return []

    frame = history.sort_index().loc[:, REQUIRED_COLUMNS].astype(float)
    close = frame["Close"]
    previous_close = close.shift(1)
    true_range = pd.concat(
        (
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ),
        axis=1,
    ).max(axis=1)
    atr20 = true_range.rolling(20).mean()
    ema20 = close.ewm(span=20, adjust=False).mean()
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    recent_high_10 = frame["High"].rolling(10).max()
    prior_pivot = close.shift(1).rolling(20).max()
    volume_ratio = frame["Volume"] / frame["Volume"].rolling(20).mean()

    rows = []
    for position, (_, source) in enumerate(frame.iterrows()):
        row = _empty_row()
        current_close = float(source["Close"])
        atr = atr20.iloc[position]
        if not _finite(atr) or float(atr) <= 0.0 or current_close <= 0.0:
            rows.append(row)
            continue
        atr = float(atr)
        reversal = reversal_rows[position]
        raw_candidates = (
            _candidate("ema20", ema20.iloc[position], current_close),
            _candidate("sma50", sma50.iloc[position], current_close),
            _candidate("sma200", sma200.iloc[position], current_close),
            _candidate(
                "recent_high_10",
                recent_high_10.iloc[position],
                current_close,
            ),
            _candidate(
                "confirmed_swing_high",
                _resolved_swing_high(reversal, close),
                current_close,
            ),
            _candidate(
                "descending_trendline",
                reversal.get("descending_trendline"),
                current_close,
            ),
            _candidate(
                "twenty_session_pivot",
                prior_pivot.iloc[position],
                current_close,
            ),
        )
        candidates = [candidate for candidate in raw_candidates if candidate]
        if not candidates:
            rows.append(row)
            continue

        groups = _clusters(candidates, atr * 0.5)
        selected = groups[0]
        prices = [price for price, _ in selected]
        sources = [
            key
            for key in SOURCE_ORDER
            if any(source_key == key for _, source_key in selected)
        ]
        if len(selected) == 1:
            center = prices[0]
            lower = max(current_close, center - atr * 0.15)
            upper = center + atr * 0.15
        else:
            lower = min(prices)
            upper = max(prices)
        midpoint = (lower + upper) / 2.0

        far_candidates = [
            price
            for price, source_key in candidates
            if source_key in FAR_SOURCES and price > upper + atr * 0.5
        ]
        far_resistance = min(far_candidates) if far_candidates else None
        row.update(
            {
                "near_resistance_lower": float(lower),
                "near_resistance_upper": float(upper),
                "near_resistance_mid": float(midpoint),
                "near_resistance_distance_pct": float(
                    (lower / current_close - 1.0) * 100.0
                ),
                "near_resistance_score": _strength_score(
                    frame,
                    position,
                    lower,
                    upper,
                    atr,
                    len(sources),
                    volume_ratio,
                ),
                "near_resistance_sources": sources,
                "far_resistance": (
                    None if far_resistance is None else float(far_resistance)
                ),
            }
        )
        if not all(
            value is None or _finite(value)
            for key, value in row.items()
            if key != "near_resistance_sources"
        ):
            raise ValueError("near-resistance output must be finite or null")
        rows.append(row)
    return rows
