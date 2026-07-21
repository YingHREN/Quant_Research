from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from research.market_data import bar_on, next_bar


def _atr_asof(history: pd.DataFrame, observation_date: pd.Timestamp, periods: int = 20):
    known = history.loc[history.index <= pd.Timestamp(observation_date)]
    if len(known) < periods + 1:
        return None
    previous = known["Close"].shift(1)
    true_range = pd.concat(
        [
            known["High"] - known["Low"],
            (known["High"] - previous).abs(),
            (known["Low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return float(true_range.iloc[-periods:].mean())


def forward_outcomes(
    history: pd.DataFrame,
    benchmark: pd.DataFrame,
    observation_date: pd.Timestamp,
    horizons: Sequence[int] = (20, 40, 60),
) -> dict:
    """Compute next-open outcomes without filling missing execution bars."""
    result = {
        "entry_date": None,
        "entry_price": None,
        "missing_entry_bar": False,
    }
    for horizon in horizons:
        result[f"ret_{horizon}"] = None
        result[f"spy_ret_{horizon}"] = None
        result[f"rel_ret_{horizon}"] = None
        result[f"missing_exit_{horizon}"] = False

    entry = next_bar(history, pd.Timestamp(observation_date))
    if entry is None:
        result["missing_entry_bar"] = True
        return result
    entry_date, entry_bar = entry
    entry_price = float(entry_bar["Open"])
    result["entry_date"] = entry_date
    result["entry_price"] = entry_price
    future = history.loc[history.index > pd.Timestamp(observation_date)]

    benchmark_entry = bar_on(benchmark, entry_date)
    for horizon in horizons:
        if len(future) < horizon:
            result[f"missing_exit_{horizon}"] = True
            continue
        exit_date = pd.Timestamp(future.index[horizon - 1])
        exit_price = float(future["Close"].iloc[horizon - 1])
        stock_return = exit_price / entry_price - 1 if entry_price else None
        result[f"ret_{horizon}"] = stock_return

        benchmark_exit = bar_on(benchmark, exit_date)
        if benchmark_entry is None or benchmark_exit is None:
            continue
        benchmark_open = float(benchmark_entry["Open"])
        benchmark_return = (
            float(benchmark_exit["Close"]) / benchmark_open - 1 if benchmark_open else None
        )
        result[f"spy_ret_{horizon}"] = benchmark_return
        if stock_return is not None and benchmark_return is not None:
            result[f"rel_ret_{horizon}"] = stock_return - benchmark_return
    return result


def barrier_outcome(
    history: pd.DataFrame,
    observation_date: pd.Timestamp,
    horizon: int = 40,
    up_atr: float = 2.0,
    down_atr: float = 1.0,
) -> dict:
    """Label a frozen-ATR path from the next open; double touches are ambiguous."""
    result = {
        "barrier_label": None,
        "barrier_day": None,
        "entry_date": None,
        "entry_price": None,
        "atr_asof": None,
        "missing_entry_bar": False,
    }
    atr = _atr_asof(history, observation_date)
    entry = next_bar(history, observation_date)
    if atr is None:
        result["barrier_label"] = "insufficient_history"
        return result
    if entry is None:
        result["barrier_label"] = "missing_entry"
        result["missing_entry_bar"] = True
        return result

    entry_date, entry_bar = entry
    entry_price = float(entry_bar["Open"])
    upper = entry_price + up_atr * atr
    lower = entry_price - down_atr * atr
    result.update({"entry_date": entry_date, "entry_price": entry_price, "atr_asof": atr})

    future = history.loc[history.index >= entry_date].iloc[:horizon]
    for day, (_, bar) in enumerate(future.iterrows(), start=1):
        hit_up = float(bar["High"]) >= upper
        hit_down = float(bar["Low"]) <= lower
        if hit_up and hit_down:
            result.update({"barrier_label": "ambiguous", "barrier_day": day})
            return result
        if hit_up:
            result.update({"barrier_label": "up", "barrier_day": day})
            return result
        if hit_down:
            result.update({"barrier_label": "down", "barrier_day": day})
            return result
    result.update({"barrier_label": "timeout", "barrier_day": len(future)})
    return result
