"""Built-in dashboard factors backed by the project's existing calculations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from factors.compute import (
    _atr,
    adr_pct,
    avg_dollar_volume,
    high_low_52w,
    moving_averages,
    overheat,
    pivot_breakout,
    pocket_pivot,
    rs_rating,
    tight_platform,
    vcp_analysis,
    volume_stats,
)
from research.momentum import momentum_features
from run import market_uptrend
from scoring.engine import evaluate
from web.contracts import iso_date
from web.factors.base import FactorGroup
from web.factors.registry import FactorRegistry
from web.services.analysis import AnalysisContext


@dataclass(frozen=True)
class BuiltinFactor:
    key: str
    label: str
    group: str
    direction: str
    description: str
    _compute: Callable[[AnalysisContext], Any]
    _format: Callable[[Any], str] = str
    version: str = "builtin-v1"
    methodology: str = "Computed point in time from local OHLCV history through the observation date."
    overview: bool = True

    def compute(self, context: AnalysisContext):
        return self._compute(context)

    def format(self, value):
        return self._format(value)


def _cached(context, key, function):
    return context.cached(f"builtin:{key}", function)


def _vcp(context):
    return _cached(context, "strict_vcp", lambda: vcp_analysis(context.history_asof()))


def _platform(context):
    return _cached(
        context, "tight_platform", lambda: tight_platform(context.history_asof())
    )


def _pivot(context):
    return _cached(context, "pivot", lambda: pivot_breakout(context.history_asof()))


def _overheat(context):
    return _cached(context, "overheat", lambda: overheat(context.history_asof()))


def _momentum(context):
    def calculate():
        benchmark = context.benchmark_asof()
        if benchmark is None or benchmark.empty:
            return {}
        return momentum_features(
            context.history_asof(), benchmark, context.observation_date
        )

    return _cached(context, "momentum", calculate)


def _legacy_inputs(context):
    def calculate():
        history = context.history_asof()
        benchmark = context.benchmark_asof()
        return {
            "ticker": context.ticker,
            "ma": moving_averages(history),
            "rs": rs_rating(history, benchmark),
            "hl52": high_low_52w(history),
            "adr_pct": adr_pct(history),
            "avg_dollar_vol": avg_dollar_volume(history),
            "volume": volume_stats(history),
            "pocket_pivot": pocket_pivot(history),
            "vcp": _vcp(context),
            "pivot": _pivot(context),
            "overheat": _overheat(context),
            "fundamentals": context.metadata.get("fundamentals", {}),
        }

    return _cached(context, "legacy_inputs", calculate)


def _legacy_score(context):
    benchmark = context.benchmark_asof()
    result = evaluate(
        _legacy_inputs(context),
        market_ok=market_uptrend(benchmark),
        price_only=False,
    )
    return result.total


def _chart_value(key):
    def compute(context):
        rows = build_chart_rows(context)
        return rows[-1][key] if rows else None

    return compute


def _momentum_value(key):
    return lambda context: _momentum(context).get(key)


def _dict_format(value):
    if value.get("reject_reason"):
        return f"Rejected: {value['reject_reason']}"
    if value.get("reason"):
        return f"Rejected: {value['reason']}"
    return "Detected"


def _percent(value):
    return f"{value:.2f}%"


def _ratio(value):
    return f"{value:.2f}x"


def build_default_registry():
    """Return the ordered first-party factor collection used by the dashboard."""
    factors = [
        BuiltinFactor("close_vs_ema20_pct", "Close vs EMA20", "trend", "higher",
                      "Close relative to the point-in-time 20-session EMA.",
                      lambda c: _distance_from(c, "ema20"), _percent,
                      methodology="Close divided by the 20-session exponential moving average, minus one, expressed in percent."),
        BuiltinFactor("close_vs_sma50_pct", "Close vs SMA50", "trend", "higher",
                      "Close relative to the point-in-time 50-session average.",
                      lambda c: _distance_from(c, "sma50"), _percent,
                      methodology="Close divided by the trailing 50-session simple moving average, minus one, expressed in percent."),
        BuiltinFactor("close_vs_sma200_pct", "Close vs SMA200", "trend", "higher",
                      "Close relative to the point-in-time 200-session average.",
                      lambda c: _distance_from(c, "sma200"), _percent,
                      methodology="Close divided by the trailing 200-session simple moving average, minus one, expressed in percent."),
        BuiltinFactor("mom_3_1", "3-1 month momentum", "momentum", "higher",
                      "Three-month return excluding the latest month.",
                      _momentum_value("mom_3_1"), lambda v: f"{v:.2%}",
                      methodology="Point-in-time 63-session return ending 21 sessions before the observation date."),
        BuiltinFactor("mom_6_1", "6-1 month momentum", "momentum", "higher",
                      "Six-month return excluding the latest month.",
                      _momentum_value("mom_6_1"), lambda v: f"{v:.2%}",
                      methodology="Point-in-time 126-session return ending 21 sessions before the observation date."),
        BuiltinFactor("mom_12_1", "12-1 month momentum", "momentum", "higher",
                      "Twelve-month return excluding the latest month.",
                      _momentum_value("mom_12_1"), lambda v: f"{v:.2%}",
                      methodology="Point-in-time 252-session return ending 21 sessions before the observation date."),
        BuiltinFactor("strict_vcp", "Strict VCP", "structure", "neutral",
                      "Precision-first VCP diagnostic, including its rejection reason.",
                      _vcp, _dict_format,
                      methodology="Canonical strict VCP gates evaluate trend, base depth, contraction legs, volume dry-up, and extension."),
        BuiltinFactor("tight_platform", "Tight platform", "structure", "neutral",
                      "High-level tight-platform diagnostic, including its rejection reason.",
                      _platform, _dict_format,
                      methodology="Canonical tight-platform gates evaluate trend, high proximity, 20-session width, efficiency, and volume dry-up."),
        BuiltinFactor("pivot_distance_pct", "Distance to pivot", "structure", "neutral",
                      "Close distance from the prior 20-session pivot.",
                      _chart_value("pivot_distance_pct"), _percent,
                      methodology="Close divided by the highest close in the prior 20 sessions, minus one, expressed in percent."),
        BuiltinFactor("volume_ratio", "Volume ratio", "volume", "higher",
                      "Current volume divided by its point-in-time 20-session average.",
                      _chart_value("volume_ratio"), _ratio,
                      methodology="Session volume divided by the trailing 20-session simple average volume."),
        BuiltinFactor("atr20_pct", "ATR20", "risk", "lower",
                      "Twenty-session average true range as a percentage of close.",
                      lambda c: _atr_percent(c), _percent,
                      methodology="Canonical 20-session average true range divided by observation-date close and expressed in percent."),
        BuiltinFactor("realized_vol_63", "63-day realized volatility", "risk", "lower",
                      "Annualized volatility from up to 63 point-in-time daily returns.",
                      _momentum_value("realized_vol_63"), lambda v: f"{v:.2%}",
                      methodology="Standard deviation of up to 63 daily close returns annualized with the square root of 252."),
        BuiltinFactor("overheat_score", "Overheat", "risk", "lower",
                      "Existing non-monotonic extension and volatility diagnostic.",
                      lambda c: _overheat(c).get("overheat_score"), lambda v: f"{v:.1f}",
                      methodology="Canonical descriptive composite of ATR-normalized short returns, moving-average extension, streak, and recent range."),
        BuiltinFactor("legacy_score", "Traditional rules score", "legacy", "neutral",
                      "Not validated for prediction; retained only as a traditional-rule diagnostic.",
                      _legacy_score, lambda v: f"{v:.1f}",
                      methodology="Existing traditional rule engine evaluated point in time with price and benchmark inputs; not validated for prediction.",
                      overview=False),
    ]
    groups = [
        FactorGroup("trend", "Trend", "Moving-average position diagnostics.", True),
        FactorGroup("momentum", "Momentum", "Point-in-time trailing returns excluding the latest month.", True),
        FactorGroup("structure", "VCP / structure", "Canonical strict-VCP, platform, and pivot diagnostics.", True),
        FactorGroup("volume", "Volume / price", "Volume participation relative to trailing local history.", True),
        FactorGroup("risk", "Risk", "Range, volatility, and extension diagnostics.", True),
        FactorGroup("legacy", "Traditional rules", "Legacy descriptive rule output retained for comparison only.", False),
    ]
    return FactorRegistry(factors, group_metadata=groups)


def _distance_from(context, average_key):
    rows = build_chart_rows(context)
    if not rows:
        return None
    close, average = rows[-1]["close"], rows[-1][average_key]
    return None if average in (None, 0) else (close / average - 1) * 100


def _atr_percent(context):
    history = context.history_asof()
    if history.empty:
        return None
    value = _cached(context, "atr20", lambda: _atr(history, 20))
    close = float(history["Close"].iloc[-1])
    return None if value is None or close == 0 else value / close * 100


def _optional_float(value):
    return None if pd.isna(value) else float(value)


def build_chart_rows(context: AnalysisContext):
    """Build point-in-time chart and crosshair values without remote data access."""
    def calculate():
        history = context.history_asof()
        if history.empty:
            return []
        close = history["Close"].astype(float)
        high = history["High"].astype(float)
        low = history["Low"].astype(float)
        volume = history["Volume"].astype(float)
        previous_close = close.shift(1)
        daily_return = close.pct_change()
        volume_change = volume.pct_change()
        true_range = pd.concat(
            [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
            axis=1,
        ).max(axis=1)
        ema20 = close.ewm(span=20, adjust=False).mean()
        sma50 = close.rolling(50).mean()
        sma200 = close.rolling(200).mean()
        atr20 = true_range.rolling(20).mean()
        volume_ma20 = volume.rolling(20).mean()
        volume_ratio = volume / volume_ma20
        volume_ratio_change = volume_ratio.diff()
        pivot = close.shift(1).rolling(20).max()
        pivot_distance = (close / pivot - 1) * 100
        pivot_distance_change = pivot_distance.diff()
        atr20.iloc[:20] = float("nan")
        pivot.iloc[:21] = float("nan")
        above_ema20 = close >= ema20
        above_sma50 = close >= sma50

        rows = []
        for position, (timestamp, source) in enumerate(history.iterrows()):
            row_close = float(source["Close"])
            row_pivot = _optional_float(pivot.iloc[position])
            crossed_ema20 = bool(
                position > 0
                and above_ema20.iloc[position] != above_ema20.iloc[position - 1]
            )
            crossed_sma50 = bool(
                position > 0
                and pd.notna(sma50.iloc[position - 1])
                and above_sma50.iloc[position] != above_sma50.iloc[position - 1]
            )
            rows.append(
                {
                    "time": iso_date(timestamp),
                    "open": float(source["Open"]),
                    "high": float(source["High"]),
                    "low": float(source["Low"]),
                    "close": row_close,
                    "volume": float(source["Volume"]),
                    "daily_return": _optional_float(daily_return.iloc[position]),
                    "true_range_pct": (
                        float(true_range.iloc[position] / row_close * 100)
                        if row_close
                        else None
                    ),
                    "volume_change": _optional_float(volume_change.iloc[position]),
                    "volume_ma20": _optional_float(volume_ma20.iloc[position]),
                    "volume_ratio": _optional_float(volume_ratio.iloc[position]),
                    "volume_ratio_change": _optional_float(
                        volume_ratio_change.iloc[position]
                    ),
                    "ema20": _optional_float(ema20.iloc[position]),
                    "sma50": _optional_float(sma50.iloc[position]),
                    "sma200": _optional_float(sma200.iloc[position]),
                    "atr20": _optional_float(atr20.iloc[position]),
                    "pivot": row_pivot,
                    "pivot_distance_pct": _optional_float(
                        pivot_distance.iloc[position]
                    ),
                    "pivot_distance_change_pct": _optional_float(
                        pivot_distance_change.iloc[position]
                    ),
                    "crossed_ema20": crossed_ema20,
                    "crossed_sma50": crossed_sma50,
                    "ema20_cross": (
                        "above" if above_ema20.iloc[position] else "below"
                    ) if crossed_ema20 else None,
                    "sma50_cross": (
                        "above" if above_sma50.iloc[position] else "below"
                    ) if crossed_sma50 else None,
                }
            )
        return rows

    return _cached(context, "chart_rows", calculate)
