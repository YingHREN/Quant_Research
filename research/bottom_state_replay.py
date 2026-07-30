"""Pure causal replay of the evidence consumed by the bottom-state model."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from research.bottom_state import build_bottom_state_rows
from research.entry_signals import build_entry_signal_rows
from research.market_gate import build_market_gate_frame
from web.factors.builtin import build_chart_rows
from web.services.analysis import AnalysisContext
from web.services.bottom_state import bottom_evidence_frame
from web.services.entry_signals import merge_entry_signal_rows
from web.services.historical_demand_support import (
    attach_historical_demand_support_rows,
)
from web.services.supply_demand import attach_supply_demand_rows


def build_bottom_state_replay(
    ticker: str,
    histories: Mapping[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return causal evidence and bottom-state rows for one ticker."""
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")
    normalized = str(ticker).strip().upper()
    if not normalized:
        raise ValueError("ticker must be non-empty")
    history = histories.get(normalized)
    if not isinstance(history, pd.DataFrame) or history.empty:
        raise ValueError("ticker history is unavailable")
    history = history.sort_index().copy(deep=True)
    benchmark = histories.get("SPY")
    if isinstance(benchmark, pd.DataFrame):
        benchmark = benchmark.sort_index().copy(deep=True)
    else:
        benchmark = None
    context = AnalysisContext(
        ticker=normalized,
        observation_date=pd.Timestamp(history.index[-1]),
        history=history,
        benchmark_history=benchmark,
    )
    chart = build_chart_rows(context)
    chart = merge_entry_signal_rows(
        chart,
        build_entry_signal_rows(history),
    )
    attach_supply_demand_rows(chart, normalized, histories)
    attach_historical_demand_support_rows(chart, normalized, histories)
    _attach_market_states(chart, histories)
    evidence = bottom_evidence_frame(chart)
    states = build_bottom_state_rows(history, evidence)
    return evidence, states


def _attach_market_states(
    chart: list[dict],
    histories: Mapping[str, pd.DataFrame],
) -> None:
    frame = build_market_gate_frame(histories)
    by_date = {
        pd.Timestamp(timestamp).date().isoformat(): str(row["market_state"])
        for timestamp, row in frame.iterrows()
    }
    for row in chart:
        row["market_regime_gate"] = {
            "market_state": by_date.get(row["time"], "unavailable")
        }
