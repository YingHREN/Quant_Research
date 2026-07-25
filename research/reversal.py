"""Causal reversal-pattern features aligned to daily OHLCV rows.

Pivot dates describe where an extreme occurred.  A pivot is never exposed
until the later session that confirms the reversal, so callers can safely use
the returned rows in point-in-time research.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ConfirmedPivot:
    index: int
    date: pd.Timestamp
    price: float
    kind: str
    confirmed_index: int
    confirmed_date: pd.Timestamp


def _iso(value: pd.Timestamp | None) -> str | None:
    return pd.Timestamp(value).date().isoformat() if value is not None else None


def _atr(history: pd.DataFrame) -> pd.Series:
    previous = history["Close"].shift(1)
    true_range = pd.concat(
        [
            history["High"] - history["Low"],
            (history["High"] - previous).abs(),
            (history["Low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(20, min_periods=1).mean()


def _line_value(first: ConfirmedPivot, second: ConfirmedPivot, index: int) -> float:
    span = second.index - first.index
    slope = (second.price - first.price) / span
    return float(second.price + slope * (index - second.index))


def _meets_higher_low_threshold(previous: float, latest: float, atr20: float) -> bool:
    return bool(latest >= previous + 0.25 * atr20)


def _empty_row() -> dict[str, object]:
    return {
        "prior_high_resistance": None,
        "prior_high_breakout_pct": None,
        "prior_high_breakout": False,
        "descending_trendline": None,
        "trendline_breakout": False,
        "trendline_high_1_date": None,
        "trendline_high_2_date": None,
        "latest_confirmed_high_date": None,
        "latest_confirmed_high_confirmed_date": None,
        "latest_confirmed_low_date": None,
        "latest_confirmed_low_price": None,
        "latest_confirmed_low_confirmed_date": None,
        "higher_low_confirmed": False,
        "higher_low_previous_date": None,
        "higher_low_previous_price": None,
        "higher_low_latest_date": None,
        "higher_low_latest_price": None,
        "higher_low_confirmation_date": None,
        "reversal_signal_count": 0,
        "reversal_candidate": False,
    }


def build_reversal_rows(history: pd.DataFrame) -> list[dict[str, object]]:
    """Return causal reversal features for every input row."""
    if history.empty:
        return []
    history = history.sort_index()
    close = history["Close"].astype(float).to_numpy()
    atr = _atr(history).to_numpy(dtype=float)
    dates = pd.DatetimeIndex(history.index)

    rows: list[dict[str, object]] = []
    confirmed_highs: list[ConfirmedPivot] = []
    confirmed_lows: list[ConfirmedPivot] = []
    trend = 0
    high_index = low_index = extreme_index = 0
    high_price = low_price = extreme_price = float(close[0])
    previous_trendline: float | None = None
    previous_trendline_pair: tuple[int, int] | None = None

    for index, price_value in enumerate(close):
        price = float(price_value)
        threshold = float(np.clip((atr[index] / price) * 1.5, 0.03, 0.10)) if price else 0.03
        confirmed_today: ConfirmedPivot | None = None

        if index > 0 and trend == 0:
            if price > high_price:
                high_index, high_price = index, price
            if price < low_price:
                low_index, low_price = index, price
            if low_price and (price - low_price) / low_price >= threshold:
                confirmed_today = ConfirmedPivot(
                    low_index, dates[low_index], low_price, "L", index, dates[index]
                )
                confirmed_lows.append(confirmed_today)
                trend = 1
                extreme_index, extreme_price = index, price
            elif high_price and (high_price - price) / high_price >= threshold:
                confirmed_today = ConfirmedPivot(
                    high_index, dates[high_index], high_price, "H", index, dates[index]
                )
                confirmed_highs.append(confirmed_today)
                trend = -1
                extreme_index, extreme_price = index, price
        elif index > 0 and trend == 1:
            if price > extreme_price:
                extreme_index, extreme_price = index, price
            if extreme_price and (extreme_price - price) / extreme_price >= threshold:
                confirmed_today = ConfirmedPivot(
                    extreme_index,
                    dates[extreme_index],
                    extreme_price,
                    "H",
                    index,
                    dates[index],
                )
                confirmed_highs.append(confirmed_today)
                trend = -1
                extreme_index, extreme_price = index, price
        elif index > 0 and trend == -1:
            if price < extreme_price:
                extreme_index, extreme_price = index, price
            if extreme_price and (price - extreme_price) / extreme_price >= threshold:
                confirmed_today = ConfirmedPivot(
                    extreme_index,
                    dates[extreme_index],
                    extreme_price,
                    "L",
                    index,
                    dates[index],
                )
                confirmed_lows.append(confirmed_today)
                trend = 1
                extreme_index, extreme_price = index, price

        row = _empty_row()
        if index >= 20:
            resistance = float(np.max(close[index - 20:index]))
            prior_resistance = (
                float(np.max(close[index - 21:index - 1])) if index >= 21 else None
            )
            row["prior_high_resistance"] = resistance
            row["prior_high_breakout_pct"] = (price / resistance - 1.0) * 100
            row["prior_high_breakout"] = bool(
                prior_resistance is not None
                and close[index - 1] <= prior_resistance
                and price > resistance
            )

        if confirmed_highs:
            latest_high = confirmed_highs[-1]
            row["latest_confirmed_high_date"] = _iso(latest_high.date)
            row["latest_confirmed_high_confirmed_date"] = _iso(
                latest_high.confirmed_date
            )
        if confirmed_lows:
            latest_low = confirmed_lows[-1]
            row["latest_confirmed_low_date"] = _iso(latest_low.date)
            row["latest_confirmed_low_price"] = float(latest_low.price)
            row["latest_confirmed_low_confirmed_date"] = _iso(
                latest_low.confirmed_date
            )
        active_trendline: float | None = None
        active_trendline_pair: tuple[int, int] | None = None
        if len(confirmed_highs) >= 2:
            first, second = confirmed_highs[-2:]
            if second.price < first.price and second.index > first.index:
                line = _line_value(first, second, index)
                active_trendline = line
                active_trendline_pair = (first.index, second.index)
                row["descending_trendline"] = line
                row["trendline_high_1_date"] = _iso(first.date)
                row["trendline_high_2_date"] = _iso(second.date)
                row["trendline_breakout"] = bool(
                    index > 0
                    and previous_trendline_pair == active_trendline_pair
                    and previous_trendline is not None
                    and close[index - 1] <= previous_trendline
                    and price > line
                )

        if (
            confirmed_today is not None
            and confirmed_today.kind == "L"
            and len(confirmed_lows) >= 2
        ):
            previous_low, latest_low = confirmed_lows[-2:]
            if _meets_higher_low_threshold(
                previous_low.price, latest_low.price, float(atr[index])
            ):
                row.update(
                    {
                        "higher_low_confirmed": True,
                        "higher_low_previous_date": _iso(previous_low.date),
                        "higher_low_previous_price": previous_low.price,
                        "higher_low_latest_date": _iso(latest_low.date),
                        "higher_low_latest_price": latest_low.price,
                        "higher_low_confirmation_date": _iso(
                            latest_low.confirmed_date
                        ),
                    }
                )

        count = sum(
            bool(row[key])
            for key in (
                "prior_high_breakout",
                "trendline_breakout",
                "higher_low_confirmed",
            )
        )
        row["reversal_signal_count"] = count
        row["reversal_candidate"] = count >= 2
        rows.append(row)
        previous_trendline = active_trendline
        previous_trendline_pair = active_trendline_pair

    return rows
